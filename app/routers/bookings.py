from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_member
from app.db import get_db
from app.models import Booking, Member
from app.schemas import ConfirmPaymentIn
from app.services.payment_mock import PaymentTimeoutError
from app.services.payments import confirm_booking_payment

router = APIRouter(prefix="/bookings", tags=["bookings"])


@router.post("/{booking_id}/confirm-payment")
def confirm_payment(
    booking_id: int,
    payload: ConfirmPaymentIn,
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
):
    booking = (
        db.query(Booking)
        .filter(Booking.id == booking_id, Booking.member_id == member.id)
        .first()
    )
    if not booking:
        raise HTTPException(status_code=404, detail="booking not found")

    try:
        return confirm_booking_payment(booking.id, payload.amount_cents, db)
    except PaymentTimeoutError:
        raise HTTPException(status_code=504, detail="payment provider timed out")
