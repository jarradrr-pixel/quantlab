"""Deterministic paper-trading simulation. No network.

Unlike ``AlpacaPaperBroker``, ``MockBroker`` holds no external state of its
own -- it reads and writes the account-wide ``mock_fills`` table directly,
exactly as ``AlpacaPaperBroker`` reads and writes Alpaca's own books. The
project-scoped ``Order`` table is always QuantLab's own mirror, written by
the route layer after a successful ``submit_order`` call, never by an
adapter -- this keeps both broker implementations interchangeable behind the
same calling contract.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brokers.base import (
    BrokerAccount,
    BrokerAdapter,
    BrokerOrder,
    BrokerOrderRejectedError,
    BrokerPosition,
)
from app.db.base import utcnow
from app.db.models import MarketBar, MockFill

STARTING_CASH: Decimal = Decimal("100000")


class MockBroker(BrokerAdapter):
    """Fills orders synchronously against the latest cached market price."""

    def __init__(self, db: Session, *, starting_cash: Decimal = STARTING_CASH) -> None:
        self._db = db
        self._starting_cash = starting_cash

    def _fills(self) -> list[MockFill]:
        return list(self._db.scalars(select(MockFill).order_by(MockFill.filled_at)))

    def _latest_price(self, symbol: str) -> Decimal | None:
        bar = self._db.scalars(
            select(MarketBar)
            .where(MarketBar.symbol == symbol)
            .order_by(MarketBar.timestamp.desc())
            .limit(1)
        ).first()
        return bar.close if bar else None

    def _net_positions(self, fills: list[MockFill]) -> dict[str, Decimal]:
        net: dict[str, Decimal] = {}
        for fill in fills:
            signed = fill.qty if fill.side == "buy" else -fill.qty
            net[fill.symbol] = net.get(fill.symbol, Decimal(0)) + signed
        return net

    def _avg_entry_price(self, fills: list[MockFill], symbol: str) -> Decimal:
        buys = [f for f in fills if f.symbol == symbol and f.side == "buy"]
        if not buys:
            return Decimal(0)
        total_qty = sum((f.qty for f in buys), Decimal(0))
        total_cost = sum((f.qty * f.price for f in buys), Decimal(0))
        return total_cost / total_qty if total_qty else Decimal(0)

    def get_account(self) -> BrokerAccount:
        fills = self._fills()
        cash = self._starting_cash
        for fill in fills:
            proceeds = fill.qty * fill.price
            cash = cash - proceeds if fill.side == "buy" else cash + proceeds

        portfolio_value = cash
        for symbol, qty in self._net_positions(fills).items():
            if qty == 0:
                continue
            price = self._latest_price(symbol) or self._avg_entry_price(fills, symbol)
            portfolio_value += qty * price

        return BrokerAccount(
            account_id="mock-account",
            status="ACTIVE",
            currency="USD",
            cash=cash,
            portfolio_value=portfolio_value,
            buying_power=cash,
            pattern_day_trader=False,
            trading_blocked=False,
            is_paper=True,
            raw={"backend": "mock"},
        )

    def list_positions(self) -> list[BrokerPosition]:
        fills = self._fills()
        positions: list[BrokerPosition] = []
        for symbol, qty in self._net_positions(fills).items():
            if qty == 0:
                continue
            avg_entry_price = self._avg_entry_price(fills, symbol)
            price = self._latest_price(symbol) or avg_entry_price
            positions.append(
                BrokerPosition(
                    symbol=symbol,
                    qty=qty,
                    side="long" if qty > 0 else "short",
                    market_value=qty * price,
                    avg_entry_price=avg_entry_price,
                )
            )
        return positions

    def list_open_orders(self) -> list[BrokerOrder]:
        """Fills happen synchronously in ``submit_order`` -- there is never
        an open (unfilled) mock order."""
        return []

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
        price = self._latest_price(symbol)
        if price is None:
            raise BrokerOrderRejectedError(
                f"No cached market data for {symbol}; run a backtest or fetch data "
                "for this symbol first."
            )

        filled_at: datetime = utcnow()
        fill = MockFill(symbol=symbol, side=side, qty=qty, price=price, filled_at=filled_at)
        self._db.add(fill)
        self._db.flush()

        return BrokerOrder(
            order_id=fill.id,
            symbol=symbol,
            side=side,
            qty=qty,
            order_type=order_type,
            time_in_force=time_in_force,
            status="filled",
            submitted_at=filled_at,
        )
