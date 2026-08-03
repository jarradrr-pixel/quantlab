"""Performance review and experiment-tracking route tests.

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
            "name": "Performance review project",
            "research_objective": "Test the performance review routes.",
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


def _advance_to_paper_trading(client: TestClient, project_id: str) -> None:
    start = date(2024, 1, 1)
    bars = _uptrend_then_downtrend_bars(start)
    client.app.dependency_overrides[get_market_data_provider] = (  # type: ignore[attr-defined]
        lambda: FakeMarketDataProvider(bars)
    )

    for target in (
        "KNOWLEDGE_REVIEW",
        "KNOWLEDGE_TESTING",
        "HYPOTHESIS_DEVELOPMENT",
        "STRATEGY_SPECIFICATION",
    ):
        _advance(client, project_id, target)

    token = _csrf_for(client, project_id)
    client.post(
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

    for target in ("CODE_GENERATION", "CODE_VALIDATION", "BACKTESTING"):
        _advance(client, project_id, target)

    token = _csrf_for(client, project_id)
    client.post(
        f"/projects/{project_id}/backtests",
        data={
            "start_date": start.isoformat(),
            "end_date": (start + timedelta(days=len(bars) - 1)).isoformat(),
            "csrf_token": token,
        },
        follow_redirects=False,
    )

    _advance(client, project_id, "RISK_REVIEW")
    token = _csrf_for(client, project_id)
    client.post(
        f"/projects/{project_id}/risk-review",
        data={"csrf_token": token},
        follow_redirects=False,
    )

    _advance(client, project_id, "AWAITING_HUMAN_APPROVAL")
    token = _csrf_for(client, project_id)
    client.post(
        f"/projects/{project_id}/approvals",
        data={
            "kind": "enter:PAPER_TRADING",
            "decision": "approved",
            "notes": "",
            "csrf_token": token,
        },
        follow_redirects=False,
    )

    kill_switch_page = client.get("/kill-switch")
    token = csrf_from(kill_switch_page.text)
    client.post(
        "/kill-switch",
        data={"engaged": "false", "reason": "Starting a paper session.", "csrf_token": token},
        follow_redirects=False,
    )

    _advance(client, project_id, "PAPER_TRADING")


def _submit_order(client: TestClient, project_id: str, side: str, qty: str) -> None:
    token = _csrf_for(client, project_id)
    response = client.post(
        f"/projects/{project_id}/orders",
        data={"symbol": SYMBOL, "side": side, "qty": qty, "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


# --- performance review -----------------------------------------------------


def test_performance_review_with_no_strategy_returns_400(
    signed_in_client: TestClient,
) -> None:
    project_id = _create_project(signed_in_client)
    token = _csrf_for(signed_in_client, project_id)
    response = signed_in_client.post(
        f"/projects/{project_id}/performance-review",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_performance_review_happy_path_creates_row_and_audits(
    signed_in_client: TestClient,
) -> None:
    client = signed_in_client
    project_id = _create_project(client)
    _advance_to_paper_trading(client, project_id)
    _submit_order(client, project_id, "buy", "1")
    _submit_order(client, project_id, "sell", "1")

    token = _csrf_for(client, project_id)
    response = client.post(
        f"/projects/{project_id}/performance-review",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303

    page = client.get(f"/projects/{project_id}")
    assert "trade(s)" in page.text

    audit_page = client.get("/audit")
    assert "performance_reviewed" in audit_page.text


def test_performance_review_requires_csrf(signed_in_client: TestClient) -> None:
    project_id = _create_project(signed_in_client)
    response = signed_in_client.post(
        f"/projects/{project_id}/performance-review", data={}, follow_redirects=False
    )
    assert response.status_code == 403


# --- experiments page --------------------------------------------------------


def test_experiments_page_with_no_strategy_shows_empty_state(
    signed_in_client: TestClient,
) -> None:
    project_id = _create_project(signed_in_client)
    response = signed_in_client.get(f"/projects/{project_id}/experiments")
    assert response.status_code == 200
    assert "No strategy has been specified" in response.text


def test_experiments_page_lists_multiple_strategy_versions(
    signed_in_client: TestClient,
) -> None:
    client = signed_in_client
    project_id = _create_project(client)

    token = _csrf_for(client, project_id)
    client.post(
        f"/projects/{project_id}/strategy",
        data={
            "name": "v1",
            "symbol": SYMBOL,
            "timeframe": "1Day",
            "fast_window": "20",
            "slow_window": "100",
            "minimum_out_of_sample_trades": "30",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    token = _csrf_for(client, project_id)
    client.post(
        f"/projects/{project_id}/strategy",
        data={
            "name": "v2",
            "symbol": SYMBOL,
            "timeframe": "1Day",
            "fast_window": "10",
            "slow_window": "50",
            "minimum_out_of_sample_trades": "10",
            "csrf_token": token,
        },
        follow_redirects=False,
    )

    response = client.get(f"/projects/{project_id}/experiments")
    assert response.status_code == 200
    assert "v1 — v1" in response.text
    assert "v2 — v2" in response.text
    assert "fast 20 / slow 100" in response.text
    assert "fast 10 / slow 50" in response.text
