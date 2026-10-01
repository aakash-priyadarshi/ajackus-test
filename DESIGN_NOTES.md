## Part 2 — Payment confirmation

In `app/services/session_flow.py`, an `affirm` turn now confirms an existing booking selected by the session, and `app/services/payments.py` checks the amount against that booking's stored price. The payment intent is committed with the existing `PaymentAttempt.idempotency_key` before calling the provider, so retries use the same key and confirmed bookings do not charge twice. The production payment provider must honor that key across process restarts; the in-process mock demonstrates retries and simulated crashes without adding database schema.
