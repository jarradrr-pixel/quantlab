"""MockBroker tests against a real (temporary) database session."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.brokers.base import BrokerOrderRejectedError
from app.brokers.mock import MockBroker
from app.db.models import MarketBar


def _cache_bar(db: Session, symbol: str, price: str, *, day: int = 1) -> None:
    db.add(
        MarketBar(
            symbol=symbol,
            timeframe="1Day",
            timestamp=datetime(2024, 1, day, tzinfo=UTC),
            open=Decimal(price),
            high=Decimal(price),
            low=Decimal(price),
            close=Decimal(price),
            volume=1000,
            source="yfinance",
        )
    )
    db.commit()


def test_submit_order_rejected_without_cached_market_data(db: Session) -> None:
    broker = MockBroker(db)
    with pytest.raises(BrokerOrderRejectedError, match="SPY"):
        broker.submit_order(
            symbol="SPY",
            qty=Decimal("1"),
            side="buy",
            order_type="market",
            time_in_force="day",
        )


def test_submit_order_rejects_an_invalid_side(db: Session) -> None:
    _cache_bar(db, "SPY", "500")
    broker = MockBroker(db)
    with pytest.raises(ValueError, match="side must be"):
        broker.submit_order(
            symbol="SPY",
            qty=Decimal("1"),
            side="sideways",
            order_type="market",
            time_in_force="day",
        )


def test_get_account_starts_with_the_default_cash(db: Session) -> None:
    broker = MockBroker(db)
    account = broker.get_account()
    assert account.cash == Decimal("100000")
    assert account.portfolio_value == Decimal("100000")
    assert account.is_paper is True


def test_buy_reduces_cash_and_creates_a_position(db: Session) -> None:
    _cache_bar(db, "SPY", "500")
    broker = MockBroker(db)

    order = broker.submit_order(
        symbol="SPY", qty=Decimal("10"), side="buy", order_type="market", time_in_force="day"
    )
    db.commit()

    assert order.status == "filled"
    account = broker.get_account()
    assert account.cash == Decimal("95000")
    assert account.portfolio_value == Decimal("100000")  # cash -> equal-value position

    positions = broker.list_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "SPY"
    assert positions[0].qty == Decimal("10")
    assert positions[0].side == "long"


def test_sell_reduces_the_position(db: Session) -> None:
    _cache_bar(db, "SPY", "500")
    broker = MockBroker(db)
    broker.submit_order(
        symbol="SPY", qty=Decimal("10"), side="buy", order_type="market", time_in_force="day"
    )
    broker.submit_order(
        symbol="SPY", qty=Decimal("4"), side="sell", order_type="market", time_in_force="day"
    )
    db.commit()

    positions = broker.list_positions()
    assert len(positions) == 1
    assert positions[0].qty == Decimal("6")


def test_list_open_orders_is_always_empty(db: Session) -> None:
    _cache_bar(db, "SPY", "500")
    broker = MockBroker(db)
    broker.submit_order(
        symbol="SPY", qty=Decimal("1"), side="buy", order_type="market", time_in_force="day"
    )
    assert broker.list_open_orders() == []


def test_portfolio_value_tracks_the_latest_cached_price(db: Session) -> None:
    _cache_bar(db, "SPY", "500")
    broker = MockBroker(db)
    broker.submit_order(
        symbol="SPY", qty=Decimal("10"), side="buy", order_type="market", time_in_force="day"
    )
    db.commit()

    _cache_bar(db, "SPY", "600", day=2)  # price moves up after the fill
    account = broker.get_account()
    # cash 95000 + 10 * 600 = 101000
    assert account.portfolio_value == Decimal("101000")
