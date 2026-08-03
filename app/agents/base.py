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
from typing import Literal

from pydantic import BaseModel, ValidationInfo, field_validator


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


class AgentError(Exception):
    """Raised when a Phase 5 pipeline agent cannot produce a proposal."""


class AcceptedFinding(BaseModel):
    """Plain data the route layer builds from accepted ``ResearchFinding``
    rows -- agents never query the database themselves."""

    id: str
    claim: str
    citation_urls: list[str]


class KnowledgeTestSummary(BaseModel):
    """Plain data the route layer builds from a project's past ``KnowledgeTest``
    rows, for the hypothesis agent's context."""

    question: str
    verdict: str
    reasoning: str


class KnowledgeTestProposal(BaseModel):
    verdict: Literal["supported", "not_supported", "contradicted"]
    reasoning: str
    cited_finding_ids: list[str]

    @field_validator("cited_finding_ids")
    @classmethod
    def _at_least_one_citation(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("a knowledge test verdict must cite at least one finding")
        return value


class HypothesisProposal(BaseModel):
    statement: str
    rationale: str
    cited_finding_ids: list[str]

    @field_validator("cited_finding_ids")
    @classmethod
    def _at_least_one_citation(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("a hypothesis must cite at least one finding")
        return value


class StrategySpecProposal(BaseModel):
    """Deliberately narrow: no ``strategy_type``/``symbol``/``timeframe`` field.
    Those are inherited verbatim from the base strategy the route passes in
    as fixed context -- the agent only refines the numeric windows, which
    keeps this proposal structurally incapable of picking an unapproved
    instrument no matter what the model outputs.
    """

    fast_window: int
    slow_window: int
    minimum_out_of_sample_trades: int
    rationale: str

    @field_validator("slow_window")
    @classmethod
    def _slow_beats_fast(cls, value: int, info: ValidationInfo) -> int:
        fast_window = info.data.get("fast_window")
        if fast_window is not None and fast_window <= 0:
            raise ValueError("fast_window must be positive")
        if value <= 0:
            raise ValueError("slow_window must be positive")
        if fast_window is not None and value <= fast_window:
            raise ValueError("slow_window must be greater than fast_window")
        return value


class KnowledgeTestAgent(ABC):
    @abstractmethod
    def test(
        self, question: str, findings: list[AcceptedFinding]
    ) -> KnowledgeTestProposal: ...


class HypothesisAgent(ABC):
    @abstractmethod
    def propose(
        self,
        objective: str,
        findings: list[AcceptedFinding],
        prior_tests: list[KnowledgeTestSummary],
    ) -> HypothesisProposal: ...


class StrategyCodeAgent(ABC):
    @abstractmethod
    def generate(
        self,
        hypothesis_statement: str,
        *,
        symbol: str,
        timeframe: str,
        current_fast_window: int,
        current_slow_window: int,
    ) -> StrategySpecProposal: ...
