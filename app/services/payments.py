from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Booking, ConversationSession, PaymentAttempt
from app.services.payment_mock import PaymentTimeoutError, payment_mock_client


class SimulatedCrash(Exception):
    """Simulate a crash after the provider charges but before finalization."""


def _lock_payment_context(booking_id: int, db: Session):
    # Always lock sessions before bookings, including payments made directly
    # through the booking endpoint, to avoid competing state transitions.
    session = (
        db.query(ConversationSession)
        .filter(ConversationSession.booking_id == booking_id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    booking = (
        db.query(Booking)
        .filter(Booking.id == booking_id)
        .populate_existing()
        .with_for_update()
        .one()
    )
    return session, booking


def confirm_booking_payment(
    booking_id: int, amount_cents: int, db: Session, *, simulate_crash: bool = False
) -> dict:
    session, booking = _lock_payment_context(booking_id, db)
    if amount_cents <= 0 or amount_cents != booking.amount_cents:
        raise HTTPException(status_code=422, detail="payment amount must match the booking price")
    if session is not None and session.status == "escalated":
        raise HTTPException(status_code=409, detail="session requires staff review")
    if booking.status not in {"pending", "confirmed"}:
        raise HTTPException(status_code=409, detail="booking cannot be paid in its current state")

    attempt = (
        db.query(PaymentAttempt)
        .filter(PaymentAttempt.booking_id == booking.id)
        .order_by((PaymentAttempt.status == "succeeded").desc(), PaymentAttempt.id.desc())
        .first()
    )
    if booking.status == "confirmed" and (attempt is None or attempt.status != "succeeded"):
        raise HTTPException(status_code=409, detail="confirmed booking needs payment reconciliation")

    if attempt is None or attempt.status == "failed":
        # A definite decline can be tried again with a new key. Pending or
        # unknown outcomes must keep their existing key until reconciled.
        attempt = PaymentAttempt(
            booking_id=booking.id,
            amount_cents=booking.amount_cents,
            idempotency_key=str(uuid4()),
            status="pending",
        )
        db.add(attempt)
    elif attempt.amount_cents != booking.amount_cents:
        raise HTTPException(status_code=409, detail="booking price changed after payment was started")
    elif attempt.status != "succeeded" and attempt.idempotency_key is None:
        raise HTTPException(status_code=409, detail="existing payment has no replay key; staff review required")

    # The intent, key and (for sessions) booking must survive a provider
    # timeout or a crash. Never hold only uncommitted state before charging.
    db.commit()

    # Committing released our locks. Reload both rows under locks, since a
    # concurrent retry may already have completed this payment.
    session, booking = _lock_payment_context(booking_id, db)
    db.refresh(attempt)
    if session is not None and session.status == "escalated":
        raise HTTPException(status_code=409, detail="session requires staff review")
    if booking.amount_cents != attempt.amount_cents:
        raise HTTPException(status_code=409, detail="booking price changed after payment was started")
    if booking.status not in {"pending", "confirmed"}:
        raise HTTPException(status_code=409, detail="booking cannot be paid in its current state")

    if attempt.status not in {"succeeded", "failed"}:
        try:
            result = payment_mock_client.charge(
                attempt.amount_cents,
                idempotency_key=attempt.idempotency_key,
            )
        except PaymentTimeoutError:
            attempt.status = "unknown"
            db.commit()
            raise
        if simulate_crash:
            raise SimulatedCrash("process died after charge, before payment finalization")
        attempt.status = result.status

    if attempt.status == "succeeded":
        booking.status = "confirmed"
        if session is not None:
            session.status = "confirmed"
            session.awaiting_confirmation = False
    db.commit()
    return {"status": attempt.status, "attempt_id": attempt.id, "booking_status": booking.status}
