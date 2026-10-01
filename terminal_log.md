
## Original payment bug reproduction (baseline code)

Baseline: committed `HEAD` source, isolated PostgreSQL database and local mock payment provider.

Health check: `GET /health` → `200 {"status":"ok"}`.

`GET http://127.0.0.1:8001/sessions/debug/payment-charge-log`
`X-Member-Token: trace-admin`
Response status: `200`
Response body: `{"charges":[]}`

`POST http://127.0.0.1:8001/bookings/1/confirm-payment`
`X-Member-Token: trace-member`
`Content-Type: application/json`
Request body: `{"amount_cents":8000}`
Response status: `200`
Response body: `{"status":"succeeded","attempt_id":1,"booking_status":"confirmed"}`

`POST http://127.0.0.1:8001/bookings/1/confirm-payment`
`X-Member-Token: trace-member`
`Content-Type: application/json`
Request body: `{"amount_cents":8000}`
Response status: `200`
Response body: `{"status":"succeeded","attempt_id":2,"booking_status":"confirmed"}`

`GET http://127.0.0.1:8001/sessions/debug/payment-charge-log`
`X-Member-Token: trace-admin`
Response status: `200`
Response body: `{"charges":[8000,8000]}`

Baseline database evidence: booking_id=`1`, attempts_for_booking=`2`, distinct_bookings=`1`, booking_status=`confirmed`. Mock provider ledger: `{"charges":[8000,8000]}`.

---

## Local payment verification (2026-10-01)

App: `http://127.0.0.1:8000` (Uvicorn startup completed; `/health` returned `{"status":"ok"}`).

Scenario: the repository documents `999999` cents as the mock provider timeout trigger. The previous literal trace was not present in the repository, so this replays that documented timeout/retry case against the current code. A dedicated pending quote was added directly to the local database; no seed script or seed data was changed.

`GET http://127.0.0.1:8000/sessions/debug/payment-charge-log`
`X-Member-Token: riverside-admin`
Response status: `200`
Response body: `{"charges":[]}`

`POST http://127.0.0.1:8000/bookings/3/confirm-payment`
`X-Member-Token: riverside-member-1`
`Content-Type: application/json`
Request body: `{"amount_cents":999999}`
Response status: `504`
Response body: `{"detail":"payment provider timed out"}`

After first timeout: booking_id=`3`, attempt_count=`1`, attempt_status=`unknown`, idempotency_key=`1520bf6a-8343-48cf-9ded-04e13397adf7`, booking_status=`pending`.

`POST http://127.0.0.1:8000/bookings/3/confirm-payment`
`X-Member-Token: riverside-member-1`
`Content-Type: application/json`
Request body: `{"amount_cents":999999}`
Response status: `200`
Response body: `{"status":"succeeded","attempt_id":1,"booking_status":"confirmed"}`

After retry: booking_id=`3`, attempt_count=`1`, distinct_idempotency_keys=`1`, same_key_reused=`true`, attempt_status=`succeeded`, booking_status=`confirmed`.

`GET http://127.0.0.1:8000/sessions/debug/payment-charge-log`
`X-Member-Token: riverside-admin`
Response status: `200`
Response body: `{"charges":[999999]}`

Provider mock ledger after timeout retry: literal response `{"charges":[999999]}`; charges for timeout quote `999999` cents: `1`.

`POST http://127.0.0.1:8000/bookings/1/confirm-payment`
`X-Member-Token: riverside-member-1`
`Content-Type: application/json`
Request body: `{"amount_cents":8000}`
Response status: `200`
Response body: `{"status":"succeeded","attempt_id":2,"booking_status":"confirmed"}`

Normal payment verification: booking_id=`1`, stored quote=`8000` cents, attempt_status=`succeeded`, booking_status=`confirmed`.

`GET http://127.0.0.1:8000/sessions/debug/payment-charge-log`
`X-Member-Token: riverside-admin`
Response status: `200`
Response body: `{"charges":[999999,8000]}`

Final provider mock ledger: literal response `{"charges":[999999,8000]}`. The timeout quote appears once and the normal payment appears once.


## Final verification (2026-10-01)

Command: `python -m pytest tests/test_payment_safety.py tests/test_bookings.py tests/test_sessions.py -q`

```text
.................................                                        [100%]
33 passed, 65 warnings in 3.66s
```

Command: `python -m pytest -q`

```text
.....................................                                    [100%]
37 passed, 72 warnings in 3.32s
```

Command: `git diff --check`

```text
(exit code 0; no output)
```
