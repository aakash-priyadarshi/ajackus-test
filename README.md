# Kindred Concierge — Assessment Starter

A small FastAPI + PostgreSQL/pgvector service simulating Kindred's member-matching and concierge backend. Used for the Round 3 take-home — see the assignment brief you were given for what to build.

## Assignment submission

Repository: https://github.com/aakash-priyadarshi/ajackus-test

Template: https://github.com/ajackus/attachai-ai-ml-eng. The original template commit and subsequent implementation commits are preserved without squashing.

**Screen recording link: TODO — paste the narrated recording URL here.**

| Required item | Location |
| --- | --- |
| Prioritized review and literal original payment bug trace | [REVIEW.md](REVIEW.md) |
| Setup, initial tests, bug/fix traces, real extraction/repeat, eval, final tests | [TERMINAL_LOG.md](TERMINAL_LOG.md) |
| Payment/session design, extraction idempotency and retry strategy, restricted filtering, closing judgment | [DESIGN_NOTES.md](DESIGN_NOTES.md) |
| Genuine Part 3 prompts, assistant outputs, and the user's correction | [.ai-conversations/part3.md](.ai-conversations/part3.md) |
| Real golden-set evaluator | [eval/run_extraction_eval.py](eval/run_extraction_eval.py) |
| Fake-client extraction tests | [tests/test_extraction.py](tests/test_extraction.py) |

Parts 1–3 are implemented. Bonus Parts 4a/4b were not attempted. Verified results: 9 Part 3 tests passed, the real eval passed 12/12 checks (100%, threshold 80%), and the full suite passed 46 tests. The seed data, golden set, and database schema are unchanged.

## Setup

1. `docker compose up -d db` — starts PostgreSQL/pgvector on `localhost:5434` with these Compose settings.
2. `python3 -m venv .venv && source .venv/bin/activate`
3. `pip install -r requirements.txt`
4. `cp .env.example .env` (defaults match the docker-compose service)
5. `python -m scripts.seed` — creates tables, wipes and re-seeds two clubs (`riverside`, `oakhurst`) with members, conversation history, extracted attributes, a knowledge base, and a couple of bookings. Prints every member's auth token when done.
6. `uvicorn app.main:app --reload` — runs the API on `http://localhost:8000`.

Run the tests with `python -m pytest` — they use the isolated `kindred_test` database. It is created by `init-scripts/init-test-db.sql` on the first container initialization. If an existing volume lacks that database, create it with `docker compose exec db psql -U kindred -d postgres -c "CREATE DATABASE kindred_test;"`. Tests clear their test database; `TEST_DATABASE_URL` can override it, but the database name must end in `_test`.

For extraction, set `OPENAI_API_KEY=your_actual_key` in the project-root `.env`. `OPENAI_MODEL` is optional and defaults to `gpt-4o-mini`; the recorded real verification used `OPENAI_MODEL=gpt-6-luna`. Restart the API after changing `.env`. Unit tests inject fake clients and make no OpenAI calls.

## Auth

There's no login flow — every member has a fixed bearer-style token, sent as `X-Member-Token`. The seed script prints all of them; the two you'll use most:

- `riverside-admin`, `oakhurst-admin` — the admin/service role for each club
- `riverside-member-1` through `riverside-member-6`, and the same for `oakhurst`

## What's already built

- `GET /clubs/{club_id}/knowledge/query?q=...` — RAG-style lookup over a club's knowledge base
- `GET /members/{member_id}/candidates?reason=...` — ranks other members in the same club as introduction candidates
- `GET /introductions/{member_a_id}/{member_b_id}?reason=...` — generates a human-readable reason for an introduction
- `POST /bookings/{booking_id}/confirm-payment` — charges a booking via a mock payment client
- `POST /sessions`, `GET /sessions/{id}`, `POST /sessions/{id}/turn` — a minimal multi-turn booking flow (book → confirm → charge), plus a `refund_dispute` intent that always escalates the session instead of being auto-resolved
- `GET /sessions/debug/payment-charge-log` (admin only) — every charge attempted against the mock payment client, in order
- `POST /clubs/{club_id}/extract-attributes` — real OpenAI extraction of the supplied `message_ids`, restricted to the club's admin/service members

The original endpoints use a local mock payment provider and deterministic embeddings from `app/embeddings.py`. Part 3's extraction endpoint and golden-set evaluator call the real OpenAI API through `app/llm_client.py`.

## Part 3 verification

```sh
python -m pytest tests/test_extraction.py -v
python eval/run_extraction_eval.py
python -m pytest -v
```

The evaluator reports kind, exact restricted status, and expected-keyword containment for each golden record and exits non-zero below the 80% threshold.

An extraction request uses `X-Member-Token: riverside-admin` and a JSON body such as `{"message_ids":[1,3,6,4,5]}`. The recorded live batch deliberately included an Oakhurst source message third to demonstrate club isolation and continued processing of the fourth message. Its repeat preserved the same seven attribute IDs and values, with zero new rows. The separate fake-client test covers a permanent LLM failure at message three.

The unchanged seed script already links attributes to every seeded source message, so a request against freshly seeded app data skips the model call. The recorded real demo used unchanged copies of the source records in the existing isolated test database with no initial extracted rows; details and literal responses are in `TERMINAL_LOG.md`.

## Notes for your own testing

- The mock payment client (`app/services/payment_mock.py`) always succeeds for a normal amount, always fails for `amount_cents <= 0`, and raises a simulated timeout (`PaymentTimeoutError`, surfaced as a 504) for `amount_cents: 999999`.
- A session's `turn` payload accepts an optional `"simulate_crash": true` field, for your own testing.
- `eval/golden_set.json` is a small labeled set of raw messages for the extraction pipeline's eval requirement (see the assignment brief, Part 3).
