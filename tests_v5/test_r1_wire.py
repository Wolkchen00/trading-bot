import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pytest
import requests

from v5.broker import (
    AlpacaBroker,
    AmbiguousSubmit,
    BrokerError,
    BrokerRejected,
    BrokerUnavailable,
)
from v5.config import RailNumbers, Settings


FIXTURES = Path(__file__).parent / "fixtures" / "alpaca"


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def settings(tmp_path):
    return Settings(
        "paper", "dummy-key", "dummy-secret", "https://paper-api.alpaca.markets",
        "https://data.alpaca.markets", tmp_path, True, "high",
        RailNumbers(0.37, 0.15, 0.60), "", "https://ntfy.sh",
    )


class Stub:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, params, json_body, timeout):
        self.calls.append((method, urlparse(url).path, params, json_body, timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        status, body = response
        return status, body if isinstance(body, str) else json.dumps(body)


def from_fixture(name):
    item = fixture(name)
    return item["status"], item["body"]


class Odd(Exception):
    """A transport failure of a class the gateway has never heard of."""


def order_body(**changes):
    body = dict(fixture("buy_notional_20_TQQQ")["body"])
    body.update(changes)
    return body


def without(body, *keys):
    return {name: value for name, value in body.items() if name not in keys}


def test_trading_methods_exact_wire_and_parsing(settings):
    account_stub = Stub(from_fixture("account"))
    account = AlpacaBroker(settings, account_stub).get_account()
    assert account.equity == 62357.28
    assert account_stub.calls == [("GET", "/v2/account", None, None, 15)]

    positions_stub = Stub(from_fixture("positions_before"))
    positions = AlpacaBroker(settings, positions_stub).get_positions()
    assert len(positions) == 2 and positions[0].qty == "33.221113325"
    assert positions_stub.calls[0][1:4] == ("/v2/positions", None, None)

    open_stub = Stub(from_fixture("open_orders_before"))
    assert AlpacaBroker(settings, open_stub).get_open_orders() == []
    assert open_stub.calls[0][2] == {"status": "open", "limit": 500}

    list_stub = Stub(from_fixture("list_orders_all_today"))
    orders, complete = AlpacaBroker(settings, list_stub).list_orders("2026-11-16T00:00:00Z", "TQQQ")
    assert len(orders) == 10 and complete
    assert list_stub.calls[0][2] == {
        "status": "all", "after": "2026-11-16T00:00:00Z", "limit": 500,
        "direction": "asc", "nested": "false", "symbols": "TQQQ",
    }

    submit_stub = Stub(from_fixture("buy_notional_20_TQQQ"))
    order = AlpacaBroker(settings, submit_stub).submit_order(
        "TQQQ", "buy", "v5probe-6e02e58d-buy1", notional="20.00"
    )
    assert order.status == "pending_new" and order.notional == "20" and order.qty is None
    assert submit_stub.calls[0][0:4] == (
        "POST", "/v2/orders", None,
        {"symbol": "TQQQ", "side": "buy", "type": "market", "time_in_force": "day",
         "client_order_id": "v5probe-6e02e58d-buy1", "notional": "20.00"},
    )

    cancel_stub = Stub(from_fixture("cancel_by_id"))
    AlpacaBroker(settings, cancel_stub).cancel_order("abc")
    assert cancel_stub.calls[0][0:4] == ("DELETE", "/v2/orders/abc", None, None)

    body = fixture("get_by_client_order_id")["body"]
    get_stub = Stub((200, body))
    assert AlpacaBroker(settings, get_stub).get_order(body["id"]).status == "filled"
    assert get_stub.calls[0][1] == f"/v2/orders/{body['id']}"

    by_client_stub = Stub(from_fixture("get_by_client_order_id"))
    found = AlpacaBroker(settings, by_client_stub).get_order_by_client_id("v5probe-6e02e58d-buy1")
    assert found is not None and found.filled_avg_price == 54.468
    assert by_client_stub.calls[0][2] == {"client_order_id": "v5probe-6e02e58d-buy1"}

    unknown_stub = Stub(from_fixture("get_by_client_order_id_unknown"))
    assert AlpacaBroker(settings, unknown_stub).get_order_by_client_id("<unknown>") is None


def test_clock_calendar_and_data_methods_exact_wire(settings):
    clock_stub = Stub(from_fixture("clock"))
    clock = AlpacaBroker(settings, clock_stub).get_clock()
    assert clock.is_open and clock.timestamp.microsecond == 446130
    assert clock_stub.calls[0][1] == "/v2/clock"

    cal_stub = Stub(from_fixture("calendar_window"))
    rows = AlpacaBroker(settings, cal_stub).get_calendar("2025-10-12", "2026-12-02")
    assert len(rows) == 30
    assert cal_stub.calls[0][2] == {"start": "2025-10-12", "end": "2026-12-02"}

    daily_stub = Stub(from_fixture("daily_bars_QQQ_sip_all"))
    bars = AlpacaBroker(settings, daily_stub).get_daily_bars("QQQ", "2025-09-22")
    assert len(bars) == 291 and bars[-1].t.date().isoformat() == "2026-11-16"
    assert daily_stub.calls[0][2] == {
        "symbols": "QQQ", "timeframe": "1Day", "start": "2025-09-22",
        "adjustment": "all", "feed": "sip", "limit": 10000,
    }

    minute_stub = Stub(from_fixture("minute_bars_QQQ_iex_noend"))
    minute = AlpacaBroker(settings, minute_stub).get_minute_bars("QQQ", "2026-11-16T14:30:00Z")
    assert len(minute) == 10
    assert minute_stub.calls[0][2]["feed"] == "iex"
    assert minute_stub.calls[0][2]["adjustment"] == "raw"

    trade_stub = Stub(from_fixture("latest_trade_QQQ_iex"))
    trade = AlpacaBroker(settings, trade_stub).get_latest_trade("QQQ")
    assert trade is not None and trade.price == 667.62
    assert trade_stub.calls[0][1:3] == ("/v2/stocks/QQQ/trades/latest", {"feed": "iex"})


def test_paging_completeness_deduplication_and_errors(settings):
    row = fixture("get_by_client_order_id")["body"]
    page1 = {"bars": {"QQQ": [{"t": "2026-11-16T14:30:00Z", "o": 1, "h": 2, "l": 1, "c": 2, "v": 3}]}, "next_page_token": "next"}
    page2 = {"bars": {"QQQ": [{"t": "2026-11-16T14:31:00Z", "o": 2, "h": 3, "l": 2, "c": 3, "v": 4}]}, "next_page_token": None}
    stub = Stub((200, page1), (200, page2))
    assert len(AlpacaBroker(settings, stub).get_minute_bars("QQQ", "start")) == 2
    assert stub.calls[1][2]["page_token"] == "next"

    many = []
    for index in range(500):
        copy = dict(row)
        copy["id"] = str(index)
        many.append(copy)
    list_stub = Stub((200, many))
    orders, complete = AlpacaBroker(settings, list_stub).list_orders("2026-11-16T00:00:00Z")
    assert len(orders) == 500 and not complete

    duplicate_stub = Stub(from_fixture("duplicate_client_order_id"))
    with pytest.raises(BrokerRejected) as caught:
        AlpacaBroker(settings, duplicate_stub).submit_order("TQQQ", "buy", "same", notional="20")
    assert caught.value.code == 40010001

    large_stub = Stub(from_fixture("reject_too_large"))
    with pytest.raises(BrokerRejected) as caught:
        AlpacaBroker(settings, large_stub).submit_order("TQQQ", "buy", "large", notional="99999999")
    assert caught.value.code == 40310000

    invalid_stub = Stub((200, "not json"))
    with pytest.raises(BrokerError):
        AlpacaBroker(settings, invalid_stub).get_account()


@pytest.mark.parametrize("response", [(429, {}), (503, {}), requests.Timeout("timeout")])
def test_get_retries_with_recorded_pauses(settings, response):
    stub = Stub(response, response, response, response, response)
    pauses = []
    with pytest.raises(BrokerUnavailable):
        AlpacaBroker(settings, stub, pauses.append).get_clock()
    assert len(stub.calls) == 5
    assert pauses == [0.5, 1.0, 2.0, 4.0]


@pytest.mark.parametrize("response", [(429, {}), (503, {}), requests.Timeout("dummy-secret")])
def test_post_is_never_retried_and_secret_is_redacted(settings, response):
    stub = Stub(response)
    with pytest.raises(AmbiguousSubmit) as caught:
        AlpacaBroker(settings, stub).submit_order("TQQQ", "buy", "id", notional="1")
    assert len(stub.calls) == 1
    assert "dummy-secret" not in str(caught.value)


@pytest.mark.parametrize("kwargs", [{}, {"notional": "1", "qty": "1"}, {"notional": "1.001"}, {"qty": "0.0000000001"}])
def test_size_validation_precedes_http(settings, kwargs):
    stub = Stub()
    with pytest.raises(ValueError):
        AlpacaBroker(settings, stub).submit_order("TQQQ", "buy", "id", **kwargs)
    assert stub.calls == []


# Fix round 1: what a submission may end in, and what may leave the gateway.
@pytest.mark.parametrize("failure", [
    requests.exceptions.ChunkedEncodingError("dummy-secret"),
    requests.exceptions.RequestException("dummy-secret"),
    OSError("dummy-secret"),
    RuntimeError("dummy-secret"),
    Odd("dummy-secret"),
])
def test_submit_transport_failure_is_ambiguous_and_names_only_its_class(settings, failure):
    stub, pauses = Stub(failure), []
    with pytest.raises(AmbiguousSubmit) as caught:
        AlpacaBroker(settings, stub, pauses.append).submit_order("TQQQ", "buy", "id", notional="1")
    assert len(stub.calls) == 1 and pauses == []
    assert type(failure).__name__ in str(caught.value) and "dummy-secret" not in str(caught.value)


@pytest.mark.parametrize("status", [100, 302, 408, 425, 429, 500, 599, 600])
def test_submit_status_that_proves_nothing_is_ambiguous(settings, status):
    stub, pauses = Stub((status, {"code": 1, "message": "dummy-secret"})), []
    with pytest.raises(AmbiguousSubmit) as caught:
        AlpacaBroker(settings, stub, pauses.append).submit_order("TQQQ", "buy", "id", notional="1")
    assert len(stub.calls) == 1 and pauses == []
    assert f"HTTP {status}" in str(caught.value) and "dummy-secret" not in str(caught.value)


@pytest.mark.parametrize("body", [
    "not json", "", "null", "[]", "7", {"id": "only-an-id"},
    order_body(id=None), order_body(status=None), without(order_body(), "client_order_id"),
    without(order_body(), "type", "order_type"),
])
def test_accepted_submit_without_a_readable_order_is_ambiguous(settings, body):
    for status in (200, 201, 204):
        stub = Stub((status, body))
        with pytest.raises(AmbiguousSubmit) as caught:
            AlpacaBroker(settings, stub).submit_order("TQQQ", "buy", "id", notional="1")
        assert len(stub.calls) == 1 and f"HTTP {status}" in str(caught.value)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 499])
