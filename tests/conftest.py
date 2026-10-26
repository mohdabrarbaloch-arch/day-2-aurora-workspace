"""Shared pytest fixtures.

Environment variables are set at import time — *before* any `app.*` module is
imported — because `app.db` builds its engine and `app.security` derives its
signing key at module import. Setting them later would leave tests pointed at
the developer's real database.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="aurora-test-"))

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["STORAGE_DIR"] = str(_TMP / "storage")
os.environ["SECRET_KEY"] = "test-key-only-not-a-production-secret"
os.environ["AI_PROVIDER"] = "local"
# Tests issue many requests; the limiter is exercised directly instead.
os.environ["RATE_LIMIT_REQUESTS"] = "100000"
os.environ["RATE_LIMIT_WINDOW_SECONDS"] = "60"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    """Give every test an empty schema."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def auth(client: TestClient):
    """Register a user and return (headers, user_payload)."""

    def _make(email: str | None = None, password: str = "correct-horse-1"):
        import uuid

        payload = {
            "email": email or f"user-{uuid.uuid4().hex[:10]}@example.com",
            "password": password,
            "display_name": "Test User",
        }
        response = client.post("/api/auth/register", json=payload)
        assert response.status_code == 201, response.text
        body = response.json()
        return {"Authorization": f"Bearer {body['access_token']}"}, body

    return _make


@pytest.fixture()
def workspace(client: TestClient, auth):
    headers, _ = auth()
    response = client.post("/api/workspaces", json={"title": "Test WS"}, headers=headers)
    assert response.status_code == 201, response.text
    return headers, response.json()
