## Part 2 — Payment confirmation

In `app/services/session_flow.py`, an `affirm` turn now confirms an existing booking selected by the session, and `app/services/payments.py` checks the amount against that booking's stored price. The payment intent is committed with the existing `PaymentAttempt.idempotency_key` before calling the provider, so retries use the same key and confirmed bookings do not charge twice. The production payment provider must honor that key across process restarts; the in-process mock demonstrates retries and simulated crashes without adding database schema.

## Part 3 — Structured attribute extraction

`POST /clubs/{club_id}/extract-attributes` calls the real OpenAI Chat Completions API with JSON Schema output in production; tests replace the client with `FakeLLMClient`. Admin and service members are authorized only for their own `club_id`. The request processes each `message_id` independently. The retry strategy gives transient HTTP 408/409/425/429/5xx and network failures at most three attempts, honoring `Retry-After` when provided and otherwise backing off for 0.5 then 1 second.

The idempotency key is `MemberAttribute.source_message_id`, set to the originating `ConversationMessage.id`. The handler checks for existing rows, calls the model without a database lock, then locks the source message and checks again before inserting; this prevents concurrent requests from writing duplicates without holding locks across network calls. When the model returns zero attributes, no existing column can record that empty result, so a later request can call the model again but still writes no duplicate rows. No tables, columns, migrations, or seed data were added or changed.

If persistent caching of empty results becomes required, I would raise a processed marker with the schema owner because the existing fields cannot record completion without an attribute row.

Restricted attributes are stored with `MemberAttribute.restricted=True`. The matching representation is built in `app/services/matching_service.py::build_member_profile_text`, whose query requires `MemberAttribute.restricted.is_(False)` as well as matching member and club IDs; `refresh_member_embedding` and both the request-side and candidate vectors in `rank_candidates` call this builder. The ranker rebuilds candidate vectors from filtered attributes instead of trusting a possibly stale stored vector, so restricted text cannot influence its score.

The prompt was shaped using the seeded messages about seed fundraising, angel investing, anxiety, padel, fractional CFO work, and a chronic condition, along with the four labeled examples in `eval/golden_set.json`. The eval script calls the real OpenAI client for every golden message and scores kind, exact restricted status, and expected-keyword containment separately. Its pass threshold is 0.80 across those checks: with four records (12 checks), at least 10 must match, which gives a useful small-set gate without claiming statistical confidence from four examples.

If a batch's third message fails permanently, that result is returned as `failed`, its transaction is rolled back, and processing continues; message four can still be stored and returned as `processed`. The endpoint response includes one result per unique requested message ID and summary counts.

Set `OPENAI_API_KEY=...` in the project-root `.env` before using the endpoint or real eval. `OPENAI_MODEL` is optional and defaults to `gpt-4o-mini`; the recorded verification used `gpt-6-luna`.

The client uses the model's default sampling settings: the real API rejected `temperature=0` for the configured `gpt-6-luna` model, so the request omits that optional parameter.

The first real golden-set eval scored 9/12 (75%) because a recreational invitation was classified as an offer. The prompt now defines all four kinds and explicitly includes invitations to social or recreational activities under interest; the golden data and 80% threshold remain unchanged.

The final real eval using `gpt-6-luna` passed 12/12 checks (100%). The live endpoint demo used the existing isolated `kindred_test` database with copies of the unchanged club, member, and source-message records: the main database already had seeded attributes for every source message, so it would otherwise skip all real API calls. In a batch of five, the third entry was an Oakhurst message rejected by the Riverside endpoint while the fourth succeeded; repeating the identical batch left the same seven attribute IDs and values, and the source database snapshot remained unchanged. Genuine outputs, including the initial failures, are appended to `TERMINAL_LOG.md`.

## Closing judgment

I left the bonus grounded-introduction and grounded-knowledge work outside this Part 3 implementation. With another day, I would add those evidence-grounded flows and run a larger labeled extraction evaluation before tuning confidence thresholds or prompt examples.
