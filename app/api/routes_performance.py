"""Performance review and experiment-tracking routes (Phase 6).

``POST /projects/{id}/performance-review`` mirrors ``run_risk_review`` from
``app.api.routes_trading`` exactly: a deterministic engine
(``app.core.performance``) runs over data already in the database -- here,
the project's own ``Order`` fills -- and persists a verdict. No agent, no
network call.

``GET /projects/{id}/experiments`` is the "experiment tracking" and
"strategy versioning" view: every ``Strategy`` version for the project,
each with its own backtests, risk assessments, orders and performance
reviews, using the foreign keys those models already carry -- no new join
table. ``RiskAssessment`` has no ``strategy_id`` of its own, so its rows are
reached by joining through ``Backtest.strategy_id``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.core.audit import AuditCategory, AuditLog
from app.core.performance import compute_order_performance, evaluate_performance
from app.db.models import (
    Backtest,
    Order,
    PerformanceReview,
    Project,
    RiskAssessment,
    Strategy,
)
from app.deps import DbDep, OperatorDep, require_csrf
from app.web.templating import templates

router = APIRouter(tags=["performance"])


def _project_or_404(db: DbDep, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project


def _latest_strategy(db: DbDep, project_id: str) -> Strategy | None:
    return db.scalars(
        select(Strategy)
        .where(Strategy.project_id == project_id)
        .order_by(Strategy.version.desc())
    ).first()


@router.post("/projects/{project_id}/performance-review")
def run_performance_review(
    project_id: str,
    db: DbDep,
    operator: OperatorDep,
    _: Annotated[None, Depends(require_csrf)],
) -> Response:
    project = _project_or_404(db, project_id)
    strategy = _latest_strategy(db, project_id)
    if strategy is None:
        raise HTTPException(status_code=400, detail="Specify a strategy first.")

    orders = list(
        db.scalars(select(Order).where(Order.strategy_id == strategy.id))
    )
    result = compute_order_performance(orders)
    verdict = evaluate_performance(result)

    review = PerformanceReview(
        project_id=project.id,
        strategy_id=strategy.id,
        trade_count=result.trade_count,
        realized_pnl=result.realized_pnl,
        win_rate_pct=result.win_rate_pct,
        max_drawdown=result.max_drawdown,
        accepted=verdict.accepted,
        reasons=verdict.reasons,
        equity_curve=result.equity_curve,
        created_by=operator.email,
    )
    db.add(review)
    db.flush()
    AuditLog(db).record(
        category=AuditCategory.PERFORMANCE,
        action="performance_reviewed",
        actor=operator.email,
        project_id=project.id,
        payload={
            "strategy_version": strategy.version,
            "trade_count": result.trade_count,
            "realized_pnl": str(result.realized_pnl),
            "accepted": verdict.accepted,
        },
    )
    db.commit()
    return RedirectResponse(f"/projects/{project_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/projects/{project_id}/experiments")
def list_experiments(
    request: Request,
    project_id: str,
    db: DbDep,
    operator: OperatorDep,
) -> Response:
    project = _project_or_404(db, project_id)
    strategies = list(
        db.scalars(
            select(Strategy)
            .where(Strategy.project_id == project_id)
            .order_by(Strategy.version.desc())
        )
    )

    experiments = []
    for strategy in strategies:
        backtests = list(
            db.scalars(
                select(Backtest)
                .where(Backtest.strategy_id == strategy.id)
                .order_by(Backtest.created_at.desc())
            )
        )
        backtest_ids = [b.id for b in backtests]
        risk_assessments = (
            list(
                db.scalars(
                    select(RiskAssessment)
                    .where(RiskAssessment.backtest_id.in_(backtest_ids))
                    .order_by(RiskAssessment.created_at.desc())
                )
            )
            if backtest_ids
            else []
        )
        orders = list(
            db.scalars(
                select(Order)
                .where(Order.strategy_id == strategy.id)
                .order_by(Order.created_at.desc())
            )
        )
        performance_reviews = list(
            db.scalars(
                select(PerformanceReview)
                .where(PerformanceReview.strategy_id == strategy.id)
                .order_by(PerformanceReview.created_at.desc())
            )
        )
        experiments.append(
            {
                "strategy": strategy,
                "backtests": backtests,
                "risk_assessments": risk_assessments,
                "orders": orders,
                "performance_reviews": performance_reviews,
            }
        )

    return templates.TemplateResponse(
        request,
        "experiments.html",
        {
            "title": f"{project.name} · Experiments",
            "operator": operator,
            "project": project,
            "experiments": experiments,
        },
    )
