"""Broker connectivity: read-only account verification and reconciliation.

No route here submits an order. See ``app.brokers.base`` for why.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.brokers.base import BrokerError
from app.core.audit import AuditCategory, AuditLog
from app.db.models import BrokerAccountSnapshot, BrokerReconciliationRun, Order
from app.deps import BrokerDep, DbDep, OperatorDep, SettingsDep, get_session_data, require_csrf
from app.logging_config import mask_identifier
from app.security import SessionData
from app.web.templating import templates

logger = logging.getLogger(__name__)
router = APIRouter(tags=["broker"])

SessionDep = Annotated[SessionData | None, Depends(get_session_data)]


def _csrf(session: SessionData | None) -> str:
    return session.csrf_token if session else ""


@router.get("/broker")
def broker_page(
    request: Request,
    db: DbDep,
    operator: OperatorDep,
    settings: SettingsDep,
    session: SessionDep,
) -> Response:
    snapshot = db.scalars(
        select(BrokerAccountSnapshot).order_by(BrokerAccountSnapshot.created_at.desc())
    ).first()
    run = db.scalars(
        select(BrokerReconciliationRun).order_by(BrokerReconciliationRun.created_at.desc())
    ).first()
    return templates.TemplateResponse(
        request,
        "broker.html",
        {
            "title": "Broker",
            "operator": operator,
            "backend": settings.broker_backend,
            "snapshot": snapshot,
            "run": run,
            "csrf_token": _csrf(session),
        },
    )


@router.post("/broker/verify")
def verify_broker(
    db: DbDep,
    operator: OperatorDep,
    broker: BrokerDep,
    _: Annotated[None, Depends(require_csrf)],
) -> Response:
    try:
        account = broker.get_account()
    except BrokerError as exc:
        AuditLog(db).record(
            category=AuditCategory.BROKER,
            action="broker_verify_failed",
            actor=operator.email,
            payload={"error": str(exc), "error_type": type(exc).__name__},
        )
        db.commit()  # keep the failure audit entry
        raise HTTPException(
            status_code=502, detail="Could not verify the broker connection."
        ) from exc

    snapshot = BrokerAccountSnapshot(
        actor=operator.email,
        account_id=account.account_id,
        account_status=account.status,
        is_paper=account.is_paper,
        currency=account.currency,
        cash=account.cash,
        portfolio_value=account.portfolio_value,
        buying_power=account.buying_power,
        pattern_day_trader=account.pattern_day_trader,
        trading_blocked=account.trading_blocked,
        raw_response=account.raw,
    )
    db.add(snapshot)
    db.flush()
    AuditLog(db).record(
        category=AuditCategory.BROKER,
        action="broker_verified",
        actor=operator.email,
        payload={"account_id": mask_identifier(account.account_id), "status": account.status},
    )
    db.commit()
    return RedirectResponse("/broker", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/broker/reconcile")
def reconcile_broker(
    db: DbDep,
    operator: OperatorDep,
    broker: BrokerDep,
    _: Annotated[None, Depends(require_csrf)],
) -> Response:
    try:
        positions = broker.list_positions()
        orders = broker.list_open_orders()
    except BrokerError as exc:
        AuditLog(db).record(
            category=AuditCategory.BROKER,
            action="broker_reconcile_failed",
            actor=operator.email,
            payload={"error": str(exc), "error_type": type(exc).__name__},
        )
        db.commit()
        raise HTTPException(
            status_code=502, detail="Could not reconcile with the broker."
        ) from exc

    # A position is "tracked" if QuantLab's own ledger has ever filled that
    # symbol; an order is "tracked" if its broker id matches a ledger row
    # exactly. Everything else is untracked -- activity the broker reports
    # that QuantLab did not originate.
    traded_symbols = {row[0] for row in db.execute(select(Order.symbol).distinct())}
    known_broker_order_ids = {
        row[0] for row in db.execute(select(Order.broker_order_id).distinct())
    }

    position_findings: list[dict[str, str]] = [
        {"kind": "position", "symbol": p.symbol, "side": p.side, "qty": str(p.qty)}
        for p in positions
        if p.symbol not in traded_symbols
    ]
    order_findings: list[dict[str, str]] = [
        {
            "kind": "order",
            "symbol": o.symbol,
            "side": o.side,
            "qty": str(o.qty),
            "status": o.status,
            "broker_id": mask_identifier(o.order_id),
        }
        for o in orders
        if o.order_id not in known_broker_order_ids
    ]
    findings: list[dict[str, str]] = position_findings + order_findings
    run = BrokerReconciliationRun(
        actor=operator.email,
        open_position_count=len(positions),
        open_order_count=len(orders),
        untracked_position_count=len(position_findings),
        untracked_order_count=len(order_findings),
        findings=findings,
    )
    db.add(run)
    db.flush()
    AuditLog(db).record(
        category=AuditCategory.BROKER,
        action="broker_reconciled",
        actor=operator.email,
        payload={"open_positions": len(positions), "open_orders": len(orders)},
    )
    db.commit()
    return RedirectResponse("/broker", status_code=status.HTTP_303_SEE_OTHER)
