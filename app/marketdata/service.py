"""Cache-first market data access.

Simplification, documented rather than hidden: caching here is
range-complete, not gap-aware. If any cached bar for (symbol, timeframe)
already spans the full requested [start, end], the cache is used as-is;
otherwise the whole requested range is re-fetched from the provider and
upserted. There is no per-day gap detection -- fine for an MVP where a
project typically backtests the same symbol/window repeatedly, but a future
phase fetching many overlapping ranges would want real gap-filling.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import MarketBar
from app.marketdata.base import Bar, MarketDataProvider

DEFAULT_TIMEFRAME = "1Day"


class MarketDataService:
    def __init__(self, db: Session, provider: MarketDataProvider) -> None:
        self._db = db
        self._provider = provider

    def get_or_fetch_bars(
        self, symbol: str, start: date, end: date, *, timeframe: str = DEFAULT_TIMEFRAME
    ) -> list[Bar]:
        cached = self._cached_bars(symbol, timeframe, start, end)
        starts_early_enough = bool(cached) and cached[0].timestamp.date() <= start
        ends_late_enough = bool(cached) and cached[-1].timestamp.date() >= end
        if starts_early_enough and ends_late_enough:
            return cached

        fetched = self._provider.get_bars(symbol, start, end)
        self._upsert(symbol, timeframe, fetched)
        return self._cached_bars(symbol, timeframe, start, end)

    def _cached_bars(self, symbol: str, timeframe: str, start: date, end: date) -> list[Bar]:
        rows = self._db.scalars(
            select(MarketBar)
            .where(MarketBar.symbol == symbol, MarketBar.timeframe == timeframe)
            .order_by(MarketBar.timestamp)
        ).all()
        return [
            Bar(
                timestamp=row.timestamp,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume,
            )
            for row in rows
            if start <= row.timestamp.date() <= end
        ]

    def _upsert(self, symbol: str, timeframe: str, bars: list[Bar]) -> None:
        # Compared naive: SQLite drops tzinfo on round-trip (see app.core.audit's
        # identical note), and every Bar.timestamp is UTC midnight anyway, so
        # stripping tzinfo before comparing is lossless here.
        existing = {
            row.timestamp.replace(tzinfo=None)
            for row in self._db.scalars(
                select(MarketBar).where(
                    MarketBar.symbol == symbol, MarketBar.timeframe == timeframe
                )
            )
        }
        for bar in bars:
            if bar.timestamp.replace(tzinfo=None) in existing:
                continue
            self._db.add(
                MarketBar(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=bar.timestamp,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    source="yfinance",
                )
            )
        self._db.flush()
