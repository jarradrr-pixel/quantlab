"""Alpaca paper-trading HTTP client.

Refuses to talk to any host other than Alpaca's paper endpoint, even if
constructed directly with a bypassed or forged ``base_url`` -- this is a
second, redundant check on top of the validator in ``app.config.Settings``,
matching the project's fail-loudly, defense-in-depth conventions.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

import httpx

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

logger = logging.getLogger(__name__)

_ALPACA_PAPER_HOST = "paper-api.alpaca.markets"


def _ensure_paper_host(base_url: str) -> None:
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or parsed.hostname != _ALPACA_PAPER_HOST:
        raise BrokerProtocolError(
            f"Refusing to connect: {base_url!r} is not Alpaca's paper endpoint."
        )


class AlpacaPaperBroker(BrokerAdapter):
    """Talks to Alpaca's paper-trading REST API over httpx."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: str,
        client: httpx.Client | None = None,
    ) -> None:
        _ensure_paper_host(base_url)
        auth_headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret}
        if client is None:
            self._client = httpx.Client(base_url=base_url, headers=auth_headers, timeout=10.0)
        else:
            client.headers.update(auth_headers)
            self._client = client

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, *, json: dict[str, Any] | None = None) -> Any:
        try:
            response = self._client.request(method, path, json=json)
        except httpx.TimeoutException as exc:
            raise BrokerUnavailableError(f"Alpaca request to {path} timed out.") from exc
        except httpx.TransportError as exc:
            raise BrokerUnavailableError(f"Could not reach Alpaca: {exc}") from exc

        if response.status_code in (401, 403):
            raise BrokerAuthenticationError("Alpaca rejected the API key/secret.")
        if response.status_code in (400, 422):
            raise BrokerOrderRejectedError(_error_message(response))
        if response.status_code >= 500:
            raise BrokerUnavailableError(f"Alpaca returned {response.status_code} for {path}.")
        if response.status_code not in (200, 201):
            raise BrokerProtocolError(
                f"Unexpected status {response.status_code} from {path}."
            )
        try:
            return response.json()
        except ValueError as exc:
            raise BrokerProtocolError(f"Non-JSON response from {path}.") from exc

    def get_account(self) -> BrokerAccount:
        data = self._request("GET", "/v2/account")
        try:
            return BrokerAccount(
                account_id=data["id"],
                status=data["status"],
                currency=data["currency"],
                cash=Decimal(data["cash"]),
                portfolio_value=Decimal(data["portfolio_value"]),
                buying_power=Decimal(data["buying_power"]),
                pattern_day_trader=bool(data["pattern_day_trader"]),
                trading_blocked=bool(data["trading_blocked"]),
                is_paper=True,  # asserted by the paper-host check above, not read from Alpaca
                raw=data,
            )
        except (KeyError, TypeError, InvalidOperation) as exc:
            raise BrokerProtocolError(
                "Alpaca account response is missing or has malformed fields."
            ) from exc

    def list_positions(self) -> list[BrokerPosition]:
        data = self._request("GET", "/v2/positions")
        try:
            return [
                BrokerPosition(
                    symbol=item["symbol"],
                    qty=Decimal(item["qty"]),
                    side=item["side"],
                    market_value=Decimal(item["market_value"]),
                    avg_entry_price=Decimal(item["avg_entry_price"]),
                )
                for item in data
            ]
        except (KeyError, TypeError, InvalidOperation) as exc:
            raise BrokerProtocolError(
                "Alpaca positions response is missing or has malformed fields."
            ) from exc

    def list_open_orders(self) -> list[BrokerOrder]:
        data = self._request("GET", "/v2/orders?status=open")
        try:
            return [_parse_order(item) for item in data]
        except (KeyError, TypeError, InvalidOperation) as exc:
            raise BrokerProtocolError(
                "Alpaca orders response is missing or has malformed fields."
            ) from exc

    def submit_order(
        self,
        *,
        symbol: str,
        qty: Decimal,
        side: str,
        order_type: str,
        time_in_force: str,
        client_order_id: str | None = None,
    ) -> BrokerOrder:
        if side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
        body: dict[str, Any] = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force,
        }
        if client_order_id is not None:
            body["client_order_id"] = client_order_id
        data = self._request("POST", "/v2/orders", json=body)
        try:
            return _parse_order(data)
        except (KeyError, TypeError, InvalidOperation) as exc:
            raise BrokerProtocolError(
                "Alpaca order response is missing or has malformed fields."
            ) from exc


def _parse_order(item: dict[str, Any]) -> BrokerOrder:
    return BrokerOrder(
        order_id=item["id"],
        symbol=item["symbol"],
        side=item["side"],
        qty=Decimal(item["qty"]),
        order_type=item["type"],
        time_in_force=item["time_in_force"],
        status=item["status"],
        submitted_at=datetime.fromisoformat(item["submitted_at"].replace("Z", "+00:00")),
    )


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"Alpaca rejected the request ({response.status_code})."
    message = payload.get("message") if isinstance(payload, dict) else None
    if message:
        return str(message)
    return f"Alpaca rejected the request ({response.status_code})."