def test_submit_definite_refusal_stays_a_rejection(settings, status):
    stub = Stub((status, {"code": 12345, "message": "no"}))
    with pytest.raises(BrokerRejected) as caught:
        AlpacaBroker(settings, stub).submit_order("TQQQ", "buy", "id", notional="1")
    assert not isinstance(caught.value, BrokerUnavailable) and len(stub.calls) == 1
    assert (caught.value.status, caught.value.code, caught.value.message) == (status, 12345, "no")


@pytest.mark.parametrize("failure", [
    requests.exceptions.ChunkedEncodingError("dummy-secret"),
    OSError("dummy-secret"),
    RuntimeError("dummy-secret"),
    Odd("dummy-secret"),
])
def test_any_transport_failure_on_get_and_delete_is_unavailable(settings, failure):
    stub, pauses = Stub(*[failure] * 5), []
    with pytest.raises(BrokerUnavailable) as caught:
        AlpacaBroker(settings, stub, pauses.append).get_positions()
    assert len(stub.calls) == 5 and pauses == [0.5, 1.0, 2.0, 4.0]
    assert not isinstance(caught.value, AmbiguousSubmit)
    assert type(failure).__name__ in str(caught.value) and "dummy-secret" not in str(caught.value)

    recovered, pauses = Stub(failure, from_fixture("positions_before")), []
    assert len(AlpacaBroker(settings, recovered, pauses.append).get_positions()) == 2
    assert pauses == [0.5]

    delete_stub, pauses = Stub(failure), []
    with pytest.raises(BrokerUnavailable) as caught:
        AlpacaBroker(settings, delete_stub, pauses.append).cancel_order("abc")
    assert len(delete_stub.calls) == 1 and pauses == []
    assert not isinstance(caught.value, AmbiguousSubmit)


