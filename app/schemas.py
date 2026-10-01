from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConfirmPaymentIn(BaseModel):
    amount_cents: int = Field(strict=True, gt=0, le=2_147_483_647)


class SessionTurnIn(BaseModel):
    intent: Literal["book", "affirm", "refund_dispute"]
    party_size: int | None = Field(default=None, strict=True, gt=0, le=2_147_483_647)
    booking_id: int | None = Field(default=None, strict=True, gt=0, le=2_147_483_647)
    amount_cents: int | None = Field(default=None, strict=True, gt=0, le=2_147_483_647)
    simulate_crash: bool = Field(default=False, strict=True)


AttributeKind = Literal["need", "offer", "context", "interest"]


class ExtractedAttributeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: AttributeKind
    text: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    restricted: bool

    @field_validator("text")
    @classmethod
    def strip_nonempty_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("attribute text must not be blank")
        return value


class ExtractAttributesIn(BaseModel):
    message_ids: list[Annotated[int, Field(strict=True, gt=0)]] = Field(min_length=1)
