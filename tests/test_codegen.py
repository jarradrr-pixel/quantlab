"""app.core.codegen.validate_strategy_spec unit tests. Pure function, no
agent, no database, no network.
"""

from __future__ import annotations

from app.agents.base import StrategySpecProposal
from app.core.codegen import MAX_WINDOW_BARS, validate_strategy_spec


def _spec(**overrides: object) -> StrategySpecProposal:
    defaults: dict[str, object] = {
        "fast_window": 20,
        "slow_window": 100,
        "minimum_out_of_sample_trades": 5,
        "rationale": "r",
    }
    defaults.update(overrides)
    return StrategySpecProposal(**defaults)  # type: ignore[arg-type]


def test_valid_spec_passes() -> None:
    verdict = validate_strategy_spec(_spec())
    assert verdict.valid
    assert verdict.reasons == []


def test_slow_window_over_max_is_rejected() -> None:
    verdict = validate_strategy_spec(_spec(fast_window=20, slow_window=MAX_WINDOW_BARS + 1))
    assert not verdict.valid
    assert "slow_window exceeds the maximum" in verdict.reasons[0]


def test_slow_window_at_max_is_accepted() -> None:
    verdict = validate_strategy_spec(_spec(fast_window=20, slow_window=MAX_WINDOW_BARS))
    assert verdict.valid


def test_negative_minimum_out_of_sample_trades_is_rejected() -> None:
    verdict = validate_strategy_spec(_spec(minimum_out_of_sample_trades=-1))
    assert not verdict.valid
    assert "cannot be negative" in verdict.reasons[0]


def test_multiple_failures_are_all_reported() -> None:
    verdict = validate_strategy_spec(
        _spec(
            fast_window=20,
            slow_window=MAX_WINDOW_BARS + 100,
            minimum_out_of_sample_trades=-5,
        )
    )
    assert not verdict.valid
    assert len(verdict.reasons) == 2
