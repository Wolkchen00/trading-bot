import json
from decimal import Decimal
from pathlib import Path

import pytest

from tests_v5.fakebroker import FakeBroker, invisible, partial, reject
from v5.broker import (
    AlpacaBroker,
    AmbiguousSubmit,
    BrokerRejected,
    BrokerUnavailable,
    TERMINAL_STATUSES,
    is_terminal,
    validate_order_request,
)
from v5.config import RailNumbers, Settings


FIXTURES = Path(__file__).parent / "fixtures" / "alpaca"


def fake():
    return FakeBroker(cash="100", prices={"TQQQ": "10"}, positions={"TQQQ": "2"})


def test_status_contract():
    for status in TERMINAL_STATUSES:
        assert is_terminal(status)
    for status in ["new", "accepted", "pending_new", "partially_filled", "unknown"]:
        assert not is_terminal(status)


def test_fake_duplicate_unknown_minimum_precision_and_limits():
    broker = fake()
    broker.submit_order("TQQQ", "buy", "one", notional="10")
    with pytest.raises(BrokerRejected) as caught:
        broker.submit_order("TQQQ", "buy", "one", notional="10")
    assert caught.value.status == 422 and caught.value.code == 40010001
    assert broker.get_order_by_client_id("unknown") is None

    with pytest.raises(BrokerRejected) as caught:
        broker.submit_order("TQQQ", "buy", "small", notional="0.50")
    assert caught.value.code == 42210000
    with pytest.raises(ValueError):
        broker.submit_order("TQQQ", "buy", "precise", notional="1.001")
    with pytest.raises(ValueError):
        broker.submit_order("TQQQ", "sell", "precise-qty", qty="1.0000000001")
    with pytest.raises(BrokerRejected) as caught:
        broker.submit_order("TQQQ", "buy", "large", notional="101")
    assert caught.value.message.startswith("insufficient buying power")
    with pytest.raises(BrokerRejected) as caught:
        broker.submit_order("TQQQ", "sell", "sell-large", qty="4")
    assert caught.value.message.startswith("insufficient qty available for order")


def test_fake_fill_arithmetic_terminal_cancel_and_calls():
    broker = FakeBroker(cash="100", prices={"TQQQ": "10"})
    pending = broker.submit_order("TQQQ", "buy", "buy", notional="20")
    assert pending.status == "pending_new"
    filled = broker.get_order(pending.id)
    assert filled.status == "filled" and filled.filled_qty == "2"
    account = broker.get_account()
    position = broker.get_positions()[0]
    assert account.cash == 80 and position.qty == "2" and account.equity == 100
    broker.cancel_order(pending.id)
    assert broker.get_order(pending.id).status == "filled"
    assert [call[0] for call in broker.calls[:3]] == ["submit_order", "get_order", "get_account"]


def test_fake_fractional_floor_and_adverse_move():
    broker = FakeBroker(cash="100", prices={"TQQQ": "3"}, adverse_bps=100)
    order = broker.submit_order("TQQQ", "buy", "buy", notional="10")
    filled = broker.get_order(order.id)
    assert filled.filled_qty == "3.300330033"
    assert filled.filled_avg_price == pytest.approx(3.03)
    assert broker.positions["TQQQ"] == Decimal("3.300330033")


