"""Research route tests via a FakeResearchAgent double + dependency_overrides.

No test here contacts the real Anthropic API.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.agents.base import (
    CitationProposal,
    ClaimProposal,
    ResearchAgent,
    ResearchAgentError,
    ResearchProposal,
)
from app.deps import get_research_agent
from tests.conftest import csrf_from

QUESTION = "How did SPY perform in Q1 2024?"


class FakeResearchAgent(ResearchAgent):
    def __init__(
        self, proposal: ResearchProposal | None = None, error: str | None = None
    ) -> None:
        self._proposal = proposal
        self._error = error

    def research(self, question: str) -> ResearchProposal:
        if self._error is not None:
            raise ResearchAgentError(self._error)
        assert self._proposal is not None
        return self._proposal


def _cited_proposal() -> ResearchProposal:
    return ResearchProposal(
        claims=[
            ClaimProposal(
                text="SPY rose in Q1 2024.",
                citations=[
                    CitationProposal(
                        url="https://example.com/spy-q1",
                        title="SPY Q1 2024 recap",
                        quoted_text="SPY rose in the first quarter of 2024",
                    )
                ],
            )
        ],
        discarded_uncited_segment_count=2,
    )


def _create_project(client: TestClient) -> str:
    page = client.get("/")
    token = csrf_from(page.text)
    response = client.post(
        "/projects",
        data={
            "name": "Research project",
            "research_objective": "Test the research routes.",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    return response.headers["location"].removeprefix("/projects/")


def _csrf_for(client: TestClient, project_id: str) -> str:
    return csrf_from(client.get(f"/projects/{project_id}").text)


def test_research_with_no_agent_configured_returns_409(signed_in_client: TestClient) -> None:
    project_id = _create_project(signed_in_client)
    token = _csrf_for(signed_in_client, project_id)
    response = signed_in_client.post(
        f"/projects/{project_id}/research",
        data={"question": QUESTION, "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 409


def test_research_happy_path_creates_finding_and_citations(
    signed_in_client: TestClient,
) -> None:
    client = signed_in_client
    client.app.dependency_overrides[get_research_agent] = (  # type: ignore[attr-defined]
        lambda: FakeResearchAgent(proposal=_cited_proposal())
    )
    project_id = _create_project(client)
    token = _csrf_for(client, project_id)

    response = client.post(
        f"/projects/{project_id}/research",
        data={"question": QUESTION, "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303

    page = client.get(f"/projects/{project_id}")
    assert "SPY rose in Q1 2024." in page.text
    assert "SPY Q1 2024 recap" in page.text
    assert "pending" in page.text

    audit_page = client.get("/audit")
    assert "research_completed" in audit_page.text


def test_research_failure_is_audited_and_returns_502(signed_in_client: TestClient) -> None:
    client = signed_in_client
    client.app.dependency_overrides[get_research_agent] = (  # type: ignore[attr-defined]
        lambda: FakeResearchAgent(error="the model declined")
    )
    project_id = _create_project(client)
    token = _csrf_for(client, project_id)

    response = client.post(
        f"/projects/{project_id}/research",
        data={"question": QUESTION, "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 502

    audit_page = client.get("/audit")
    assert "research_failed" in audit_page.text


def test_research_requires_csrf(signed_in_client: TestClient) -> None:
    project_id = _create_project(signed_in_client)
    response = signed_in_client.post(
        f"/projects/{project_id}/research",
        data={"question": QUESTION},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_research_rejects_blank_question(signed_in_client: TestClient) -> None:
    client = signed_in_client
    client.app.dependency_overrides[get_research_agent] = (  # type: ignore[attr-defined]
        lambda: FakeResearchAgent(proposal=_cited_proposal())
    )
    project_id = _create_project(client)
    token = _csrf_for(client, project_id)

    response = client.post(
        f"/projects/{project_id}/research",
        data={"question": "   ", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_review_finding_accepts_and_audits(signed_in_client: TestClient) -> None:
    client = signed_in_client
    client.app.dependency_overrides[get_research_agent] = (  # type: ignore[attr-defined]
        lambda: FakeResearchAgent(proposal=_cited_proposal())
    )
    project_id = _create_project(client)
    token = _csrf_for(client, project_id)
    client.post(
        f"/projects/{project_id}/research",
        data={"question": QUESTION, "csrf_token": token},
        follow_redirects=False,
    )

    page = client.get(f"/projects/{project_id}")
    marker = f'/projects/{project_id}/findings/'
    start = page.text.index(marker) + len(marker)
    finding_id = page.text[start : page.text.index("/review", start)]

    token = _csrf_for(client, project_id)
    response = client.post(
        f"/projects/{project_id}/findings/{finding_id}/review",
        data={"decision": "accepted", "notes": "Looks solid.", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303

    page = client.get(f"/projects/{project_id}")
    assert "accepted" in page.text

    audit_page = client.get("/audit")
    assert "finding_reviewed" in audit_page.text


def test_review_finding_rejects_invalid_decision(signed_in_client: TestClient) -> None:
    client = signed_in_client
    client.app.dependency_overrides[get_research_agent] = (  # type: ignore[attr-defined]
        lambda: FakeResearchAgent(proposal=_cited_proposal())
    )
    project_id = _create_project(client)
    token = _csrf_for(client, project_id)
    client.post(
        f"/projects/{project_id}/research",
        data={"question": QUESTION, "csrf_token": token},
        follow_redirects=False,
    )

    page = client.get(f"/projects/{project_id}")
    marker = f'/projects/{project_id}/findings/'
    start = page.text.index(marker) + len(marker)
    finding_id = page.text[start : page.text.index("/review", start)]

    token = _csrf_for(client, project_id)
    response = client.post(
        f"/projects/{project_id}/findings/{finding_id}/review",
        data={"decision": "maybe", "notes": "", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_review_finding_requires_csrf(signed_in_client: TestClient) -> None:
    client = signed_in_client
    client.app.dependency_overrides[get_research_agent] = (  # type: ignore[attr-defined]
        lambda: FakeResearchAgent(proposal=_cited_proposal())
    )
    project_id = _create_project(client)
    token = _csrf_for(client, project_id)
    client.post(
        f"/projects/{project_id}/research",
        data={"question": QUESTION, "csrf_token": token},
        follow_redirects=False,
    )

    page = client.get(f"/projects/{project_id}")
    marker = f'/projects/{project_id}/findings/'
    start = page.text.index(marker) + len(marker)
    finding_id = page.text[start : page.text.index("/review", start)]

    response = client.post(
        f"/projects/{project_id}/findings/{finding_id}/review",
        data={"decision": "accepted", "notes": ""},
        follow_redirects=False,
    )
    assert response.status_code == 403
