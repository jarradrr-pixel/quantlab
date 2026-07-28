"""SMA crossover backtest engine and acceptance rule.

Long-only. A bullish cross (fast SMA moves above slow SMA) opens a full
position; a bearish cross (fast moves below slow) closes it. To avoid
look-ahead bias, a cross confirmed using data through bar ``i`` executes at
bar ``i + 1``'s open, not bar ``i``'s close.

Equity is tracked as a base-100 index (100 == the starting value), so
``total_return_pct`` is simply the final index value minus 100 -- this
includes mark-to-market on a position still open at the end of the window,
not just realised (closed) trades.

Out-of-sample split is a fixed 70/30 by bar count: the last 30% of bars are
"out-of-sample"; a trade counts there if its entry falls on or after that
split date. This is what a per-strategy ``minimum_out_of_sample_trades``
gates in ``evaluate_acceptance``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.marketdata.base import Bar

IN_SAMPLE_FRACTION = 0.7


@dataclass(frozen=True)
class Trade:
    entry_date: date
    entry_price: Decimal
    exit_date: date
    exit_price: Decimal
    closed: bool
    """False for a position still open when the backtest window ended."""


@dataclass(frozen=True)
class BacktestResult:
    total_return_pct: Decimal
    benchmark_return_pct: Decimal
    max_drawdown_pct: Decimal
    trades: list[Trade]
    out_of_sample_trade_count: int
    equity_curve: list[dict[str, str]]


@dataclass(frozen=True)
class AcceptanceVerdict:
    accepted: bool
    reason: str


def _sma(values: list[Decimal], window: int) -> list[Decimal | None]:
    result: list[Decimal | None] = []
    for i in range(len(values)):
        if i + 1 < window:
            result.append(None)
        else:
            window_slice = values[i + 1 - window : i + 1]
            result.append(sum(window_slice, start=Decimal(0)) / window)
    return result


def simulate_sma_crossover(
    bars: list[Bar], fast_window: int, slow_window: int
) -> BacktestResult:
    if fast_window <= 0 or slow_window <= 0 or fast_window >= slow_window:
        raise ValueError("fast_window must be a positive integer less than slow_window")
    if len(bars) < slow_window + 2:
        raise ValueError(
            f"Not enough bars ({len(bars)}) for a {slow_window}-bar slow window plus "
            "at least one bar to trade on."
        )

    closes = [bar.close for bar in bars]
    fast = _sma(closes, fast_window)
    slow = _sma(closes, slow_window)

    trades: list[Trade] = []
    equity_curve: list[dict[str, str]] = []
    realized_equity = Decimal(100)
    in_position = False
    entry_price: Decimal | None = None
    entry_index: int | None = None

    for i, bar in enumerate(bars):
        current_equity = (
            realized_equity * (bar.close / entry_price)
            if in_position and entry_price is not None
            else realized_equity
        )
        equity_curve.append(
            {"date": bar.timestamp.date().isoformat(), "equity": str(current_equity)}
        )

        if i == 0:
            continue
        prev_fast, prev_slow, curr_fast, curr_slow = fast[i - 1], slow[i - 1], fast[i], slow[i]
        if None in (prev_fast, prev_slow, curr_fast, curr_slow):
            continue
        assert prev_fast is not None and prev_slow is not None
        assert curr_fast is not None and curr_slow is not None

        bullish_cross = prev_fast <= prev_slow and curr_fast > curr_slow
        bearish_cross = prev_fast >= prev_slow and curr_fast < curr_slow

        if not in_position and bullish_cross and i + 1 < len(bars):
            entry_index = i + 1
            entry_price = bars[i + 1].open
            in_position = True
        elif in_position and bearish_cross and i + 1 < len(bars) and entry_price is not None:
            assert entry_index is not None
            exit_index = i + 1
            exit_price = bars[exit_index].open
            realized_equity = realized_equity * (exit_price / entry_price)
            trades.append(
                Trade(
                    entry_date=bars[entry_index].timestamp.date(),
                    entry_price=entry_price,
                    exit_date=bars[exit_index].timestamp.date(),
                    exit_price=exit_price,
                    closed=True,
                )
            )
            in_position = False
            entry_price = None
            entry_index = None

    if in_position and entry_index is not None and entry_price is not None:
        trades.append(
            Trade(
                entry_date=bars[entry_index].timestamp.date(),
                entry_price=entry_price,
                exit_date=bars[-1].timestamp.date(),
                exit_price=bars[-1].close,
                closed=False,
            )
        )

    total_return_pct = Decimal(equity_curve[-1]["equity"]) - 100
    benchmark_return_pct = (bars[-1].close - bars[0].open) / bars[0].open * 100
    max_drawdown_pct = _max_drawdown([Decimal(point["equity"]) for point in equity_curve])

    split_index = int(len(bars) * IN_SAMPLE_FRACTION)
    split_date = bars[min(split_index, len(bars) - 1)].timestamp.date()
    out_of_sample_trade_count = sum(1 for t in trades if t.entry_date >= split_date)

    return BacktestResult(
        total_return_pct=total_return_pct,
        benchmark_return_pct=benchmark_return_pct,
        max_drawdown_pct=max_drawdown_pct,
        trades=trades,
        out_of_sample_trade_count=out_of_sample_trade_count,
        equity_curve=equity_curve,
    )


def _max_drawdown(equity_values: list[Decimal]) -> Decimal:
    peak = equity_values[0]
    worst = Decimal(0)
    for value in equity_values:
        peak = max(peak, value)
        drawdown = (peak - value) / peak * 100
        worst = max(worst, drawdown)
    return worst


def evaluate_acceptance(
    result: BacktestResult, minimum_out_of_sample_trades: int
) -> AcceptanceVerdict:
    """Accepted iff the out-of-sample trade count clears its per-strategy
    threshold AND the strategy beat its buy-and-hold benchmark."""
    if result.out_of_sample_trade_count < minimum_out_of_sample_trades:
        return AcceptanceVerdict(
            accepted=False,
            reason=(
                f"only {result.out_of_sample_trade_count} out-of-sample trade(s); "
                f"needs at least {minimum_out_of_sample_trades}"
            ),
        )
    if result.total_return_pct <= result.benchmark_return_pct:
        return AcceptanceVerdict(
            accepted=False,
            reason=(
                f"total return {result.total_return_pct:.2f}% did not beat the "
                f"buy-and-hold benchmark of {result.benchmark_return_pct:.2f}%"
            ),
        )
    return AcceptanceVerdict(
        accepted=True,
        reason="cleared the out-of-sample trade count and beat the buy-and-hold benchmark",
    )
