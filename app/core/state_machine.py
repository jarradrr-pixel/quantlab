"""The orchestrator.

Every state change goes through :meth:`Orchestrator.transition`, which refuses
anything not in the transition table, enforces the guards, and writes an audit
entry in the same transaction as the change itself.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import AuditCategory, AuditLog
from app.core.states import (
    APPROVAL_GATED_STATES,
    TERMINAL_STATES,
    TRANSITIONS,
    ProjectState,
    is_allowed,
)
from app.db.models import KILL_SWITCH_KEY, Approval, Project, StateTransition, SystemFlag

logger = logging.getLogger(__name__)


class TransitionError(Exception):
    """Raised when a requested transition is not permitted."""


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    reason: str


class Orchestrator:
    """Deterministic driver of the project lifecycle."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._audit = AuditLog(session)

    # -- guards ---------------------------------------------------------

    def _kill_switch_engaged(self) -> bool:
        flag = self._session.get(SystemFlag, KILL_SWITCH_KEY)
        # Absent flag means unknown state, and unknown means halted.
        return True if flag is None else flag.value

    def _has_approval(self, project_id: str, kind: str) -> bool:
        approval = self._session.scalars(
            select(Approval)
            .where(
                Approval.project_id == project_id,
                Approval.kind == kind,
                Approval.decision == "approved",
            )
            .order_by(Approval.created_at.desc())
            .limit(1)
        ).first()
        return approval is not None

    def check_guards(self, project: Project, target: ProjectState) -> GuardResult:
        """Apply the non-table rules that gate specific target states."""
        if target in APPROVAL_GATED_STATES:
            if self._kill_switch_engaged():
                return GuardResult(False, "kill switch is engaged")
            if not self._has_approval(project.id, kind=f"enter:{target.value}"):
                return GuardResult(
                    False,
                    f"entering {target.value} requires a recorded human approval",
                )
        if project.state in TERMINAL_STATES:
            return GuardResult(False, f"{project.state.value} is terminal")
        return GuardResult(True, "guards satisfied")

    # -- transition -----------------------------------------------------

    def transition(
        self,
        project: Project,
        target: ProjectState,
        *,
        actor: str,
        reason: str,
    ) -> StateTransition:
        """Move ``project`` to ``target`` or raise :class:`TransitionError`."""
        source = ProjectState(project.state)

        if not is_allowed(source, target):
            permitted = ", ".join(sorted(s.value for s in TRANSITIONS[source])) or "none"
            raise TransitionError(
                f"{source.value} -> {target.value} is not permitted. "
                f"Permitted targets: {permitted}."
            )

        guard = self.check_guards(project, target)
        if not guard.allowed:
            self._audit.record(
                category=AuditCategory.STATE_TRANSITION,
                action="transition_blocked",
                actor=actor,
                project_id=project.id,
                payload={
                    "from_state": source.value,
                    "to_state": target.value,
                    "reason": guard.reason,
                },
            )
            raise TransitionError(guard.reason)

        # Remember where to resume from when pausing; clear it on resume.
        if target is ProjectState.PAUSED:
            project.resume_state = source
        elif source is ProjectState.PAUSED:
            project.resume_state = None

        project.state = target

        record = StateTransition(
            project_id=project.id,
            from_state=source,
            to_state=target,
            reason=reason,
            actor=actor,
        )
        self._session.add(record)

        self._audit.record(
            category=AuditCategory.STATE_TRANSITION,
            action="transition",
            actor=actor,
            project_id=project.id,
            payload={
                "from_state": source.value,
                "to_state": target.value,
                "reason": reason,
            },
        )
        self._session.flush()
        logger.info(
            "state transition",
            extra={
                "context": {
                    "project_id": project.id,
                    "from": source.value,
                    "to": target.value,
                    "actor": actor,
                }
            },
        )
        return record

    def resume(self, project: Project, *, actor: str, reason: str) -> StateTransition:
        """Return a paused project to the stage it was paused from."""
        if ProjectState(project.state) is not ProjectState.PAUSED:
            raise TransitionError("project is not paused")
        if project.resume_state is None:
            raise TransitionError("no resume state recorded; move the project explicitly")
        return self.transition(
            project, ProjectState(project.resume_state), actor=actor, reason=reason
        )
