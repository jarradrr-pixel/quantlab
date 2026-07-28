"""Broker adapter unit tests and /broker route integration tests.

Adapter tests use ``httpx.MockTransport`` (built into httpx, no extra
dependency) to fake Alpaca's HTTP responses. Route tests use a `FakeBroker`
substituted via FastAPI's `dependency_overrides` -- no test here contacts a
real broker, paper or otherwise.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from app.brokers.alpaca import AlpacaPaperBroker
from app.brokers.base import (
    BrokerAccount,
    BrokerAdapter,
    BrokerAuthenticationError,
    BrokerOrder,
    BrokerOrderRejectedError,
    BrokerPosition,
    BrokerProtocolError,
    BrokerUnavailableError,
)
from app.deps import get_broker
from tests.conftest import csrf_from

BASE_URL = "https://paper-api.alpaca.markets"

_ACCOUNT_JSON = {
    "id": "acct-123",
    "status": "ACTIVE",
    "currency": "USD",
    "cash": "1000.00",
    "portfolio_value": "1500.00",
    "buying_power": "2000.00",
    "pattern_day_trader": False,
    "trading_blocked": False,
}

_POSITION_JSON = [
    {
        "symbol": "SPY",
        "qty": "10",
        "side": "long",
        "market_value": "5000.00",
        "avg_entry_price": "480.00",
    }
]

_ORDER_JSON = [
    {
        "id": "order-1",
        "symbol": "SPY",
        "side": "buy",
        "qty": "5",
        "type": "market",
        "time_in_force": "day",
        "status": "new",
        "submitted_at": "2026-07-28T12:00:00Z",
    }
]


def _broker_with(handler: object) -> AlpacaPaperBroker:
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE_URL)  # type: ignore[arg-type]
    return AlpacaPaperBroker(
        api_key="key", api_secret="secret", base_url=BASE_URL, client=client
    )


# --- adapter: get_account -----------------------------------------------


def test_get_account_parses_a_successful_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["APCA-API-KEY-ID"] == "key"
        assert request.headers["APCA-API-SECRET-KEY"] == "secret"
        return httpx.Response(200, json=_ACCOUNT_JSON)

    account = _broker_with(handler).get_account()
    assert account.account_id == "acct-123"
    assert account.cash == Decimal("1000.00")
    assert account.is_paper is True


def test_401_raises_authentication_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "unauthorized"})

    with pytest.raises(BrokerAuthenticationError):
        _broker_with(handler).get_account()


def test_5xx_raises_unavailable_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    with pytest.raises(BrokerUnavailableError):
        _broker_with(handler).get_account()


def test_timeout_raises_unavailable_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    with pytest.raises(BrokerUnavailableError):
        _broker_with(handler).get_account()


def test_malformed_json_raises_protocol_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    with pytest.raises(BrokerProtocolError):
        _broker_with(handler).get_account()


def test_missing_fields_raise_protocol_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "acct-123"})

    with pytest.raises(BrokerProtocolError):
        _broker_with(handler).get_account()


def test_constructor_refuses_a_non_paper_host() -> None:
    with pytest.raises(BrokerProtocolError, match="not Alpaca's paper endpoint"):
        AlpacaPaperBroker(
            api_key="key", api_secret="secret", base_url="https://api.alpaca.markets"
        )


# --- adapter: positions / orders -----------------------------------------


def test_list_positions_parses_response() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_POSITION_JSON)

    positions = _broker_with(handler).list_positions()
    assert positions == [
        BrokerPosition(
            symbol="SPY",
            qty=Decimal("10"),
            side="long",
            market_value=Decimal("5000.00"),
            avg_entry_price=Decimal("480.00"),
        )
    ]


def test_list_open_orders_parses_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/orders"
        assert "status=open" in str(request.url)
        return httpx.Response(200, json=_ORDER_JSON)

    orders = _broker_with(handler).list_open_orders()
    assert len(orders) == 1
    assert orders[0].order_id == "order-1"
    assert orders[0].submitted_at == datetime(2026, 7, 28, 12, 0, 0, tzinfo=UTC)


# --- adapter: submit_order ------------------------------------------------


def test_submit_order_parses_a_successful_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        return httpx.Response(200, json=_ORDER_JSON[0])

    order = _broker_with(handler).submit_order(
        symbol="SPY", qty=Decimal("5"), side="buy", order_type="market", time_in_force="day"
    )
    assert isinstance(order, BrokerOrder)
    assert order.symbol == "SPY"


def test_submit_order_422_raises_order_rejected_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": "insufficient buying power"})

    with pytest.raises(BrokerOrderRejectedError, match="insufficient buying power"):
        _broker_with(handler).submit_order(
            symbol="SPY",
            qty=Decimal("5"),
            side="buy",
            order_type="market",
            time_in_force="day",
        )


def test_submit_order_rejects_an_invalid_side() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover - unreachable
        raise AssertionError("should not be called")

    with pytest.raises(ValueError, match="side must be"):
        _broker_with(handler).submit_order(
            symbol="SPY",
            qty=Decimal("5"),
            side="sideways",
            order_type="market",
            time_in_force="day",
        )


# --- routes: /broker ------------------------------------------------------


class FakeBroker(BrokerAdapter):
    """Test double: returns canned data or raises a canned error."""

    def __init__(
        self,
        *,
        account: BrokerAccount | None = None,
        positions: list[BrokerPosition] | None = None,
        orders: list[BrokerOrder] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._account = account
        self._positions = positions or []
        self._orders = orders or []
        self._error = error

    def get_account(self) -> BrokerAccount:
        if self._error is not None:
            raise self._error
        assert self._account is not None
        return self._account

    def list_positions(self) -> list[BrokerPosition]:
        if self._error is not None:
            raise self._error
        return self._positions

    def list_open_orders(self) -> list[BrokerOrder]:
        if self._error is not None:
            raise self._error
        return self._orders

    def submit_order(self, **_kwargs: object) -> BrokerOrder:
        raise NotImplementedError("not exercised by route tests")


def _fake_account() -> BrokerAccount:
    return BrokerAccount(
        account_id="acct-123",
        status="ACTIVE",
        currency="USD",
        cash=Decimal("1000.00"),
        portfolio_value=Decimal("1500.00"),
        buying_power=Decimal("2000.00"),
        pattern_day_trader=False,
        trading_blocked=False,
        is_paper=True,
        raw={"id": "acct-123"},
    )


def test_broker_page_defaults_to_the_mock_backend(
    signed_in_client: TestClient,
) -> None:
    response = signed_in_client.get("/broker")
    assert response.status_code == 200
    assert "mock" in response.text


def test_broker_actions_require_csrf(signed_in_client: TestClient) -> None:
    signed_in_client.app.dependency_overrides[get_broker] = lambda: FakeBroker(  # type: ignore[attr-defined]
        account=_fake_account()
    )
    response = signed_in_client.post(
        "/broker/verify", data={}, follow_redirects=False
    )
    assert response.status_code == 403


def test_verify_creates_a_snapshot_and_audit_entry(signed_in_client: TestClient) -> None:
    signed_in_client.app.dependency_overrides[get_broker] = lambda: FakeBroker(  # type: ignore[attr-defined]
        account=_fake_account()
    )
    page = signed_in_client.get("/broker")
    token = csrf_from(page.text)
    response = signed_in_client.post(
        "/broker/verify", data={"csrf_token": token}, follow_redirects=False
    )
    assert response.status_code == 303

    broker_page = signed_in_client.get("/broker")
    assert "ACTIVE" in broker_page.text
    assert "1000.00" in broker_page.text

    audit_page = signed_in_client.get("/audit")
    assert "broker_verified" in audit_page.text


def test_verify_failure_is_audited_and_returns_502(signed_in_client: TestClient) -> None:
    signed_in_client.app.dependency_overrides[get_broker] = lambda: FakeBroker(  # type: ignore[attr-defined]
        error=BrokerUnavailableError("Alpaca is down")
    )
    page = signed_in_client.get("/broker")
    token = csrf_from(page.text)
    response = signed_in_client.post(
        "/broker/verify", data={"csrf_token": token}, follow_redirects=False
    )
    assert response.status_code == 502

    audit_page = signed_in_client.get("/audit")
    assert "broker_verify_failed" in audit_page.text


def test_reconcile_lists_every_broker_position_and_order_as_untracked(
    signed_in_client: TestClient,
) -> None:
    positions = [
        BrokerPosition(
            symbol="SPY",
            qty=Decimal("10"),
            side="long",
            market_value=Decimal("5000.00"),
            avg_entry_price=Decimal("480.00"),
        )
    ]
    orders = [
        BrokerOrder(
            order_id="order-1",
            symbol="QQQ",
            side="buy",
            qty=Decimal("3"),
            order_type="market",
            time_in_force="day",
            status="new",
            submitted_at=datetime(2026, 7, 28, 12, 0, 0, tzinfo=UTC),
        )
    ]
    signed_in_client.app.dependency_overrides[get_broker] = lambda: FakeBroker(  # type: ignore[attr-defined]
        positions=positions, orders=orders
    )
    page = signed_in_client.get("/broker")
    token = csrf_from(page.text)
    response = signed_in_client.post(
        "/broker/reconcile", data={"csrf_token": token}, follow_redirects=False
    )
    assert response.status_code == 303

    broker_page = signed_in_client.get("/broker")
    assert "SPY" in broker_page.text
    assert "QQQ" in broker_page.text
    assert "1 open position" in broker_page.text
    assert "1 open order" in broker_page.text

    audit_page = signed_in_client.get("/audit")
    assert "broker_reconciled" in audit_page.text
