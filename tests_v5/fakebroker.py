"""Deterministic in-memory implementation of the v5 broker contract."""

from __future__ import annotations

from collections import deque
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN

from v5.broker import (
    Account,
    AmbiguousSubmit,
    Bar,
    BrokerClock,
    BrokerRejected,
    BrokerUnavailable,
    Order,
    Position,
    Trade,
    is_terminal,
    validate_order_request,
)


NINE_PLACES = Decimal("0.000000001")


def invisible(n):
    return ("invisible", n)


def partial(fraction):
    return ("partial", fraction)


def reject(status, code, message):
    return ("reject", status, code, message)


def _decimal(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _floor9(value: Decimal) -> Decimal:
    return value.quantize(NINE_PLACES, rounding=ROUND_DOWN)


def _text(value: Decimal) -> str:
    result = format(value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return result or "0"


class FakeBroker:
    def __init__(
        self,
        cash=10000,
        positions=None,
        prices=None,
        last_equity=None,
        clock=None,
        calendar=None,
        adverse_bps=0,
    ):
        self.cash = _decimal(cash)
        self.positions = {symbol: _floor9(_decimal(qty)) for symbol, qty in (positions or {}).items()}
        self.prices = {symbol: _decimal(price) for symbol, price in (prices or {}).items()}
        self.last_equity = _decimal(last_equity) if last_equity is not None else self.equity
        self.balance_asof = None
        now = datetime(2026, 11, 16, 16, 10, tzinfo=timezone.utc)
        self.clock = clock or BrokerClock(now, True, now + timedelta(hours=22), now + timedelta(hours=5))
        self.calendar = list(calendar or [])
        self.adverse_bps = _decimal(adverse_bps)
        self.orders: list[Order] = []
        self.faults = deque()
        self.calls: list[tuple[str, dict]] = []
        self.daily_bars: dict[str, list[Bar]] = {}
        self.minute_bars: dict[str, list[Bar]] = {}
        self.latest_trades: dict[str, Trade | None] = {}
        self._fill_modes: dict[str, tuple] = {}
        self._invisible: dict[str, int] = {}
        self._counter = 0
        self._order_indexes: dict[str, int] = {}
        self._client_indexes: dict[str, int] = {}
        self._pending_ids: set[str] = set()
        self._open_ids: set[str] = set()

    @property
    def equity(self) -> Decimal:
        return self.cash + sum(
            (qty * self.prices.get(symbol, Decimal("0")) for symbol, qty in self.positions.items()),
            Decimal("0"),
        )

    def set_price(self, symbol, price) -> None:
        self.prices[symbol] = _decimal(price)

    def set_clock(self, clock: BrokerClock) -> None:
        self.clock = clock

    def script(self, *faults) -> None:
        self.faults.extend(faults)

    queue_fault = script

    def _record(self, method, **arguments):
        self.calls.append((method, arguments))

    def _get_fault(self):
        if not self.faults:
            return None
        fault = self.faults[0]
        name = fault if isinstance(fault, str) else fault[0]
        if name not in {"http_429", "http_5xx"}:
            return None
        self.faults.popleft()
        status = 429 if name == "http_429" else 503
        raise BrokerUnavailable(f"broker unavailable (HTTP {status})")

    def _advance(self):
        for order_id in tuple(self._pending_ids):
            index = self._find_index(order_id)
            if index is None:
                self._pending_ids.discard(order_id)
                continue
            order = self.orders[index]
            if order.status != "pending_new":
                self._pending_ids.discard(order_id)
                continue
            mode = self._fill_modes.get(order.id, ("fill",))
            if mode[0] == "never_fill":
                continue
            fraction = _decimal(mode[1]) if mode[0] == "partial" else Decimal("1")
            self._fill(index, order, fraction, mode[0] == "partial")

    def _begin_get(self, method, **arguments):
        self._record(method, **arguments)
        self._get_fault()
        self._advance()

    def _price_for_fill(self, order: Order) -> Decimal:
        base = self.prices[order.symbol]
        move = self.adverse_bps / Decimal("10000")
        return base * (Decimal("1") + move if order.side == "buy" else Decimal("1") - move)

    def _fill(self, index: int, order: Order, fraction: Decimal, remains_open: bool):
        price = self._price_for_fill(order)
        requested = (
            _floor9(_decimal(order.notional) / price)
            if order.notional is not None
            else _decimal(order.qty)
        )
        filled = _floor9(requested * fraction)
        value = filled * price
        current = self.positions.get(order.symbol, Decimal("0"))
        if order.side == "buy":
            self.cash -= value
            self.positions[order.symbol] = current + filled
        else:
            self.cash += value
            self.positions[order.symbol] = current - filled
        if self.positions.get(order.symbol, Decimal("0")) == 0:
            self.positions.pop(order.symbol, None)
        status = "partially_filled" if remains_open else "filled"
        now = self.clock.timestamp
        self.orders[index] = replace(
            order,
            filled_qty=_text(filled),
            filled_avg_price=float(price),
            status=status,
            filled_at=None if remains_open else now,
        )
        self._pending_ids.discard(order.id)
        if not remains_open:
            self._open_ids.discard(order.id)

    def _find_index(self, order_id):
        cached = self._order_indexes.get(order_id)
        if cached is not None and cached < len(self.orders) and self.orders[cached].id == order_id:
            return cached
        for index, order in enumerate(self.orders):
            if order.id == order_id:
                self._order_indexes[order_id] = index
                return index
        return None

    def _visible(self, order: Order) -> bool:
        remaining = self._invisible.get(order.id, 0)
        if remaining <= 0:
            return True
        self._invisible[order.id] = remaining - 1
        return False

    def get_account(self):
        self._begin_get("get_account")
        return Account(
            status="ACTIVE",
            equity=float(self.equity),
            last_equity=float(self.last_equity),
            cash=float(self.cash),
            buying_power=float(self.cash),
            trading_blocked=False,
            account_blocked=False,
            balance_asof=self.balance_asof,
        )

    def _available(self, symbol) -> Decimal:
        available = self.positions.get(symbol, Decimal("0"))
        for order_id in tuple(self._open_ids):
            index = self._find_index(order_id)
            if index is None:
                continue
            order = self.orders[index]
            if order.symbol == symbol and order.side == "sell" and not is_terminal(order.status):
                requested = _decimal(order.qty) if order.qty else _floor9(_decimal(order.notional) / self.prices[symbol])
                available -= max(Decimal("0"), requested - _decimal(order.filled_qty))
        return max(Decimal("0"), available)

    def get_positions(self):
        self._begin_get("get_positions")
        result = []
        for symbol in sorted(self.positions):
            qty = self.positions[symbol]
            if qty == 0:
                continue
            price = self.prices[symbol]
            result.append(
                Position(symbol, _text(qty), _text(self._available(symbol)), float(qty * price), float(price), "us_equity")
            )
        return result

    def get_open_orders(self):
        self._begin_get("get_open_orders")
        found = []
        for order_id in tuple(self._open_ids):
            index = self._find_index(order_id)
            if index is not None and not is_terminal(self.orders[index].status):
                found.append(index)
        return [self.orders[index] for index in sorted(found)]

    def list_orders(self, after, symbol=None):
        self._begin_get("list_orders", after=after, symbol=symbol)
        after_dt = after if isinstance(after, datetime) else datetime.fromisoformat(str(after).replace("Z", "+00:00"))
        result = [
            order
            for order in self.orders
            if order.submitted_at is not None
            and order.submitted_at >= after_dt
            and (symbol is None or order.symbol == symbol)
        ]
        unique = {order.id: order for order in result}
        return list(unique.values()), len(result) < 500

    def submit_order(self, symbol, side, client_order_id, notional=None, qty=None):
        # Same validator as the real gateway, first: a malformed request never reaches the broker.
        validate_order_request(symbol, side, client_order_id, notional, qty)
        self._record(
            "submit_order",
            symbol=symbol,
            side=side,
            client_order_id=client_order_id,
            notional=notional,
            qty=qty,
        )
        self._advance()
        known = client_order_id in self._client_indexes
        if not known and len(self._client_indexes) != len(self.orders):
            # Only when a test appended to `orders` behind the indexes: fall back to the full scan.
            known = any(order.client_order_id == client_order_id for order in self.orders)
        if known:
            raise BrokerRejected(422, 40010001, "client_order_id must be unique", {"code": 40010001, "message": "client_order_id must be unique"})

        fault = self.faults[0] if self.faults else None
        fault_name = fault if isinstance(fault, str) else (fault[0] if fault else None)
        submit_faults = {"timeout_before_accept", "timeout_after_accept", "invisible", "partial", "never_fill", "reject"}
        if fault_name in submit_faults:
            self.faults.popleft()
        else:
            fault = None
            fault_name = None
        if fault_name == "timeout_before_accept":
            raise AmbiguousSubmit("order submission outcome is unknown")
        if fault_name == "reject":
            _, status, code, message = fault
            raise BrokerRejected(status, code, message, {"code": code, "message": message})

        amount = _decimal(notional) if notional is not None else None
        if amount is not None and amount < Decimal("1.00"):
            message = "notional amount must be >= 1.00"
            raise BrokerRejected(422, 42210000, message, {"code": 42210000, "message": message})
        if symbol not in self.prices or self.prices[symbol] <= 0:
            raise ValueError(f"no positive price configured for {symbol}")
        price = self.prices[symbol]
        requested_qty = _floor9(amount / price) if amount is not None else _decimal(qty)
        estimated = amount if amount is not None else requested_qty * price
        if side == "buy" and estimated > self.cash:
            message = "insufficient buying power"
            raise BrokerRejected(403, 40310000, message, {"code": 40310000, "message": message})
        if side == "sell" and requested_qty > self._available(symbol):
            message = "insufficient qty available for order"
            raise BrokerRejected(403, 40310000, message, {"code": 40310000, "message": message})

        self._counter += 1
        order = Order(
            id=f"fake-{self._counter}",
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            type="market",
            time_in_force="day",
            qty=None if qty is None else _text(_decimal(qty)),
            notional=None if amount is None else _text(amount),
            filled_qty="0",
            filled_avg_price=None,
            status="pending_new",
            submitted_at=self.clock.timestamp,
            filled_at=None,
        )
        self.orders.append(order)
        self._order_indexes[order.id] = len(self.orders) - 1
        self._client_indexes[client_order_id] = len(self.orders) - 1
        self._pending_ids.add(order.id)
        self._open_ids.add(order.id)
        if fault_name == "invisible":
            self._invisible[order.id] = int(fault[1])
        elif fault_name in {"partial", "never_fill"}:
            self._fill_modes[order.id] = fault if isinstance(fault, tuple) else (fault_name,)
        if fault_name == "timeout_after_accept":
            raise AmbiguousSubmit("order submission outcome is unknown")
        return order

    def cancel_order(self, order_id):
        self._record("cancel_order", order_id=order_id)
        self._advance()
        index = self._find_index(order_id)
        if index is None:
            raise BrokerRejected(404, 40410000, "order not found", {"code": 40410000, "message": "order not found"})
        order = self.orders[index]
        if not is_terminal(order.status):
            self.orders[index] = replace(order, status="canceled")
            self._pending_ids.discard(order.id)
            self._open_ids.discard(order.id)

    def get_order(self, order_id):
        self._begin_get("get_order", order_id=order_id)
        index = self._find_index(order_id)
        if index is None or not self._visible(self.orders[index]):
            raise BrokerRejected(404, 40410000, "order not found", {"code": 40410000, "message": "order not found"})
        return self.orders[index]

    def get_order_by_client_id(self, client_order_id):
        self._begin_get("get_order_by_client_id", client_order_id=client_order_id)
        cached = self._client_indexes.get(client_order_id)
        if cached is not None and cached < len(self.orders):
            order = self.orders[cached]
            if order.client_order_id == client_order_id:
                return order if self._visible(order) else None
        for order in self.orders:
            if order.client_order_id == client_order_id:
                return order if self._visible(order) else None
        return None

    def get_clock(self):
        self._begin_get("get_clock")
        return self.clock

    def get_calendar(self, start, end):
        self._begin_get("get_calendar", start=start, end=end)
        first = start if isinstance(start, date) else date.fromisoformat(str(start))
        last = end if isinstance(end, date) else date.fromisoformat(str(end))
        return [row for row in self.calendar if first <= date.fromisoformat(row["date"]) <= last]

    def get_daily_bars(self, symbol, start, adjustment="all", feed="sip"):
        self._begin_get("get_daily_bars", symbol=symbol, start=start, adjustment=adjustment, feed=feed)
        return list(self.daily_bars.get(symbol, []))

    def get_minute_bars(self, symbol, start, feed="iex"):
        self._begin_get("get_minute_bars", symbol=symbol, start=start, feed=feed)
        return list(self.minute_bars.get(symbol, []))

    def get_latest_trade(self, symbol, feed="iex"):
        self._begin_get("get_latest_trade", symbol=symbol, feed=feed)
        return self.latest_trades.get(symbol)
