"""Strategy specification, backtesting, risk review, approvals and order
submission -- the routes that give the pipeline's later stages real work to
do, instead of a bare state-transition label.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.brokers.base import BrokerError
from app.core.audit import AuditCategory, AuditLog
from app.core.backtest import evaluate_acceptance, simulate_sma_crossover
from app.core.risk import assess_order, assess_strategy
from app.core.states import TRADING_STATES, ProjectState
from app.db.base import utcnow
from app.db.models import (
    KILL_SWITCH_KEY,
    Approval,
    ApprovalKind,
    Backtest,
    DailyEquityMark,
    MarketBar,
    Order,
    Project,
    RiskAssessment,
    Strategy,
    SystemFlag,
)
from app.deps import BrokerDep, DbDep, MarketDataDep, OperatorDep, SettingsDep, require_csrf
from app.logging_config import mask_identifier
from app.marketdata.base import MarketDataError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["trading"])


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


def _kill_switch_released(db: DbDep) -> bool:
    flag = db.get(SystemFlag, KILL_SWITCH_KEY)
    return flag is not None and not flag.value


@router.post("/projects/{project_id}/strategy")
def specify_strategy(
    project_id: str,
    db: DbDep,
    operator: OperatorDep,
    name: Annotated[str, Form()],
    symbol: Annotated[str, Form()],
    timeframe: Annotated[str, Form()],
    fast_window: Annotated[int, Form()],
    slow_window: Annotated[int, Form()],
    minimum_out_of_sample_trades: Annotated[int, Form()],
    _: Annotated[None, Depends(require_csrf)],
) -> Response:
    project = _project_or_404(db, project_id)
    if fast_window <= 0 or slow_window <= 0 or fast_window >= slow_window:
        raise HTTPException(
            status_code=400,
            detail="fast_window must be a positive integer less than slow_window.",
        )

    previous = _latest_strategy(db, project_id)
    strategy = Strategy(
        project_id=project.id,
        version=(previous.version + 1 if previous else 1),
        name=name.strip(),
        symbol=symbol.strip().upper(),
        timeframe=timeframe.strip(),
        fast_window=fast_window,
        slow_window=slow_window,
        minimum_out_of_sample_trades=minimum_out_of_sample_trades,
        created_by=operator.email,
    )
    db.add(strategy)
    db.flush()
    AuditLog(db).record(
        category=AuditCategory.SYSTEM,
        action="strategy_specified",
        actor=operator.email,
        project_id=project.id,
        payload={"symbol": strategy.symbol, "version": strategy.version},
    )
    db.commit()
    return RedirectResponse(f"/projects/{project_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/projects/{project_id}/backtests")
def run_backtest(
    project_id: str,
    db: DbDep,
    operator: OperatorDep,
    market_data: MarketDataDep,
    start_date: Annotated[str, Form()] = "",
    end_date: Annotated[str, Form()] = "",
    _: Annotated[None, Depends(require_csrf)] = None,
) -> Response:
    project = _project_or_404(db, project_id)
    strategy = _latest_strategy(db, project_id)
    if strategy is None:
        raise HTTPException(status_code=400, detail="Specify a strategy first.")

    # A blank HTML date input submits "", not an omitted field -- treat both
    # as "use the default."
    end = date.fromisoformat(end_date) if end_date else utcnow().date() - timedelta(days=1)
    start = date.fromisoformat(start_date) if start_date else end - timedelta(days=365 * 10)

    try:
        bars = market_data.get_or_fetch_bars(
            strategy.symbol, start, end, timeframe=strategy.timeframe
        )
        result = simulate_sma_crossover(bars, strategy.fast_window, strategy.slow_window)
    except (MarketDataError, ValueError) as exc:
        AuditLog(db).record(
            category=AuditCategory.BACKTEST,
            action="backtest_failed",
            actor=operator.email,
            project_id=project.id,
            payload={"error": str(exc)},
        )
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    verdict = evaluate_acceptance(result, strategy.minimum_out_of_sample_trades)
    backtest = Backtest(
        project_id=project.id,
        strategy_id=strategy.id,
        start_date=start,
        end_date=end,
        total_return_pct=result.total_return_pct,
        benchmark_return_pct=result.benchmark_return_pct,
        max_drawdown_pct=result.max_drawdown_pct,
        trade_count=len(result.trades),
        out_of_sample_trade_count=result.out_of_sample_trade_count,
        accepted=verdict.accepted,
        verdict_reason=verdict.reason,
        equity_curve=result.equity_curve,
        created_by=operator.email,
    )
    db.add(backtest)
    db.flush()
    AuditLog(db).record(
        category=AuditCategory.BACKTEST,
        action="backtest_completed",
        actor=operator.email,
        project_id=project.id,
        payload={
            "accepted": verdict.accepted,
            "total_return_pct": str(result.total_return_pct),
            "benchmark_return_pct": str(result.benchmark_return_pct),
            "out_of_sample_trade_count": result.out_of_sample_trade_count,
        },
    )
    db.commit()
    return RedirectResponse(f"/projects/{project_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/projects/{project_id}/risk-review")
def run_risk_review(
    project_id: str,
    db: DbDep,
    operator: OperatorDep,
    settings: SettingsDep,
    _: Annotated[None, Depends(require_csrf)],
) -> Response:
    project = _project_or_404(db, project_id)
    strategy = _latest_strategy(db, project_id)
    if strategy is None:
        raise HTTPException(status_code=400, detail="Specify a strategy first.")

    latest_backtest = db.scalars(
        select(Backtest)
        .where(Backtest.project_id == project_id)
        .order_by(Backtest.created_at.desc())
    ).first()

    verdict = assess_strategy(
        symbol=strategy.symbol,
        timeframe=strategy.timeframe,
        fast_window=strategy.fast_window,
        slow_window=strategy.slow_window,
        settings=settings,
    )
    assessment = RiskAssessment(
        project_id=project.id,
        backtest_id=latest_backtest.id if latest_backtest else None,
        approved=verdict.approved,
        reasons=verdict.reasons,
        checked_limits={
            "allowed_symbols": settings.allowed_symbols,
            "allowed_timeframes": settings.allowed_timeframes,
        },
        created_by=operator.email,
    )
    db.add(assessment)
    db.flush()
    AuditLog(db).record(
        category=AuditCategory.RISK,
        action="risk_reviewed",
        actor=operator.email,
        project_id=project.id,
        payload={"approved": verdict.approved, "reasons": verdict.reasons},
    )
    db.commit()
    return RedirectResponse(f"/projects/{project_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/projects/{project_id}/approvals")
def record_approval(
    project_id: str,
    db: DbDep,
    operator: OperatorDep,
    kind: Annotated[str, Form()],
    decision: Annotated[str, Form()],
    notes: Annotated[str, Form()],
    _: Annotated[None, Depends(require_csrf)],
) -> Response:
    project = _project_or_404(db, project_id)
    if decision not in ("approved", "rejected"):
        raise HTTPException(
            status_code=400, detail="decision must be 'approved' or 'rejected'."
        )
    if db.get(ApprovalKind, kind) is None:
        raise HTTPException(status_code=400, detail=f"Unknown approval kind {kind!r}.")

    approval = Approval(
        project_id=project.id,
        operator_id=operator.id,
        kind=kind,
        decision=decision,
        notes=notes.strip() or None,
    )
    db.add(approval)
    db.flush()
    AuditLog(db).record(
        category=AuditCategory.APPROVAL,
        action="approval_recorded",
        actor=operator.email,
        project_id=project.id,
        payload={"kind": kind, "decision": decision},
    )
    db.commit()
    return RedirectResponse(f"/projects/{project_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/projects/{project_id}/orders")
def submit_project_order(
    project_id: str,
    db: DbDep,
    operator: OperatorDep,
    settings: SettingsDep,
    broker: BrokerDep,
    symbol: Annotated[str, Form()],
    side: Annotated[str, Form()],
    qty: Annotated[str, Form()],
    _: Annotated[None, Depends(require_csrf)],
) -> Response:
    project = _project_or_404(db, project_id)
    if ProjectState(project.state) not in TRADING_STATES:
        raise HTTPException(
            status_code=409, detail="Orders may only be submitted while PAPER_TRADING."
        )
    if not _kill_switch_released(db):
        raise HTTPException(status_code=409, detail="The kill switch is engaged.")

    try:
        qty_decimal = Decimal(qty)
    except InvalidOperation as exc:
        raise HTTPException(status_code=400, detail="qty must be a number.") from exc
    symbol = symbol.strip().upper()

    latest_bar = db.scalars(
        select(MarketBar)
        .where(MarketBar.symbol == symbol)
        .order_by(MarketBar.timestamp.desc())
    ).first()
    if latest_bar is None:
        raise HTTPException(
            status_code=400,
            detail=f"No cached market data for {symbol}; run a backtest for it first.",
        )
    price = latest_bar.close

    today = utcnow().date()
    orders_today = sum(
        1
        for o in db.scalars(select(Order).where(Order.project_id == project_id))
        if o.created_at.date() == today
    )

    account = broker.get_account()
    positions = broker.list_positions()

    equity_mark = db.scalars(
        select(DailyEquityMark).where(DailyEquityMark.date == today)
    ).first()
    if equity_mark is None:
        # The first observed equity of the day becomes its baseline -- there
        # is no scheduled market-open snapshot to read instead.
        equity_mark = DailyEquityMark(date=today, opening_equity=account.portfolio_value)
        db.add(equity_mark)
        db.flush()

    verdict = assess_order(
        symbol=symbol,
        side=side,
        qty=qty_decimal,
        price=price,
        account=account,
        positions=positions,
        orders_today=orders_today,
        day_start_equity=equity_mark.opening_equity,
        settings=settings,
    )
    if not verdict.approved:
        AuditLog(db).record(
            category=AuditCategory.RISK,
            action="order_refused",
            actor=operator.email,
            project_id=project.id,
            payload={"symbol": symbol, "side": side, "reasons": verdict.reasons},
        )
        db.commit()
        raise HTTPException(status_code=409, detail="; ".join(verdict.reasons))

    strategy = _latest_strategy(db, project_id)
    try:
        broker_order = broker.submit_order(
            symbol=symbol, qty=qty_decimal, side=side, order_type="market", time_in_force="day"
        )
    except BrokerError as exc:
        AuditLog(db).record(
            category=AuditCategory.ORDER,
            action="order_failed",
            actor=operator.email,
            project_id=project.id,
            payload={"error": str(exc), "error_type": type(exc).__name__},
        )
        db.commit()
        raise HTTPException(status_code=502, detail="The broker rejected the order.") from exc

    filled = broker_order.status.lower() == "filled"
    order = Order(
        project_id=project.id,
        strategy_id=strategy.id if strategy else None,
        broker_order_id=broker_order.order_id,
        symbol=broker_order.symbol,
        side=broker_order.side,
        qty=broker_order.qty,
        order_type=broker_order.order_type,
        time_in_force=broker_order.time_in_force,
        status=broker_order.status,
        # Reference price used for the risk check, not a confirmed fill price
        # from the broker -- Alpaca fills asynchronously and doesn't return
        # one synchronously. Good enough for a paper-trading MVP.
        filled_avg_price=price if filled else None,
        filled_at=broker_order.submitted_at if filled else None,
        submitted_by=operator.email,
    )
    db.add(order)
    db.flush()
    AuditLog(db).record(
        category=AuditCategory.ORDER,
        action="order_submitted",
        actor=operator.email,
        project_id=project.id,
        payload={
            "symbol": symbol,
            "side": side,
            "qty": str(qty_decimal),
            "broker_order_id": mask_identifier(broker_order.order_id),
            "status": broker_order.status,
        },
    )
    db.commit()
    return RedirectResponse(f"/projects/{project_id}", status_code=status.HTTP_303_SEE_OTHER)
