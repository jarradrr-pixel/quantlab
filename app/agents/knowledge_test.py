"""Claude-backed knowledge test agent.

Closed-book: judges a question against the project's own accepted research
findings only. Unlike ``ClaudeResearchAgent``, it never searches the web -- a
knowledge test checks what the project already knows, it doesn't gather more.
"""

from __future__ import annotations

import anthropic

from app.agents._common import call_claude_structured
from app.agents.base import AcceptedFinding, KnowledgeTestAgent, KnowledgeTestProposal

_SYSTEM_PROMPT = (
    "You are testing whether a specific question is supported by a fixed set "
    "of previously accepted research findings for a quantitative trading "
    "research project. You may use ONLY the findings given to you -- do not "
    "draw on outside knowledge or invent additional information. Decide "
    "whether the findings support the question (verdict 'supported'), say "
    "nothing relevant to it (verdict 'not_supported'), or directly conflict "
    "with it (verdict 'contradicted'). Cite the id of every finding your "
    "verdict relies on."
)


class ClaudeKnowledgeTestAgent(KnowledgeTestAgent):
    def __init__(self, api_key: str, *, client: anthropic.Anthropic | None = None) -> None:
        self._client = client or anthropic.Anthropic(api_key=api_key)

    def test(self, question: str, findings: list[AcceptedFinding]) -> KnowledgeTestProposal:
        findings_block = "\n".join(
            f"- id={f.id}: {f.claim} (sources: {', '.join(f.citation_urls)})"
            for f in findings
        )
        user_content = (
            f"Question to test: {question}\n\nAccepted findings:\n{findings_block}"
        )
        return call_claude_structured(
            self._client,
            system=_SYSTEM_PROMPT,
            user_content=user_content,
            output_format=KnowledgeTestProposal,
        )
