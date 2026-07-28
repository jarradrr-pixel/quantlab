"""Risk engine tests. Pure functions, no network, no database."""

from __future__ import annotations

from decimal import Decimal

from app.brokers.base import BrokerAccount, BrokerPosition
from app.config import Settings
from app.core.risk import assess_order, assess_strategy


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


def _account(**overrides: object) -> BrokerAccount:
    base = {
        "account_id": "a",
        "status": "ACTIVE",
        "currency": "USD",
        "cash": Decimal("100000"),
        "portfolio_value": Decimal("100000"),
        "buying_power": Decimal("100000"),
        "pattern_day_trader": False,
        "trading_blocked": False,
        "is_paper": True,
        "raw": {},
    }
    base.update(overrides)
    return BrokerAccount(**base)  # type: ignore[arg-type]


# --- assess_strategy ------------------------------------------------------


def test_strategy_approved_within_allowlists() -> None:
    verdict = assess_strategy(
        symbol="SPY", timeframe="1Day", fast_window=20, slow_window=100, settings=_settings()
    )
    assert verdict.approved is True


def test_strategy_refused_for_a_disallowed_symbol() -> None:
    verdict = assess_strategy(
        symbol="TSLA", timeframe="1Day", fast_window=20, slow_window=100, settings=_settings()
    )
    assert verdict.approved is False
    assert any("TSLA" in r for r in verdict.reasons)


def test_strategy_refused_for_a_disallowed_timeframe() -> None:
    verdict = assess_strategy(
        symbol="SPY", timeframe="1Hour", fast_window=20, slow_window=100, settings=_settings()
    )
    assert verdict.approved is False


def test_strategy_refused_when_fast_window_not_less_than_slow() -> None:
    verdict = assess_strategy(
        symbol="SPY", timeframe="1Day", fast_window=100, slow_window=20, settings=_settings()
    )
    assert verdict.approved is False


# --- assess_order -----------------------------------------------------------


def test_order_approved_within_all_limits() -> None:
    verdict = assess_order(
        symbol="SPY",
        side="buy",
        qty=Decimal("1"),
        price=Decimal("500"),
        account=_account(),
        positions=[],
        orders_today=0,
        settings=_settings(),
    )
    assert verdict.approved is True


def test_order_refused_for_a_disallowed_symbol() -> None:
    verdict = assess_order(
        symbol="TSLA",
        side="buy",
        qty=Decimal("1"),
        price=Decimal("10"),
        account=_account(),
        positions=[],
        orders_today=0,
        settings=_settings(),
    )
    assert verdict.approved is False


def test_order_refused_when_exceeding_max_position_percentage() -> None:
    verdict = assess_order(
        symbol="SPY",
        side="buy",
        qty=Decimal("100"),
        price=Decimal("500"),
        account=_account(),
        positions=[],
        orders_today=0,
        settings=_settings(),
    )
    assert verdict.approved is False
    assert any("max_position_percentage" in r for r in verdict.reasons)


def test_order_refused_at_max_orders_per_day() -> None:
    verdict = assess_order(
        symbol="SPY",
        side="buy",
        qty=Decimal("1"),
        price=Decimal("500"),
        account=_account(),
        positions=[],
        orders_today=2,
        settings=_settings(),
    )
    assert verdict.approved is False
    assert any("max_orders_per_day" in r for r in verdict.reasons)


def test_order_refused_opening_a_new_symbol_at_max_open_positions() -> None:
    existing = BrokerPosition(
        symbol="SPY",
        qty=Decimal("1"),
        side="long",
        market_value=Decimal("500"),
        avg_entry_price=Decimal("500"),
    )
    verdict = assess_order(
        symbol="QQQ",
        side="buy",
        qty=Decimal("1"),
        price=Decimal("400"),
        account=_account(),
        positions=[existing],
        orders_today=0,
        settings=_settings(allowed_symbols=["SPY", "QQQ"]),
    )
    assert verdict.approved is False
    assert any("max_open_positions" in r for r in verdict.reasons)


def test_short_sale_refused_without_allow_shorting() -> None:
    verdict = assess_order(
        symbol="SPY",
        side="sell",
        qty=Decimal("1"),
        price=Decimal("500"),
        account=_account(),
        positions=[],
        orders_today=0,
        settings=_settings(),
    )
    assert verdict.approved is False
    assert any("shorting is disabled" in r for r in verdict.reasons)


def test_sell_within_an_existing_long_position_is_approved() -> None:
    existing = BrokerPosition(
        symbol="SPY",
        qty=Decimal("5"),
        side="long",
        market_value=Decimal("2500"),
        avg_entry_price=Decimal("500"),
    )
    verdict = assess_order(
        symbol="SPY",
        side="sell",
        qty=Decimal("1"),
        price=Decimal("500"),
        account=_account(portfolio_value=Decimal("100000")),
        positions=[existing],
        orders_today=0,
        settings=_settings(),
    )
    assert verdict.approved is True


def test_order_refused_exceeding_buying_power_without_leverage() -> None:
    verdict = assess_order(
        symbol="SPY",
        side="buy",
        qty=Decimal("1000"),
        price=Decimal("500"),
        account=_account(
            cash=Decimal("100"),
            buying_power=Decimal("100"),
            portfolio_value=Decimal("1000000"),
        ),
        positions=[],
        orders_today=0,
        settings=_settings(max_position_percentage=100.0, max_total_exposure_percentage=100.0),
    )
    assert verdict.approved is False
    assert any("buying power" in r for r in verdict.reasons)
