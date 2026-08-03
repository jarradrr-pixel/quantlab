"""ClaudeKnowledgeTestAgent unit tests against a fake Anthropic-shaped client.

No network call is ever made -- the fake client's ``.messages.parse`` returns
canned responses built from plain ``SimpleNamespace`` objects shaped like the
real SDK's ``ParsedMessage``/``ParsedTextBlock`` types.
"""

from __future__ import annotations

import types
from typing import Any

import pydantic
import pytest

from app.agents.base import AcceptedFinding, AgentError, KnowledgeTestProposal
from app.agents.knowledge_test import ClaudeKnowledgeTestAgent


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


def test_test_returns_parsed_verdict() -> None:
    proposal = KnowledgeTestProposal(
        verdict="supported", reasoning="matches finding f1", cited_finding_ids=["f1"]
    )
    response = _response("end_turn", [_block("text", parsed_output=proposal)])
    client = FakeClient(response)
    agent = ClaudeKnowledgeTestAgent(api_key="fake", client=client)

    result = agent.test("Did SPY rise?", FINDINGS)

    assert result is proposal
    assert client.messages.calls[0]["output_format"] is KnowledgeTestProposal


def test_refusal_stop_reason_raises() -> None:
    agent = ClaudeKnowledgeTestAgent(
        api_key="fake", client=FakeClient(_response("refusal", []))
    )
    with pytest.raises(AgentError, match="declined"):
        agent.test("q", FINDINGS)


def test_no_parseable_output_raises() -> None:
    response = _response("end_turn", [_block("text", parsed_output=None)])
    agent = ClaudeKnowledgeTestAgent(api_key="fake", client=FakeClient(response))
    with pytest.raises(AgentError, match="no parseable output"):
        agent.test("q", FINDINGS)


def test_malformed_response_raises_agent_error() -> None:
    validation_error = pydantic.ValidationError.from_exception_data(
        "KnowledgeTestProposal", []
    )
    agent = ClaudeKnowledgeTestAgent(
        api_key="fake", client=FakeClient(raises=validation_error)
    )
    with pytest.raises(AgentError, match="did not match the expected schema"):
        agent.test("q", FINDINGS)


def test_proposal_rejects_zero_citations() -> None:
    with pytest.raises(ValueError, match="at least one finding"):
        KnowledgeTestProposal(verdict="supported", reasoning="r", cited_finding_ids=[])
