"""Tests for auth primitives and the API surface."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.security import (
    AuthError,
    create_access_token,
    decode_access_token,
    hash_password,
    validate_password_strength,
    verify_password,
)


def _reassemble_answer(sse_body: str) -> str:
    """Join the `delta` events back into the answer text.

    The stream emits one word per frame, so asserting on a multi-word phrase
    against the raw body would always fail even when the answer is correct.
    """
    import json as _json

    parts: list[str] = []
    for frame in sse_body.split("\n\n"):
        event = ""
        data = ""
        for line in frame.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if event == "delta" and data:
            parts.append(_json.loads(data).get("text", ""))
    return "".join(parts)


# 1 ---------------------------------------------------------------------------
def test_password_hash_is_not_the_plaintext_and_verifies():
    digest = hash_password("correct-horse-1")
    assert digest != "correct-horse-1"
    assert digest.startswith("$2b$"), "bcrypt hashes carry a $2b$ prefix"
    assert verify_password("correct-horse-1", digest) is True
    assert verify_password("wrong-horse-1", digest) is False


# 2 ---------------------------------------------------------------------------
def test_hash_is_salted_so_identical_passwords_differ():
    first = hash_password("same-password-9")
    second = hash_password("same-password-9")
    assert first != second
    assert verify_password("same-password-9", first)
    assert verify_password("same-password-9", second)


# 3 ---------------------------------------------------------------------------
def test_verify_password_returns_false_for_malformed_hash():
    """A corrupt stored hash must read as 'wrong password', never raise."""
    assert verify_password("anything", "not-a-bcrypt-hash") is False
    assert verify_password("", "") is False


# 4 ---------------------------------------------------------------------------
def test_validate_password_strength_rejects_short_and_oversized():
    with pytest.raises(AuthError):
        validate_password_strength("short")
    # bcrypt silently truncates past 72 bytes, which would let two different
    # long passwords collide — so we reject instead.
    with pytest.raises(AuthError):
        validate_password_strength("x" * 73)


# 5 ---------------------------------------------------------------------------
def test_token_roundtrip_carries_subject():
    token = create_access_token("user-123")
    payload = decode_access_token(token)
    assert payload["sub"] == "user-123"
    assert payload["exp"] > payload["iat"]


# 6 ---------------------------------------------------------------------------
def test_tampered_token_is_rejected():
    token = create_access_token("user-123")
    with pytest.raises(AuthError):
        decode_access_token(token[:-4] + "aaaa")
    with pytest.raises(AuthError):
        decode_access_token("not.a.jwt")


# 7 ---------------------------------------------------------------------------
def test_register_then_login_returns_usable_token(client: TestClient):
    client.post(
        "/api/auth/register",
        json={"email": "a@example.com", "password": "good-password-1"},
    )
    response = client.post(
        "/api/auth/login",
        json={"email": "a@example.com", "password": "good-password-1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == "a@example.com"


# 8 ---------------------------------------------------------------------------
def test_duplicate_registration_is_409(client: TestClient):
    payload = {"email": "dup@example.com", "password": "good-password-1"}
    assert client.post("/api/auth/register", json=payload).status_code == 201
    assert client.post("/api/auth/register", json=payload).status_code == 409


# 9 ---------------------------------------------------------------------------
def test_login_with_wrong_password_is_401_and_does_not_leak_which_field(client: TestClient):
    client.post(
        "/api/auth/register",
        json={"email": "b@example.com", "password": "good-password-1"},
    )
    wrong_password = client.post(
        "/api/auth/login", json={"email": "b@example.com", "password": "nope-password-1"}
    )
    unknown_user = client.post(
        "/api/auth/login", json={"email": "ghost@example.com", "password": "nope-password-1"}
    )
    assert wrong_password.status_code == 401
    assert unknown_user.status_code == 401
    # Identical messages: no account-enumeration oracle.
    assert wrong_password.json()["detail"] == unknown_user.json()["detail"]


# 10 --------------------------------------------------------------------------
def test_protected_route_requires_a_token(client: TestClient):
    assert client.get("/api/workspaces").status_code == 401
    bad = client.get("/api/workspaces", headers={"Authorization": "Bearer nonsense"})
    assert bad.status_code == 401


# 11 --------------------------------------------------------------------------
def test_workspace_crud_roundtrip(client: TestClient, auth):
    headers, _ = auth()
    created = client.post("/api/workspaces", json={"title": "Notes"}, headers=headers)
    assert created.status_code == 201
    workspace_id = created.json()["id"]

    listed = client.get("/api/workspaces", headers=headers)
    assert [w["id"] for w in listed.json()] == [workspace_id]

    detail = client.get(f"/api/workspaces/{workspace_id}", headers=headers)
    assert detail.json()["title"] == "Notes"

    assert client.delete(f"/api/workspaces/{workspace_id}", headers=headers).status_code == 204
    assert client.get(f"/api/workspaces/{workspace_id}", headers=headers).status_code == 404


# 12 --------------------------------------------------------------------------
def test_another_users_workspace_returns_404_not_403(client: TestClient, auth):
    """A 403 would confirm the row exists; 404 keeps it invisible."""
    owner_headers, _ = auth()
    other_headers, _ = auth()

    workspace_id = client.post(
        "/api/workspaces", json={"title": "Private"}, headers=owner_headers
    ).json()["id"]

    response = client.get(f"/api/workspaces/{workspace_id}", headers=other_headers)
    assert response.status_code == 404


# 13 --------------------------------------------------------------------------
def test_upload_text_asset_extracts_readable_text(client: TestClient, workspace):
    headers, ws = workspace
    files = {"file": ("notes.txt", b"FastAPI uses Pydantic for validation.", "text/plain")}
    response = client.post(f"/api/workspaces/{ws['id']}/assets", files=files, headers=headers)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "text"
    assert body["has_text"] is True


# 14 --------------------------------------------------------------------------
def test_upload_rejects_unsupported_binary(client: TestClient, workspace):
    headers, ws = workspace
    files = {"file": ("blob.bin", b"\x00\x01\x02\x03\x00\xff", "application/octet-stream")}
    response = client.post(f"/api/workspaces/{ws['id']}/assets", files=files, headers=headers)
    assert response.status_code == 415


# 15 --------------------------------------------------------------------------
def test_upload_rejects_empty_file(client: TestClient, workspace):
    headers, ws = workspace
    files = {"file": ("empty.txt", b"", "text/plain")}
    response = client.post(f"/api/workspaces/{ws['id']}/assets", files=files, headers=headers)
    assert response.status_code == 422


# 16 --------------------------------------------------------------------------
def test_ask_with_no_assets_is_422(client: TestClient, workspace):
    headers, ws = workspace
    response = client.post(
        f"/api/workspaces/{ws['id']}/messages",
        json={"question": "what does this say?"},
        headers=headers,
    )
    assert response.status_code == 422
    assert "at least one" in response.json()["detail"].lower()


# 17 --------------------------------------------------------------------------
def test_ask_streams_sse_and_cites_the_source(client: TestClient, workspace):
    headers, ws = workspace
    body = (
        b"Karachi in October is hot. Afternoons reach the high thirties. "
        b"Evenings cool down noticeably after sunset."
    )
    client.post(
        f"/api/workspaces/{ws['id']}/assets",
        files={"file": ("weather.txt", body, "text/plain")},
        headers=headers,
    )

    response = client.post(
        f"/api/workspaces/{ws['id']}/messages",
        json={"question": "how hot is Karachi in October"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    text = response.text
    assert "event: start" in text
    assert "event: delta" in text
    assert "event: done" in text

    # The answer streams word by word, so a whole phrase never appears as one
    # contiguous string — reassemble the deltas before asserting on the content.
    answer = _reassemble_answer(text)
    assert "weather.txt" in text  # citation references the uploaded filename
    assert "thirties" in answer
    assert "hot" in answer


# 18 --------------------------------------------------------------------------
def test_ungrounded_question_declines_instead_of_inventing(client: TestClient, workspace):
    headers, ws = workspace
    client.post(
        f"/api/workspaces/{ws['id']}/assets",
        files={"file": ("weather.txt", b"Karachi is hot in October.", "text/plain")},
        headers=headers,
    )
    response = client.post(
        f"/api/workspaces/{ws['id']}/messages",
        json={"question": "what is the capital of Portugal"},
        headers=headers,
    )
    assert response.status_code == 200
    answer = _reassemble_answer(response.text).lower()
    assert "couldn't find anything" in answer
    # And it must not have produced a citation for an answer it did not have.
    assert '"grounded": false' in response.text


# 19 --------------------------------------------------------------------------
def test_health_reports_provider_and_database(client: TestClient):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["provider"] == "local"
    assert body["database"] == "ok"
