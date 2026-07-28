"""SMA crossover engine and acceptance-rule tests against synthetic bars.
No network, no database -- pure functions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.backtest import evaluate_acceptance, simulate_sma_crossover
from app.marketdata.base import Bar


def _bars(prices: list[int]) -> list[Bar]:
    start = datetime(2020, 1, 1, tzinfo=UTC)
    return [
        Bar(
            timestamp=start + timedelta(days=i),
            open=Decimal(p),
            high=Decimal(p + 1),
            low=Decimal(p - 1),
            close=Decimal(p),
            volume=1000,
        )
        for i, p in enumerate(prices)
    ]


def _uptrend_then_downtrend() -> list[Bar]:
    # Flat run-up so both SMAs settle equal, then a clean bullish then
    # bearish cross once both windows are computable.
    prices = (
        [100] * 25
        + [100 + 2 * i for i in range(1, 31)]
        + [162 - 2 * i for i in range(1, 31)]
    )
    return _bars(prices)


def test_detects_a_clean_round_trip_trade() -> None:
    result = simulate_sma_crossover(_uptrend_then_downtrend(), fast_window=5, slow_window=20)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.closed is True
    assert trade.exit_price > trade.entry_price


def test_total_return_beats_benchmark_on_a_clean_uptrend() -> None:
    result = simulate_sma_crossover(_uptrend_then_downtrend(), fast_window=5, slow_window=20)
    assert result.total_return_pct > result.benchmark_return_pct


def test_flat_prices_produce_no_trades() -> None:
    result = simulate_sma_crossover(_bars([100] * 40), fast_window=5, slow_window=20)
    assert result.trades == []
    assert result.total_return_pct == 0


def test_rejects_fast_window_not_less_than_slow_window() -> None:
    with pytest.raises(ValueError, match="fast_window"):
        simulate_sma_crossover(_bars([100] * 40), fast_window=20, slow_window=20)


def test_rejects_too_few_bars_for_the_slow_window() -> None:
    with pytest.raises(ValueError, match="Not enough bars"):
        simulate_sma_crossover(_bars([100] * 8), fast_window=2, slow_window=8)


def test_an_open_position_at_the_end_is_still_counted_as_a_trade() -> None:
    # Flat then a rise that never reverses -- position stays open at the end.
    prices = [100] * 25 + [100 + 2 * i for i in range(1, 31)]
    result = simulate_sma_crossover(_bars(prices), fast_window=5, slow_window=20)
    assert len(result.trades) == 1
    assert result.trades[0].closed is False


def test_acceptance_requires_the_out_of_sample_trade_threshold() -> None:
    result = simulate_sma_crossover(_uptrend_then_downtrend(), fast_window=5, slow_window=20)
    verdict = evaluate_acceptance(result, minimum_out_of_sample_trades=100)
    assert verdict.accepted is False
    assert "out-of-sample" in verdict.reason


def test_acceptance_requires_beating_the_benchmark() -> None:
    # A trade count met but a benchmark that isn't beaten, e.g. flat data.
    result = simulate_sma_crossover(_bars([100] * 40), fast_window=5, slow_window=20)
    verdict = evaluate_acceptance(result, minimum_out_of_sample_trades=0)
    assert verdict.accepted is False
    assert "benchmark" in verdict.reason


def test_acceptance_passes_when_both_conditions_are_met() -> None:
    result = simulate_sma_crossover(_uptrend_then_downtrend(), fast_window=5, slow_window=20)
    verdict = evaluate_acceptance(result, minimum_out_of_sample_trades=0)
    assert verdict.accepted is True
