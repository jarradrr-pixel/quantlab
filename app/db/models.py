"""ORM models.

Phase 1: operators, projects, the state history, human approvals and the
audit chain. Phase 3: broker account snapshots and reconciliation runs.
Phase 2: market data cache, strategies, backtests, risk assessments and the
internal order ledger.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.core.states import ProjectState
from app.db.base import Base, utcnow

#: JSONB on PostgreSQL, plain JSON on the SQLite fallback.
JSONType = JSON().with_variant(JSONB(), "postgresql")  # type: ignore[no-untyped-call]


def new_id() -> str:
    return str(uuid.uuid4())


class Operator(Base):
    """A human user of the dashboard. Only humans may approve anything."""

    __tablename__ = "operators"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    approvals: Mapped[list[Approval]] = relationship(back_populates="operator")


class Project(Base):
    """One research objective moving through the pipeline."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    research_objective: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[ProjectState] = mapped_column(
        String(40), default=ProjectState.RESEARCHING, nullable=False
    )
    resume_state: Mapped[ProjectState | None] = mapped_column(String(40), nullable=True)
    """Set when the project is paused so it can resume to the exact prior stage."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    transitions: Mapped[list[StateTransition]] = relationship(
        back_populates="project", order_by="StateTransition.created_at"
    )
    approvals: Mapped[list[Approval]] = relationship(back_populates="project")


class StateTransition(Base):
    """An immutable record of one movement through the state machine."""

    __tablename__ = "state_transitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    from_state: Mapped[ProjectState] = mapped_column(String(40), nullable=False)
    to_state: Mapped[ProjectState] = mapped_column(String(40), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    """Who caused it: an operator email, ``system``, or an agent name."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="transitions")


class ApprovalKind(Base):
    """Lookup of approval kinds, kept as data so new gates need no migration."""

    __tablename__ = "approval_kinds"

    code: Mapped[str] = mapped_column(String(60), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class Approval(Base):
    """A human decision. The AI can never write rows to this table.

    Enforcement is twofold: the service layer requires an ``Operator`` row, and
    agent code runs without a database session at all.
    """

    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('approved', 'rejected')", name="decision_is_valid"
        ),
        Index("ix_approvals_project_kind", "project_id", "kind"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    operator_id: Mapped[str] = mapped_column(
        ForeignKey("operators.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    subject_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    """Identifies exactly what was approved, e.g. a strategy version or order id."""

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="approvals")
    operator: Mapped[Operator] = relationship(back_populates="approvals")


class SystemFlag(Base):
    """Global operational switches, most importantly the kill switch."""

    __tablename__ = "system_flags"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(120), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


KILL_SWITCH_KEY = "kill_switch_engaged"


class AuditEvent(Base):
    """Append-only, hash-chained audit record.

    ``previous_hash`` links each row to its predecessor so that deletion or
    modification of any row breaks verification of every later row. The
    application never issues UPDATE or DELETE against this table, and the
    production database role is granted only INSERT and SELECT.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("sequence", name="sequence_is_unique"),
        Index("ix_audit_events_category_created", "category", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class BrokerAccountSnapshot(Base):
    """A point-in-time read of the linked Alpaca paper account.

    Written only by the ``/broker/verify`` action. Account-wide, not scoped to
    a project -- the brokerage account isn't a research artifact. Append-only:
    each verify call inserts a new row rather than updating the last one, so
    the connectivity history survives.
    """

    __tablename__ = "broker_account_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    account_status: Mapped[str] = mapped_column(String(40), nullable=False)
    is_paper: Mapped[bool] = mapped_column(Boolean, nullable=False)
    """Asserted by the adapter's base-URL allowlist, not read from Alpaca's
    response -- Alpaca's account object carries no explicit paper/live flag."""

    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    cash: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    portfolio_value: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    buying_power: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    pattern_day_trader: Mapped[bool] = mapped_column(Boolean, nullable=False)
    trading_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False)
    raw_response: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )


class BrokerReconciliationRun(Base):
    """One 'what does the broker see that QuantLab doesn't' check.

    Phase 3 ships no internal order/position ledger, so every position or open
    order the broker reports is untracked by definition; ``untracked_*_count``
    mirrors ``open_*_count`` today and becomes meaningful once a later phase
    adds a ledger to diff against.
    """

    __tablename__ = "broker_reconciliation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("broker_account_snapshots.id", ondelete="RESTRICT"), nullable=True
    )
    open_position_count: Mapped[int] = mapped_column(Integer, nullable=False)
    open_order_count: Mapped[int] = mapped_column(Integer, nullable=False)
    untracked_position_count: Mapped[int] = mapped_column(Integer, nullable=False)
    untracked_order_count: Mapped[int] = mapped_column(Integer, nullable=False)
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )


