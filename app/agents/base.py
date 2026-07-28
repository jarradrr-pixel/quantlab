"""Research agent contract.

Per docs/architecture.md's trust table, an LLM agent may only emit validated
proposals -- never write to the database, never hold broker/session
credentials, never approve anything. ``ResearchProposal`` is that proposal:
a plain Pydantic object with no route, no session, no way to persist itself.
The route layer (ordinary, reviewed Python) is what turns it into
``ResearchFinding``/``Citation`` rows, and every row it creates starts
``status="pending"`` -- nothing trusts a finding until a human operator
explicitly accepts it.

``ClaimProposal``'s own validator refuses to construct a claim with no
citations, so an uncited assertion cannot reach a ``ResearchProposal`` even
if the parsing code that builds one has a bug.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, field_validator


class CitationProposal(BaseModel):
    url: str
    title: str
    quoted_text: str


class ClaimProposal(BaseModel):
    text: str
    citations: list[CitationProposal]

    @field_validator("citations")
    @classmethod
    def _at_least_one_citation(cls, value: list[CitationProposal]) -> list[CitationProposal]:
        if not value:
            raise ValueError("every claim must have at least one citation")
        return value


class ResearchProposal(BaseModel):
    claims: list[ClaimProposal]
    discarded_uncited_segment_count: int = 0
    """Text segments the model produced with no supporting citation -- dropped
    rather than persisted as a claim, but counted here so the route layer can
    audit that they existed instead of the discard being silent."""


class ResearchAgentError(Exception):
    """Raised when a research agent cannot produce a proposal."""


class ResearchAgent(ABC):
    @abstractmethod
    def research(self, question: str) -> ResearchProposal: ...
