import json
from datetime import datetime, time, timedelta, timezone

import pytest

from tests_v5.fakebroker import FakeBroker
from v5.broker import Bar, BrokerRejected, Trade
from v5.clock import ET, Session, SessionCalendar, _parse_timestamp
from v5.data import DecisionInputs, build_inputs
from v5.packages import PACKAGES


UTC = timezone.utc


def fixture_market():
    with open("tests_v5/fixtures/alpaca/daily_bars_QQQ_sip_all.json", encoding="utf-8") as handle:
        rows = json.load(handle)["body"]["bars"]["QQQ"]
    bars = [Bar(_parse_timestamp(r["t"]), r["o"], r["h"], r["l"], r["c"], r["v"]) for r in rows]
    current = bars[-1].t.astimezone(ET).date()
    dates = [bar.t.astimezone(ET).date() for bar in bars[:-1]]
    sessions = [
        Session(d, datetime.combine(d, time(14, 30), UTC), datetime.combine(d, time(21), UTC))
        for d in dates
    ]
    opened = datetime.combine(current, time(14, 30), UTC)
    session = Session(current, opened, opened + timedelta(hours=6, minutes=30))
    calendar = SessionCalendar(sessions + [session])
    now = opened + timedelta(minutes=100)
    broker = FakeBroker(prices={"QQQ": 500})
    broker.daily_bars["QQQ"] = bars
    broker.minute_bars["QQQ"] = [
        Bar(opened + timedelta(minutes=i), 500, 501, 499, 500.5, 1) for i in range(100)
    ]
    broker.latest_trades["QQQ"] = Trade(now - timedelta(seconds=10), 500.75)
    return broker, calendar, session, now, dates


def test_fixture_happy_path_drops_today_and_round_trips_json():
    broker, calendar, session, now, dates = fixture_market()
    result = build_inputs(broker, calendar, session, now, PACKAGES["high"])
    assert isinstance(result, DecisionInputs)
    assert result.close_dates[-1] == dates[-1] and session.date not in result.close_dates
    assert result.px_source == "iex_trade" and result.range_coverage == 1.0
    assert DecisionInputs.from_bundle(json.loads(json.dumps(result.to_bundle()))) == result


@pytest.mark.parametrize("offset,code", [(-1, "HISTORY_STALE"), (-20, "HISTORY_GAP")])
def test_missing_history(offset, code):
    broker, calendar, session, now, dates = fixture_market()
    missing = dates[offset]
    broker.daily_bars["QQQ"] = [b for b in broker.daily_bars["QQQ"] if b.t.astimezone(ET).date() != missing]
    assert build_inputs(broker, calendar, session, now, PACKAGES["high"]).code == code


@pytest.mark.parametrize("kind", ["duplicate", "reverse"])
def test_duplicate_and_out_of_order_history(kind):
    broker, calendar, session, now, _ = fixture_market()
    if kind == "duplicate":
        broker.daily_bars["QQQ"].insert(-1, broker.daily_bars["QQQ"][-2])
    else:
        broker.daily_bars["QQQ"][-3:-1] = reversed(broker.daily_bars["QQQ"][-3:-1])
    assert build_inputs(broker, calendar, session, now, PACKAGES["high"]).code == "HISTORY_GAP"


def test_short_calendar():
    broker, calendar, session, now, dates = fixture_market()
    short = SessionCalendar([
        Session(d, datetime.combine(d, time(14, 30), UTC), datetime.combine(d, time(21), UTC))
        for d in dates[-248:]
    ] + [session])
    assert build_inputs(broker, short, session, now, PACKAGES["high"]).code == "CALENDAR_MISSING"


@pytest.mark.parametrize("offset", [timedelta(minutes=-3), timedelta(seconds=6)])
def test_bad_trade_uses_fresh_bar(offset):
    broker, calendar, session, now, _ = fixture_market()
    broker.latest_trades["QQQ"] = Trade(now + offset, 700)
    result = build_inputs(broker, calendar, session, now, PACKAGES["high"])
    assert result.px_source == "iex_bar" and result.px == 500.5


def test_stale_trade_and_bar_are_not_ready():
    broker, calendar, session, now, _ = fixture_market()
    broker.latest_trades["QQQ"] = None
    broker.minute_bars["QQQ"] = broker.minute_bars["QQQ"][:-10]
    assert build_inputs(broker, calendar, session, now, PACKAGES["high"]).code == "PRICE_STALE"


@pytest.mark.parametrize(
    "factory,reason",
    [
        (lambda op: [], "RANGE_EMPTY"),
        (lambda op: [Bar(op + timedelta(minutes=i), 1, 2, 1, 1.5, 1) for i in range(11, 100)], "RANGE_LATE_START"),
        (lambda op: [Bar(op + timedelta(minutes=i), 1, 2, 1, 1.5, 1) for i in range(90)], "RANGE_STALE"),
        (lambda op: [Bar(op + timedelta(minutes=2*i), 1, 2, 1, 1.5, 1) for i in range(50)], "RANGE_THIN"),
    ],
)
def test_range_reasons(factory, reason):
    broker, calendar, session, now, _ = fixture_market()
    broker.minute_bars["QQQ"] = factory(session.open)
    result = build_inputs(broker, calendar, session, now, PACKAGES["high"])
    assert result.range_reason == reason and result.session_high is None


def test_bad_outside_and_duplicate_minutes_are_dropped():
    broker, calendar, session, now, _ = fixture_market()
    good = broker.minute_bars["QQQ"]
    broker.minute_bars["QQQ"] = [
        Bar(session.open - timedelta(minutes=1), 1, 900, 1, 1, 1), good[0], good[0],
        Bar(session.open + timedelta(seconds=30), 1, float("nan"), 1, 1, 1),
        *good[1:], Bar(now, 1, 900, 1, 1, 1),
    ]
    result = build_inputs(broker, calendar, session, now, PACKAGES["high"])
    assert result.range_bars == 100 and result.session_high == 501


def test_broker_down_and_rejection_are_values():
    broker, calendar, session, now, _ = fixture_market()
    broker.script("http_5xx")
    assert build_inputs(broker, calendar, session, now, PACKAGES["high"]).code == "BROKER_DOWN"

    class Rejected(FakeBroker):
        def get_daily_bars(self, *args, **kwargs):
            raise BrokerRejected(403, 1, "rejected", {})

    assert build_inputs(Rejected(), calendar, session, now, PACKAGES["high"]).code == "DATA_ERROR"

