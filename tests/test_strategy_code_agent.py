"""ClaudeStrategyCodeAgent unit tests against a fake Anthropic-shaped client.

No network call is ever made -- see test_knowledge_test_agent.py for the same
fake-client pattern applied to a sibling structured-output agent.
"""

from __future__ import annotations

import types
from typing import Any

import pydantic
import pytest

from app.agents.base import AgentError, StrategySpecProposal
from app.agents.strategy_code import ClaudeStrategyCodeAgent


def _block(type_: str, **kwargs: Any) -> Any:
    return types.SimpleNamespace(type=type_, **kwargs)


def _response(stop_reason: str, content: list[Any]) -> Any:
    return types.SimpleNamespace(stop_reason=stop_reason, content=content)


class FakeMessages:
    def __init__(self, response: Any = None, *, raises: Exception | None = None) -> None:
        self._response = response
        self._raises = raises
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return self._response


class FakeClient:
    def __init__(self, response: Any = None, *, raises: Exception | None = None) -> None:
        self.messages = FakeMessages(response, raises=raises)


def test_generate_returns_parsed_spec() -> None:
    proposal = StrategySpecProposal(
        fast_window=15, slow_window=60, minimum_out_of_sample_trades=5, rationale="fits window"
    )
    response = _response("end_turn", [_block("text", parsed_output=proposal)])
    client = FakeClient(response)
    agent = ClaudeStrategyCodeAgent(api_key="fake", client=client)

    result = agent.generate(
        "Buy SPY in Q1",
        symbol="SPY",
        timeframe="1Day",
        current_fast_window=20,
        current_slow_window=100,
    )

    assert result is proposal
    assert client.messages.calls[0]["output_format"] is StrategySpecProposal


def test_refusal_stop_reason_raises() -> None:
    agent = ClaudeStrategyCodeAgent(
        api_key="fake", client=FakeClient(_response("refusal", []))
    )
    with pytest.raises(AgentError, match="declined"):
        agent.generate(
            "h",
            symbol="SPY",
            timeframe="1Day",
            current_fast_window=20,
            current_slow_window=100,
        )


def test_no_parseable_output_raises() -> None:
    response = _response("end_turn", [_block("text", parsed_output=None)])
    agent = ClaudeStrategyCodeAgent(api_key="fake", client=FakeClient(response))
    with pytest.raises(AgentError, match="no parseable output"):
        agent.generate(
            "h",
            symbol="SPY",
            timeframe="1Day",
            current_fast_window=20,
            current_slow_window=100,
        )


def test_malformed_response_raises_agent_error() -> None:
    validation_error = pydantic.ValidationError.from_exception_data("StrategySpecProposal", [])
    agent = ClaudeStrategyCodeAgent(api_key="fake", client=FakeClient(raises=validation_error))
    with pytest.raises(AgentError, match="did not match the expected schema"):
        agent.generate(
            "h",
            symbol="SPY",
            timeframe="1Day",
            current_fast_window=20,
            current_slow_window=100,
        )


def test_proposal_rejects_slow_window_not_greater_than_fast() -> None:
    with pytest.raises(ValueError, match="slow_window must be greater than fast_window"):
        StrategySpecProposal(
            fast_window=50, slow_window=10, minimum_out_of_sample_trades=5, rationale="bad"
        )


def test_proposal_rejects_non_positive_fast_window() -> None:
    with pytest.raises(ValueError, match="fast_window must be positive"):
        StrategySpecProposal(
            fast_window=0, slow_window=10, minimum_out_of_sample_trades=5, rationale="bad"
        )
