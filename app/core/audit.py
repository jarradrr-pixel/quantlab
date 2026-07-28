"""Hash-chained audit log.

Each entry commits to its predecessor via SHA-256 over a canonical JSON form.
Removing or editing any row invalidates every subsequent row, so tampering is
detectable by replaying the chain rather than trusted by convention.

Secrets are redacted from payloads before hashing, so a redacted payload is
what the chain actually commits to.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditEvent, new_id
from app.logging_config import redact_mapping

logger = logging.getLogger(__name__)

#: The chain root. The first entry's ``previous_hash``.
GENESIS_HASH: Final[str] = "0" * 64


class AuditCategory:
    """Stable category codes. Strings, not an enum, so adding one is not a migration."""

    AUTH = "auth"
    STATE_TRANSITION = "state_transition"
    RESEARCH = "research"
    AGENT = "agent"
    CODE = "code"
    BACKTEST = "backtest"
    RISK = "risk"
    APPROVAL = "approval"
    ORDER = "order"
    BROKER = "broker"
    PERFORMANCE = "performance"
    SYSTEM = "system"
    ERROR = "error"


def _canonical_timestamp(value: datetime) -> str:
    """Render a timestamp identically whichever backend stored it.

    SQLite has no timezone-aware type, so a row written as aware comes back
    naive. Naive values are therefore read as UTC (which is what the writer
    always uses) and everything is normalised to UTC before hashing, so the
    chain verifies on both SQLite and PostgreSQL.
    """
    moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def compute_entry_hash(
    *,
    sequence: int,
    category: str,
    action: str,
    actor: str,
    project_id: str | None,
    payload: dict[str, Any],
    previous_hash: str,
    created_at: datetime,
) -> str:
    """Return the SHA-256 digest of an entry's canonical form.

    ``sort_keys`` and a fixed separator set make the encoding stable across
    Python versions and dict insertion order, which the chain depends on.
    """
    canonical = json.dumps(
        {
            "sequence": sequence,
            "category": category,
            "action": action,
            "actor": actor,
            "project_id": project_id,
            "payload": payload,
            "previous_hash": previous_hash,
            "created_at": _canonical_timestamp(created_at),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChainVerification:
    """Result of replaying the audit chain."""

    valid: bool
    entries_checked: int
    first_invalid_sequence: int | None = None
    detail: str = "chain intact"


class AuditLog:
    """Append-only writer. There is deliberately no update or delete method."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _tail(self) -> AuditEvent | None:
        return self._session.scalars(
            select(AuditEvent).order_by(AuditEvent.sequence.desc()).limit(1)
        ).first()

    def record(
        self,
        *,
        category: str,
        action: str,
        actor: str,
        payload: dict[str, Any] | None = None,
        project_id: str | None = None,
    ) -> AuditEvent:
        """Append one entry and return it.

        The caller is responsible for committing the surrounding transaction so
        that the audit entry and the fact it describes commit atomically.
        """
        previous = self._tail()
        sequence = 1 if previous is None else previous.sequence + 1
        previous_hash = GENESIS_HASH if previous is None else previous.entry_hash

        safe_payload = redact_mapping(payload or {})
        created_at = _now()

        entry_hash = compute_entry_hash(
            sequence=sequence,
            category=category,
            action=action,
            actor=actor,
            project_id=project_id,
            payload=safe_payload,
            previous_hash=previous_hash,
            created_at=created_at,
        )

        event = AuditEvent(
            id=new_id(),
            sequence=sequence,
            category=category,
            action=action,
            actor=actor,
            project_id=project_id,
            payload=safe_payload,
            previous_hash=previous_hash,
            entry_hash=entry_hash,
            created_at=created_at,
        )
        self._session.add(event)
        self._session.flush()
        logger.info(
            "audit entry recorded",
            extra={"context": {"sequence": sequence, "category": category, "action": action}},
        )
        return event

    def verify_chain(self) -> ChainVerification:
        """Replay every entry and confirm the hashes still agree."""
        events = list(
            self._session.scalars(select(AuditEvent).order_by(AuditEvent.sequence))
        )
        expected_previous = GENESIS_HASH

        for index, event in enumerate(events, start=1):
            if event.sequence != index:
                return ChainVerification(
                    valid=False,
                    entries_checked=index,
                    first_invalid_sequence=event.sequence,
                    detail=f"sequence gap: expected {index}, found {event.sequence}",
                )
            if event.previous_hash != expected_previous:
                return ChainVerification(
                    valid=False,
                    entries_checked=index,
                    first_invalid_sequence=event.sequence,
                    detail="previous_hash does not match the preceding entry",
                )
            recomputed = compute_entry_hash(
                sequence=event.sequence,
                category=event.category,
                action=event.action,
                actor=event.actor,
                project_id=event.project_id,
                payload=event.payload,
                previous_hash=event.previous_hash,
                created_at=event.created_at,
            )
            if recomputed != event.entry_hash:
                return ChainVerification(
                    valid=False,
                    entries_checked=index,
                    first_invalid_sequence=event.sequence,
                    detail="entry contents do not match the stored hash",
                )
            expected_previous = event.entry_hash

        return ChainVerification(valid=True, entries_checked=len(events))


def _now() -> datetime:
    from app.db.base import utcnow

    return utcnow()
