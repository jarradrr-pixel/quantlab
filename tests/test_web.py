"""Integration tests over the HTTP layer.

These exercise the real application with a temporary database. Nothing here
contacts a broker.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.api import routes_auth
from tests.conftest import TEST_PASSWORD, csrf_from

PROTECTED_PATHS = ["/", "/audit", "/settings", "/kill-switch", "/broker"]


@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_protected_pages_redirect_anonymous_users(client: TestClient, path: str) -> None:
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_page_is_public(client: TestClient) -> None:
    response = client.get("/login")
    assert response.status_code == 200
    assert "Sign in" in response.text


def test_login_rejects_wrong_password(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"email": "operator@example.test", "password": "definitely-wrong"},
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert "incorrect" in response.text


def test_login_failure_message_does_not_reveal_account_existence(
    client: TestClient,
) -> None:
    unknown = client.post(
        "/login",
        data={"email": "nobody@example.test", "password": "whatever-long-enough"},
    )
    wrong = client.post(
        "/login",
        data={"email": "operator@example.test", "password": "definitely-wrong"},
    )
    assert unknown.status_code == wrong.status_code == 401
    assert "incorrect" in unknown.text and "incorrect" in wrong.text


def test_successful_login_sets_a_session_cookie(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"email": "operator@example.test", "password": TEST_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303
    cookie_header = response.headers.get("set-cookie", "")
    assert "quantlab_session" in cookie_header
    assert "HttpOnly" in cookie_header
    assert "SameSite=lax" in cookie_header


def test_repeated_wrong_passwords_lock_the_account(client: TestClient) -> None:
    for _ in range(5):
        response = client.post(
            "/login",
            data={"email": "operator@example.test", "password": "definitely-wrong"},
            follow_redirects=False,
        )
        assert response.status_code == 401

    locked = client.post(
        "/login",
        data={"email": "operator@example.test", "password": "definitely-wrong"},
        follow_redirects=False,
    )
    assert locked.status_code == 429
    assert "Too many failed attempts" in locked.text

    audit_page = client.post(
        "/login",
        data={"email": "operator@example.test", "password": TEST_PASSWORD},
        follow_redirects=False,
    )
    assert audit_page.status_code == 429, "even the correct password is refused while locked"


def test_lockout_is_audited(signed_in_client: TestClient) -> None:
    # signed_in_client's session cookie was issued before any of these
    # failed attempts -- a subsequent lockout does not invalidate an
    # already-issued cookie, only new password checks, so it can still be
    # used to read /audit afterward.
    for _ in range(6):
        signed_in_client.post(
            "/login",
            data={"email": "operator@example.test", "password": "definitely-wrong"},
        )
    assert "login_locked" in signed_in_client.get("/audit").text


def test_successful_login_resets_the_lockout_counter(client: TestClient) -> None:
    for _ in range(3):
        response = client.post(
            "/login",
            data={"email": "operator@example.test", "password": "definitely-wrong"},
            follow_redirects=False,
        )
        assert response.status_code == 401

    success = client.post(
        "/login",
        data={"email": "operator@example.test", "password": TEST_PASSWORD},
        follow_redirects=False,
    )
    assert success.status_code == 303

    # If the counter had not reset, 3 (already spent) + 4 more would trip the
    # 5-attempt threshold on this 4th post-success attempt. It must not.
    for _ in range(4):
        response = client.post(
            "/login",
            data={"email": "operator@example.test", "password": "definitely-wrong"},
            follow_redirects=False,
        )
    assert response.status_code == 401


def test_lockout_expires_and_allows_a_fresh_attempt(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    for _ in range(5):
        client.post(
            "/login",
            data={"email": "operator@example.test", "password": "definitely-wrong"},
        )
    locked = client.post(
        "/login",
        data={"email": "operator@example.test", "password": TEST_PASSWORD},
        follow_redirects=False,
    )
    assert locked.status_code == 429

    # No injectable clock exists for the route itself, so move time forward
    # by patching the exact name the route calls (routes_auth imported
    # `utcnow` via `from ... import`, so the module-local reference is what
    # must be patched, not app.db.base.utcnow).
    real_utcnow = routes_auth.utcnow
    monkeypatch.setattr(routes_auth, "utcnow", lambda: real_utcnow() + timedelta(hours=1))

    response = client.post(
        "/login",
        data={"email": "operator@example.test", "password": TEST_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_overview_renders_for_signed_in_operator(signed_in_client: TestClient) -> None:
    response = signed_in_client.get("/")
    assert response.status_code == 200
    assert "Overview" in response.text
    assert "paper trading only" in response.text.lower()


def test_security_headers_are_present(client: TestClient) -> None:
    response = client.get("/login")
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_project_creation_requires_a_csrf_token(signed_in_client: TestClient) -> None:
    response = signed_in_client.post(
        "/projects",
        data={"name": "No token", "research_objective": "Should fail."},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_project_creation_rejects_a_forged_csrf_token(
    signed_in_client: TestClient,
) -> None:
    response = signed_in_client.post(
        "/projects",
        data={
            "name": "Forged",
            "research_objective": "Should fail.",
            "csrf_token": "not-the-real-token",
        },
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_project_lifecycle_through_the_ui(signed_in_client: TestClient) -> None:
    token = csrf_from(signed_in_client.get("/").text)
    created = signed_in_client.post(
        "/projects",
        data={
            "name": "Momentum study",
            "research_objective": "Does a 20/100 crossover survive costs?",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    project_url = created.headers["location"]

    page = signed_in_client.get(project_url)
    assert page.status_code == 200
    assert "RESEARCHING" in page.text

    project_token = csrf_from(page.text)
    moved = signed_in_client.post(
        f"{project_url}/transition",
        data={
            "target": "KNOWLEDGE_REVIEW",
            "reason": "Sources gathered.",
            "csrf_token": project_token,
        },
        follow_redirects=False,
    )
    assert moved.status_code == 303
    assert "KNOWLEDGE_REVIEW" in signed_in_client.get(project_url).text


def test_illegal_transition_is_refused_over_http(signed_in_client: TestClient) -> None:
    token = csrf_from(signed_in_client.get("/").text)
    created = signed_in_client.post(
        "/projects",
        data={
            "name": "Skipper",
            "research_objective": "Tries to jump ahead.",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    project_url = created.headers["location"]
    project_token = csrf_from(signed_in_client.get(project_url).text)

    response = signed_in_client.post(
        f"{project_url}/transition",
        data={
            "target": "PAPER_TRADING",
            "reason": "Straight to trading.",
            "csrf_token": project_token,
        },
        follow_redirects=False,
        headers={"accept": "application/json"},
    )
    assert response.status_code == 409


def test_system_boots_with_the_kill_switch_engaged(signed_in_client: TestClient) -> None:
    response = signed_in_client.get("/kill-switch")
    assert response.status_code == 200
    assert "ENGAGED" in response.text


def test_kill_switch_can_be_released_and_re_engaged(
    signed_in_client: TestClient,
) -> None:
    page = signed_in_client.get("/kill-switch")
    token = csrf_from(page.text)
    released = signed_in_client.post(
        "/kill-switch",
        data={"engaged": "false", "reason": "Starting a session.", "csrf_token": token},
        follow_redirects=False,
    )
    assert released.status_code == 303
    page = signed_in_client.get("/kill-switch")
    assert "RELEASED" in page.text

    token = csrf_from(page.text)
    signed_in_client.post(
        "/kill-switch",
        data={"engaged": "true", "reason": "Done for the day.", "csrf_token": token},
        follow_redirects=False,
    )
    assert "ENGAGED" in signed_in_client.get("/kill-switch").text


def test_audit_page_reports_an_intact_chain(signed_in_client: TestClient) -> None:
    response = signed_in_client.get("/audit")
    assert response.status_code == 200
    assert "CHAIN INTACT" in response.text


def test_login_is_audited(signed_in_client: TestClient) -> None:
    response = signed_in_client.get("/audit")
    assert "login_succeeded" in response.text


def test_settings_page_never_renders_secrets(signed_in_client: TestClient) -> None:
    response = signed_in_client.get("/settings")
    assert response.status_code == 200
    assert "test-secret-key-not-used-anywhere-real" not in response.text
    assert TEST_PASSWORD not in response.text


def test_logout_clears_the_session(signed_in_client: TestClient) -> None:
    signed_in_client.post("/logout", follow_redirects=False)
    response = signed_in_client.get("/", follow_redirects=False)
    assert response.status_code == 303


def test_healthz_is_available(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
