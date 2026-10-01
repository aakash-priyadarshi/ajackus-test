# Part 1 — Code review

## P1 — Payment confirmation can charge the same booking more than once

- **File:** `app/routers/bookings.py:28-41` (template baseline `4f4eefb`)
- **Category:** Data Integrity
- **Severity:** Critical
- **Description:** The endpoint sends every request to the provider before it records a `PaymentAttempt`, and it does not stop when the booking is already confirmed. A client retry after a successful but unobserved response therefore charges the same booking again. The nearby ownership filter checks the booking ID and member ID, but it does not check the booking's payment state or deduplicate the provider call.
- **Recommended fix:** Persist a payment attempt and stable idempotency key before contacting the provider, reuse that key for ambiguous retries, validate the amount against the stored quote, and make the provider call through one locked payment service.

### Literal baseline trace

Captured against the committed template baseline (`4f4eefb`) running locally on `127.0.0.1:8001`, with an isolated PostgreSQL database and the repository's mock provider. Both requests below used the same booking and amount:

```http
GET /sessions/debug/payment-charge-log HTTP/1.1
Host: 127.0.0.1:8001
X-Member-Token: trace-admin

HTTP/1.1 200
{"charges":[]}

POST /bookings/1/confirm-payment HTTP/1.1
Host: 127.0.0.1:8001
X-Member-Token: trace-member
Content-Type: application/json

{"amount_cents":8000}

HTTP/1.1 200
{"status":"succeeded","attempt_id":1,"booking_status":"confirmed"}

POST /bookings/1/confirm-payment HTTP/1.1
Host: 127.0.0.1:8001
X-Member-Token: trace-member
Content-Type: application/json

{"amount_cents":8000}

HTTP/1.1 200
{"status":"succeeded","attempt_id":2,"booking_status":"confirmed"}

GET /sessions/debug/payment-charge-log HTTP/1.1
Host: 127.0.0.1:8001
X-Member-Token: trace-admin

HTTP/1.1 200
{"charges":[8000,8000]}
```

The database confirmed two payment attempts for the same booking, and the mock provider ledger recorded two charges. This is a mock-provider reproduction, not a live payment.

The final fixed-code replay sent those same two 8000-cent requests. Both responses returned `attempt_id: 142`, the database contained one attempt, and the provider ledger was `{"charges":[8000]}`. The literal fixed responses and database checks are in [TERMINAL_LOG.md](TERMINAL_LOG.md), under "Final fixed-code payment trace matching the original 8000-cent replay".

## P1 — Session confirmation trusts a client price and can record a false confirmation

- **File:** `app/services/session_flow.py:31-55` (template baseline `4f4eefb`)
- **Category:** Data Integrity
- **Severity:** Critical
- **Description:** `affirm` accepts the amount from the request and charges before creating a booking or payment attempt, so a crash leaves no durable intent and a retry can charge again. After a definite provider decline, the flow still sets the session status to `confirmed`.
- **Recommended fix:** Bind the session to an existing server-priced quote, require the submitted amount to match that quote, persist the attempt with the existing idempotency key before calling the provider, and confirm the session only after a successful result.

## P1 — Knowledge search can return another club's content

- **File:** `app/routers/knowledge.py:19-38`
- **Category:** Data Isolation
- **Severity:** High
- **Description:** The route checks that the caller belongs to the requested club, but the similarity query does not filter `KnowledgeChunk.club_id`. A valid member can therefore receive nearest-neighbour results from other clubs.
- **Recommended fix:** Add `KnowledgeChunk.club_id == member.club_id` to the query before similarity ordering and limiting.

## P2 — Introduction lookup exposes attributes by caller-supplied member IDs

- **File:** `app/routers/introductions.py:19-24`
- **Category:** Security
- **Severity:** High
- **Description:** Authentication establishes only that the caller is a member; the two attribute queries accept arbitrary member IDs and do not restrict results to the caller's club or an authorized introduction. A caller who can enumerate IDs can retrieve member attributes across the club boundary.
- **Recommended fix:** Authorize both members against the caller's club and the introduction policy before loading or returning their attributes.
