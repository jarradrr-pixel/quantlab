"""ClaudeResearchAgent unit tests against a fake Anthropic-shaped client.

No network call is ever made -- the fake client's ``.beta.messages.create``
returns canned responses built from plain ``SimpleNamespace`` objects shaped
like the real SDK's beta response types.
"""

from __future__ import annotations

import types
from typing import Any

import pytest

from app.agents.base import CitationProposal, ClaimProposal, ResearchAgentError
from app.agents.claude import ClaudeResearchAgent


def _block(type_: str, **kwargs: Any) -> Any:
    return types.SimpleNamespace(type=type_, **kwargs)


def _citation(url: str, title: str, cited_text: str) -> Any:
    return types.SimpleNamespace(
        type="web_search_result_location", url=url, title=title, cited_text=cited_text
    )


def _response(stop_reason: str, content: list[Any]) -> Any:
    return types.SimpleNamespace(stop_reason=stop_reason, content=content)


class FakeMessages:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._responses.pop(0)


class FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.beta = types.SimpleNamespace(messages=FakeMessages(responses))


def test_research_returns_cited_claims_and_drops_uncited_segments() -> None:
    response = _response(
        "end_turn",
        [
            _block(
                "text",
                text="Widgets grew 5% in Q1.",
                citations=[_citation("https://example.com/a", "Example A", "Widgets grew 5%")],
            ),
            _block("text", text="Some uncited musing.", citations=[]),
        ],
    )
    agent = ClaudeResearchAgent(api_key="fake", client=FakeClient([response]))

    proposal = agent.research("How did widgets do in Q1?")

    assert len(proposal.claims) == 1
    assert proposal.claims[0].text == "Widgets grew 5% in Q1."
    assert proposal.claims[0].citations[0].url == "https://example.com/a"
    assert proposal.discarded_uncited_segment_count == 1


def test_refusal_stop_reason_raises_before_reading_content() -> None:
    client = FakeClient([_response("refusal", [])])
    agent = ClaudeResearchAgent(api_key="fake", client=client)

    with pytest.raises(ResearchAgentError, match="declined"):
        agent.research("a disallowed question")


def test_pause_turn_resumes_with_assistant_content_then_succeeds() -> None:
    paused = _response("pause_turn", [_block("text", text="partial", citations=[])])
    final = _response(
        "end_turn",
        [
            _block(
                "text",
                text="Final cited answer.",
                citations=[_citation("https://example.com/b", "B", "cited")],
            )
        ],
    )
    client = FakeClient([paused, final])
    agent = ClaudeResearchAgent(api_key="fake", client=client)

    proposal = agent.research("q")

    assert len(proposal.claims) == 1
    assert len(client.beta.messages.calls) == 2
    second_call_messages = client.beta.messages.calls[1]["messages"]
    assert second_call_messages[1]["role"] == "assistant"


def test_all_uncited_segments_raises_instead_of_empty_proposal() -> None:
    client = FakeClient(
        [_response("end_turn", [_block("text", text="no cites", citations=[])])]
    )
    agent = ClaudeResearchAgent(api_key="fake", client=client)

    with pytest.raises(ResearchAgentError, match="No cited findings"):
        agent.research("q")


def test_claim_proposal_rejects_zero_citations() -> None:
    with pytest.raises(ValueError, match="at least one citation"):
        ClaimProposal(text="an assertion", citations=[])


def test_claim_proposal_accepts_at_least_one_citation() -> None:
    claim = ClaimProposal(
        text="an assertion",
        citations=[CitationProposal(url="https://example.com", title="T", quoted_text="q")],
    )
    assert len(claim.citations) == 1
