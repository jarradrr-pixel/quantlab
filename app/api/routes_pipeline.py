"""Knowledge test, hypothesis and strategy-code-generation routes (Phase 5).

Same trust boundary as ``app.api.routes_research``: each agent
(``KnowledgeTestAgent``, ``HypothesisAgent``, ``StrategyCodeAgent``) returns a
validated proposal and holds no database session; the route layer is what
persists rows. ``KnowledgeTest``, ``Hypothesis`` and ``GeneratedStrategyCode``
are engine-run records like ``Backtest``/``RiskAssessment`` -- no separate
accept/reject workflow -- and ``/validate-code`` only ever creates a new
``Strategy`` version when the deterministic validator in ``app.core.codegen``
passes, never on the agent's say-so alone.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.agents.base import (
    AcceptedFinding,
    AgentError,
    KnowledgeTestSummary,
    StrategySpecProposal,
)
from app.core.audit import AuditCategory, AuditLog
from app.core.codegen import validate_strategy_spec
from app.db.models import (
    GeneratedStrategyCode,
    Hypothesis,
    KnowledgeTest,
    Project,
    ResearchFinding,
    Strategy,
)
from app.deps import (
    DbDep,
    HypothesisAgentDep,
    KnowledgeTestAgentDep,
    OperatorDep,
    StrategyCodeAgentDep,
    require_csrf,
)

router = APIRouter(tags=["pipeline"])


def _project_or_404(db: DbDep, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project


def _latest_strategy(db: DbDep, project_id: str) -> Strategy | None:
    return db.scalars(
        select(Strategy)
        .where(Strategy.project_id == project_id)
        .order_by(Strategy.version.desc())
    ).first()


def _latest_hypothesis(db: DbDep, project_id: str) -> Hypothesis | None:
    return db.scalars(
        select(Hypothesis)
        .where(Hypothesis.project_id == project_id)
        .order_by(Hypothesis.created_at.desc())
    ).first()


def _latest_generated_code(db: DbDep, project_id: str) -> GeneratedStrategyCode | None:
    return db.scalars(
        select(GeneratedStrategyCode)
        .where(GeneratedStrategyCode.project_id == project_id)
        .order_by(GeneratedStrategyCode.created_at.desc())
    ).first()


def _accepted_findings(db: DbDep, project_id: str) -> list[AcceptedFinding]:
    findings = db.scalars(
        select(ResearchFinding).where(
            ResearchFinding.project_id == project_id,
            ResearchFinding.status == "accepted",
        )
    )
    return [
        AcceptedFinding(
            id=f.id,
            claim=f.claim,
            citation_urls=[c.url for c in f.citations],
        )
        for f in findings
    ]


@router.post("/projects/{project_id}/knowledge-tests")
def run_knowledge_test(
    project_id: str,
    db: DbDep,
    operator: OperatorDep,
    knowledge_test_agent: KnowledgeTestAgentDep,
    question: Annotated[str, Form()],
    _: Annotated[None, Depends(require_csrf)],
) -> Response:
    project = _project_or_404(db, project_id)
    if knowledge_test_agent is None:
        raise HTTPException(
            status_code=409, detail="Research agent is not configured (no Anthropic API key)."
        )

    question = question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="A test question is required.")

    findings = _accepted_findings(db, project_id)
    if not findings:
        raise HTTPException(
            status_code=400, detail="Accept at least one research finding first."
        )

    try:
        proposal = knowledge_test_agent.test(question, findings)
    except AgentError as exc:
        AuditLog(db).record(
            category=AuditCategory.RESEARCH,
            action="knowledge_test_failed",
            actor=operator.email,
            project_id=project.id,
            payload={"question": question, "error": str(exc)},
        )
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    test = KnowledgeTest(
        project_id=project.id,
        question=question,
        verdict=proposal.verdict,
        reasoning=proposal.reasoning,
        cited_finding_ids=proposal.cited_finding_ids,
        created_by="agent:claude-opus-5",
    )
    db.add(test)
    db.flush()
    AuditLog(db).record(
        category=AuditCategory.RESEARCH,
        action="knowledge_test_completed",
        actor=operator.email,
        project_id=project.id,
        payload={"question": question, "verdict": proposal.verdict},
    )
    db.commit()
    return RedirectResponse(f"/projects/{project_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/projects/{project_id}/hypotheses")
def run_hypothesis(
    project_id: str,
    db: DbDep,
    operator: OperatorDep,
    hypothesis_agent: HypothesisAgentDep,
    _: Annotated[None, Depends(require_csrf)],
) -> Response:
    project = _project_or_404(db, project_id)
    if hypothesis_agent is None:
        raise HTTPException(
            status_code=409, detail="Research agent is not configured (no Anthropic API key)."
        )

    findings = _accepted_findings(db, project_id)
    if not findings:
        raise HTTPException(
            status_code=400, detail="Accept at least one research finding first."
        )

    prior_tests = [
        KnowledgeTestSummary(question=t.question, verdict=t.verdict, reasoning=t.reasoning)
        for t in db.scalars(
            select(KnowledgeTest).where(KnowledgeTest.project_id == project_id)
        )
    ]

    try:
        proposal = hypothesis_agent.propose(project.research_objective, findings, prior_tests)
    except AgentError as exc:
        AuditLog(db).record(
            category=AuditCategory.RESEARCH,
            action="hypothesis_failed",
            actor=operator.email,
            project_id=project.id,
            payload={"error": str(exc)},
        )
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    hypothesis = Hypothesis(
        project_id=project.id,
        statement=proposal.statement,
        rationale=proposal.rationale,
        cited_finding_ids=proposal.cited_finding_ids,
        created_by="agent:claude-opus-5",
    )
    db.add(hypothesis)
    db.flush()
    AuditLog(db).record(
        category=AuditCategory.RESEARCH,
        action="hypothesis_proposed",
        actor=operator.email,
        project_id=project.id,
        payload={"statement": proposal.statement},
    )
    db.commit()
    return RedirectResponse(f"/projects/{project_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/projects/{project_id}/generate-code")
def generate_code(
    project_id: str,
    db: DbDep,
    operator: OperatorDep,
    strategy_code_agent: StrategyCodeAgentDep,
    _: Annotated[None, Depends(require_csrf)],
) -> Response:
    project = _project_or_404(db, project_id)
    if strategy_code_agent is None:
        raise HTTPException(
            status_code=409, detail="Research agent is not configured (no Anthropic API key)."
        )

    strategy = _latest_strategy(db, project_id)
    if strategy is None:
        raise HTTPException(status_code=400, detail="Specify a strategy first.")
    hypothesis = _latest_hypothesis(db, project_id)
    if hypothesis is None:
        raise HTTPException(status_code=400, detail="Develop a hypothesis first.")

    try:
        proposal = strategy_code_agent.generate(
            hypothesis.statement,
            symbol=strategy.symbol,
            timeframe=strategy.timeframe,
            current_fast_window=strategy.fast_window,
            current_slow_window=strategy.slow_window,
        )
    except AgentError as exc:
        AuditLog(db).record(
            category=AuditCategory.CODE,
            action="code_generation_failed",
            actor=operator.email,
            project_id=project.id,
            payload={"error": str(exc)},
        )
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    generated = GeneratedStrategyCode(
        project_id=project.id,
        base_strategy_id=strategy.id,
        fast_window=proposal.fast_window,
        slow_window=proposal.slow_window,
        minimum_out_of_sample_trades=proposal.minimum_out_of_sample_trades,
        rationale=proposal.rationale,
        created_by="agent:claude-opus-5",
    )
    db.add(generated)
    db.flush()
    AuditLog(db).record(
        category=AuditCategory.CODE,
        action="code_generated",
        actor=operator.email,
        project_id=project.id,
        payload={
            "fast_window": proposal.fast_window,
            "slow_window": proposal.slow_window,
        },
    )
    db.commit()
    return RedirectResponse(f"/projects/{project_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/projects/{project_id}/validate-code")
def validate_code(
    project_id: str,
    db: DbDep,
    operator: OperatorDep,
    _: Annotated[None, Depends(require_csrf)],
) -> Response:
    project = _project_or_404(db, project_id)
    generated = _latest_generated_code(db, project_id)
    if generated is None:
        raise HTTPException(status_code=400, detail="Generate code first.")

    spec = StrategySpecProposal(
        fast_window=generated.fast_window,
        slow_window=generated.slow_window,
        minimum_out_of_sample_trades=generated.minimum_out_of_sample_trades,
        rationale=generated.rationale,
    )
    verdict = validate_strategy_spec(spec)
    generated.validated = verdict.valid
    generated.validation_reasons = verdict.reasons

    produced_version = None
    if verdict.valid:
        base = generated.base_strategy
        new_strategy = Strategy(
            project_id=project.id,
            version=base.version + 1,
            name=base.name,
            symbol=base.symbol,
            timeframe=base.timeframe,
            fast_window=generated.fast_window,
            slow_window=generated.slow_window,
            minimum_out_of_sample_trades=generated.minimum_out_of_sample_trades,
            created_by="agent:claude-opus-5",
        )
        db.add(new_strategy)
        db.flush()
        generated.produced_strategy_id = new_strategy.id
        produced_version = new_strategy.version

    db.flush()
    AuditLog(db).record(
        category=AuditCategory.CODE,
        action="code_validated",
        actor=operator.email,
        project_id=project.id,
        payload={
            "valid": verdict.valid,
            "reasons": verdict.reasons,
            "produced_strategy_version": produced_version,
        },
    )
    db.commit()
    return RedirectResponse(f"/projects/{project_id}", status_code=status.HTTP_303_SEE_OTHER)
