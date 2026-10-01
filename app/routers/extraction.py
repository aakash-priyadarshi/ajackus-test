import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_member
from app.db import get_db
from app.llm_client import LLMClient, LLMError, OpenAIClient
from app.models import Member
from app.schemas import ExtractAttributesIn
from app.services.attribute_extraction import extract_message_attributes

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/clubs", tags=["attribute extraction"])
llm_client: LLMClient = OpenAIClient()


@router.post("/{club_id}/extract-attributes")
def extract_attributes(
    club_id: str,
    payload: ExtractAttributesIn,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    if member.role not in {"admin", "service"} or member.club_id != club_id:
        raise HTTPException(status_code=403, detail="admin or club-scoped service role required")

    results = []
    # Preserve input order while avoiding duplicate provider calls in one batch.
    for message_id in dict.fromkeys(payload.message_ids):
        try:
            results.append(extract_message_attributes(db, club_id, message_id, llm_client))
        except Exception as error:
            db.rollback()
            logger.warning(
                "Attribute extraction failed for message_id=%s club_id=%s error_type=%s",
                message_id,
                club_id,
                type(error).__name__,
            )
            public_error = str(error) if isinstance(error, LLMError) else "attribute extraction failed"
            results.append({"message_id": message_id, "status": "failed", "error": public_error})

    return {
        "club_id": club_id,
        "results": results,
        "summary": {
            "processed": sum(result["status"] == "processed" for result in results),
            "already_processed": sum(result["status"] == "already_processed" for result in results),
            "failed": sum(result["status"] == "failed" for result in results),
        },
    }
