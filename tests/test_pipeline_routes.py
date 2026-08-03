"""Pipeline route tests: knowledge test, hypothesis, code generation and code
validation. Fake*Agent doubles + dependency_overrides -- no test here
contacts a real Anthropic API.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.agents.base import (
    AcceptedFinding,
    AgentError,
    CitationProposal,
    ClaimProposal,
    HypothesisAgent,
    HypothesisProposal,
    KnowledgeTestAgent,
    KnowledgeTestProposal,
    KnowledgeTestSummary,
    ResearchAgent,
    ResearchProposal,
    StrategyCodeAgent,
    StrategySpecProposal,
)
from app.deps import (
    get_hypothesis_agent,
    get_knowledge_test_agent,
    get_research_agent,
    get_strategy_code_agent,
)
from tests.conftest import csrf_from

QUESTION = "Did SPY rise in Q1 2024?"


class FakeResearchAgent(ResearchAgent):
    def research(self, question: str) -> ResearchProposal:
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
            ]
        )


class FakeKnowledgeTestAgent(KnowledgeTestAgent):
    def __init__(
        self, proposal: KnowledgeTestProposal | None = None, error: str | None = None
    ) -> None:
        self._proposal = proposal
        self._error = error

    def test(
        self, question: str, findings: list[AcceptedFinding]
    ) -> KnowledgeTestProposal:
        if self._error is not None:
            raise AgentError(self._error)
        assert self._proposal is not None
        return self._proposal


class FakeHypothesisAgent(HypothesisAgent):
    def __init__(
        self, proposal: HypothesisProposal | None = None, error: str | None = None
    ) -> None:
        self._proposal = proposal
        self._error = error

    def propose(
        self,
        objective: str,
        findings: list[AcceptedFinding],
        prior_tests: list[KnowledgeTestSummary],
    ) -> HypothesisProposal:
        if self._error is not None:
            raise AgentError(self._error)
        assert self._proposal is not None
        return self._proposal


class FakeStrategyCodeAgent(StrategyCodeAgent):
    def __init__(
        self, proposal: StrategySpecProposal | None = None, error: str | None = None
    ) -> None:
        self._proposal = proposal
        self._error = error

    def generate(
        self,
        hypothesis_statement: str,
        *,
        symbol: str,
        timeframe: str,
        current_fast_window: int,
        current_slow_window: int,
    ) -> StrategySpecProposal:
        if self._error is not None:
            raise AgentError(self._error)
        assert self._proposal is not None
        return self._proposal


def _create_project(client: TestClient) -> str:
    page = client.get("/")
    token = csrf_from(page.text)
    response = client.post(
        "/projects",
        data={
            "name": "Pipeline project",
            "research_objective": "Test knowledge test, hypothesis and codegen routes.",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    return response.headers["location"].removeprefix("/projects/")


def _csrf_for(client: TestClient, project_id: str) -> str:
    return csrf_from(client.get(f"/projects/{project_id}").text)


def _accept_one_finding(client: TestClient, project_id: str) -> None:
    client.app.dependency_overrides[get_research_agent] = (  # type: ignore[attr-defined]
        lambda: FakeResearchAgent()
    )
    token = _csrf_for(client, project_id)
    client.post(
        f"/projects/{project_id}/research",
        data={"question": QUESTION, "csrf_token": token},
        follow_redirects=False,
    )

    page = client.get(f"/projects/{project_id}")
    marker = f"/projects/{project_id}/findings/"
    start = page.text.index(marker) + len(marker)
    finding_id = page.text[start : page.text.index("/review", start)]

    token = _csrf_for(client, project_id)
    client.post(
        f"/projects/{project_id}/findings/{finding_id}/review",
        data={"decision": "accepted", "notes": "", "csrf_token": token},
        follow_redirects=False,
    )
    del client.app.dependency_overrides[get_research_agent]  # type: ignore[attr-defined]


def _specify_strategy(client: TestClient, project_id: str) -> None:
    token = _csrf_for(client, project_id)
    response = client.post(
        f"/projects/{project_id}/strategy",
        data={
            "name": "SMA crossover",
            "symbol": "SPY",
            "timeframe": "1Day",
            "fast_window": "20",
            "slow_window": "100",
            "minimum_out_of_sample_trades": "30",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


# --- knowledge tests -------------------------------------------------------


def test_knowledge_test_with_no_agent_configured_returns_409(
    signed_in_client: TestClient,
) -> None:
    project_id = _create_project(signed_in_client)
    token = _csrf_for(signed_in_client, project_id)
    response = signed_in_client.post(
        f"/projects/{project_id}/knowledge-tests",
        data={"question": QUESTION, "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 409


def test_knowledge_test_requires_an_accepted_finding(
    signed_in_client: TestClient,
) -> None:
    client = signed_in_client
    proposal = KnowledgeTestProposal(
        verdict="supported", reasoning="matches finding", cited_finding_ids=["f1"]
    )
    client.app.dependency_overrides[get_knowledge_test_agent] = (  # type: ignore[attr-defined]
        lambda: FakeKnowledgeTestAgent(proposal=proposal)
    )
    project_id = _create_project(client)
    token = _csrf_for(client, project_id)
    response = client.post(
        f"/projects/{project_id}/knowledge-tests",
        data={"question": QUESTION, "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_knowledge_test_happy_path_creates_row_and_audits(
    signed_in_client: TestClient,
) -> None:
    client = signed_in_client
    project_id = _create_project(client)
    _accept_one_finding(client, project_id)

    proposal = KnowledgeTestProposal(
        verdict="supported", reasoning="matches the accepted finding", cited_finding_ids=["f1"]
    )
    client.app.dependency_overrides[get_knowledge_test_agent] = (  # type: ignore[attr-defined]
        lambda: FakeKnowledgeTestAgent(proposal=proposal)
    )
    token = _csrf_for(client, project_id)
    response = client.post(
        f"/projects/{project_id}/knowledge-tests",
        data={"question": QUESTION, "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303

    page = client.get(f"/projects/{project_id}")
    assert "supported" in page.text
    assert "matches the accepted finding" in page.text

    audit_page = client.get("/audit")
    assert "knowledge_test_completed" in audit_page.text


def test_knowledge_test_failure_is_audited_and_returns_502(
    signed_in_client: TestClient,
) -> None:
    client = signed_in_client
    project_id = _create_project(client)
    _accept_one_finding(client, project_id)

    client.app.dependency_overrides[get_knowledge_test_agent] = (  # type: ignore[attr-defined]
        lambda: FakeKnowledgeTestAgent(error="the model declined")
    )
    token = _csrf_for(client, project_id)
    response = client.post(
        f"/projects/{project_id}/knowledge-tests",
        data={"question": QUESTION, "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 502

    audit_page = client.get("/audit")
    assert "knowledge_test_failed" in audit_page.text


def test_knowledge_test_requires_csrf(signed_in_client: TestClient) -> None:
    project_id = _create_project(signed_in_client)
    response = signed_in_client.post(
        f"/projects/{project_id}/knowledge-tests",
        data={"question": QUESTION},
        follow_redirects=False,
    )
    assert response.status_code == 403


# --- hypotheses -------------------------------------------------------------


def test_hypothesis_with_no_agent_configured_returns_409(
    signed_in_client: TestClient,
) -> None:
    project_id = _create_project(signed_in_client)
    token = _csrf_for(signed_in_client, project_id)
    response = signed_in_client.post(
        f"/projects/{project_id}/hypotheses",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 409


def test_hypothesis_requires_an_accepted_finding(signed_in_client: TestClient) -> None:
    client = signed_in_client
    proposal = HypothesisProposal(
        statement="Buy SPY in Q1", rationale="seasonal effect", cited_finding_ids=["f1"]
    )
    client.app.dependency_overrides[get_hypothesis_agent] = (  # type: ignore[attr-defined]
        lambda: FakeHypothesisAgent(proposal=proposal)
    )
    project_id = _create_project(client)
    token = _csrf_for(client, project_id)
    response = client.post(
        f"/projects/{project_id}/hypotheses",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_hypothesis_happy_path_creates_row_and_audits(signed_in_client: TestClient) -> None:
    client = signed_in_client
    project_id = _create_project(client)
    _accept_one_finding(client, project_id)

    proposal = HypothesisProposal(
        statement="Buy SPY in Q1", rationale="seasonal effect", cited_finding_ids=["f1"]
    )
    client.app.dependency_overrides[get_hypothesis_agent] = (  # type: ignore[attr-defined]
        lambda: FakeHypothesisAgent(proposal=proposal)
    )
    token = _csrf_for(client, project_id)
    response = client.post(
        f"/projects/{project_id}/hypotheses",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303

    page = client.get(f"/projects/{project_id}")
    assert "Buy SPY in Q1" in page.text

    audit_page = client.get("/audit")
    assert "hypothesis_proposed" in audit_page.text


def test_hypothesis_failure_is_audited_and_returns_502(signed_in_client: TestClient) -> None:
    client = signed_in_client
    project_id = _create_project(client)
    _accept_one_finding(client, project_id)

    client.app.dependency_overrides[get_hypothesis_agent] = (  # type: ignore[attr-defined]
        lambda: FakeHypothesisAgent(error="the model declined")
    )
    token = _csrf_for(client, project_id)
    response = client.post(
        f"/projects/{project_id}/hypotheses",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 502

    audit_page = client.get("/audit")
    assert "hypothesis_failed" in audit_page.text


def test_hypothesis_requires_csrf(signed_in_client: TestClient) -> None:
    project_id = _create_project(signed_in_client)
    response = signed_in_client.post(
        f"/projects/{project_id}/hypotheses", data={}, follow_redirects=False
    )
    assert response.status_code == 403


# --- code generation ---------------------------------------------------------


def test_generate_code_with_no_agent_configured_returns_409(
    signed_in_client: TestClient,
) -> None:
    project_id = _create_project(signed_in_client)
    token = _csrf_for(signed_in_client, project_id)
    response = signed_in_client.post(
        f"/projects/{project_id}/generate-code",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 409


def test_generate_code_requires_a_strategy_and_hypothesis(
    signed_in_client: TestClient,
) -> None:
    client = signed_in_client
    proposal = StrategySpecProposal(
        fast_window=15, slow_window=60, minimum_out_of_sample_trades=10, rationale="r"
    )
    client.app.dependency_overrides[get_strategy_code_agent] = (  # type: ignore[attr-defined]
        lambda: FakeStrategyCodeAgent(proposal=proposal)
    )
    project_id = _create_project(client)
    token = _csrf_for(client, project_id)
    response = client.post(
        f"/projects/{project_id}/generate-code",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 400

    _specify_strategy(client, project_id)
    token = _csrf_for(client, project_id)
    response = client.post(
        f"/projects/{project_id}/generate-code",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 400  # still no hypothesis


def test_generate_code_happy_path_creates_row_and_audits(
    signed_in_client: TestClient,
) -> None:
    client = signed_in_client
    project_id = _create_project(client)
    _accept_one_finding(client, project_id)
    _specify_strategy(client, project_id)

    hyp_proposal = HypothesisProposal(
        statement="Buy SPY in Q1", rationale="seasonal effect", cited_finding_ids=["f1"]
    )
    client.app.dependency_overrides[get_hypothesis_agent] = (  # type: ignore[attr-defined]
        lambda: FakeHypothesisAgent(proposal=hyp_proposal)
    )
    token = _csrf_for(client, project_id)
    client.post(
        f"/projects/{project_id}/hypotheses",
        data={"csrf_token": token},
        follow_redirects=False,
    )

    code_proposal = StrategySpecProposal(
        fast_window=15, slow_window=60, minimum_out_of_sample_trades=10, rationale="refined"
    )
    client.app.dependency_overrides[get_strategy_code_agent] = (  # type: ignore[attr-defined]
        lambda: FakeStrategyCodeAgent(proposal=code_proposal)
    )
    token = _csrf_for(client, project_id)
    response = client.post(
        f"/projects/{project_id}/generate-code",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303

    page = client.get(f"/projects/{project_id}")
    assert "refined" in page.text
    assert "PENDING" in page.text

    audit_page = client.get("/audit")
    assert "code_generated" in audit_page.text


def test_generate_code_failure_is_audited_and_returns_502(
    signed_in_client: TestClient,
) -> None:
    client = signed_in_client
    project_id = _create_project(client)
    _accept_one_finding(client, project_id)
    _specify_strategy(client, project_id)

    hyp_proposal = HypothesisProposal(
        statement="Buy SPY in Q1", rationale="seasonal effect", cited_finding_ids=["f1"]
    )
    client.app.dependency_overrides[get_hypothesis_agent] = (  # type: ignore[attr-defined]
        lambda: FakeHypothesisAgent(proposal=hyp_proposal)
    )
    token = _csrf_for(client, project_id)
    client.post(
        f"/projects/{project_id}/hypotheses",
        data={"csrf_token": token},
        follow_redirects=False,
    )

    client.app.dependency_overrides[get_strategy_code_agent] = (  # type: ignore[attr-defined]
        lambda: FakeStrategyCodeAgent(error="the model declined")
    )
    token = _csrf_for(client, project_id)
    response = client.post(
        f"/projects/{project_id}/generate-code",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 502

    audit_page = client.get("/audit")
    assert "code_generation_failed" in audit_page.text


def test_generate_code_requires_csrf(signed_in_client: TestClient) -> None:
    project_id = _create_project(signed_in_client)
    response = signed_in_client.post(
        f"/projects/{project_id}/generate-code", data={}, follow_redirects=False
    )
    assert response.status_code == 403


# --- code validation ---------------------------------------------------------


def _generate_code(
    client: TestClient, project_id: str, proposal: StrategySpecProposal
) -> None:
    _accept_one_finding(client, project_id)
    _specify_strategy(client, project_id)

    hyp_proposal = HypothesisProposal(
        statement="Buy SPY in Q1", rationale="seasonal effect", cited_finding_ids=["f1"]
    )
    client.app.dependency_overrides[get_hypothesis_agent] = (  # type: ignore[attr-defined]
        lambda: FakeHypothesisAgent(proposal=hyp_proposal)
    )
    token = _csrf_for(client, project_id)
    client.post(
        f"/projects/{project_id}/hypotheses",
        data={"csrf_token": token},
        follow_redirects=False,
    )

    client.app.dependency_overrides[get_strategy_code_agent] = (  # type: ignore[attr-defined]
        lambda: FakeStrategyCodeAgent(proposal=proposal)
    )
    token = _csrf_for(client, project_id)
    response = client.post(
        f"/projects/{project_id}/generate-code",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_validate_code_requires_generated_code_first(signed_in_client: TestClient) -> None:
    project_id = _create_project(signed_in_client)
    token = _csrf_for(signed_in_client, project_id)
    response = signed_in_client.post(
        f"/projects/{project_id}/validate-code",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_validate_code_valid_spec_creates_new_strategy_version(
    signed_in_client: TestClient,
) -> None:
    client = signed_in_client
    project_id = _create_project(client)
    _generate_code(
        client,
        project_id,
        StrategySpecProposal(
            fast_window=15, slow_window=60, minimum_out_of_sample_trades=10, rationale="r"
        ),
    )

    token = _csrf_for(client, project_id)
    response = client.post(
        f"/projects/{project_id}/validate-code",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303

    page = client.get(f"/projects/{project_id}")
    assert "VALID" in page.text
    assert "v2" in page.text  # a new strategy version was produced
    assert "fast 15 / slow 60" in page.text

    audit_page = client.get("/audit")
    assert "code_validated" in audit_page.text


def test_validate_code_invalid_spec_does_not_create_a_new_strategy_version(
    signed_in_client: TestClient,
) -> None:
    client = signed_in_client
    project_id = _create_project(client)
    _generate_code(
        client,
        project_id,
        StrategySpecProposal(
            fast_window=15, slow_window=600, minimum_out_of_sample_trades=10, rationale="r"
        ),
    )

    token = _csrf_for(client, project_id)
    response = client.post(
        f"/projects/{project_id}/validate-code",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303

    page = client.get(f"/projects/{project_id}")
    assert "INVALID" in page.text
    assert "slow_window exceeds the maximum" in page.text
    # still v1 -- no new strategy version was produced from an invalid spec
    assert "v2" not in page.text
    assert "fast 20 / slow 100" in page.text  # original strategy unchanged


def test_validate_code_requires_csrf(signed_in_client: TestClient) -> None:
    project_id = _create_project(signed_in_client)
    response = signed_in_client.post(
        f"/projects/{project_id}/validate-code", data={}, follow_redirects=False
    )
    assert response.status_code == 403
