from dataclasses import dataclass
from threading import Lock

from app.config import settings


class PaymentTimeoutError(Exception):
    """Raised when the (mock) payment provider times out with an ambiguous
    response — the caller does not know whether the charge went through."""


@dataclass
class ChargeResult:
    status: str  # "succeeded" | "failed"


class PaymentMockClient:
    def __init__(self) -> None:
        self._charges: dict[str, tuple[int, ChargeResult]] = {}
        self._charge_log: list[int] = []
        self._lock = Lock()

    def charge(self, amount_cents: int, *, idempotency_key: str) -> ChargeResult:
        with self._lock:
            saved = self._charges.get(idempotency_key)
            created = saved is None
            if saved is not None:
                saved_amount, result = saved
                if saved_amount != amount_cents:
                    raise ValueError("idempotency key cannot be reused for a different charge")
            else:
                result = ChargeResult(status="succeeded" if amount_cents > 0 else "failed")
                self._charges[idempotency_key] = (amount_cents, result)
                self._charge_log.append(amount_cents)

        # The mock keeps results for this process lifetime. A real provider
        # must persist and honor these keys across process restarts.
        if created and amount_cents == settings.payment_timeout_trigger_cents:
            raise PaymentTimeoutError("provider timed out")
        return result

    @property
    def charge_log(self) -> list[int]:
        with self._lock:
            return list(self._charge_log)

    def reset(self) -> None:
        with self._lock:
            self._charges.clear()
            self._charge_log.clear()


payment_mock_client = PaymentMockClient()
