"""State-machine transition tests."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.state_machine import Orchestrator, TransitionError
from app.core.states import PIPELINE_ORDER, TRANSITIONS, ProjectState, is_allowed
from app.db.models import Approval, Operator, Project


def advance_to(
    orchestrator: Orchestrator, project: Project, target: ProjectState
) -> None:
    """Walk the happy path until ``target`` is reached."""
    for state in PIPELINE_ORDER[1:]:
        if ProjectState(project.state) is target:
            return
        orchestrator.transition(project, state, actor="test", reason="advance")
        if state is target:
            return


def test_projects_start_in_researching(project: Project) -> None:
    assert ProjectState(project.state) is ProjectState.RESEARCHING


def test_full_pipeline_is_reachable_step_by_step(
    db: Session, project: Project, operator: Operator, release_kill_switch: None
) -> None:
    orchestrator = Orchestrator(db)
    for state in PIPELINE_ORDER[1:]:
        if state is ProjectState.PAPER_TRADING:
            db.add(
                Approval(
                    project_id=project.id,
                    operator_id=operator.id,
                    kind="enter:PAPER_TRADING",
                    decision="approved",
                )
            )
            db.flush()
        orchestrator.transition(project, state, actor="test", reason="advance")
        assert ProjectState(project.state) is state


def test_stages_cannot_be_skipped(db: Session, project: Project) -> None:
    orchestrator = Orchestrator(db)
    with pytest.raises(TransitionError, match="not permitted"):
        orchestrator.transition(
            project, ProjectState.BACKTESTING, actor="test", reason="skip ahead"
        )
    assert ProjectState(project.state) is ProjectState.RESEARCHING


def test_no_forward_edge_skips_a_pipeline_stage() -> None:
    """Every forward edge advances by exactly one stage."""
    index = {state: position for position, state in enumerate(PIPELINE_ORDER)}
    for source, targets in TRANSITIONS.items():
        if source not in index:
            continue
        for target in targets:
            if target not in index:
                continue  # PAUSED / REJECTED
            step = index[target] - index[source]
            assert step <= 1, f"{source} -> {target} skips a stage"


def test_rejected_is_terminal(db: Session, project: Project) -> None:
    orchestrator = Orchestrator(db)
    orchestrator.transition(
        project, ProjectState.REJECTED, actor="test", reason="not viable"
    )
    assert TRANSITIONS[ProjectState.REJECTED] == frozenset()
    with pytest.raises(TransitionError):
        orchestrator.transition(
            project, ProjectState.RESEARCHING, actor="test", reason="revive"
        )


def test_pause_records_resume_state_and_resumes(db: Session, project: Project) -> None:
    orchestrator = Orchestrator(db)
    orchestrator.transition(
        project, ProjectState.KNOWLEDGE_REVIEW, actor="test", reason="advance"
    )
    orchestrator.transition(project, ProjectState.PAUSED, actor="test", reason="halt")

    assert ProjectState(project.state) is ProjectState.PAUSED
    assert ProjectState(project.resume_state) is ProjectState.KNOWLEDGE_REVIEW

    orchestrator.resume(project, actor="test", reason="continue")
    assert ProjectState(project.state) is ProjectState.KNOWLEDGE_REVIEW
    assert project.resume_state is None


def test_resume_requires_a_paused_project(db: Session, project: Project) -> None:
    with pytest.raises(TransitionError, match="not paused"):
        Orchestrator(db).resume(project, actor="test", reason="nope")


def test_paper_trading_requires_human_approval(
    db: Session, project: Project, release_kill_switch: None
) -> None:
    orchestrator = Orchestrator(db)
    advance_to(orchestrator, project, ProjectState.AWAITING_HUMAN_APPROVAL)

    with pytest.raises(TransitionError, match="requires a recorded human approval"):
        orchestrator.transition(
            project, ProjectState.PAPER_TRADING, actor="test", reason="go"
        )
    assert ProjectState(project.state) is ProjectState.AWAITING_HUMAN_APPROVAL


def test_kill_switch_blocks_entry_to_paper_trading(
    db: Session, project: Project, operator: Operator
) -> None:
    """The kill switch is engaged by default in the fixtures."""
    orchestrator = Orchestrator(db)
    advance_to(orchestrator, project, ProjectState.AWAITING_HUMAN_APPROVAL)
    db.add(
        Approval(
            project_id=project.id,
            operator_id=operator.id,
            kind="enter:PAPER_TRADING",
            decision="approved",
        )
    )
    db.flush()

    with pytest.raises(TransitionError, match="kill switch"):
        orchestrator.transition(
            project, ProjectState.PAPER_TRADING, actor="test", reason="go"
        )


def test_rejected_approval_does_not_unlock_paper_trading(
    db: Session, project: Project, operator: Operator, release_kill_switch: None
) -> None:
    orchestrator = Orchestrator(db)
    advance_to(orchestrator, project, ProjectState.AWAITING_HUMAN_APPROVAL)
    db.add(
        Approval(
            project_id=project.id,
            operator_id=operator.id,
            kind="enter:PAPER_TRADING",
            decision="rejected",
        )
    )
    db.flush()

    with pytest.raises(TransitionError, match="requires a recorded human approval"):
        orchestrator.transition(
            project, ProjectState.PAPER_TRADING, actor="test", reason="go"
        )


def test_blocked_transition_is_audited(
    db: Session, project: Project, release_kill_switch: None
) -> None:
    from app.db.models import AuditEvent

    orchestrator = Orchestrator(db)
    advance_to(orchestrator, project, ProjectState.AWAITING_HUMAN_APPROVAL)
    with pytest.raises(TransitionError):
        orchestrator.transition(
            project, ProjectState.PAPER_TRADING, actor="test", reason="go"
        )
    db.flush()
    blocked = [
        event
        for event in db.query(AuditEvent).all()
        if event.action == "transition_blocked"
    ]
    assert len(blocked) == 1
    assert blocked[0].payload["to_state"] == "PAPER_TRADING"


def test_is_allowed_matches_the_table() -> None:
    assert is_allowed(ProjectState.RESEARCHING, ProjectState.KNOWLEDGE_REVIEW)
    assert not is_allowed(ProjectState.RESEARCHING, ProjectState.PAPER_TRADING)