def test_fake_ambiguous_faults_visibility_partial_never_fill_and_reject():
    before = fake()
    before.script("timeout_before_accept")
    with pytest.raises(AmbiguousSubmit):
        before.submit_order("TQQQ", "buy", "before", notional="10")
    assert before.orders == []

    after = fake()
    after.script("timeout_after_accept")
    with pytest.raises(AmbiguousSubmit):
        after.submit_order("TQQQ", "buy", "after", notional="10")
    assert len(after.orders) == 1

    hidden = fake()
    hidden.script(invisible(2))
    hidden.submit_order("TQQQ", "buy", "hidden", notional="10")
    assert hidden.get_order_by_client_id("hidden") is None
    assert hidden.get_order_by_client_id("hidden") is None
    assert hidden.get_order_by_client_id("hidden") is not None

    part = fake()
    part.script(partial("0.25"))
    order = part.submit_order("TQQQ", "buy", "part", notional="20")
    assert part.get_order(order.id).status == "partially_filled"
    part.cancel_order(order.id)
    assert part.get_order(order.id).status == "canceled"

    never = fake()
    never.script("never_fill")
    order = never.submit_order("TQQQ", "buy", "never", notional="10")
    assert never.get_order(order.id).status == "pending_new"
    never.cancel_order(order.id)
    assert never.get_order(order.id).status == "canceled"

    rejected = fake()
    rejected.script(reject(422, 123, "scripted"))
    with pytest.raises(BrokerRejected) as caught:
        rejected.submit_order("TQQQ", "buy", "reject", notional="10")
    assert caught.value.code == 123


@pytest.mark.parametrize("fault", ["http_429", "http_5xx"])
def test_fake_get_faults(fault):
    broker = fake()
    broker.script(fault)
    with pytest.raises(BrokerUnavailable):
        broker.get_account()


def test_fixture_contract_for_alpaca_rejections_and_unknown(tmp_path):
    settings = Settings(
        "paper", "dummy", "secret", "https://paper-api.alpaca.markets", "https://data.alpaca.markets",
        tmp_path, True, "high", RailNumbers(0.37, 0.15, 0.6), "", "https://ntfy.sh",
    )

    def broker_for(name):
        item = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
        return AlpacaBroker(settings, lambda *args: (item["status"], json.dumps(item["body"])))

    with pytest.raises(BrokerRejected) as caught:
        broker_for("duplicate_client_order_id").submit_order("TQQQ", "buy", "duplicate", notional="20")
    assert caught.value.code == 40010001
    assert broker_for("get_by_client_order_id_unknown").get_order_by_client_id("unknown") is None
    with pytest.raises(BrokerRejected) as caught:
        broker_for("buy_notional_0_50").submit_order("TQQQ", "buy", "small", notional="0.50")
    assert caught.value.code == 42210000
    with pytest.raises(BrokerRejected) as caught:
        broker_for("sell_qty_above_position").submit_order("TQQQ", "sell", "large", qty="100")
    assert caught.value.code == 40310000


GOOD_ID = "v5-paper-20261116-dec-1"
INVALID_REQUESTS = [
    ("TQQQ", "buy", GOOD_ID, {}),
    ("TQQQ", "buy", GOOD_ID, {"notional": "1", "qty": "1"}),
    ("TQQQ", "buy", GOOD_ID, {"notional": "1.001"}),
    ("TQQQ", "buy", GOOD_ID, {"qty": "0.0000000001"}),
    ("TQQQ", "buy", GOOD_ID, {"qty": "1e3"}),
    ("TQQQ", "buy", GOOD_ID, {"notional": "+5"}),
    ("TQQQ", "buy", GOOD_ID, {"notional": "-5"}),
    ("TQQQ", "buy", GOOD_ID, {"notional": " 5"}),
    ("TQQQ", "buy", GOOD_ID, {"qty": "5."}),
    ("TQQQ", "buy", GOOD_ID, {"qty": ".5"}),
    ("TQQQ", "buy", GOOD_ID, {"qty": "5_0"}),
    ("TQQQ", "buy", GOOD_ID, {"qty": "5\n"}),
    ("TQQQ", "buy", GOOD_ID, {"qty": "1,5"}),
    ("TQQQ", "buy", GOOD_ID, {"notional": ""}),
    ("TQQQ", "buy", GOOD_ID, {"notional": "nan"}),
    ("TQQQ", "buy", GOOD_ID, {"notional": 5.0}),
    ("TQQQ", "buy", GOOD_ID, {"qty": 1}),
    ("TQQQ", "buy", GOOD_ID, {"notional": chr(0x665)}),  # a digit, but not an ASCII one
    ("TQQQ", "buy", GOOD_ID, {"notional": "0"}),
    ("TQQQ", "buy", GOOD_ID, {"notional": "0.00"}),
    ("TQQQ", "buy", GOOD_ID, {"qty": "0.000000000"}),
    ("TQQQ", "hold", GOOD_ID, {"notional": "5.00"}),
    ("TQQQ", "BUY", GOOD_ID, {"notional": "5.00"}),
    ("TQQQ", None, GOOD_ID, {"notional": "5.00"}),
    ("TQQQ", ["buy"], GOOD_ID, {"notional": "5.00"}),
    ("", "buy", GOOD_ID, {"notional": "5.00"}),
    (None, "buy", GOOD_ID, {"notional": "5.00"}),
    ("TQ QQ", "buy", GOOD_ID, {"notional": "5.00"}),
    ("TQQQ\n", "buy", GOOD_ID, {"notional": "5.00"}),
    ("X" * 21, "buy", GOOD_ID, {"notional": "5.00"}),
    ("TQQQ", "buy", "", {"notional": "5.00"}),
    ("TQQQ", "buy", None, {"notional": "5.00"}),
    ("TQQQ", "buy", 12345, {"notional": "5.00"}),
    ("TQQQ", "buy", "has space", {"notional": "5.00"}),
    ("TQQQ", "buy", "new\nline", {"notional": "5.00"}),
    ("TQQQ", "buy", "slash/id", {"notional": "5.00"}),
    ("TQQQ", "buy", "x" * 129, {"notional": "5.00"}),
]