def test_wrong_shaped_answers_raise_broker_error_in_every_method(settings):
    def broker(body):
        return AlpacaBroker(settings, Stub((200, body)))

    start = "2026-11-16T14:30:00Z"
    for body in ("null", "[]", "7", '"text"', "{}"):
        for call in (
            lambda b: b.get_account(),
            lambda b: b.get_order("abc"),
            lambda b: b.get_order_by_client_id("abc"),
            lambda b: b.get_clock(),
        ):
            with pytest.raises(BrokerError):
                call(broker(body))
    for body in ("null", "7", '"text"', "{}", "[null]", "[7]", "[[]]", "[{}]"):
        for call in (
            lambda b: b.get_positions(),
            lambda b: b.get_open_orders(),
            lambda b: b.list_orders(start),
        ):
            with pytest.raises(BrokerError):
                call(broker(body))
    for body in ("null", "[]", "7", '"text"'):
        for call in (
            lambda b: b.get_latest_trade("QQQ"),
            lambda b: b.get_daily_bars("QQQ", "2026-01-02"),
            lambda b: b.get_minute_bars("QQQ", start),
        ):
            with pytest.raises(BrokerError):
                call(broker(body))
    for body in ("null", "7", "{}", "[null]", "[7]"):
        with pytest.raises(BrokerError):
            broker(body).get_calendar("2026-11-01", "2026-11-30")
    for rows in ("[null]", "[7]", "[[]]", "[{}]"):
        with pytest.raises(BrokerError):
            broker('{"bars": {"QQQ": %s}, "next_page_token": null}' % rows).get_daily_bars("QQQ", "2026-01-02")

    # An object without the optional key still means "nothing there".
    assert broker({"symbol": "QQQ"}).get_latest_trade("QQQ") is None
    assert broker({"bars": {}, "next_page_token": None}).get_daily_bars("QQQ", "2026-01-02") == []
    assert broker({}).get_minute_bars("QQQ", start) == []


