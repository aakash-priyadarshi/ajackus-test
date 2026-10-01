from typing import Literal

from pydantic import BaseModel, Field


class ConfirmPaymentIn(BaseModel):
    amount_cents: int = Field(strict=True, gt=0, le=2_147_483_647)


class SessionTurnIn(BaseModel):
    intent: Literal["book", "affirm", "refund_dispute"]
    party_size: int | None = Field(default=None, strict=True, gt=0, le=2_147_483_647)
    booking_id: int | None = Field(default=None, strict=True, gt=0, le=2_147_483_647)
    amount_cents: int | None = Field(default=None, strict=True, gt=0, le=2_147_483_647)
    simulate_crash: bool = Field(default=False, strict=True)
