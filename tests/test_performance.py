"""Performance engine tests. Pure functions, no network, no database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.core.performance import compute_order_performance, evaluate_performance
from app.db.models import Order

T0 = datetime(2024, 1, 1, tzinfo=UTC)


def _order(
    side: str,
    qty: str,
    price: str | None,
    when: datetime,
    *,
    status: str = "filled",
    symbol: str = "SPY",
) -> Order:
    return Order(
        project_id="p",
        strategy_id="s",
        broker_order_id="b",
        symbol=symbol,
        side=side,
        qty=Decimal(qty),
        order_type="market",
        time_in_force="day",
        status=status,
        filled_avg_price=Decimal(price) if price is not None else None,
        filled_at=when,
        submitted_by="operator@example.test",
        created_at=when,
    )


def test_full_match_realizes_profit() -> None:
    orders = [
        _order("buy", "10", "100", T0),
        _order("sell", "10", "110", T0 + timedelta(days=1)),
    ]
    result = compute_order_performance(orders)
    assert result.trade_count == 1
    assert result.realized_pnl == Decimal("100")
    assert result.win_rate_pct == Decimal("100")
    assert result.max_drawdown == Decimal("0")


def test_sell_matches_multiple_buy_lots_fifo() -> None:
    orders = [
        _order("buy", "5", "50", T0),
        _order("buy", "5", "60", T0 + timedelta(days=1)),
        _order("sell", "8", "70", T0 + timedelta(days=2)),
    ]
    result = compute_order_performance(orders)
    assert result.trade_count == 1
    # 5 @ (70-50) + 3 @ (70-60) = 100 + 30
    assert result.realized_pnl == Decimal("130")


def test_sell_with_no_open_lot_is_skipped() -> None:
    orders = [_order("sell", "5", "100", T0)]
    result = compute_order_performance(orders)
    assert result.trade_count == 0
    assert result.realized_pnl == Decimal("0")


def test_losing_trade_reduces_win_rate() -> None:
    orders = [
        _order("buy", "10", "100", T0),
        _order("sell", "10", "90", T0 + timedelta(days=1)),
    ]
    result = compute_order_performance(orders)
    assert result.trade_count == 1
    assert result.realized_pnl == Decimal("-100")
    assert result.win_rate_pct == Decimal("0")


def test_max_drawdown_tracks_peak_to_trough_decline() -> None:
    orders = [
        _order("buy", "10", "100", T0),
        _order("sell", "10", "110", T0 + timedelta(days=1)),  # +100
        _order("buy", "10", "100", T0 + timedelta(days=2)),
        _order("sell", "10", "85", T0 + timedelta(days=3)),  # -150
    ]
    result = compute_order_performance(orders)
    assert result.trade_count == 2
    assert result.realized_pnl == Decimal("-50")
    assert result.max_drawdown == Decimal("150")


def test_unfilled_orders_are_ignored() -> None:
    order = _order("buy", "10", None, T0, status="pending")
    result = compute_order_performance([order])
    assert result.trade_count == 0


def test_empty_order_list_has_no_trades() -> None:
    result = compute_order_performance([])
    assert result.trade_count == 0
    assert result.win_rate_pct == Decimal("0")
    assert result.equity_curve == []


def test_evaluate_performance_accepts_profitable_result() -> None:
    orders = [
        _order("buy", "10", "100", T0),
        _order("sell", "10", "110", T0 + timedelta(days=1)),
    ]
    verdict = evaluate_performance(compute_order_performance(orders))
    assert verdict.accepted is True
    assert verdict.reasons == []


def test_evaluate_performance_rejects_unprofitable_result() -> None:
    orders = [
        _order("buy", "10", "100", T0),
        _order("sell", "10", "90", T0 + timedelta(days=1)),
    ]
    verdict = evaluate_performance(compute_order_performance(orders))
    assert verdict.accepted is False
    assert "not positive" in verdict.reasons[0]


def test_evaluate_performance_rejects_no_trades_yet() -> None:
    verdict = evaluate_performance(compute_order_performance([]))
    assert verdict.accepted is False
    assert "no completed round-trip trades" in verdict.reasons[0]
