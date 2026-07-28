"""yfinance-backed market data provider.

Daily bars only, matching ``QUANTLAB_ALLOWED_TIMEFRAMES``'s only supported
value today. ``auto_adjust=True`` means ``close`` is already split/dividend
adjusted -- no separate adjusted-close column is needed anywhere downstream.

yfinance returns an index tz-localized to the exchange timezone. Since only
daily bars are supported, the time-of-day is not meaningful -- each bar's
timestamp is normalised to UTC midnight of its calendar date. This also
sidesteps the SQLite round-trip issue documented in ``app.core.audit``
(a tz-aware datetime written to SQLite comes back naive): comparing calendar
dates at a fixed UTC midnight is safe either way.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal

import yfinance as yf

from app.marketdata.base import Bar, MarketDataError, MarketDataProvider

logger = logging.getLogger(__name__)


class YFinanceProvider(MarketDataProvider):
    def get_bars(self, symbol: str, start: date, end: date) -> list[Bar]:
        try:
            history = yf.Ticker(symbol).history(
                start=start, end=end, interval="1d", auto_adjust=True
            )
        except Exception as exc:  # yfinance/requests raise many, no stable base class
            raise MarketDataError(f"Could not fetch bars for {symbol}: {exc}") from exc

        if history.empty:
            raise MarketDataError(
                f"No bars returned for {symbol} between {start} and {end}."
            )

        bars: list[Bar] = []
        for timestamp, row in history.iterrows():
            calendar_date = timestamp.to_pydatetime().date()
            ts: datetime = datetime(
                calendar_date.year, calendar_date.month, calendar_date.day, tzinfo=UTC
            )
            bars.append(
                Bar(
                    timestamp=ts,
                    open=Decimal(str(row["Open"])),
                    high=Decimal(str(row["High"])),
                    low=Decimal(str(row["Low"])),
                    close=Decimal(str(row["Close"])),
                    volume=int(row["Volume"]),
                )
            )
        return bars
