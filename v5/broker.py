"""Typed broker contract and the Alpaca HTTP gateway."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Callable, Protocol

import requests

from .clock import _parse_timestamp


class BrokerError(RuntimeError):
    pass


class BrokerUnavailable(BrokerError):
    pass


class AmbiguousSubmit(BrokerUnavailable):
    pass


class BrokerRejected(BrokerError):
    def __init__(self, status: int, code: int | None, message: str, body):
        self.status = status
        self.code = code
        self.message = message
        self.body = body
        super().__init__(f"broker rejected request ({status}, code={code}): {message}")


@dataclass(frozen=True)
class Account:
    status: str
    equity: float
    last_equity: float
    cash: float
    buying_power: float
    trading_blocked: bool
    account_blocked: bool


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: str
    qty_available: str
    market_value: float
    current_price: float
    asset_class: str


@dataclass(frozen=True)
class Order:
    id: str
    client_order_id: str
    symbol: str
    side: str
    type: str
    time_in_force: str
    qty: str | None
    notional: str | None
    filled_qty: str
    filled_avg_price: float | None
    status: str
    submitted_at: datetime | None
    filled_at: datetime | None


@dataclass(frozen=True)
class BrokerClock:
    timestamp: datetime
    is_open: bool
    next_open: datetime
    next_close: datetime


@dataclass(frozen=True)
class Bar:
    t: datetime
    o: float
    h: float
    l: float
    c: float
    v: float


@dataclass(frozen=True)
class Trade:
    t: datetime
    price: float


TERMINAL_STATUSES = {
    "filled",
    "canceled",
    "expired",
    "rejected",
    "replaced",
    "done_for_day",
    "calculated",
}


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATUSES


class Broker(Protocol):
    def get_account(self) -> Account: ...
    def get_positions(self) -> list[Position]: ...
    def get_open_orders(self) -> list[Order]: ...
    def list_orders(self, after, symbol=None) -> tuple[list[Order], bool]: ...
    def submit_order(self, symbol, side, client_order_id, notional=None, qty=None) -> Order: ...
    def cancel_order(self, order_id) -> None: ...
    def get_order(self, order_id) -> Order: ...
    def get_order_by_client_id(self, client_order_id) -> Order | None: ...
    def get_clock(self) -> BrokerClock: ...
    def get_calendar(self, start, end) -> list[dict]: ...
    def get_daily_bars(self, symbol, start, adjustment="all", feed="sip") -> list[Bar]: ...
    def get_minute_bars(self, symbol, start, feed="iex") -> list[Bar]: ...
    def get_latest_trade(self, symbol, feed="iex") -> Trade | None: ...


Transport = Callable[[str, str, dict | None, dict | None, float], tuple[int, str]]
# A submission answered with one of these statuses may still have been accepted.
_UNCERTAIN_SUBMIT_STATUSES = frozenset({408, 425, 429})
# Everything a wrong-shaped answer can raise while it is being read.
_PARSE_ERRORS = (AttributeError, KeyError, TypeError, ValueError, OverflowError)
_MAX_BAR_PAGES = 50
_SYMBOL = re.compile(r"[A-Za-z0-9./-]{1,20}")
_CLIENT_ORDER_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_NOTIONAL = re.compile(r"[0-9]+(\.[0-9]{1,2})?")
_QTY = re.compile(r"[0-9]+(\.[0-9]{1,9})?")
_ORDER_ID = re.compile(r"[A-Za-z0-9-]{1,64}")
_PATH_SYMBOL = re.compile(r"[A-Za-z0-9.-]{1,20}")


def _optional_datetime(value) -> datetime | None:
    return _parse_timestamp(value) if value else None


def _iso(value) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _required_text(row, key) -> str:
    """A field the answer must carry: absent or null is a wrong shape, never the text "None"."""
    value = row[key]
    if value is None:
        raise ValueError(f"{key} is null")
    return str(value)


def _required_bool(row, key) -> bool:
    """A flag the answer must carry as a real boolean: null or text must never read as False."""
    value = row[key]
    if not isinstance(value, bool):
        raise ValueError(f"{key} is not a boolean")
    return value


def _path_part(value, pattern, name) -> str:
    """A value placed in the URL path: anything that could reshape the path is refused before any call."""
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{name} cannot be used in a request path")
    return value


def validate_order_request(symbol, side, client_order_id, notional, qty) -> None:
    """Refuse a malformed order request with ValueError before anything is sent or created."""
    if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
        raise ValueError("symbol must be 1 to 20 characters of letters, digits, '.', '/' or '-'")
    if not isinstance(side, str) or side not in ("buy", "sell"):
        raise ValueError("side must be 'buy' or 'sell'")
    if not isinstance(client_order_id, str) or not _CLIENT_ORDER_ID.fullmatch(client_order_id):
        raise ValueError(
            "client_order_id must be 1 to 128 characters of letters, digits, '.', '_' or '-'"
        )
    if (notional is None) == (qty is None):
        raise ValueError("exactly one of notional and qty is required")
    value, pattern, places, name = (
        (notional, _NOTIONAL, 2, "notional") if notional is not None else (qty, _QTY, 9, "qty")
    )
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{name} must be a plain decimal string with at most {places} decimal places")
    if Decimal(value) <= 0:
        raise ValueError(f"{name} must be greater than zero")


class AlpacaBroker:
    def __init__(self, settings, transport: Transport | None = None, sleep=time.sleep):
        self.settings = settings
        self.sleep = sleep
        self.transport = transport or self._default_transport

    def _default_transport(self, method, url, params, json_body, timeout):
        response = requests.request(
            method,
            url,
            params=params,
            json=json_body,
            timeout=timeout,
            # requests would replay a redirected POST as a GET: never follow a redirect.
            allow_redirects=False,
            headers={
                "APCA-API-KEY-ID": self.settings.api_key,
                "APCA-API-SECRET-KEY": self.settings.api_secret,
            },
        )
        return response.status_code, response.text

    @staticmethod
    def _decode(text: str):
        try:
            return json.loads(text)
        except (TypeError, ValueError, RecursionError) as exc:
            raise BrokerError("broker returned invalid JSON") from exc

    @staticmethod
    def _rejection(status: int, text: str) -> BrokerRejected:
        try:
            body = json.loads(text)
        except (TypeError, ValueError, RecursionError):
            body = text
        code = body.get("code") if isinstance(body, dict) else None
        message = body.get("message", "broker request rejected") if isinstance(body, dict) else "broker request rejected"
        if not isinstance(code, int):
            code = None
        return BrokerRejected(status, code, str(message), body)

    def _request(self, method, base_url, path, params=None, json_body=None, expect_json=True):
        url = base_url.rstrip("/") + path
        attempts = 5 if method == "GET" else 1
        pauses = (0.5, 1.0, 2.0, 4.0)
        for attempt in range(attempts):
            try:
                status, text = self.transport(method, url, params, json_body, 15)
                if isinstance(status, bool) or not isinstance(status, int):
                    raise TypeError("transport returned a non-integer status")
            except Exception as exc:
                # Any transport failure counts. Only its class is reported, never its text.
                reason = type(exc).__name__
                if method == "POST":
                    raise AmbiguousSubmit(f"order submission outcome is unknown ({reason})") from exc
                if method != "GET" or attempt == attempts - 1:
                    raise BrokerUnavailable(f"broker transport is unavailable ({reason})") from exc
                self.sleep(pauses[attempt])
                continue

            if method == "POST":
                return self._submitted(status, text)
            if status == 429 or 500 <= status <= 599:
                if method != "GET" or attempt == attempts - 1:
                    raise BrokerUnavailable(f"broker unavailable (HTTP {status})")
                self.sleep(pauses[attempt])
                continue
            if 400 <= status <= 499:
                raise self._rejection(status, text)
            if not 200 <= status <= 299:
                raise BrokerError(f"unexpected broker HTTP status {status}")
            if not expect_json:
                return None
            return self._decode(text)
        raise BrokerUnavailable("broker is unavailable")

    def _submitted(self, status, text) -> Order:
        """Read the answer to the single POST: an order, a definite refusal, or an unknown outcome."""
        if 400 <= status <= 499 and status not in _UNCERTAIN_SUBMIT_STATUSES:
            raise self._rejection(status, text)
        if not 200 <= status <= 299:
            raise AmbiguousSubmit(f"order submission outcome is unknown (HTTP {status})")
        try:
            return self._order(self._decode(text))
        except BrokerError as exc:
            # Accepted, but the answer is not an order we can read: the order may exist.
            raise AmbiguousSubmit(
                f"order submission outcome is unknown (HTTP {status}, answer is not an order)"
            ) from exc

    @staticmethod
    def _account(row) -> Account:
        try:
            return Account(
                status=_required_text(row, "status"),
                equity=float(row["equity"]),
                last_equity=float(row["last_equity"]),
                cash=float(row["cash"]),
                buying_power=float(row["buying_power"]),
                trading_blocked=_required_bool(row, "trading_blocked"),
                account_blocked=_required_bool(row, "account_blocked"),
            )
        except _PARSE_ERRORS as exc:
            raise BrokerError("invalid account response") from exc

    @staticmethod
    def _position(row) -> Position:
        try:
            return Position(
                symbol=_required_text(row, "symbol"),
                qty=_required_text(row, "qty"),
                qty_available=_required_text(row, "qty_available"),
                market_value=float(row["market_value"]),
                current_price=float(row["current_price"]),
                asset_class=_required_text(row, "asset_class"),
            )
        except _PARSE_ERRORS as exc:
            raise BrokerError("invalid position response") from exc

    @staticmethod
    def _order(row) -> Order:
        try:
            average = row.get("filled_avg_price")
            filled = row.get("filled_qty")
            return Order(
                id=_required_text(row, "id"),
                client_order_id=_required_text(row, "client_order_id"),
                symbol=_required_text(row, "symbol"),
                side=_required_text(row, "side"),
                type=_required_text(row, "order_type" if row.get("type") is None else "type"),
                time_in_force=_required_text(row, "time_in_force"),
                qty=None if row.get("qty") is None else str(row["qty"]),
                notional=None if row.get("notional") is None else str(row["notional"]),
                # Nothing filled yet may arrive as null or be absent: that is "0", never the text "None".
                filled_qty="0" if filled is None else str(filled),
                filled_avg_price=None if average is None else float(average),
                status=_required_text(row, "status"),
                submitted_at=_optional_datetime(row.get("submitted_at")),
                filled_at=_optional_datetime(row.get("filled_at")),
            )
        except _PARSE_ERRORS as exc:
            raise BrokerError("invalid order response") from exc

    @staticmethod
    def _bar(row) -> Bar:
        try:
            return Bar(
                t=_parse_timestamp(row["t"]),
                o=float(row["o"]),
                h=float(row["h"]),
                l=float(row["l"]),
                c=float(row["c"]),
                v=float(row["v"]),
            )
        except _PARSE_ERRORS as exc:
            raise BrokerError("invalid bar response") from exc

    def get_account(self) -> Account:
        return self._account(self._request("GET", self.settings.trading_base_url, "/v2/account"))

    def get_positions(self) -> list[Position]:
        # A flat account is HTTP 200 with []. A 404 on this list is an error and must raise:
        # read as "we hold nothing" it would make the bot buy again.
        rows = self._request("GET", self.settings.trading_base_url, "/v2/positions")
        if not isinstance(rows, list):
            raise BrokerError("invalid positions response")
        return [self._position(row) for row in rows]

    def get_open_orders(self) -> list[Order]:
        rows = self._request(
            "GET", self.settings.trading_base_url, "/v2/orders", {"status": "open", "limit": 500}
        )
        if not isinstance(rows, list):
            raise BrokerError("invalid orders response")
        return [self._order(row) for row in rows]

    def list_orders(self, after, symbol=None) -> tuple[list[Order], bool]:
        params = {
            "status": "all",
            "after": _iso(after),
            "limit": 500,
            "direction": "asc",
            "nested": "false",
        }
        if symbol is not None:
            params["symbols"] = symbol
        rows = self._request("GET", self.settings.trading_base_url, "/v2/orders", params)
        if not isinstance(rows, list):
            raise BrokerError("invalid orders response")
        unique = {}
        for row in rows:
            order = self._order(row)
            unique.setdefault(order.id, order)
        return list(unique.values()), len(rows) < 500

    def submit_order(self, symbol, side, client_order_id, notional=None, qty=None) -> Order:
        validate_order_request(symbol, side, client_order_id, notional, qty)
        body = {
            "symbol": symbol,
            "side": side,
            "type": "market",
            "time_in_force": "day",
            "client_order_id": client_order_id,
        }
        body["notional" if notional is not None else "qty"] = notional if notional is not None else qty
        # For the POST, _request returns the parsed Order (see _submitted).
        return self._request("POST", self.settings.trading_base_url, "/v2/orders", json_body=body)

    def cancel_order(self, order_id) -> None:
        _path_part(order_id, _ORDER_ID, "order_id")
        try:
            self._request(
                "DELETE",
                self.settings.trading_base_url,
                f"/v2/orders/{order_id}",
                expect_json=False,
            )
        except BrokerRejected as exc:
            if exc.status not in {404, 422}:
                raise
            try:
                order = self.get_order(order_id)
            except BrokerError:
                raise exc
            if not is_terminal(order.status):
                raise exc

    def get_order(self, order_id) -> Order:
        _path_part(order_id, _ORDER_ID, "order_id")
        row = self._request("GET", self.settings.trading_base_url, f"/v2/orders/{order_id}")
        return self._order(row)

    def get_order_by_client_id(self, client_order_id) -> Order | None:
        try:
            row = self._request(
                "GET",
                self.settings.trading_base_url,
                "/v2/orders:by_client_order_id",
                {"client_order_id": client_order_id},
            )
        except BrokerRejected as exc:
            if exc.status == 404 and exc.code == 40410000:
                return None
            raise
        return self._order(row)

    def get_clock(self) -> BrokerClock:
        row = self._request("GET", self.settings.trading_base_url, "/v2/clock")
        try:
            return BrokerClock(
                timestamp=_parse_timestamp(row["timestamp"]),
                is_open=_required_bool(row, "is_open"),
                next_open=_parse_timestamp(row["next_open"]),
                next_close=_parse_timestamp(row["next_close"]),
            )
        except _PARSE_ERRORS as exc:
            raise BrokerError("invalid clock response") from exc

    def get_calendar(self, start, end) -> list[dict]:
        rows = self._request(
            "GET",
            self.settings.trading_base_url,
            "/v2/calendar",
            {"start": _iso(start), "end": _iso(end)},
        )
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise BrokerError("invalid calendar response")
        return rows

    def _bars(self, symbol, params) -> list[Bar]:
        bars = []
        for _ in range(_MAX_BAR_PAGES):
            row = self._request("GET", self.settings.data_base_url, "/v2/stocks/bars", params)
            try:
                page = row.get("bars", {}).get(symbol, [])
                token = row.get("next_page_token")
            except AttributeError as exc:
                raise BrokerError("invalid bars response") from exc
            if not isinstance(page, list):
                raise BrokerError("invalid bars response")
            bars.extend(self._bar(item) for item in page)
            if not token:
                return bars
            params = dict(params)
            params["page_token"] = token
        raise BrokerError(f"bars response did not end within {_MAX_BAR_PAGES} pages")

    def get_daily_bars(self, symbol, start, adjustment="all", feed="sip") -> list[Bar]:
        return self._bars(
            symbol,
            {
                "symbols": symbol,
                "timeframe": "1Day",
                "start": _iso(start),
                "adjustment": adjustment,
                "feed": feed,
                "limit": 10000,
            },
        )

    def get_minute_bars(self, symbol, start, feed="iex") -> list[Bar]:
        return self._bars(
            symbol,
            {
                "symbols": symbol,
                "timeframe": "1Min",
                "start": _iso(start),
                "adjustment": "raw",
                "feed": feed,
                "limit": 10000,
            },
        )

    def get_latest_trade(self, symbol, feed="iex") -> Trade | None:
        _path_part(symbol, _PATH_SYMBOL, "symbol")
        row = self._request(
            "GET",
            self.settings.data_base_url,
            f"/v2/stocks/{symbol}/trades/latest",
            {"feed": feed},
        )
        try:
            trade = row.get("trade")
            if trade is None:
                return None
            return Trade(t=_parse_timestamp(trade["t"]), price=float(trade["p"]))
        except _PARSE_ERRORS as exc:
            raise BrokerError("invalid latest trade response") from exc
