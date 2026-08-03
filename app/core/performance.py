"""Deterministic performance review. Pure functions -- no network, no
database.

Per docs/architecture.md, this is the engine behind the ``PERFORMANCE_REVIEW``
pipeline stage: it realizes profit and loss from a project's own ``Order``
ledger, never from the broker's account snapshot, so the same review is
reproducible from data QuantLab already persisted.

Known limitation, documented rather than silently skipped: a sell fill with
no matching open lot (e.g. a short QuantLab's risk engine would normally
disallow) is excluded from realized P&L rather than modeled -- there is no
short-position accounting here. ``max_drawdown`` is a dollar figure, not a
percentage: there is no fixed capital base in the order ledger to divide by.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal

from app.db.models import Order


@dataclass(frozen=True)
class PerformanceResult:
    trade_count: int
    realized_pnl: Decimal
    win_rate_pct: Decimal
    max_drawdown: Decimal
    equity_curve: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class PerformanceVerdict:
    accepted: bool
    reasons: list[str] = field(default_factory=list)


def compute_order_performance(orders: list[Order]) -> PerformanceResult:
    filled = sorted(
        (o for o in orders if o.status.lower() == "filled" and o.filled_avg_price is not None),
        key=lambda o: o.filled_at or o.created_at,
    )

    open_lots: dict[str, deque[tuple[Decimal, Decimal]]] = {}
    equity_curve: list[dict[str, str]] = []
    cumulative_pnl = Decimal(0)
    trade_count = 0
    winning_trades = 0

    for order in filled:
        price = order.filled_avg_price
        assert price is not None  # narrowed by the filter above
        lots = open_lots.setdefault(order.symbol, deque())

        if order.side == "buy":
            lots.append((order.qty, price))
            continue

        remaining = order.qty
        trade_pnl = Decimal(0)
        while remaining > 0 and lots:
            lot_qty, lot_price = lots[0]
            matched = min(remaining, lot_qty)
            trade_pnl += (price - lot_price) * matched
            remaining -= matched
            if matched == lot_qty:
                lots.popleft()
            else:
                lots[0] = (lot_qty - matched, lot_price)

        matched_qty = order.qty - remaining
        if matched_qty <= 0:
            continue  # nothing to realize -- a sell with no open lot to match

        trade_count += 1
        if trade_pnl > 0:
            winning_trades += 1
        cumulative_pnl += trade_pnl
        equity_curve.append(
            {
                "trade_number": str(trade_count),
                "cumulative_pnl": str(cumulative_pnl),
                "filled_at": (order.filled_at or order.created_at).isoformat(),
            }
        )

    win_rate_pct = (
        Decimal(winning_trades) / Decimal(trade_count) * 100 if trade_count else Decimal(0)
    )

    peak = Decimal(0)
    max_drawdown = Decimal(0)
    running = Decimal(0)
    for point in equity_curve:
        running = Decimal(point["cumulative_pnl"])
        peak = max(peak, running)
        max_drawdown = max(max_drawdown, peak - running)

    return PerformanceResult(
        trade_count=trade_count,
        realized_pnl=cumulative_pnl,
        win_rate_pct=win_rate_pct,
        max_drawdown=max_drawdown,
        equity_curve=equity_curve,
    )


def evaluate_performance(result: PerformanceResult) -> PerformanceVerdict:
    reasons: list[str] = []
    if result.trade_count == 0:
        reasons.append("no completed round-trip trades yet")
    elif result.realized_pnl <= 0:
        reasons.append(f"realized P&L is not positive ({result.realized_pnl})")
    return PerformanceVerdict(accepted=not reasons, reasons=reasons)
