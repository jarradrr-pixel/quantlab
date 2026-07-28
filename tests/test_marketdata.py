"""Market data provider and caching-service tests. No test touches yfinance's
real network -- ``FakeProvider`` stands in, and ``YFinanceProvider``'s error
translation is tested by monkeypatching ``yfinance.Ticker``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pandas as pd
import pytest
from sqlalchemy.orm import Session

from app.marketdata.base import Bar, MarketDataError, MarketDataProvider
from app.marketdata.service import MarketDataService
from app.marketdata.yfinance_provider import YFinanceProvider


class FakeProvider(MarketDataProvider):
    def __init__(self, bars: list[Bar]) -> None:
        self._bars = bars
        self.calls = 0

    def get_bars(self, symbol: str, start: date, end: date) -> list[Bar]:
        self.calls += 1
        return [bar for bar in self._bars if start <= bar.timestamp.date() <= end]


def _bar(day: int, price: str) -> Bar:
    return Bar(
        timestamp=datetime(2024, 1, day, tzinfo=UTC),
        open=Decimal(price),
        high=Decimal(price),
        low=Decimal(price),
        close=Decimal(price),
        volume=1000,
    )


def test_service_fetches_once_and_caches(db: Session) -> None:
    provider = FakeProvider([_bar(1, "100"), _bar(2, "101")])
    service = MarketDataService(db, provider)

    first = service.get_or_fetch_bars("SPY", date(2024, 1, 1), date(2024, 1, 2))
    second = service.get_or_fetch_bars("SPY", date(2024, 1, 1), date(2024, 1, 2))

    assert len(first) == len(second) == 2
    assert provider.calls == 1


def test_service_refetches_a_wider_range_without_duplicate_key_errors(
    db: Session,
) -> None:
    provider = FakeProvider([_bar(1, "100"), _bar(2, "101"), _bar(3, "102")])
    service = MarketDataService(db, provider)

    service.get_or_fetch_bars("SPY", date(2024, 1, 1), date(2024, 1, 2))
    wider = service.get_or_fetch_bars("SPY", date(2024, 1, 1), date(2024, 1, 3))

    assert len(wider) == 3
    assert provider.calls == 2


def test_service_caches_symbols_independently(db: Session) -> None:
    provider = FakeProvider([_bar(1, "100")])
    service = MarketDataService(db, provider)

    service.get_or_fetch_bars("SPY", date(2024, 1, 1), date(2024, 1, 1))
    service.get_or_fetch_bars("QQQ", date(2024, 1, 1), date(2024, 1, 1))

    assert provider.calls == 2


def test_yfinance_provider_raises_on_empty_history(monkeypatch: pytest.MonkeyPatch) -> None:
    class _EmptyTicker:
        def __init__(self, symbol: str) -> None:
            pass

        def history(self, **kwargs: object) -> pd.DataFrame:
            return pd.DataFrame()

    monkeypatch.setattr("app.marketdata.yfinance_provider.yf.Ticker", _EmptyTicker)
    with pytest.raises(MarketDataError, match="No bars returned"):
        YFinanceProvider().get_bars("NOSUCHTICKER", date(2024, 1, 1), date(2024, 1, 2))


def test_yfinance_provider_wraps_network_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BrokenTicker:
        def __init__(self, symbol: str) -> None:
            pass

        def history(self, **kwargs: object) -> pd.DataFrame:
            raise ConnectionError("no network in this test")

    monkeypatch.setattr("app.marketdata.yfinance_provider.yf.Ticker", _BrokenTicker)
    with pytest.raises(MarketDataError, match="Could not fetch bars"):
        YFinanceProvider().get_bars("SPY", date(2024, 1, 1), date(2024, 1, 2))


def test_yfinance_provider_parses_a_successful_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = pd.DataFrame(
        {"Open": [100.0], "High": [101.0], "Low": [99.0], "Close": [100.5], "Volume": [1000]},
        index=pd.DatetimeIndex([pd.Timestamp("2024-01-02", tz="America/New_York")]),
    )

    class _WorkingTicker:
        def __init__(self, symbol: str) -> None:
            pass

        def history(self, **kwargs: object) -> pd.DataFrame:
            return frame

    monkeypatch.setattr("app.marketdata.yfinance_provider.yf.Ticker", _WorkingTicker)
    bars = YFinanceProvider().get_bars("SPY", date(2024, 1, 1), date(2024, 1, 3))
    assert len(bars) == 1
    assert bars[0].close == Decimal("100.5")
    assert bars[0].timestamp.date() == date(2024, 1, 2)
