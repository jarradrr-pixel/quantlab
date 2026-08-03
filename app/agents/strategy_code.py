"""Claude-backed strategy code agent.

Proposes refined SMA-crossover windows for an already-specified strategy,
given a hypothesis. Never chooses the symbol, timeframe or strategy type --
those are fixed inputs, not the model's to pick, so a hallucinated or
adversarial response cannot select an unapproved instrument.
"""

from __future__ import annotations

import anthropic

from app.agents._common import call_claude_structured
from app.agents.base import StrategyCodeAgent, StrategySpecProposal

_SYSTEM_PROMPT = (
    "You are refining the numeric parameters of an SMA (simple moving "
    "average) crossover trading strategy to match a given hypothesis. You "
    "are given the strategy's fixed symbol, timeframe and its current "
    "fast/slow window, which you may not change. Propose fast_window, "
    "slow_window (both positive integers, slow_window strictly greater than "
    "fast_window) and minimum_out_of_sample_trades (a non-negative integer) "
    "that best implement the hypothesis, with a rationale connecting your "
    "choice to it. Keep both windows under 500 bars."
)


class ClaudeStrategyCodeAgent(StrategyCodeAgent):
    def __init__(self, api_key: str, *, client: anthropic.Anthropic | None = None) -> None:
        self._client = client or anthropic.Anthropic(api_key=api_key)

    def generate(
        self,
        hypothesis_statement: str,
        *,
        symbol: str,
        timeframe: str,
        current_fast_window: int,
        current_slow_window: int,
    ) -> StrategySpecProposal:
        user_content = (
            f"Hypothesis: {hypothesis_statement}\n\n"
            f"Strategy symbol (fixed): {symbol}\n"
            f"Strategy timeframe (fixed): {timeframe}\n"
            f"Current fast_window: {current_fast_window}\n"
            f"Current slow_window: {current_slow_window}"
        )
        return call_claude_structured(
            self._client,
            system=_SYSTEM_PROMPT,
            user_content=user_content,
            output_format=StrategySpecProposal,
        )
