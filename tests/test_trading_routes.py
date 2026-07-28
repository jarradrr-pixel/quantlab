"""End-to-end pipeline tests over HTTP: strategy spec -> backtest -> risk
review -> approval -> PAPER_TRADING -> order submission -> reconciliation.

Market data is faked via a dependency override (no yfinance network calls);
order submission uses the default mock broker backend (no Alpaca calls).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from app.deps import get_market_data_provider
from app.marketdata.base import Bar, MarketDataProvider
from tests.conftest import csrf_from

SYMBOL = "SPY"


class FakeMarketDataProvider(MarketDataProvider):
    def __init__(self, bars: list[Bar]) -> None:
        self._bars = bars

    def get_bars(self, symbol: str, start: date, end: date) -> list[Bar]:
        return [bar for bar in self._bars if start <= bar.timestamp.date() <= end]


def _uptrend_then_downtrend_bars(start: date) -> list[Bar]:
    prices = (
        [100] * 25
        + [100 + 2 * i for i in range(1, 31)]
        + [162 - 2 * i for i in range(1, 31)]
    )
    start_dt = datetime(start.year, start.month, start.day, tzinfo=UTC)
    return [
        Bar(
            timestamp=start_dt + timedelta(days=i),
            open=Decimal(p),
            high=Decimal(p + 1),
            low=Decimal(p - 1),
            close=Decimal(p),
            volume=1000,
        )
        for i, p in enumerate(prices)
    ]


def _create_project(client: TestClient) -> str:
    page = client.get("/")
    token = csrf_from(page.text)
    response = client.post(
        "/projects",
        data={
            "name": "MA Crossover",
            "research_objective": "Test the pipeline.",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    return response.headers["location"].removeprefix("/projects/")


def _advance(client: TestClient, project_id: str, target: str, reason: str = "test") -> None:
    page = client.get(f"/projects/{project_id}")
    token = csrf_from(page.text)
    response = client.post(
        f"/projects/{project_id}/transition",
        data={"target": target, "reason": reason, "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


def _csrf_for(client: TestClient, project_id: str) -> str:
    return csrf_from(client.get(f"/projects/{project_id}").text)


def test_full_pipeline_from_research_to_a_filled_paper_order(
    signed_in_client: TestClient,
) -> None:
    client = signed_in_client
    start = date(2024, 1, 1)
    bars = _uptrend_then_downtrend_bars(start)
    client.app.dependency_overrides[get_market_data_provider] = (  # type: ignore[attr-defined]
        lambda: FakeMarketDataProvider(bars)
    )

    project_id = _create_project(client)
    for target in (
        "KNOWLEDGE_REVIEW",
        "KNOWLEDGE_TESTING",
        "HYPOTHESIS_DEVELOPMENT",
        "STRATEGY_SPECIFICATION",
    ):
        _advance(client, project_id, target)

    token = _csrf_for(client, project_id)
    response = client.post(
        f"/projects/{project_id}/strategy",
        data={
            "name": "20/100-ish crossover",
            "symbol": SYMBOL,
            "timeframe": "1Day",
            "fast_window": "5",
            "slow_window": "20",
            "minimum_out_of_sample_trades": "0",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    for target in ("CODE_GENERATION", "CODE_VALIDATION", "BACKTESTING"):
        _advance(client, project_id, target)

    token = _csrf_for(client, project_id)
    response = client.post(
        f"/projects/{project_id}/backtests",
        data={
            "start_date": start.isoformat(),
            "end_date": (start + timedelta(days=len(bars) - 1)).isoformat(),
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get(f"/projects/{project_id}")
    assert "ACCEPTED" in page.text

    _advance(client, project_id, "RISK_REVIEW")
    token = _csrf_for(client, project_id)
    response = client.post(
        f"/projects/{project_id}/risk-review",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "APPROVED" in client.get(f"/projects/{project_id}").text

    _advance(client, project_id, "AWAITING_HUMAN_APPROVAL")
    token = _csrf_for(client, project_id)
    response = client.post(
        f"/projects/{project_id}/approvals",
        data={
            "kind": "enter:PAPER_TRADING",
            "decision": "approved",
            "notes": "",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    kill_switch_page = client.get("/kill-switch")
    token = csrf_from(kill_switch_page.text)
    client.post(
        "/kill-switch",
        data={"engaged": "false", "reason": "Starting a paper session.", "csrf_token": token},
        follow_redirects=False,
    )

    _advance(client, project_id, "PAPER_TRADING")

    token = _csrf_for(client, project_id)
    response = client.post(
        f"/projects/{project_id}/orders",
        data={"symbol": SYMBOL, "side": "buy", "qty": "1", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text

    page = client.get(f"/projects/{project_id}")
    assert SYMBOL in page.text
    audit_page = client.get("/audit")
    assert "order_submitted" in audit_page.text

    token = csrf_from(client.get("/broker").text)
    reconcile = client.post(
        "/broker/reconcile", data={"csrf_token": token}, follow_redirects=False
    )
    assert reconcile.status_code == 303
    broker_page = client.get("/broker")
    assert "Nothing untracked found" in broker_page.text


def test_run_backtest_without_a_strategy_returns_400(signed_in_client: TestClient) -> None:
    project_id = _create_project(signed_in_client)
    token = _csrf_for(signed_in_client, project_id)
    response = signed_in_client.post(
        f"/projects/{project_id}/backtests",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_run_risk_review_without_a_strategy_returns_400(signed_in_client: TestClient) -> None:
    project_id = _create_project(signed_in_client)
    token = _csrf_for(signed_in_client, project_id)
    response = signed_in_client.post(
        f"/projects/{project_id}/risk-review",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_unknown_approval_kind_is_rejected(signed_in_client: TestClient) -> None:
    project_id = _create_project(signed_in_client)
    token = _csrf_for(signed_in_client, project_id)
    response = signed_in_client.post(
        f"/projects/{project_id}/approvals",
        data={
            "kind": "enter:NOT_A_REAL_KIND",
            "decision": "approved",
            "notes": "",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_order_refused_while_not_in_paper_trading(signed_in_client: TestClient) -> None:
    project_id = _create_project(signed_in_client)
    token = _csrf_for(signed_in_client, project_id)
    response = signed_in_client.post(
        f"/projects/{project_id}/orders",
        data={"symbol": SYMBOL, "side": "buy", "qty": "1", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 409


def test_strategy_spec_requires_csrf(signed_in_client: TestClient) -> None:
    project_id = _create_project(signed_in_client)
    response = signed_in_client.post(
        f"/projects/{project_id}/strategy",
        data={
            "name": "x",
            "symbol": SYMBOL,
            "timeframe": "1Day",
            "fast_window": "5",
            "slow_window": "20",
            "minimum_out_of_sample_trades": "0",
        },
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_backtest_requires_csrf(signed_in_client: TestClient) -> None:
    project_id = _create_project(signed_in_client)
    response = signed_in_client.post(
        f"/projects/{project_id}/backtests", data={}, follow_redirects=False
    )
    assert response.status_code == 403


def test_risk_review_requires_csrf(signed_in_client: TestClient) -> None:
    project_id = _create_project(signed_in_client)
    response = signed_in_client.post(
        f"/projects/{project_id}/risk-review", data={}, follow_redirects=False
    )
    assert response.status_code == 403


def test_approval_requires_csrf(signed_in_client: TestClient) -> None:
    project_id = _create_project(signed_in_client)
    response = signed_in_client.post(
        f"/projects/{project_id}/approvals",
        data={"kind": "enter:PAPER_TRADING", "decision": "approved", "notes": ""},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_order_submission_requires_csrf(signed_in_client: TestClient) -> None:
    project_id = _create_project(signed_in_client)
    response = signed_in_client.post(
        f"/projects/{project_id}/orders",
        data={"symbol": SYMBOL, "side": "buy", "qty": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 403
