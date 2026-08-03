"""Claude-backed hypothesis agent.

Closed-book like the knowledge test agent: synthesizes a project's research
objective, its accepted findings and any prior knowledge-test verdicts into
one specific, testable trading hypothesis. Never searches the web.
"""

from __future__ import annotations

import anthropic

from app.agents._common import call_claude_structured
from app.agents.base import (
    AcceptedFinding,
    HypothesisAgent,
    HypothesisProposal,
    KnowledgeTestSummary,
)

_SYSTEM_PROMPT = (
    "You are proposing one specific, testable trading hypothesis for a "
    "quantitative research project, grounded ONLY in the research objective, "
    "accepted findings and knowledge-test results given to you -- do not draw "
    "on outside knowledge. State a hypothesis concrete enough to be turned "
    "into a mechanical trading rule (e.g. naming a signal, an indicator "
    "relationship, or a market condition), explain your rationale, and cite "
    "the id of every finding it relies on."
)


class ClaudeHypothesisAgent(HypothesisAgent):
    def __init__(self, api_key: str, *, client: anthropic.Anthropic | None = None) -> None:
        self._client = client or anthropic.Anthropic(api_key=api_key)

    def propose(
        self,
        objective: str,
        findings: list[AcceptedFinding],
        prior_tests: list[KnowledgeTestSummary],
    ) -> HypothesisProposal:
        findings_block = "\n".join(
            f"- id={f.id}: {f.claim} (sources: {', '.join(f.citation_urls)})"
            for f in findings
        )
        tests_block = (
            "\n".join(
                f"- Q: {t.question}\n  Verdict: {t.verdict}\n  Reasoning: {t.reasoning}"
                for t in prior_tests
            )
            or "(none yet)"
        )
        user_content = (
            f"Research objective: {objective}\n\n"
            f"Accepted findings:\n{findings_block}\n\n"
            f"Knowledge test results:\n{tests_block}"
        )
        return call_claude_structured(
            self._client,
            system=_SYSTEM_PROMPT,
            user_content=user_content,
            output_format=HypothesisProposal,
        )
