from typing import Any

from sqlalchemy.orm import Session

from app.llm_client import LLMClient
from app.models import ConversationMessage, MemberAttribute
from app.schemas import ExtractedAttributeOut


def _existing_attributes(db: Session, message_id: int) -> list[MemberAttribute]:
    return (
        db.query(MemberAttribute)
        .filter(MemberAttribute.source_message_id == message_id)
        .order_by(MemberAttribute.id)
        .all()
    )


def _attribute_payload(attribute: MemberAttribute) -> dict[str, Any]:
    return {
        "kind": attribute.kind,
        "text": attribute.text,
        "confidence": attribute.confidence,
        "restricted": attribute.restricted,
    }


def extract_message_attributes(
    db: Session, club_id: str, message_id: int, llm_client: LLMClient
) -> dict[str, Any]:
    message = (
        db.query(ConversationMessage)
        .filter(ConversationMessage.id == message_id, ConversationMessage.club_id == club_id)
        .first()
    )
    if message is None:
        return {
            "message_id": message_id,
            "status": "failed",
            "error": "message not found in requested club",
        }

    existing = _existing_attributes(db, message.id)
    if existing:
        return {
            "message_id": message.id,
            "status": "already_processed",
            "attributes": [_attribute_payload(attribute) for attribute in existing],
        }

    # Model/network work happens without a row lock. The message row is locked
    # and checked again before inserts so concurrent requests cannot duplicate
    # attributes, and the database lock is not held while waiting on OpenAI.
    drafts = [
        ExtractedAttributeOut.model_validate(item)
        for item in llm_client.extract_attributes(message.body)
    ]
    locked_message = (
        db.query(ConversationMessage)
        .filter(ConversationMessage.id == message_id, ConversationMessage.club_id == club_id)
        .with_for_update()
        .first()
    )
    if locked_message is None:
        return {
            "message_id": message_id,
            "status": "failed",
            "error": "message not found in requested club",
        }

    existing = _existing_attributes(db, locked_message.id)
    if existing:
        db.commit()
        return {
            "message_id": locked_message.id,
            "status": "already_processed",
            "attributes": [_attribute_payload(attribute) for attribute in existing],
        }

    rows = [
        MemberAttribute(
            member_id=locked_message.member_id,
            club_id=locked_message.club_id,
            kind=draft.kind,
            text=draft.text,
            confidence=draft.confidence,
            restricted=draft.restricted,
            source_message_id=locked_message.id,
        )
        for draft in drafts
    ]
    db.add_all(rows)
    db.commit()
    return {
        "message_id": locked_message.id,
        "status": "processed",
        "attributes": [draft.model_dump() for draft in drafts],
    }
