"""Structured logging with mandatory secret redaction.

Every log record passes through :class:`RedactionFilter`. Credentials must
never reach a log file or the audit trail, so redaction happens at the logging
layer rather than relying on call sites to remember.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any, Final

#: Patterns matched against rendered log text. Each pattern keeps its label
#: group so the reader can still see *which* field was suppressed.
_SECRET_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"(?i)\b(api[_-]?key|secret[_-]?key|password|passwd|token|authorization|"
        r"bearer|access[_-]?key|private[_-]?key)\b(\s*[:=]\s*)(\"?)([^\s\",;}]+)"
    ),
    re.compile(r"\bPK[A-Z0-9]{16,}\b"),  # Alpaca-style key identifiers
    re.compile(r"\b[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),  # JWT
)

REDACTED: Final[str] = "***REDACTED***"

#: Keys whose values are always suppressed in structured extras.
_SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "api_key",
        "api_secret",
        "secret",
        "secret_key",
        "password",
        "token",
        "authorization",
        "access_key",
        "private_key",
        "alpaca_api_key",
        "alpaca_api_secret",
        "anthropic_api_key",
    }
)


def redact_text(text: str) -> str:
    """Return ``text`` with any recognised secret material replaced."""
    result = _SECRET_PATTERNS[0].sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{REDACTED}", text
    )
    for pattern in _SECRET_PATTERNS[1:]:
        result = pattern.sub(REDACTED, result)
    return result


def redact_mapping(payload: dict[str, Any]) -> dict[str, Any]:
    """Recursively redact sensitive keys in a structured payload."""
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        if key.lower() in _SENSITIVE_KEYS:
            cleaned[key] = REDACTED
        elif isinstance(value, dict):
            cleaned[key] = redact_mapping(value)
        elif isinstance(value, list):
            cleaned[key] = [
                redact_mapping(item) if isinstance(item, dict) else _redact_scalar(item)
                for item in value
            ]
        else:
            cleaned[key] = _redact_scalar(value)
    return cleaned


def _redact_scalar(value: Any) -> Any:
    return redact_text(value) if isinstance(value, str) else value


def mask_identifier(identifier: str | None, visible: int = 4) -> str:
    """Mask an account or order identifier, keeping the last ``visible`` chars."""
    if not identifier:
        return "unknown"
    if len(identifier) <= visible:
        return "*" * len(identifier)
    return f"{'*' * (len(identifier) - visible)}{identifier[-visible:]}"


class RedactionFilter(logging.Filter):
    """Strip secret material from the message and structured extras."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(str(record.msg))
        context = getattr(record, "context", None)
        if isinstance(context, dict):
            record.context = redact_mapping(context)
        return True


class JsonFormatter(logging.Formatter):
    """Render records as single-line JSON for machine ingestion."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        context = getattr(record, "context", None)
        if isinstance(context, dict):
            payload["context"] = context
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON formatter and redaction filter on the root logger."""
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn installs its own noisy handlers; route them through ours.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
