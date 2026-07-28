"""Claude-backed research agent.

The only module in this codebase that calls an LLM. It has no import of
``app.db`` or SQLAlchemy and cannot write anywhere -- it returns a
``ResearchProposal`` and nothing else. See ``app.agents.base`` for the trust
boundary this exists to hold.
"""

from __future__ import annotations

import logging

import anthropic
from anthropic.types.beta import BetaContentBlock, BetaMessageParam

from app.agents.base import (
    CitationProposal,
    ClaimProposal,
    ResearchAgent,
    ResearchAgentError,
    ResearchProposal,
)

logger = logging.getLogger(__name__)

_MODEL = "claude-opus-5"
_MAX_SEARCHES = 5
_MAX_RESUMES = 3
_SYSTEM_PROMPT = (
    "You are a research assistant for a quantitative trading research tool. "
    "Research the user's question using web search and answer with "
    "well-supported, cited claims. Every factual claim must be grounded in a "
    "cited web source. Do not include claims you cannot support with a "
    "citation."
)


class ClaudeResearchAgent(ResearchAgent):
    def __init__(self, api_key: str, *, client: anthropic.Anthropic | None = None) -> None:
        self._client = client or anthropic.Anthropic(api_key=api_key)

    def research(self, question: str) -> ResearchProposal:
        messages: list[BetaMessageParam] = [{"role": "user", "content": question}]

        for _ in range(_MAX_RESUMES):
            try:
                response = self._client.beta.messages.create(
                    model=_MODEL,
                    max_tokens=4096,
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                    system=_SYSTEM_PROMPT,
                    tools=[
                        {
                            "type": "web_search_20260209",
                            "name": "web_search",
                            "max_uses": _MAX_SEARCHES,
                        }
                    ],
                    messages=messages,
                )
            except anthropic.APIError as exc:
                raise ResearchAgentError(f"Claude API request failed: {exc}") from exc

            if response.stop_reason == "refusal":
                raise ResearchAgentError("Claude declined this research request.")

            if response.stop_reason == "pause_turn":
                messages = [
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": response.content},
                ]
                continue

            return _parse_proposal(response.content)

        raise ResearchAgentError(
            "Research did not complete after multiple search rounds."
        )


def _parse_proposal(content: list[BetaContentBlock]) -> ResearchProposal:
    claims: list[ClaimProposal] = []
    uncited_segments = 0

    for block in content:
        if block.type != "text":
            continue
        citations = block.citations or []
        cited = [c for c in citations if c.type == "web_search_result_location"]
        if not cited:
            if block.text.strip():
                uncited_segments += 1
            continue
        claims.append(
            ClaimProposal(
                text=block.text,
                citations=[
                    CitationProposal(url=c.url, title=c.title or "", quoted_text=c.cited_text)
                    for c in cited
                ],
            )
        )

    if uncited_segments:
        logger.info(
            "discarded uncited research segments",
            extra={"context": {"count": uncited_segments}},
        )

    if not claims:
        raise ResearchAgentError("No cited findings were produced for this question.")

    return ResearchProposal(claims=claims, discarded_uncited_segment_count=uncited_segments)
