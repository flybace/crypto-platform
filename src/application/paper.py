"""Deterministic L2 paper broker with no real execution side effects."""

from datetime import datetime
from decimal import Decimal

from domain.market import OrderBookSnapshot
from domain.paper import PaperAccount, PaperFill, PaperOrder, PaperOrderStatus
from domain.trading import OrderIntent, OrderType, Side


class PaperBroker:
    def __init__(
        self,
        account: PaperAccount,
        fee_bps: Decimal = Decimal("0"),
        *,
        order_counter: int = 0,
        order_id_prefix: str = "paper-",
    ) -> None:
        if fee_bps < 0:
            raise ValueError("fee_bps must not be negative")
        if order_counter < 0:
            raise ValueError("order_counter must not be negative")
        if not str(order_id_prefix).strip():
            raise ValueError("order_id_prefix must not be empty")
        self.account = account
        self._fee_bps = Decimal(str(fee_bps))
        self._counter = int(order_counter)
        self._order_id_prefix = str(order_id_prefix).strip()
        self._orders: dict[str, PaperOrder] = {}

    @property
    def order_counter(self) -> int:
        return self._counter

    def submit(self, intent: OrderIntent, market: OrderBookSnapshot, now: datetime) -> PaperOrder:
        self._counter += 1
        order_id = f"{self._order_id_prefix}{self._counter:06d}"
        if market.instrument != intent.instrument:
            return self._store(PaperOrder(order_id, intent, PaperOrderStatus.REJECTED, Decimal("0"), intent.quantity, reason="MARKET_INSTRUMENT_MISMATCH"))
        if intent.order_type is OrderType.MARKET:
            return self._store(PaperOrder(order_id, intent, PaperOrderStatus.REJECTED, Decimal("0"), intent.quantity, reason="MARKET_ORDER_UNSUPPORTED"))

        levels = market.bids if intent.side is Side.SELL else market.asks
        if not levels:
            status = PaperOrderStatus.CANCELED if intent.order_type is OrderType.LIMIT_IOC else PaperOrderStatus.OPEN
            return self._store(PaperOrder(order_id, intent, status, Decimal("0"), intent.quantity, reason="NO_LIQUIDITY"))
        first_crossing = levels[0].price >= intent.limit_price if intent.side is Side.SELL else levels[0].price <= intent.limit_price
        if not first_crossing:
            status = PaperOrderStatus.CANCELED if intent.order_type is OrderType.LIMIT_IOC else PaperOrderStatus.OPEN
            return self._store(PaperOrder(order_id, intent, status, Decimal("0"), intent.quantity, reason="LIMIT_NOT_MARKETABLE"))

        remaining = intent.quantity
        fills: list[PaperFill] = []
        for level in levels:
            crossing = level.price >= intent.limit_price if intent.side is Side.SELL else level.price <= intent.limit_price
            if not crossing or remaining <= 0:
                break
            quantity = min(remaining, level.quantity)
            if intent.side is Side.SELL:
                quantity = min(quantity, self.account.available(intent.instrument.base_asset))
            else:
                affordable = self.account.available(intent.instrument.quote_asset) / (
                    level.price * (Decimal("1") + self._fee_bps / Decimal("10000"))
                )
                quantity = min(quantity, affordable)
            if quantity > 0:
                quantity = intent.instrument.normalize_quantity(quantity)
            if quantity < intent.instrument.min_quantity:
                break
            fill = PaperFill(
                fill_id=f"{order_id}-fill-{len(fills) + 1:03d}",
                order_id=order_id,
                side=intent.side,
                price=level.price,
                quantity=quantity,
                fee_amount=level.price * quantity * self._fee_bps / Decimal("10000"),
                fee_asset=intent.instrument.quote_asset,
                filled_at=now,
            )
            try:
                self.account.apply_fill(intent, fill)
            except ValueError:
                break
            fills.append(fill)
            remaining -= quantity

        if not fills:
            return self._store(PaperOrder(order_id, intent, PaperOrderStatus.REJECTED, Decimal("0"), intent.quantity, reason="INSUFFICIENT_BALANCE"))
        filled = intent.quantity - remaining
        status = PaperOrderStatus.FILLED if remaining == 0 else PaperOrderStatus.PARTIALLY_FILLED
        return self._store(PaperOrder(order_id, intent, status, filled, remaining, fills=tuple(fills)))

    def get(self, order_id: str) -> PaperOrder:
        try:
            return self._orders[order_id]
        except KeyError as error:
            raise KeyError(f"unknown paper order: {order_id}") from error

    def all(self) -> tuple[PaperOrder, ...]:
        return tuple(self._orders[key] for key in sorted(self._orders))

    def _store(self, order: PaperOrder) -> PaperOrder:
        self._orders[order.order_id] = order
        return order
