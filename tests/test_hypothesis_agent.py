"""ClaudeHypothesisAgent unit tests against a fake Anthropic-shaped client.

No network call is ever made -- see test_knowledge_test_agent.py for the same
fake-client pattern applied to a sibling structured-output agent.
"""

from __future__ import annotations

import types
from typing import Any

import pydantic
import pytest

from app.agents.base import (
    AcceptedFinding,
    AgentError,
    HypothesisProposal,
    KnowledgeTestSummary,
)
from app.agents.hypothesis import ClaudeHypothesisAgent


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


FINDINGS = [AcceptedFinding(id="f1", claim="SPY rose in Q1", citation_urls=["https://a.example"])]
PRIOR_TESTS = [KnowledgeTestSummary(question="q", verdict="supported", reasoning="r")]


def test_propose_returns_parsed_hypothesis() -> None:
    proposal = HypothesisProposal(
        statement="Buy SPY in Q1", rationale="seasonal effect", cited_finding_ids=["f1"]
    )
    response = _response("end_turn", [_block("text", parsed_output=proposal)])
    client = FakeClient(response)
    agent = ClaudeHypothesisAgent(api_key="fake", client=client)

    result = agent.propose("Find seasonal patterns", FINDINGS, PRIOR_TESTS)

    assert result is proposal
    assert client.messages.calls[0]["output_format"] is HypothesisProposal


def test_refusal_stop_reason_raises() -> None:
    agent = ClaudeHypothesisAgent(
        api_key="fake", client=FakeClient(_response("refusal", []))
    )
    with pytest.raises(AgentError, match="declined"):
        agent.propose("obj", FINDINGS, PRIOR_TESTS)


def test_no_parseable_output_raises() -> None:
    response = _response("end_turn", [_block("text", parsed_output=None)])
    agent = ClaudeHypothesisAgent(api_key="fake", client=FakeClient(response))
    with pytest.raises(AgentError, match="no parseable output"):
        agent.propose("obj", FINDINGS, PRIOR_TESTS)


def test_malformed_response_raises_agent_error() -> None:
    validation_error = pydantic.ValidationError.from_exception_data("HypothesisProposal", [])
    agent = ClaudeHypothesisAgent(api_key="fake", client=FakeClient(raises=validation_error))
    with pytest.raises(AgentError, match="did not match the expected schema"):
        agent.propose("obj", FINDINGS, PRIOR_TESTS)


def test_proposal_rejects_zero_citations() -> None:
    with pytest.raises(ValueError, match="at least one finding"):
        HypothesisProposal(statement="s", rationale="r", cited_finding_ids=[])