class MarketBar(Base):
    """One cached OHLCV bar. ``close`` is split/dividend adjusted (yfinance
    ``auto_adjust=True``) -- there is no separate adjusted-close column."""

    __tablename__ = "market_bars"
    __table_args__ = (
        UniqueConstraint(
            "symbol", "timeframe", "timestamp", name="uq_market_bars_symbol_timeframe_ts"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(20), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Strategy(Base):
    """A strategy specification. Append-only: a respec inserts a new version
    rather than updating in place; "current" is the latest by ``created_at``.
    """

    __tablename__ = "strategies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(20), nullable=False)
    fast_window: Mapped[int] = mapped_column(Integer, nullable=False)
    slow_window: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_out_of_sample_trades: Mapped[int] = mapped_column(Integer, nullable=False)
    """Per-strategy acceptance threshold -- deliberately not a global default;
    a slow reference strategy documents its low trade count as an explicit,
    intentional exception here rather than failing a one-size-fits-all gate."""

    created_by: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    project: Mapped[Project] = relationship()


class Backtest(Base):
    """One backtest run of a strategy over a historical date range."""

    __tablename__ = "backtests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    strategy_id: Mapped[str] = mapped_column(
        ForeignKey("strategies.id", ondelete="RESTRICT"), nullable=False
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_return_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    benchmark_return_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    max_drawdown_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False)
    out_of_sample_trade_count: Mapped[int] = mapped_column(Integer, nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    verdict_reason: Mapped[str] = mapped_column(Text, nullable=False)
    equity_curve: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, nullable=False)
    created_by: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    project: Mapped[Project] = relationship()
    strategy: Mapped[Strategy] = relationship()


class RiskAssessment(Base):
    """One deterministic risk-engine verdict against a strategy."""

    __tablename__ = "risk_assessments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    backtest_id: Mapped[str | None] = mapped_column(
        ForeignKey("backtests.id", ondelete="RESTRICT"), nullable=True
    )
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSONType, nullable=False)
    checked_limits: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    created_by: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    project: Mapped[Project] = relationship()


class Order(Base):
    """The internal order ledger -- what QuantLab itself submitted, whether
    filled by ``MockBroker`` or ``AlpacaPaperBroker``. This is what Phase 3's
    reconciliation compares broker-reported positions/orders against."""

    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    strategy_id: Mapped[str | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="RESTRICT"), nullable=True
    )
    broker_order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    order_type: Mapped[str] = mapped_column(String(20), nullable=False)
    time_in_force: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    filled_avg_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_by: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    project: Mapped[Project] = relationship()
    strategy: Mapped[Strategy | None] = relationship()


class MockFill(Base):
    """MockBroker's own account-wide fill ledger.

    Deliberately separate from ``Order``: a broker (real or mock) doesn't
    know about QuantLab's projects, only about fills. ``MockBroker`` reads
    and writes this table exactly as ``AlpacaPaperBroker`` reads and writes
    Alpaca's own external books -- ``Order`` is always QuantLab's own mirror
    of what a broker reported, created by the route layer, never by an
    adapter directly.
    """

    __tablename__ = "mock_fills"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    filled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
