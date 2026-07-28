"""Audit chain integrity, secret redaction, configuration and password tests."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.audit import GENESIS_HASH, AuditCategory, AuditLog
from app.db.models import AuditEvent
from app.logging_config import REDACTED, mask_identifier, redact_mapping, redact_text
from app.security import (
    SessionManager,
    csrf_tokens_match,
    hash_password,
    verify_password,
)

# --- audit chain ------------------------------------------------------


def test_first_entry_links_to_genesis(db: Session) -> None:
    event = AuditLog(db).record(
        category=AuditCategory.SYSTEM, action="boot", actor="system"
    )
    assert event.sequence == 1
    assert event.previous_hash == GENESIS_HASH


def test_chain_links_successive_entries(db: Session) -> None:
    log = AuditLog(db)
    first = log.record(category=AuditCategory.SYSTEM, action="one", actor="system")
    second = log.record(category=AuditCategory.SYSTEM, action="two", actor="system")
    assert second.previous_hash == first.entry_hash
    assert second.sequence == first.sequence + 1


def test_chain_verifies_when_intact(db: Session) -> None:
    log = AuditLog(db)
    for index in range(5):
        log.record(category=AuditCategory.SYSTEM, action=f"step-{index}", actor="system")
    db.commit()

    result = log.verify_chain()
    assert result.valid
    assert result.entries_checked == 5


def test_tampering_with_a_payload_breaks_verification(db: Session) -> None:
    log = AuditLog(db)
    for index in range(3):
        log.record(
            category=AuditCategory.RISK,
            action="decision",
            actor="system",
            payload={"approved": True, "index": index},
        )
    db.commit()

    target = db.query(AuditEvent).filter(AuditEvent.sequence == 2).one()
    target.payload = {"approved": False, "index": 2}
    db.commit()

    result = log.verify_chain()
    assert not result.valid
    assert result.first_invalid_sequence == 2
    assert "hash" in result.detail


def test_deleting_an_entry_breaks_verification(db: Session) -> None:
    log = AuditLog(db)
    for index in range(3):
        log.record(category=AuditCategory.SYSTEM, action=f"step-{index}", actor="system")
    db.commit()

    db.delete(db.query(AuditEvent).filter(AuditEvent.sequence == 2).one())
    db.commit()

    result = log.verify_chain()
    assert not result.valid


def test_audit_payload_is_redacted_before_storage(db: Session) -> None:
    event = AuditLog(db).record(
        category=AuditCategory.BROKER,
        action="connect",
        actor="system",
        payload={"api_key": "PKREALKEYVALUE1234567", "endpoint": "paper"},
    )
    assert event.payload["api_key"] == REDACTED
    assert event.payload["endpoint"] == "paper"


# --- redaction --------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "api_key=PKABCDEFGHIJKLMNOP12",
        'password: "hunter2hunter2"',
        "Authorization: Bearer abcdefghijklmnopqrst",
    ],
)
def test_secret_patterns_are_redacted(text: str) -> None:
    assert REDACTED in redact_text(text)


def test_redaction_keeps_harmless_text() -> None:
    assert redact_text("symbol=SPY quantity=3") == "symbol=SPY quantity=3"


def test_nested_payloads_are_redacted() -> None:
    payload = {
        "broker": {"api_secret": "s3cr3t-value-here", "mode": "paper"},
        "orders": [{"token": "abc123def456", "symbol": "SPY"}],
    }
    cleaned = redact_mapping(payload)
    assert cleaned["broker"]["api_secret"] == REDACTED
    assert cleaned["broker"]["mode"] == "paper"
    assert cleaned["orders"][0]["token"] == REDACTED
    assert cleaned["orders"][0]["symbol"] == "SPY"


def test_account_identifiers_are_masked() -> None:
    assert mask_identifier("PA3XYZ987654") == "********7654"
    assert mask_identifier(None) == "unknown"
    assert mask_identifier("ab") == "**"


# --- passwords and sessions -------------------------------------------


def test_password_round_trip() -> None:
    stored = hash_password("a-sufficiently-long-password")
    assert stored != "a-sufficiently-long-password"
    assert verify_password(stored, "a-sufficiently-long-password")
    assert not verify_password(stored, "wrong-password-entirely")


def test_short_passwords_are_rejected() -> None:
    with pytest.raises(ValueError, match="at least"):
        hash_password("short")


def test_verify_password_returns_false_on_malformed_hash() -> None:
    assert not verify_password("not-a-hash", "anything-at-all")


def test_tampered_session_cookie_is_rejected() -> None:
    manager = SessionManager("secret-key-for-tests", max_age_seconds=3600)
    cookie, data = manager.issue("operator-1")
    assert manager.read(cookie) is not None
    assert manager.read(cookie + "x") is None
    assert manager.read(None) is None
    assert data.csrf_token


def test_session_signed_with_another_key_is_rejected() -> None:
    cookie, _ = SessionManager("key-one", 3600).issue("operator-1")
    assert SessionManager("key-two", 3600).read(cookie) is None


def test_csrf_comparison() -> None:
    assert csrf_tokens_match("token-value", "token-value")
    assert not csrf_tokens_match("token-value", "other-value")
    assert not csrf_tokens_match(None, "other-value")
    assert not csrf_tokens_match("token-value", None)


# --- configuration ----------------------------------------------------


def test_live_trading_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="Live trading is not implemented"):
        Settings(trading_mode="live")


def test_empty_symbol_allowlist_is_rejected() -> None:
    with pytest.raises(ValueError, match="allowlist is mandatory"):
        Settings(allowed_symbols=[])


def test_symbols_are_normalised() -> None:
    assert Settings(allowed_symbols=[" spy ", "qqq"]).allowed_symbols == ["SPY", "QQQ"]


def test_position_limit_cannot_exceed_total_exposure() -> None:
    with pytest.raises(ValueError, match="cannot exceed"):
        Settings(max_position_percentage=20.0, max_total_exposure_percentage=10.0)


def test_human_approval_cannot_be_disabled_outside_tests() -> None:
    with pytest.raises(ValueError, match="test environment"):
        Settings(environment="local", require_human_approval=False)


def test_sqlite_is_rejected_in_production() -> None:
    with pytest.raises(ValueError, match="local-development fallback"):
        Settings(
            environment="production",
            database_url="sqlite+pysqlite:///./x.db",
            session_cookie_secure=True,
        )


def test_debug_is_rejected_in_production() -> None:
    with pytest.raises(ValueError, match="debug must be disabled"):
        Settings(
            environment="production",
            debug=True,
            database_url="postgresql+psycopg://u:p@h/d",
        )


def test_unknown_settings_are_rejected() -> None:
    with pytest.raises(ValueError):
        Settings(enable_live_trading_backdoor=True)  # type: ignore[call-arg]


def test_alpaca_base_url_must_be_alpacas_paper_host() -> None:
    with pytest.raises(ValueError, match="paper"):
        Settings(alpaca_paper_base_url="https://api.alpaca.markets")


def test_alpaca_key_and_secret_must_both_be_set() -> None:
    with pytest.raises(ValueError, match="must both be set"):
        Settings(alpaca_api_key="x")


def test_alpaca_unset_is_valid_and_unconfigured() -> None:
    assert Settings().broker_configured is False


def test_alpaca_configured_when_both_set() -> None:
    settings = Settings(alpaca_api_key="x", alpaca_api_secret="y")
    assert settings.broker_configured is True
