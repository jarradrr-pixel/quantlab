"""Research agent routes: run a research question, review its findings.

The route layer is the only thing that ever writes a ``ResearchFinding`` or
``Citation`` row -- ``app.agents.claude`` returns a ``ResearchProposal`` and
holds no database session. Every finding is inserted with ``status="pending"``
and stays untrusted by everything else in the system until an operator
explicitly accepts or rejects it here.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Response, status
from fastapi.responses import RedirectResponse

from app.agents.base import ResearchAgentError
from app.core.audit import AuditCategory, AuditLog
from app.db.base import utcnow
from app.db.models import Citation, Project, ResearchFinding
from app.deps import DbDep, OperatorDep, ResearchAgentDep, require_csrf

router = APIRouter(tags=["research"])


def _project_or_404(db: DbDep, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project


@router.post("/projects/{project_id}/research")
def run_research(
    project_id: str,
    db: DbDep,
    operator: OperatorDep,
    research_agent: ResearchAgentDep,
    question: Annotated[str, Form()],
    _: Annotated[None, Depends(require_csrf)],
) -> Response:
    project = _project_or_404(db, project_id)
    if research_agent is None:
        raise HTTPException(
            status_code=409, detail="Research agent is not configured (no Anthropic API key)."
        )

    question = question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="A research question is required.")

    try:
        proposal = research_agent.research(question)
    except ResearchAgentError as exc:
        AuditLog(db).record(
            category=AuditCategory.RESEARCH,
            action="research_failed",
            actor=operator.email,
            project_id=project.id,
            payload={"question": question, "error": str(exc)},
        )
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    citation_count = 0
    for claim in proposal.claims:
        finding = ResearchFinding(
            project_id=project.id,
            question=question,
            claim=claim.text,
            created_by="agent:claude-opus-5",
        )
        db.add(finding)
        db.flush()
        for citation in claim.citations:
            db.add(
                Citation(
                    finding_id=finding.id,
                    url=citation.url,
                    title=citation.title,
                    quoted_text=citation.quoted_text,
                )
            )
            citation_count += 1

    AuditLog(db).record(
        category=AuditCategory.RESEARCH,
        action="research_completed",
        actor=operator.email,
        project_id=project.id,
        payload={
            "question": question,
            "claim_count": len(proposal.claims),
            "citation_count": citation_count,
            "discarded_uncited_segment_count": proposal.discarded_uncited_segment_count,
        },
    )
    db.commit()
    return RedirectResponse(f"/projects/{project_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/projects/{project_id}/findings/{finding_id}/review")
def review_finding(
    project_id: str,
    finding_id: str,
    db: DbDep,
    operator: OperatorDep,
    decision: Annotated[str, Form()],
    notes: Annotated[str, Form()],
    _: Annotated[None, Depends(require_csrf)],
) -> Response:
    _project_or_404(db, project_id)
    if decision not in ("accepted", "rejected"):
        raise HTTPException(
            status_code=400, detail="decision must be 'accepted' or 'rejected'."
        )

    finding = db.get(ResearchFinding, finding_id)
    if finding is None or finding.project_id != project_id:
        raise HTTPException(status_code=404, detail="Finding not found.")

    finding.status = decision
    finding.reviewed_by = operator.email
    finding.reviewed_at = utcnow()
    db.flush()
    AuditLog(db).record(
        category=AuditCategory.RESEARCH,
        action="finding_reviewed",
        actor=operator.email,
        project_id=project_id,
        payload={
            "finding_id": finding_id,
            "decision": decision,
            "notes": notes.strip() or None,
        },
    )
    db.commit()
    return RedirectResponse(f"/projects/{project_id}", status_code=status.HTTP_303_SEE_OTHER)
