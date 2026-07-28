"""Operator console.

Phase 1 ships the pages that later phases hang content on: overview, project
detail with the state rail, the audit log with chain verification, the kill
switch and read-only settings.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from app.core.audit import AuditCategory, AuditLog
from app.core.state_machine import Orchestrator, TransitionError
from app.core.states import PIPELINE_ORDER, TRANSITIONS, ProjectState
from app.db.models import (
    KILL_SWITCH_KEY,
    Approval,
    ApprovalKind,
    AuditEvent,
    Backtest,
    Order,
    Project,
    RiskAssessment,
    Strategy,
    SystemFlag,
)
from app.deps import DbDep, OperatorDep, SettingsDep, get_session_data, require_csrf
from app.security import SessionData
from app.web.templating import templates

logger = logging.getLogger(__name__)
router = APIRouter(tags=["dashboard"])

SessionDep = Annotated[SessionData | None, Depends(get_session_data)]


def _kill_switch(db: DbDep) -> SystemFlag:
    flag = db.get(SystemFlag, KILL_SWITCH_KEY)
    if flag is None:  # pragma: no cover - seeded at start-up
        raise HTTPException(status_code=500, detail="Kill switch flag is missing.")
    return flag


def _csrf(session: SessionData | None) -> str:
    return session.csrf_token if session else ""


@router.get("/")
def overview(
    request: Request,
    db: DbDep,
    operator: OperatorDep,
    session: SessionDep,
    settings: SettingsDep,
) -> Response:
    projects = list(db.scalars(select(Project).order_by(Project.created_at.desc())))
    audit_count = db.scalar(select(func.count()).select_from(AuditEvent)) or 0
    return templates.TemplateResponse(
        request,
        "overview.html",
        {
            "title": "Overview",
            "operator": operator,
            "projects": projects,
            "audit_count": audit_count,
            "kill_switch": _kill_switch(db),
            "settings_obj": settings,
            "csrf_token": _csrf(session),
        },
    )


@router.post("/projects")
def create_project(
    db: DbDep,
    operator: OperatorDep,
    name: Annotated[str, Form()],
    research_objective: Annotated[str, Form()],
    _: Annotated[None, Depends(require_csrf)],
) -> Response:
    project = Project(
        name=name.strip(),
        research_objective=research_objective.strip(),
        state=ProjectState.RESEARCHING,
    )
    db.add(project)
    db.flush()
    AuditLog(db).record(
        category=AuditCategory.SYSTEM,
        action="project_created",
        actor=operator.email,
        project_id=project.id,
        payload={"name": project.name},
    )
    db.commit()
    return RedirectResponse(
        f"/projects/{project.id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/projects/{project_id}")
def project_detail(
    request: Request,
    project_id: str,
    db: DbDep,
    operator: OperatorDep,
    session: SessionDep,
) -> Response:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")

    current = ProjectState(project.state)
    strategy = db.scalars(
        select(Strategy)
        .where(Strategy.project_id == project_id)
        .order_by(Strategy.version.desc())
    ).first()
    backtests = list(
        db.scalars(
            select(Backtest)
            .where(Backtest.project_id == project_id)
            .order_by(Backtest.created_at.desc())
        )
    )
    risk_assessments = list(
        db.scalars(
            select(RiskAssessment)
            .where(RiskAssessment.project_id == project_id)
            .order_by(RiskAssessment.created_at.desc())
        )
    )
    approvals = list(
        db.scalars(
            select(Approval)
            .where(Approval.project_id == project_id)
            .order_by(Approval.created_at.desc())
        )
    )
    orders = list(
        db.scalars(
            select(Order)
            .where(Order.project_id == project_id)
            .order_by(Order.created_at.desc())
        )
    )
    approval_kinds = list(db.scalars(select(ApprovalKind)))

    return templates.TemplateResponse(
        request,
        "project.html",
        {
            "title": project.name,
            "operator": operator,
            "project": project,
            "pipeline": PIPELINE_ORDER,
            "current_state": current,
            "available_targets": sorted(t.value for t in TRANSITIONS[current]),
            "transitions": list(reversed(project.transitions)),
            "strategy": strategy,
            "backtests": backtests,
            "risk_assessments": risk_assessments,
            "approvals": approvals,
            "orders": orders,
            "approval_kinds": approval_kinds,
            "csrf_token": _csrf(session),
        },
    )


@router.post("/projects/{project_id}/transition")
def transition_project(
    project_id: str,
    db: DbDep,
    operator: OperatorDep,
    target: Annotated[str, Form()],
    reason: Annotated[str, Form()],
    _: Annotated[None, Depends(require_csrf)],
) -> Response:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    try:
        target_state = ProjectState(target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Unknown state.") from exc

    orchestrator = Orchestrator(db)
    try:
        orchestrator.transition(
            project, target_state, actor=operator.email, reason=reason.strip() or "n/a"
        )
    except TransitionError as exc:
        db.commit()  # keep the blocked-transition audit entry
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    db.commit()
    return RedirectResponse(
        f"/projects/{project_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/audit")
def audit_log(
    request: Request,
    db: DbDep,
    operator: OperatorDep,
    limit: int = 100,
) -> Response:
    limit = max(1, min(limit, 500))
    events = list(
        db.scalars(select(AuditEvent).order_by(AuditEvent.sequence.desc()).limit(limit))
    )
    verification = AuditLog(db).verify_chain()
    return templates.TemplateResponse(
        request,
        "audit.html",
        {
            "title": "Audit log",
            "operator": operator,
            "events": events,
            "verification": verification,
        },
    )


@router.get("/kill-switch")
def kill_switch_page(
    request: Request,
    db: DbDep,
    operator: OperatorDep,
    session: SessionDep,
) -> Response:
    return templates.TemplateResponse(
        request,
        "kill_switch.html",
        {
            "title": "Kill switch",
            "operator": operator,
            "kill_switch": _kill_switch(db),
            "csrf_token": _csrf(session),
        },
    )


@router.post("/kill-switch")
def set_kill_switch(
    db: DbDep,
    operator: OperatorDep,
    engaged: Annotated[str, Form()],
    reason: Annotated[str, Form()],
    _: Annotated[None, Depends(require_csrf)],
) -> Response:
    flag = _kill_switch(db)
    flag.value = engaged == "true"
    flag.updated_by = operator.email
    flag.reason = reason.strip() or None
    AuditLog(db).record(
        category=AuditCategory.SYSTEM,
        action="kill_switch_engaged" if flag.value else "kill_switch_released",
        actor=operator.email,
        payload={"engaged": flag.value, "reason": flag.reason},
    )
    db.commit()
    return RedirectResponse("/kill-switch", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/settings")
def settings_page(
    request: Request,
    operator: OperatorDep,
    settings: SettingsDep,
) -> Response:
    """Read-only view of effective limits. Secrets are never rendered."""
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"title": "Settings", "operator": operator, "settings_obj": settings},
    )


@router.get("/healthz", include_in_schema=False)
def healthz(db: DbDep) -> dict[str, str]:
    db.execute(select(1))
    return {"status": "ok"}
