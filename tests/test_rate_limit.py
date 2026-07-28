"""Login throttle tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.rate_limit import RateLimiter, client_key, parse_limit


def test_parse_limit() -> None:
    assert parse_limit("5/minute") == (5, 60)
    assert parse_limit(" 10 / hour ") == (10, 3600)


@pytest.mark.parametrize("value", ["5", "5/fortnight", "many/minute", ""])
def test_invalid_limits_are_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="invalid rate limit"):
        parse_limit(value)


def test_quota_is_enforced_then_released() -> None:
    limiter = RateLimiter("3/minute")
    assert [limiter.allow("1.2.3.4", now=100.0) for _ in range(3)] == [True] * 3
    assert not limiter.allow("1.2.3.4", now=100.0)
    # A different client has its own quota.
    assert limiter.allow("5.6.7.8", now=100.0)
    # Once the window has passed, the quota is available again.
    assert limiter.allow("1.2.3.4", now=161.0)


def test_reset_clears_a_key() -> None:
    limiter = RateLimiter("1/minute")
    assert limiter.allow("1.2.3.4", now=1.0)
    assert not limiter.allow("1.2.3.4", now=1.0)
    limiter.reset("1.2.3.4")
    assert limiter.allow("1.2.3.4", now=1.0)


def test_client_key_handles_missing_host() -> None:
    assert client_key(None) == "unknown"
    assert client_key("10.0.0.1") == "10.0.0.1"


def test_repeated_failed_logins_are_throttled(client: TestClient) -> None:
    client.app.state.login_limiter = RateLimiter("3/minute")  # type: ignore[attr-defined]
    for _ in range(3):
        response = client.post(
            "/login",
            data={"email": "operator@example.test", "password": "wrong-password-x"},
        )
        assert response.status_code == 401

    throttled = client.post(
        "/login",
        data={"email": "operator@example.test", "password": "wrong-password-x"},
    )
    assert throttled.status_code == 429
    assert "Too many attempts" in throttled.text


def test_throttling_is_audited(signed_in_client: TestClient) -> None:
    signed_in_client.app.state.login_limiter = RateLimiter("0/minute")  # type: ignore[attr-defined]
    signed_in_client.post("/login", data={"email": "a@b.test", "password": "x" * 14})
    assert "login_rate_limited" in signed_in_client.get("/audit").text
