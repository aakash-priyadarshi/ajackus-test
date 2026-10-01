from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models import Booking, Club, ConversationSession, Member, PaymentAttempt
from app.services.payment_mock import ChargeResult, payment_mock_client

client = TestClient(app)
headers = {"X-Member-Token": "tok-a"}


@pytest.fixture
def payer(db):
    db.add(Club(id="riverside", name="Riverside"))
    member = Member(club_id="riverside", name="A", email="a@example.com", token="tok-a")
    db.add(member)
    db.commit()
    return member


def make_booking(db, payer, amount=5000):
    booking = Booking(
        member_id=payer.id, club_id=payer.club_id, description="Dinner", amount_cents=amount
    )
    db.add(booking)
    db.commit()
    return booking.id


def charge_count():
    return len(payment_mock_client.charge_log)


def ready_session(db, payer, amount=5000):
    booking_id = make_booking(db, payer, amount)
    session_id = client.post("/sessions", headers=headers).json()["session_id"]
    response = client.post(
        f"/sessions/{session_id}/turn",
        json={"intent": "book", "party_size": 2, "booking_id": booking_id}, headers=headers
    )
    assert response.json()["status"] == "awaiting_confirmation"
    return session_id


def pay(booking_id, amount=5000):
    return client.post(
        f"/bookings/{booking_id}/confirm-payment", json={"amount_cents": amount}, headers=headers
    )


def affirm(session_id, amount=5000, **extra):
    return client.post(
        f"/sessions/{session_id}/turn",
        json={"intent": "affirm", "amount_cents": amount, **extra},
        headers=headers,
    )


def concurrent_calls(*calls):
    start = Barrier(len(calls))

    def run(call):
        start.wait(timeout=5)
        return call()

    with ThreadPoolExecutor(max_workers=len(calls)) as executor:
        futures = [executor.submit(run, call) for call in calls]
        return [future.result(timeout=15) for future in futures]


def test_repeated_payment_has_one_attempt_and_charge(db, payer):
    booking_id = make_booking(db, payer)
    first, second = pay(booking_id), pay(booking_id)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert db.query(PaymentAttempt).count() == 1
    assert charge_count() == 1


def test_concurrent_booking_payments_charge_once(db, payer):
    booking_id = make_booking(db, payer)
    responses = concurrent_calls(lambda: pay(booking_id), lambda: pay(booking_id))
    assert all(response.status_code == 200 for response in responses)
    assert responses[0].json() == responses[1].json()
    assert db.query(PaymentAttempt).count() == charge_count() == 1


@pytest.mark.parametrize("amount", [1, 0, -1, "5000", True, 5000.5, 2_147_483_648])
def test_invalid_or_underpaid_booking_never_charges(db, payer, amount):
    booking_id = make_booking(db, payer)
    assert pay(booking_id, amount).status_code == 422
    assert db.query(PaymentAttempt).count() == charge_count() == 0
    assert db.get(Booking, booking_id).status == "pending"


def test_booking_timeout_reconciles_with_original_key(db, payer):
    amount = settings.payment_timeout_trigger_cents
    booking_id = make_booking(db, payer, amount)
    assert pay(booking_id, amount).status_code == 504
    attempt = db.query(PaymentAttempt).one()
    original_key = attempt.idempotency_key
    assert attempt.status == "unknown"
    assert db.get(Booking, booking_id).status == "pending"
    assert pay(booking_id, amount).json()["status"] == "succeeded"
    db.expire_all()
    assert db.query(PaymentAttempt).one().idempotency_key == original_key
    assert charge_count() == 1


def test_concurrent_session_confirmation_charges_quote_once(db, payer):
    session_id = ready_session(db, payer)
    responses = concurrent_calls(lambda: affirm(session_id), lambda: affirm(session_id))
    assert all(response.status_code == 200 for response in responses)
    assert all(response.json()["status"] == "confirmed" for response in responses)
    assert len({response.json()["booking_id"] for response in responses}) == 1
    assert db.query(Booking).count() == db.query(PaymentAttempt).count() == 1
    assert charge_count() == 1


def test_session_crash_retries_with_saved_key(db, payer):
    session_id = ready_session(db, payer)
    assert affirm(session_id, simulate_crash=True).status_code == 500
    session = db.get(ConversationSession, session_id)
    booking_id = session.booking_id
    assert booking_id is not None
    assert session.status == "active" and session.awaiting_confirmation
    assert db.get(Booking, booking_id).status == "pending"
    attempt = db.query(PaymentAttempt).one()
    assert attempt.status == "pending" and attempt.idempotency_key
    assert payment_mock_client.charge_log == [5000]
    responses = concurrent_calls(lambda: affirm(session_id), lambda: pay(booking_id))
    assert all(response.status_code == 200 for response in responses)
    db.expire_all()
    session = db.get(ConversationSession, session_id)
    assert session.status == "confirmed" and not session.awaiting_confirmation
    assert db.query(Booking).count() == db.query(PaymentAttempt).count() == 1
    assert charge_count() == 1


def test_session_timeout_is_504_and_retry_reuses_booking(db, payer):
    amount = settings.payment_timeout_trigger_cents
    session_id = ready_session(db, payer, amount)
    assert affirm(session_id, amount).status_code == 504
    session = db.get(ConversationSession, session_id)
    original_booking_id = session.booking_id
    assert session.status != "confirmed"
    assert affirm(session_id, amount).json()["booking_id"] == original_booking_id
    assert db.query(Booking).count() == charge_count() == 1


