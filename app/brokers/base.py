"""Broker adapter contract.

``submit_order`` is implemented by ``AlpacaPaperBroker`` but is not called
from any route in Phase 3: nothing in this system may approve an order for
submission until a risk engine exists to gate it (see docs/architecture.md's
privilege-gradient trust table). Only ``get_account``, ``list_positions`` and
``list_open_orders`` are reachable from the operator console today.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class BrokerAccount:
    account_id: str
    status: str
    currency: str
    cash: Decimal
    portfolio_value: Decimal
    buying_power: Decimal
    pattern_day_trader: bool
    trading_blocked: bool
    is_paper: bool
    raw: dict[str, Any]


@dataclass(frozen=True)
class BrokerPosition:
    symbol: str
    qty: Decimal
    side: str
    market_value: Decimal
    avg_entry_price: Decimal


@dataclass(frozen=True)
class BrokerOrder:
    order_id: str
    symbol: str
    side: str
    qty: Decimal
    order_type: str
    time_in_force: str
    status: str
    submitted_at: datetime


class BrokerError(Exception):
    """Base class for all broker-adapter failures."""


class BrokerAuthenticationError(BrokerError):
    """The broker rejected the configured credentials (401/403)."""


class BrokerUnavailableError(BrokerError):
    """Network failure, timeout, or a 5xx response from the broker."""


class BrokerProtocolError(BrokerError):
    """The response did not have the shape the adapter expects."""


class BrokerOrderRejectedError(BrokerError):
    """The broker understood the order request and refused it (422/400).

    Distinct from ``BrokerProtocolError``: this means the adapter and the
    broker communicated correctly and the broker declined the order itself
    (bad symbol, insufficient buying power, market closed, etc.).
    """


class BrokerAdapter(ABC):
    """What every broker integration must provide."""

    @abstractmethod
    def get_account(self) -> BrokerAccount: ...

    @abstractmethod
    def list_positions(self) -> list[BrokerPosition]: ...

    @abstractmethod
    def list_open_orders(self) -> list[BrokerOrder]: ...

    @abstractmethod
    def submit_order(
        self,
        *,
        symbol: str,
        qty: Decimal,
        side: str,
        order_type: str,
        time_in_force: str,
        client_order_id: str | None = None,
    ) -> BrokerOrder: ...
