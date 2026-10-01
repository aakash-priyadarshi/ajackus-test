import os

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

# Select a separate test database even if DATABASE_URL points at app data.
test_url = os.environ.get("TEST_DATABASE_URL")
if test_url is None:
    app_url = os.environ.get("DATABASE_URL") or dotenv_values(".env").get("DATABASE_URL")
    test_url = make_url(app_url or "postgresql+psycopg://kindred:kindred@localhost:5434/kindred").set(
        database="kindred_test"
    ).render_as_string(hide_password=False)
if not make_url(test_url).database.endswith("_test"):
    raise RuntimeError("TEST_DATABASE_URL must use a database name ending in _test")
os.environ["DATABASE_URL"] = test_url

import pytest

from app.db import SessionLocal
from app.models import Base
from app.services.payment_mock import payment_mock_client
from scripts.init_db import init_db


@pytest.fixture(scope="session", autouse=True)
def _setup_db():
    init_db()
    yield


@pytest.fixture()
def db():
    session = SessionLocal()
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def _reset_mock_payments():
    payment_mock_client.reset()
    yield
    payment_mock_client.reset()
