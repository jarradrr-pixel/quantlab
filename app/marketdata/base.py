"""Market data provider contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class MarketDataError(Exception):
    """Raised when a provider cannot return bars for the requested range."""


class MarketDataProvider(ABC):
    @abstractmethod
    def get_bars(self, symbol: str, start: date, end: date) -> list[Bar]: ...
