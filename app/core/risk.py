"""Deterministic risk checks. Pure functions -- no network, no database.

Per docs/architecture.md, the risk engine's job is to approve or refuse and
never be overridden by a model. Two entry points: ``assess_strategy`` (at the
RISK_REVIEW pipeline stage, before a backtest's implied trading is trusted)
and ``assess_order`` (immediately before a broker call, using live account
state).

Known limitation, documented rather than silently skipped:
``max_daily_loss_percentage`` is not enforced here -- it needs day-start
equity tracking that doesn't exist yet. See docs/security.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.brokers.base import BrokerAccount, BrokerPosition
from app.config import Settings


@dataclass(frozen=True)
class RiskVerdict:
    approved: bool
    reasons: list[str] = field(default_factory=list)


def assess_strategy(
    *,
    symbol: str,
    timeframe: str,
    fast_window: int,
    slow_window: int,
    settings: Settings,
) -> RiskVerdict:
    reasons: list[str] = []
    if symbol not in settings.allowed_symbols:
        reasons.append(
            f"{symbol} is not in the allowed symbol list {settings.allowed_symbols}"
        )
    if timeframe not in settings.allowed_timeframes:
        reasons.append(
            f"{timeframe} is not in the allowed timeframe list {settings.allowed_timeframes}"
        )
    if not (0 < fast_window < slow_window):
        reasons.append("fast_window must be a positive integer less than slow_window")
    return RiskVerdict(approved=not reasons, reasons=reasons)


def assess_order(
    *,
    symbol: str,
    side: str,
    qty: Decimal,
    price: Decimal,
    account: BrokerAccount,
    positions: list[BrokerPosition],
    orders_today: int,
    settings: Settings,
) -> RiskVerdict:
    reasons: list[str] = []
    notional = qty * price
    existing = next((p for p in positions if p.symbol == symbol), None)

    if symbol not in settings.allowed_symbols:
        reasons.append(
            f"{symbol} is not in the allowed symbol list {settings.allowed_symbols}"
        )

    if side == "sell":
        covers = existing is not None and existing.side in ("long", "buy")
        covering_qty = existing.qty if covers and existing is not None else Decimal(0)
        if qty > covering_qty and not settings.allow_shorting:
            reasons.append(
                f"selling {qty} {symbol} would exceed the existing long position of "
                f"{covering_qty} and shorting is disabled"
            )

    if orders_today >= settings.max_orders_per_day:
        reasons.append(
            f"{orders_today} order(s) already submitted today; "
            f"max_orders_per_day is {settings.max_orders_per_day}"
        )

    is_new_symbol = existing is None or existing.qty == 0
    open_symbol_count = len({p.symbol for p in positions if p.qty != 0})
    if is_new_symbol and side == "buy" and open_symbol_count >= settings.max_open_positions:
        reasons.append(
            f"already at max_open_positions ({settings.max_open_positions}); "
            f"cannot open a new position in {symbol}"
        )

    if account.portfolio_value > 0:
        position_pct = notional / account.portfolio_value * 100
        max_position_pct = Decimal(str(settings.max_position_percentage))
        if position_pct > max_position_pct:
            reasons.append(
                f"order notional is {position_pct:.2f}% of portfolio value, exceeding "
                f"max_position_percentage of {settings.max_position_percentage}%"
            )

        existing_notional = sum((p.market_value for p in positions), Decimal(0))
        total_exposure_pct = (existing_notional + notional) / account.portfolio_value * 100
        max_total_pct = Decimal(str(settings.max_total_exposure_percentage))
        if total_exposure_pct > max_total_pct:
            reasons.append(
                f"total exposure would be {total_exposure_pct:.2f}% of portfolio value, "
                "exceeding max_total_exposure_percentage of "
                f"{settings.max_total_exposure_percentage}%"
            )

    if side == "buy" and notional > account.buying_power and not settings.allow_leverage:
        reasons.append(
            f"order notional {notional} exceeds buying power {account.buying_power} "
            "and leverage is disabled"
        )

    return RiskVerdict(approved=not reasons, reasons=reasons)
