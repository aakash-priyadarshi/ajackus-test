import io
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from urllib.error import HTTPError

from fastapi.testclient import TestClient

from app import main
from app.config import settings
from app.llm_client import FakeLLMClient, LLMError, OpenAIClient
from app.models import Club, ConversationMessage, Member, MemberAttribute
from app.services import embedding_pipeline, matching_service
from app.services.embedding_pipeline import refresh_member_embedding
from app.services.matching_service import build_member_profile_text, rank_candidates

client = TestClient(main.app)


def make_records(db):
    db.add_all([
        Club(id="riverside", name="Riverside"),
        Club(id="oakhurst", name="Oakhurst"),
    ])
    admin = Member(club_id="riverside", name="Admin", email="admin@example.com", token="tok-admin", role="admin")
    service = Member(club_id="riverside", name="Service", email="service@example.com", token="tok-service", role="service")
    member = Member(club_id="riverside", name="Member", email="member@example.com", token="tok-member", role="member")
    other_admin = Member(club_id="oakhurst", name="Other admin", email="other@example.com", token="tok-other-admin", role="admin")
    db.add_all([admin, service, member, other_admin])
    db.commit()

    messages = [
        ConversationMessage(member_id=member.id, club_id="riverside", body=f"message {number}")
        for number in range(1, 6)
    ]
    foreign_message = ConversationMessage(member_id=other_admin.id, club_id="oakhurst", body="foreign club message")
    db.add_all([*messages, foreign_message])
    db.commit()
    return admin, service, member, other_admin, messages, foreign_message


def post_extract(token, message_ids, club_id="riverside"):
    return client.post(
        f"/clubs/{club_id}/extract-attributes",
        json={"message_ids": message_ids},
        headers={"X-Member-Token": token},
    )


def test_extracts_and_skips_source_message_on_repeat(db, monkeypatch):
    _, _, member, _, messages, _ = make_records(db)
    fake = FakeLLMClient([
        {"kind": "need", "text": "Seeking a fractional CFO.", "confidence": 0.94, "restricted": False}
    ])
    monkeypatch.setattr("app.routers.extraction.llm_client", fake)

    first = post_extract("tok-admin", [messages[0].id])
    second = post_extract("tok-admin", [messages[0].id])

    assert first.status_code == second.status_code == 200
    assert first.json()["results"][0]["status"] == "processed"
    assert second.json()["results"][0]["status"] == "already_processed"
    assert len(fake.calls) == 1
    rows = db.query(MemberAttribute).filter(MemberAttribute.source_message_id == messages[0].id).all()
    assert len(rows) == 1
    assert rows[0].member_id == member.id and rows[0].club_id == "riverside"


def test_concurrent_extraction_creates_one_set_of_rows(db, monkeypatch):
    _, _, _, _, messages, _ = make_records(db)
    barrier = Barrier(2)

    class ConcurrentFake:
        def extract_attributes(self, _message_text):
            barrier.wait(timeout=5)
            return [{"kind": "interest", "text": "Enjoys running.", "confidence": 0.9, "restricted": False}]

    monkeypatch.setattr("app.routers.extraction.llm_client", ConcurrentFake())

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: post_extract("tok-admin", [messages[0].id]), range(2)))

    assert all(response.status_code == 200 for response in responses)
    statuses = [response.json()["results"][0]["status"] for response in responses]
    assert sorted(statuses) == ["already_processed", "processed"]
    db.expire_all()
    assert db.query(MemberAttribute).filter(MemberAttribute.source_message_id == messages[0].id).count() == 1


def test_admin_or_club_scoped_service_required(db, monkeypatch):
    _, _, _, _, messages, _ = make_records(db)
    fake = FakeLLMClient([])
    monkeypatch.setattr("app.routers.extraction.llm_client", fake)

    member_response = post_extract("tok-member", [messages[0].id])
    other_club_admin_response = post_extract("tok-other-admin", [messages[0].id])
    service_response = post_extract("tok-service", [messages[0].id])

    assert member_response.status_code == 403
    assert other_club_admin_response.status_code == 403
    assert service_response.status_code == 200
    assert service_response.json()["results"][0]["status"] == "processed"
    assert fake.calls == ["message 1"]


def test_foreign_club_message_fails_without_disclosing_or_calling_model(db, monkeypatch):
    _, _, _, _, _, foreign_message = make_records(db)
    fake = FakeLLMClient([])
    monkeypatch.setattr("app.routers.extraction.llm_client", fake)

    response = post_extract("tok-admin", [foreign_message.id])

    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result == {
        "message_id": foreign_message.id,
        "status": "failed",
        "error": "message not found in requested club",
    }
    assert fake.calls == []