@pytest.mark.parametrize("symbol,side,client_order_id,kwargs", INVALID_REQUESTS)
def test_malformed_order_request_is_refused_alike_before_anything_happens(
    tmp_path, symbol, side, client_order_id, kwargs
):
    with pytest.raises(ValueError):
        validate_order_request(symbol, side, client_order_id, kwargs.get("notional"), kwargs.get("qty"))

    settings = Settings(
        "paper", "dummy", "secret", "https://paper-api.alpaca.markets", "https://data.alpaca.markets",
        tmp_path, True, "high", RailNumbers(0.37, 0.15, 0.6), "", "https://ntfy.sh",
    )
    http_calls = []
    with pytest.raises(ValueError):
        AlpacaBroker(settings, lambda *args: http_calls.append(args)).submit_order(
            symbol, side, client_order_id, **kwargs
        )
    assert http_calls == []

    broker = fake()
    broker.script("never_fill")
    with pytest.raises(ValueError):
        broker.submit_order(symbol, side, client_order_id, **kwargs)
    assert broker.orders == [] and broker.calls == [] and list(broker.faults) == ["never_fill"]


@pytest.mark.parametrize(
    "kwargs",
    [{"notional": "1"}, {"notional": "1.5"}, {"notional": "20.00"}, {"notional": "490.12"},
     {"qty": "0.000000001"}, {"qty": "33.221113325"}, {"qty": "7"}],
)
def test_plain_order_request_passes_the_shared_validator(kwargs):
    for symbol, side, client_order_id in (
        ("TQQQ", "buy", GOOD_ID),
        ("BRK.B", "sell", "A_b-1.2"),
        ("X" * 20, "sell", "x" * 128),
    ):
        validate_order_request(symbol, side, client_order_id, kwargs.get("notional"), kwargs.get("qty"))
    # The 1.00 minimum is the broker's rule, not a shape rule: the validator lets it through.
    validate_order_request("TQQQ", "buy", GOOD_ID, "0.01", None)

    broker = FakeBroker(cash="1000", prices={"TQQQ": "10"}, positions={"TQQQ": "50"})
    side = "sell" if "qty" in kwargs else "buy"
    assert broker.submit_order("TQQQ", side, GOOD_ID, **kwargs).status == "pending_new"
    assert [call[0] for call in broker.calls] == ["submit_order"]