def test_order_fill_fields_and_required_fields(settings):
    def parsed(body):
        return AlpacaBroker(settings, Stub((200, body))).get_order("abc")

    assert parsed(order_body(filled_qty=None)).filled_qty == "0"
    assert parsed(without(order_body(), "filled_qty")).filled_qty == "0"
    assert parsed(order_body(filled_qty="0.367004479")).filled_qty == "0.367004479"
    assert parsed(order_body(filled_avg_price=None)).filled_avg_price is None
    assert parsed(without(order_body(), "type")).type == "market"
    for broken in (
        order_body(id=None),
        order_body(symbol=None),
        order_body(status=None),
        order_body(type=None, order_type=None),
        without(order_body(), "type", "order_type"),
        order_body(filled_avg_price=10 ** 400),
    ):
        with pytest.raises(BrokerError):
            parsed(broken)


def test_positions_list_404_raises_and_a_flat_account_is_an_empty_200(settings):
    stub = Stub(from_fixture("position_TQQQ_after_exit"))
    with pytest.raises(BrokerRejected) as caught:
        AlpacaBroker(settings, stub).get_positions()
    assert caught.value.status == 404 and caught.value.code == 40410000 and len(stub.calls) == 1
    assert AlpacaBroker(settings, Stub((200, []))).get_positions() == []


def test_bars_paging_stops_after_50_pages(settings):
    def page(token):
        bar = {"t": "2026-11-16T14:30:00Z", "o": 1, "h": 2, "l": 1, "c": 2, "v": 3}
        return 200, {"bars": {"QQQ": [bar]}, "next_page_token": token}

    exact = Stub(*[page("more")] * 49, page(None))
    assert len(AlpacaBroker(settings, exact).get_minute_bars("QQQ", "start")) == 50
    assert len(exact.calls) == 50

    endless = Stub(*[page("more")] * 60)
    with pytest.raises(BrokerError) as caught:
        AlpacaBroker(settings, endless).get_daily_bars("QQQ", "start")
    assert len(endless.calls) == 50 and not isinstance(caught.value, BrokerUnavailable)


def test_default_transport_never_follows_a_redirect(settings, monkeypatch):
    seen = {}

    class Response:
        status_code = 200
        text = json.dumps(fixture("clock")["body"])

    def fake_request(method, url, **kwargs):
        seen.update(kwargs, method=method, url=url)
        return Response()

    monkeypatch.setattr(requests, "request", fake_request)  # nothing reaches the network
    assert AlpacaBroker(settings).get_clock().is_open
    assert seen["allow_redirects"] is False and seen["timeout"] == 15
    assert seen["headers"] == {"APCA-API-KEY-ID": "dummy-key", "APCA-API-SECRET-KEY": "dummy-secret"}
    assert (seen["method"], seen["url"]) == ("GET", "https://paper-api.alpaca.markets/v2/clock")
