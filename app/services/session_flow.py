from fastapi import HTTPException
from sqlalchemy.orm import Session as DbSession

from app.models import Booking, ConversationSession, PaymentAttempt
from app.services.payments import confirm_booking_payment


def advance_turn(session: ConversationSession, intent: str, payload: dict, db: DbSession) -> dict:
    if intent == "refund_dispute":
        session.status = "escalated"
        session.awaiting_confirmation = False
        db.commit()
        return {"status": "escalated"}

    if session.status in {"confirmed", "escalated"}:
        return {"status": session.status, "booking_id": session.booking_id}

    if intent == "book":
        if session.booking_id is not None:
            if payload.get("booking_id", session.booking_id) != session.booking_id:
                raise HTTPException(status_code=409, detail="session quote cannot be replaced")
            if db.query(PaymentAttempt).filter(PaymentAttempt.booking_id == session.booking_id).first():
                raise HTTPException(status_code=409, detail="payment is already in progress")
        booking_id = payload.get("booking_id", session.booking_id)
        if booking_id is not None:
            booking = (
                db.query(Booking)
                .filter(
                    Booking.id == booking_id,
                    Booking.member_id == session.member_id,
                    Booking.club_id == session.club_id,
                )
                .with_for_update()
                .first()
            )
            if booking is None:
                raise HTTPException(status_code=404, detail="quoted booking not found")
            if booking.status != "pending" or booking.amount_cents <= 0:
                raise HTTPException(status_code=409, detail="booking is not an unpaid quote")
            other_session = (
                db.query(ConversationSession)
                .filter(ConversationSession.booking_id == booking.id, ConversationSession.id != session.id)
                .first()
            )
            if other_session is not None:
                raise HTTPException(status_code=409, detail="quoted booking already belongs to a session")
            session.booking_id = booking.id
        party_size = payload.get("party_size")
        if party_size is not None:
            session.party_size = party_size
        session.awaiting_confirmation = session.party_size is not None and session.booking_id is not None
        db.commit()
        return {
            "status": (
                "awaiting_confirmation" if session.awaiting_confirmation
                else "need_booking" if session.booking_id is None else "need_party_size"
            ),
            "party_size": session.party_size,
            "booking_id": session.booking_id,
        }

    if intent == "affirm" and session.awaiting_confirmation and session.booking_id is not None:
        booking_id = session.booking_id
        booking = db.get(Booking, booking_id)
        amount_cents = payload.get("amount_cents", booking.amount_cents)
        result = confirm_booking_payment(
            booking_id, amount_cents, db, simulate_crash=payload.get("simulate_crash", False)
        )
        return {
            "status": "confirmed" if result["status"] == "succeeded" else "payment_failed",
            "payment_status": result["status"],
            "booking_id": booking_id,
        }

    if intent == "affirm":
        raise HTTPException(status_code=409, detail="select a quoted booking and party size before confirmation")

    return {"status": session.status}
