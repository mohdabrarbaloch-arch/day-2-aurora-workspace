"""Deployment-safety tests.

These guard the exact failure mode that broke the previous project in this
series: an app that returns HTTP 200 on `/` while every JSON route returns 500,
because a catch-all rewrite hid a backend that never imported.

Each assertion here would have caught that class of bug.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


# 1 ---------------------------------------------------------------------------
def test_root_serves_the_frontend_as_html(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert b"Aurora" in response.content


# 2 ---------------------------------------------------------------------------
def test_health_is_json_not_html(client: TestClient):
    """The decisive check: a 200 here must carry JSON, not the SPA shell."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]
    assert response.json()["status"] == "ok"


# 3 ---------------------------------------------------------------------------
def test_configured_docs_path_serves_openapi_html(client: TestClient):
    """docs_url is /api/docs, not /docs — the default path must not be assumed."""
    response = client.get("/api/docs")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


# 4 ---------------------------------------------------------------------------
def test_openapi_json_is_served_from_the_configured_path(client: TestClient):
    response = client.get("/api/openapi.json")
    assert response.status_code == 200
    spec = response.json()
    assert spec["info"]["title"] == "Aurora"
    assert "/api/health" in spec["paths"]


# 5 ---------------------------------------------------------------------------
def test_unknown_path_returns_json_404_not_the_spa_shell(client: TestClient):
    """No catch-all rewrite: an unknown route must say 404, not serve index.html."""
    response = client.get("/definitely-not-a-route")
    assert response.status_code == 404
    assert "application/json" in response.headers["content-type"]
    assert response.json()["detail"]


# 6 ---------------------------------------------------------------------------
def test_unauthenticated_api_route_is_401_json(client: TestClient):
    response = client.get("/api/workspaces")
    assert response.status_code == 401
    assert "application/json" in response.headers["content-type"]


# 7 ---------------------------------------------------------------------------
def test_vercel_config_bundles_the_app_package():
    """includeFiles must ship app/** with the entrypoint.

    Without it, `from app.main import app` raises ModuleNotFoundError at import
    time and every API route 500s — the bug from the sibling project.
    """
    config = json.loads((ROOT / "vercel.json").read_text())
    builds = config["builds"]
    python_build = next(b for b in builds if b["use"] == "@vercel/python")
    include = python_build["config"]["includeFiles"]
    assert "app/**" in include, "app/** must be bundled or the import fails in production"
    assert "requirements.txt" in include


# 8 ---------------------------------------------------------------------------
def test_vercel_config_does_not_short_circuit_static_before_the_function():
    """Every route must reach the Python function.

    If static is routed first, a crashed function is masked by a 200 shell and
    the deployment looks healthy while being completely dead.
    """
    config = json.loads((ROOT / "vercel.json").read_text())
    routes = config["routes"]
    first_dest = routes[0]["dest"]
    assert first_dest == "/api/index.py", (
        "the first route must target the Python function; routing /static first "
        "lets a broken function serve a healthy-looking static page instead"
    )


# 9 ---------------------------------------------------------------------------
def test_entrypoint_imports_the_app_object():
    """The single line in api/index.py is the whole production entrypoint."""
    from api.index import app as exported

    assert exported is not None
    assert hasattr(exported, "routes")
    assert len(exported.routes) > 5


# 10 --------------------------------------------------------------------------
def test_security_headers_are_attached(client: TestClient):
    response = client.get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" in response.headers