def test_session_price_comes_from_quote_and_rejects_underpayment(db, payer):
    session_id = ready_session(db, payer, 8000)
    assert affirm(session_id, 1).status_code == 422
    assert charge_count() == 0
    response = client.post(
        f"/sessions/{session_id}/turn", json={"intent": "affirm"}, headers=headers
    )
    assert response.json()["status"] == "confirmed"
    assert payment_mock_client.charge_log == [8000]


def test_session_cannot_create_its_own_price_without_quote(db, payer):
    session_id = client.post("/sessions", headers=headers).json()["session_id"]
    response = client.post(
        f"/sessions/{session_id}/turn", json={"intent": "book", "party_size": 2}, headers=headers
    )
    assert response.json()["status"] == "need_booking"
    assert affirm(session_id, 1).status_code == 409
    assert db.query(Booking).count() == charge_count() == 0


def test_session_quote_can_be_selected_before_party_size(db, payer):
    booking_id = make_booking(db, payer)
    session_id = client.post("/sessions", headers=headers).json()["session_id"]
    response = client.post(
        f"/sessions/{session_id}/turn", json={"intent": "book", "booking_id": booking_id}, headers=headers
    )
    assert response.json()["status"] == "need_party_size"
    response = client.post(
        f"/sessions/{session_id}/turn", json={"intent": "book", "party_size": 2}, headers=headers
    )
    assert response.json()["status"] == "awaiting_confirmation"
    assert affirm(session_id).json()["status"] == "confirmed"


def test_session_rejects_another_members_quote(db, payer):
    other = Member(club_id="riverside", name="B", email="b@example.com", token="tok-b")
    db.add(other)
    db.commit()
    booking_id = make_booking(db, other)
    session_id = client.post("/sessions", headers=headers).json()["session_id"]
    response = client.post(
        f"/sessions/{session_id}/turn",
        json={"intent": "book", "party_size": 2, "booking_id": booking_id}, headers=headers,
    )
    assert response.status_code == 404
    assert charge_count() == 0


def test_quote_cannot_be_bound_to_two_concurrent_sessions(db, payer):
    booking_id = make_booking(db, payer)
    session_ids = [client.post("/sessions", headers=headers).json()["session_id"] for _ in range(2)]

    def select_quote(session_id):
        return client.post(
            f"/sessions/{session_id}/turn",
            json={"intent": "book", "party_size": 2, "booking_id": booking_id}, headers=headers,
        )

    responses = concurrent_calls(
        lambda: select_quote(session_ids[0]), lambda: select_quote(session_ids[1])
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert db.query(ConversationSession).filter(ConversationSession.booking_id == booking_id).count() == 1


def test_declined_payment_does_not_confirm_session(db, payer, monkeypatch):
    monkeypatch.setattr(payment_mock_client, "charge", lambda *args, **kwargs: ChargeResult("failed"))
    session_id = ready_session(db, payer)
    response = affirm(session_id)
    assert response.status_code == 200
    assert response.json()["status"] == "payment_failed"
    session = db.get(ConversationSession, session_id)
    assert session.status == "active" and session.awaiting_confirmation
    assert db.get(Booking, session.booking_id).status == "pending"
    assert db.query(PaymentAttempt).one().status == "failed"


def test_definitely_declined_payment_can_be_retried(db, payer, monkeypatch):
    original_charge = payment_mock_client.charge
    monkeypatch.setattr(payment_mock_client, "charge", lambda *args, **kwargs: ChargeResult("failed"))
    booking_id = make_booking(db, payer)
    assert pay(booking_id).json()["status"] == "failed"
    original_key = db.query(PaymentAttempt).one().idempotency_key
    monkeypatch.setattr(payment_mock_client, "charge", original_charge)
    assert pay(booking_id).json()["status"] == "succeeded"
    attempts = db.query(PaymentAttempt).order_by(PaymentAttempt.id).all()
    assert [attempt.status for attempt in attempts] == ["failed", "succeeded"]
    assert attempts[1].idempotency_key != original_key
    assert charge_count() == 1


def test_legacy_ambiguous_payment_without_key_requires_review(db, payer):
    booking_id = make_booking(db, payer)
    db.add(PaymentAttempt(booking_id=booking_id, amount_cents=5000, status="unknown"))
    db.commit()
    assert pay(booking_id).status_code == 409
    assert charge_count() == 0


@pytest.mark.parametrize("intent", ["book", "affirm"])
@pytest.mark.parametrize("terminal_state", ["confirmed", "escalated"])
def test_terminal_session_cannot_reopen_or_charge_again(db, payer, intent, terminal_state):
    session_id = ready_session(db, payer)
    if terminal_state == "confirmed":
        assert affirm(session_id).json()["status"] == "confirmed"
    else:
        client.post(f"/sessions/{session_id}/turn", json={"intent": "refund_dispute"}, headers=headers)
    before = charge_count()
    response = client.post(
        f"/sessions/{session_id}/turn",
        json={"intent": intent, "party_size": 3, "amount_cents": 5000},
        headers=headers,
    )
    assert response.json()["status"] == terminal_state
    assert charge_count() == before


@pytest.mark.parametrize("payload", [
    {"intent": "affirm", "amount_cents": "5000"}, {"intent": "affirm", "amount_cents": 0},
    {"intent": "book", "party_size": -1}, {"intent": "book", "party_size": "two"},
    {"intent": "unknown"},
])
def test_session_rejects_invalid_input_before_charging(db, payer, payload):
    session_id = ready_session(db, payer)
    response = client.post(f"/sessions/{session_id}/turn", json=payload, headers=headers)
    assert response.status_code == 422
    assert charge_count() == 0
