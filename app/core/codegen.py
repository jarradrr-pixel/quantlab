"""Deterministic strategy-code validation. Pure functions -- no network, no
database, no agent call.

Per docs/architecture.md, ``CODE_VALIDATION`` gates a ``StrategySpecProposal``
before it can produce a new ``Strategy`` version. ``StrategySpecProposal``'s
own validator already makes ``fast_window <= 0``, ``slow_window <= 0`` and
``slow_window <= fast_window`` impossible to construct (see
``app.agents.base``), so this function does not re-check them -- only the
constraints that Pydantic construction does *not* already guarantee: an
upper bound on window size, and a non-negative trade-count threshold.
Symbol/timeframe/allowlist checks are not this function's job either --
``StrategySpecProposal`` never carries a symbol or timeframe, so there is
nothing for the model to have gotten wrong there;
``app.core.risk.assess_strategy`` still checks the allowlists later, at
``RISK_REVIEW``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.base import StrategySpecProposal

MAX_WINDOW_BARS = 500


@dataclass(frozen=True)
class CodeValidationVerdict:
    valid: bool
    reasons: list[str] = field(default_factory=list)


def validate_strategy_spec(spec: StrategySpecProposal) -> CodeValidationVerdict:
    reasons: list[str] = []

    if spec.slow_window > MAX_WINDOW_BARS:
        reasons.append(f"slow_window exceeds the maximum of {MAX_WINDOW_BARS} bars")
    if spec.minimum_out_of_sample_trades < 0:
        reasons.append("minimum_out_of_sample_trades cannot be negative")

    return CodeValidationVerdict(valid=not reasons, reasons=reasons)