def test_permanent_message_failure_does_not_stop_later_messages(db, monkeypatch):
    _, _, _, _, messages, _ = make_records(db)

    class SelectiveFake:
        def __init__(self):
            self.calls = []

        def extract_attributes(self, message_text):
            self.calls.append(message_text)
            if message_text == "message 3":
                raise LLMError("permanent test failure")
            return [{"kind": "context", "text": message_text, "confidence": 0.8, "restricted": False}]

    fake = SelectiveFake()
    monkeypatch.setattr("app.routers.extraction.llm_client", fake)
    response = post_extract("tok-admin", [message.id for message in messages])

    assert response.status_code == 200
    results = response.json()["results"]
    assert [result["status"] for result in results] == ["processed", "processed", "failed", "processed", "processed"]
    assert "permanent test failure" in results[2]["error"]
    assert fake.calls == [f"message {number}" for number in range(1, 6)]
    assert db.query(MemberAttribute).filter(MemberAttribute.source_message_id == messages[2].id).count() == 0
    assert db.query(MemberAttribute).filter(MemberAttribute.source_message_id == messages[3].id).count() == 1


def test_empty_extraction_is_valid_and_writes_no_sentinel_rows(db, monkeypatch):
    _, _, _, _, messages, _ = make_records(db)
    fake = FakeLLMClient([])
    monkeypatch.setattr("app.routers.extraction.llm_client", fake)

    response = post_extract("tok-admin", [messages[0].id])

    assert response.status_code == 200
    assert response.json()["results"][0] == {
        "message_id": messages[0].id,
        "status": "processed",
        "attributes": [],
    }
    assert db.query(MemberAttribute).filter(MemberAttribute.source_message_id == messages[0].id).count() == 0


def test_restricted_attributes_are_excluded_from_all_profile_vectors(db, monkeypatch):
    _, _, member, _, _, _ = make_records(db)
    candidate = Member(club_id="riverside", name="Candidate", email="candidate@example.com", token="tok-candidate")
    db.add(candidate)
    db.commit()
    db.add_all([
        MemberAttribute(member_id=member.id, club_id="riverside", kind="interest", text="Enjoys padel.", confidence=0.9, restricted=False),
        MemberAttribute(member_id=member.id, club_id="riverside", kind="context", text="Has a clinical diagnosis.", confidence=0.99, restricted=True),
        MemberAttribute(member_id=member.id, club_id="oakhurst", kind="offer", text="Wrong club attribute.", confidence=1.0, restricted=False),
        MemberAttribute(member_id=candidate.id, club_id="riverside", kind="offer", text="Offers networking.", confidence=0.9, restricted=False),
    ])
    db.commit()
    profile_text = build_member_profile_text(member, db)
    assert profile_text == "Enjoys padel."

    vectors = []

    def fake_embed(text):
        vectors.append(text)
        return [1.0] * settings.embed_dim

    monkeypatch.setattr(embedding_pipeline.embedding_client, "embed", fake_embed)
    monkeypatch.setattr(matching_service.embedding_client, "embed", fake_embed)
    refresh_member_embedding(member, db)
    candidate.profile_embedding = [-1.0] * settings.embed_dim  # simulate an old vector that must not be trusted
    db.commit()
    rank_candidates(member, "business", db)

    assert vectors == ["Enjoys padel.", "Enjoys padel.", "Offers networking."]
    assert all("clinical diagnosis" not in vector for vector in vectors)


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


def test_openai_client_retries_rate_limit_and_server_error_with_backoff():
    attributes = [{"kind": "need", "text": "Needs a CFO.", "confidence": 0.9, "restricted": False}]
    provider_body = json.dumps({
        "choices": [{"message": {"content": json.dumps({"attributes": attributes})}}]
    }).encode()
    attempts = []
    delays = []

    def fake_open(request, timeout):
        attempts.append((request, timeout))
        if len(attempts) == 1:
            raise HTTPError(request.full_url, 429, "rate limited", {"Retry-After": "0.25"}, io.BytesIO())
        if len(attempts) == 2:
            raise HTTPError(request.full_url, 503, "unavailable", {}, io.BytesIO())
        return FakeResponse(provider_body)

    client = OpenAIClient(api_key="test-only", model="test-model", opener=fake_open, sleeper=delays.append)
    result = client.extract_attributes("I need a fractional CFO.")

    assert result == attributes
    assert len(attempts) == 3
    assert delays == [0.25, 1.0]
    assert attempts[0][0].full_url == OpenAIClient.endpoint
    assert attempts[0][0].get_header("Authorization") == "Bearer test-only"
    assert attempts[0][1] == OpenAIClient.timeout_seconds
    request_body = json.loads(attempts[0][0].data)
    assert request_body["response_format"]["type"] == "json_schema"
    # Some supported models reject sampling overrides, including temperature=0.
    assert "temperature" not in request_body


def test_openai_client_fails_fast_without_key(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", None)
    client = OpenAIClient(api_key="", opener=lambda *_args, **_kwargs: None, sleeper=lambda _delay: None)
    try:
        client.extract_attributes("A sample message")
    except LLMError as error:
        assert "OPENAI_API_KEY" in str(error)
    else:
        raise AssertionError("missing API key should fail before any request")
