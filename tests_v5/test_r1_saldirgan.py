"""R1 adversarial suite (written by Claude from the contract, before reading the implementation).

Targets the places where v4 actually failed: wrong keys against the wrong endpoint, a silently replaced state
file, a stale lock, retried order submissions, broker answers that are not what the code expects, and time
handling around daylight saving and early closes.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from v5 import broker as vb
from v5 import clock as vc
from v5 import config as vcfg
from v5 import state as vs

FIX = Path(__file__).parent / "fixtures" / "alpaca"
SECRET = "S3CR3T-do-not-leak-0123456789"
KEY = "PKTESTKEY000000000000"


def fx(name):
    return json.loads((FIX / (name + ".json")).read_text(encoding="utf-8"))


def env(**over):
    base = {"TRADING_MODE": "paper", "ALPACA_PAPER_API_KEY": KEY, "ALPACA_PAPER_SECRET_KEY": SECRET,
            "ALPACA_LIVE_API_KEY": "LIVE" + KEY, "ALPACA_LIVE_SECRET_KEY": "LIVE" + SECRET}
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


class Transport:
    """Scripted transport: a list of (status, body) or exceptions; records every call."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, method, url, params, json_body, timeout):
        self.calls.append({"method": method, "url": url, "params": params, "json": json_body})
        if not self.script:
            raise AssertionError("unexpected extra HTTP call: %s %s" % (method, url))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        status, body = item
        return status, body if isinstance(body, str) else json.dumps(body)


def make(tmp_path, script, mode="paper"):
    e = env(TRADING_MODE=mode, ALPACA_KEY_PREFIX="LIVE" if mode == "live" else "PAPER")
    settings = vcfg.load_settings(env=e, app_dir=tmp_path)
    sleeps = []
    t = Transport(script)
    return vb.AlpacaBroker(settings, transport=t, sleep=sleeps.append), t, sleeps


# ----------------------------------------------------------------------------- config
def test_mode_and_key_prefix_must_agree(tmp_path):
    with pytest.raises(vcfg.ConfigError):
        vcfg.load_settings(env=env(TRADING_MODE="live", ALPACA_KEY_PREFIX="PAPER"), app_dir=tmp_path)
    with pytest.raises(vcfg.ConfigError):
        vcfg.load_settings(env=env(TRADING_MODE="live", ALPACA_KEY_PREFIX="PAPER2"), app_dir=tmp_path)
    with pytest.raises(vcfg.ConfigError):
        vcfg.load_settings(env=env(TRADING_MODE="paper", ALPACA_KEY_PREFIX="LIVE"), app_dir=tmp_path)
    with pytest.raises(vcfg.ConfigError):
        vcfg.load_settings(env=env(TRADING_MODE="production"), app_dir=tmp_path)
    with pytest.raises(vcfg.ConfigError):
        vcfg.load_settings(env=env(TRADING_MODE="live", ALPACA_LIVE_SECRET_KEY=""), app_dir=tmp_path)


def test_live_never_gets_the_paper_endpoint_or_keys(tmp_path):
    live = vcfg.load_settings(env=env(TRADING_MODE="live"), app_dir=tmp_path)
    paper = vcfg.load_settings(env=env(), app_dir=tmp_path)
    assert live.trading_base_url == "https://api.alpaca.markets" and live.api_key == "LIVE" + KEY
    assert paper.trading_base_url == "https://paper-api.alpaca.markets" and paper.api_key == KEY
    assert live.state_dir != paper.state_dir
    assert live.state_dir.parts[-2:] == ("state_live", "v5") and paper.state_dir.parts[-2:] == ("state_paper", "v5")
    assert live.live_trading_enabled is False and live.orders_enabled is False     # dry-run by default
    assert paper.orders_enabled is True
    for word in ("1", "true", "TRUE", "yes", "On"):
        assert vcfg.load_settings(env=env(TRADING_MODE="live", LIVE_TRADING_ENABLED=word), app_dir=tmp_path).orders_enabled is True
    for word in ("", "0", "false", "enabled", "y", " true ", "2"):
        got = vcfg.load_settings(env=env(TRADING_MODE="live", LIVE_TRADING_ENABLED=word), app_dir=tmp_path).orders_enabled
        assert got is False, word


def test_secret_never_printed(tmp_path):
    s = vcfg.load_settings(env=env(), app_dir=tmp_path)
    assert SECRET not in repr(s) and SECRET not in str(s) and KEY not in repr(s)
    assert s.api_secret == SECRET


def test_package_and_rails_defaults_and_overrides(tmp_path):
    s = vcfg.load_settings(env=env(), app_dir=tmp_path)
    assert s.package == "high" and (s.rails.alert_dd, s.rails.breaker_day, s.rails.floor_dd) == (0.37, 0.15, 0.60)
    m = vcfg.load_settings(env=env(STRATEGY_PACKAGE="moderate"), app_dir=tmp_path)
    assert (m.rails.alert_dd, m.rails.breaker_day, m.rails.floor_dd) == (0.31, 0.12, 0.50)
    o = vcfg.load_settings(env=env(V5_FLOOR_DD="0.5", V5_ALERT_DD="0.3"), app_dir=tmp_path)
    assert o.rails.floor_dd == 0.5 and o.rails.alert_dd == 0.3 and o.rails.breaker_day == 0.15
    for bad in ({"STRATEGY_PACKAGE": "insane"}, {"V5_FLOOR_DD": "0"}, {"V5_FLOOR_DD": "1"}, {"V5_FLOOR_DD": "abc"},
                {"V5_FLOOR_DD": "nan"}, {"V5_ALERT_DD": "0.7"}, {"V5_BREAKER_DAY": "-0.1"}, {"V5_BREAKER_DAY": "inf"}):
        with pytest.raises(vcfg.ConfigError):
            vcfg.load_settings(env=env(**bad), app_dir=tmp_path)


def test_state_root_override_and_no_side_effects(tmp_path):
    s = vcfg.load_settings(env=env(STATE_VOLUME_PATH=str(tmp_path / "vol")), app_dir=tmp_path / "app")
    assert s.state_dir == tmp_path / "vol" / "state_paper" / "v5"
    assert not (tmp_path / "vol").exists()            # load_settings creates nothing


def test_parse_env_file(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# comment\n\nA=1\nB = two words \nC=\"quoted value\"\nD='single'\nE=a=b=c\nexport F=5\n", encoding="utf-8")
    got = {k.strip(): v for k, v in vcfg.parse_env_file(p).items()}
    assert got["A"] == "1" and got["C"] == "quoted value" and got["D"] == "single" and got["E"] == "a=b=c"
    assert got["B"].strip() == "two words"


# ----------------------------------------------------------------------------- state
def test_atomic_write_keeps_old_content_when_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "deep" / "x.json"
    vs.atomic_write_json(target, {"v": 1})
    assert vs.read_json(target) == {"v": 1}

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    if hasattr(vs, "replace"):
        monkeypatch.setattr(vs, "replace", boom)
    with pytest.raises(OSError):
        vs.atomic_write_json(target, {"v": 2})
    monkeypatch.undo()
    assert vs.read_json(target) == {"v": 1}
    vs.atomic_write_json(target, {"v": 3, "u": "çğı"})
    assert vs.read_json(target) == {"v": 3, "u": "çğı"}


@pytest.mark.parametrize("content", [b"", b"{", b"not json", b"\xff\xfe\x00", b"[1, 2"])
def test_corrupt_state_is_reported_and_left_untouched(tmp_path, content):
    p = tmp_path / "peak.json"
    p.write_bytes(content)
    with pytest.raises(vs.StateCorrupt):
        vs.read_json(p, default={"fresh": True})
    assert p.read_bytes() == content                  # never replaced, never deleted
    assert vs.read_json(tmp_path / "missing.json", default=7) == 7
    with pytest.raises(FileNotFoundError):
        vs.read_json(tmp_path / "missing.json")


def test_state_dir_must_be_writable(tmp_path):
    ok = tmp_path / "a" / "b"
    vs.ensure_state_dir(ok)
    assert ok.is_dir() and list(ok.iterdir()) == []   # the probe file is removed
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(vs.StateDirError):
        vs.ensure_state_dir(blocker / "sub")


LOCK_CHILD = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, sys.argv[2])
    from v5.state import ProcessLock
    lock = ProcessLock(sys.argv[1])
    got = lock.acquire()
    print("ACQUIRED" if got else "BUSY", flush=True)
    if got and len(sys.argv) > 3:
        time.sleep(float(sys.argv[3]))
""")


def test_process_lock_excludes_and_dies_with_its_process(tmp_path):
    root = str(Path(__file__).resolve().parents[1])
    lock_path = tmp_path / "process.lock"
    holder = subprocess.Popen([sys.executable, "-c", LOCK_CHILD, str(lock_path), root, "60"], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "ACQUIRED"
        mine = vs.ProcessLock(lock_path)
        assert mine.acquire() is False                 # a live holder excludes us
        second = subprocess.run([sys.executable, "-c", LOCK_CHILD, str(lock_path), root], capture_output=True, text=True, timeout=60)
        assert second.stdout.strip() == "BUSY"
    finally:
        holder.kill()
        holder.wait(timeout=30)
    assert lock_path.exists()                          # the stale file is still there ...
    again = vs.ProcessLock(lock_path)
    assert again.acquire() is True                     # ... and must not block (the OS released the lock)
    again.release()
    third = vs.ProcessLock(lock_path)
    assert third.acquire() is True
    third.release()


def test_session_record_paths(tmp_path):
    d = dt.date(2026, 11, 16)
    assert vs.session_record_path(tmp_path, d) == tmp_path / "decisions" / "2026-11-16.json"
    assert vs.session_record_path(tmp_path, d, dry_run=True) == tmp_path / "decisions_dryrun" / "2026-11-16.json"


# ----------------------------------------------------------------------------- clock
def rows(*items):
    return [{"date": d, "open": o, "close": c, "session_open": "0400", "session_close": "2000"} for d, o, c in items]


def test_calendar_handles_dst_and_early_close():
    cal = vc.SessionCalendar(vc.parse_calendar(rows(("2026-03-06", "09:30", "16:00"), ("2026-03-09", "09:30", "16:00"),
                                                    ("2026-11-02", "09:30", "16:00"), ("2026-11-27", "09:30", "13:00"),
                                                    ("2026-10-30", "09:30", "16:00"))))
    utc = dt.timezone.utc
    assert cal.get(dt.date(2026, 3, 6)).close == dt.datetime(2026, 3, 6, 21, 0, tzinfo=utc)     # EST
    assert cal.get(dt.date(2026, 3, 9)).close == dt.datetime(2026, 3, 9, 20, 0, tzinfo=utc)     # EDT after the change
    assert cal.get(dt.date(2026, 10, 30)).open == dt.datetime(2026, 10, 30, 13, 30, tzinfo=utc)  # EDT
    assert cal.get(dt.date(2026, 11, 2)).open == dt.datetime(2026, 11, 2, 14, 30, tzinfo=utc)    # EST again
    early = cal.get(dt.date(2026, 11, 27))
    assert early.close == dt.datetime(2026, 11, 27, 18, 0, tzinfo=utc)
    w = vc.window_for(early)
    assert w.decide_from == dt.datetime(2026, 11, 27, 17, 50, tzinfo=utc)
    assert w.decide_until == dt.datetime(2026, 11, 27, 17, 56, tzinfo=utc)
    assert w.submit_until == dt.datetime(2026, 11, 27, 17, 57, tzinfo=utc)
    assert w.cancel_at == dt.datetime(2026, 11, 27, 17, 58, 30, tzinfo=utc)
    assert w.reconcile_from == dt.datetime(2026, 11, 27, 18, 1, tzinfo=utc)
    assert w.reconcile_until == dt.datetime(2026, 11, 27, 18, 5, tzinfo=utc)
    # covers = the date lies inside the known range, so "no session" there means a holiday, not "unknown"
    assert cal.get(dt.date(2026, 11, 26)) is None and cal.covers(dt.date(2026, 11, 27)) and cal.covers(dt.date(2026, 11, 26))
    assert not cal.covers(dt.date(2026, 11, 28)) and not cal.covers(dt.date(2026, 3, 5))
    assert not vc.SessionCalendar([]).covers(dt.date(2026, 11, 16))
    assert cal.previous(dt.date(2026, 11, 27), 2) == [dt.date(2026, 10, 30), dt.date(2026, 11, 2)]
    assert cal.previous(dt.date(2026, 3, 6), 5) == []
    assert cal.previous(dt.date(2026, 12, 1), 99) == [dt.date(2026, 3, 6), dt.date(2026, 3, 9), dt.date(2026, 10, 30),
                                                      dt.date(2026, 11, 2), dt.date(2026, 11, 27)]
    back = vc.SessionCalendar.from_json(json.loads(json.dumps(cal.to_json())))
    assert back.get(dt.date(2026, 11, 27)) == early


def test_calendar_rejects_garbage_and_dedupes():
    good = rows(("2026-11-16", "09:30", "16:00"), ("2026-11-16", "09:30", "16:00"), ("2026-11-13", "09:30", "16:00"))
    sessions = vc.parse_calendar(good)
    assert [s.date for s in sessions] == [dt.date(2026, 11, 13), dt.date(2026, 11, 16)]
    for bad in ([{"date": "2026-13-45", "open": "09:30", "close": "16:00"}], [{"date": "2026-11-16", "open": "9h", "close": "16:00"}],
                [{"date": "2026-11-16", "open": "16:00", "close": "09:30"}], [{"open": "09:30", "close": "16:00"}], ["x"], [None]):
        with pytest.raises(ValueError):
            vc.parse_calendar(bad)


def test_fixture_calendar_and_clock_skew():
    body = fx("calendar_window")["body"]
    cal = vc.SessionCalendar(vc.parse_calendar(body))
    s = cal.get(dt.date(2026, 11, 16))
    assert s is not None and s.open == dt.datetime(2026, 11, 16, 14, 30, tzinfo=dt.timezone.utc)
    stamp = fx("clock")["body"]["timestamp"]                       # nanoseconds and an ET offset
    local = dt.datetime(2026, 11, 16, 16, 10, 41, 446130, tzinfo=dt.timezone.utc)
    assert abs(vc.clock_skew_seconds(stamp, local)) < 0.001
    assert abs(vc.clock_skew_seconds(stamp, local + dt.timedelta(seconds=90)) ) > 89
    assert abs(abs(vc.clock_skew_seconds(stamp, local - dt.timedelta(seconds=75))) - 75) < 0.001


# ----------------------------------------------------------------------------- broker wire rules
def test_submit_is_sent_once_and_ambiguity_is_named(tmp_path):
    for outcome in (TimeoutError("read timeout"), ConnectionError("reset"), (429, {"message": "too many requests"}),
                    (500, "oops"), (502, "<html>bad gateway</html>"), (503, {"message": "unavailable"})):
        b, t, sleeps = make(tmp_path, [outcome])
        with pytest.raises(vb.AmbiguousSubmit):
            b.submit_order("TQQQ", "buy", "v5-paper-20261116-dec-1", notional="20.00")
        assert len(t.calls) == 1 and sleeps == [], outcome     # exactly one attempt, no retry, no sleep
        assert t.calls[0]["method"] == "POST" and t.calls[0]["url"].endswith("/v2/orders")
        body = t.calls[0]["json"]
        assert body["symbol"] == "TQQQ" and body["side"] == "buy" and body["type"] == "market"
        assert body["time_in_force"] == "day" and body["client_order_id"] == "v5-paper-20261116-dec-1"
        assert float(body["notional"]) == 20.0 and body.get("qty") is None


def test_submit_rejections_carry_the_broker_code(tmp_path):
    cases = [("duplicate_client_order_id", 40010001), ("reject_too_large", 40310000), ("buy_notional_0_50", 42210000),
             ("sell_qty_above_position", 40310000), ("reject_gtc_fractional", 42210000)]
    for name, code in cases:
        f = fx(name)
        b, t, _ = make(tmp_path, [(f["status"], f["body"])])
        with pytest.raises(vb.BrokerRejected) as ei:
            b.submit_order("TQQQ", "sell", "v5-paper-20261116-dec-2", qty="1.5")
        assert ei.value.code == code and ei.value.status == f["status"] and f["body"]["message"] in ei.value.message
        assert not isinstance(ei.value, vb.BrokerUnavailable)
        assert len(t.calls) == 1


@pytest.mark.parametrize("kwargs", [{}, {"notional": "20.00", "qty": "1"}, {"notional": "20.001"}, {"qty": "0.1234567891"},
                                    {"notional": "abc"}, {"qty": "-1"}, {"notional": "-5.00"}, {"notional": "nan"}, {"qty": "inf"}])
def test_submit_validates_before_any_http_call(tmp_path, kwargs):
    b, t, _ = make(tmp_path, [])
    with pytest.raises(ValueError):
        b.submit_order("TQQQ", "buy", "v5-paper-20261116-dec-1", **kwargs)
    assert t.calls == []
    with pytest.raises(ValueError):
        b.submit_order("TQQQ", "hold", "v5-paper-20261116-dec-1", notional="5.00")
    assert t.calls == []


def test_submit_parses_the_recorded_answer(tmp_path):
    f = fx("buy_notional_20_TQQQ")
    b, t, _ = make(tmp_path, [(f["status"], f["body"])])
    o = b.submit_order("TQQQ", "buy", "v5probe-x", notional="20.00")
    assert o.status == "pending_new" and o.qty is None and o.notional == "20" and o.filled_qty == "0"
    assert o.filled_avg_price is None and o.symbol == "TQQQ" and o.side == "buy" and not vb.is_terminal(o.status)
    done = fx("buy_notional_20_TQQQ_final")
    b, t, _ = make(tmp_path, [(done["status"], done["body"])])
    o = b.get_order(done["body"]["id"])
    assert o.status == "filled" and o.filled_qty == "0.367004479" and abs(o.filled_avg_price - 54.468) < 1e-12
    assert o.filled_at is not None and o.filled_at.tzinfo is not None and vb.is_terminal(o.status)
    assert t.calls[0]["method"] == "GET" and t.calls[0]["url"].endswith("/v2/orders/" + done["body"]["id"])


def test_unknown_status_is_never_terminal():
    for st in ("new", "accepted", "pending_new", "partially_filled", "pending_cancel", "held", "stopped", "suspended",
               "something_new_from_alpaca", "", None):
        assert vb.is_terminal(st) is False, st
    for st in ("filled", "canceled", "expired", "rejected", "replaced", "done_for_day", "calculated"):
        assert vb.is_terminal(st) is True, st


def test_lookup_by_client_id_none_only_for_the_known_404(tmp_path):
    f = fx("get_by_client_order_id_unknown")
    b, t, _ = make(tmp_path, [(f["status"], f["body"])])
    assert b.get_order_by_client_id("v5-paper-20261116-dec-1") is None
    assert t.calls[0]["params"]["client_order_id"] == "v5-paper-20261116-dec-1"
    assert t.calls[0]["url"].endswith("/v2/orders:by_client_order_id")
    # any other failure must NOT read as "the broker does not know this order"
    for outcome in ((404, {"code": 40410001, "message": "something else"}), (404, "not json at all"), (403, {"code": 40310000, "message": "forbidden"}),
                    (401, {"message": "unauthorized"})):
        b, t, _ = make(tmp_path, [outcome])
        with pytest.raises(vb.BrokerError):
            b.get_order_by_client_id("v5-paper-20261116-dec-1")
    b, t, sleeps = make(tmp_path, [(500, "x")] * 5)
    with pytest.raises(vb.BrokerUnavailable):
        b.get_order_by_client_id("v5-paper-20261116-dec-1")
    assert len(t.calls) == 5 and sleeps == [0.5, 1, 2, 4]


def test_get_retries_then_recovers_and_post_delete_do_not(tmp_path):
    acc = fx("account")
    b, t, sleeps = make(tmp_path, [TimeoutError("t"), (429, {"message": "slow down"}), (503, "x"), (acc["status"], acc["body"])])
    a = b.get_account()
    assert a.status == "ACTIVE" and abs(a.equity - float(acc["body"]["equity"])) < 1e-9 and a.trading_blocked is False
    assert len(t.calls) == 4 and sleeps == [0.5, 1, 2]
    b, t, sleeps = make(tmp_path, [(500, "x")])
    with pytest.raises(vb.BrokerError):
        b.cancel_order("abc")
    assert len(t.calls) == 1 and sleeps == [] and t.calls[0]["method"] == "DELETE"
    b, t, _ = make(tmp_path, [(204, "")])
    assert b.cancel_order("abc") is None
    done, still_open = fx("buy_notional_20_TQQQ_final"), fx("buy_notional_20_TQQQ")
    for outcome in ((404, {"code": 40410000, "message": "order not found"}), (422, {"code": 42210000, "message": "order is not cancelable"})):
        b, t, _ = make(tmp_path, [outcome, (200, done["body"])])
        assert b.cancel_order("abc") is None           # refused, but proven terminal: success
        assert [c["method"] for c in t.calls] == ["DELETE", "GET"]
        b, t, _ = make(tmp_path, [outcome, (200, still_open["body"])])
        with pytest.raises(vb.BrokerRejected):
            b.cancel_order("abc")                      # refused and still open: the refusal is real
        b, t, _ = make(tmp_path, [outcome] + [(500, "x")] * 5)
        with pytest.raises(vb.BrokerError):
            b.cancel_order("abc")                      # terminal state cannot be proven: not a success
    b, t, _ = make(tmp_path, [(401, {"message": "unauthorized"})])
    with pytest.raises(vb.BrokerRejected):
        b.get_account()                                # a definite 4xx is not retried and not "unavailable"
    assert len(t.calls) == 1


def test_secret_never_in_errors_and_headers_not_in_call_args(tmp_path):
    b, t, _ = make(tmp_path, [(401, {"message": "unauthorized " + "x"})])
    with pytest.raises(vb.BrokerError) as ei:
        b.get_positions()
    text = str(ei.value) + repr(ei.value) + repr(getattr(ei.value, "body", ""))
    assert SECRET not in text and KEY not in text
    b, t, _ = make(tmp_path, [(200, "<<<garbage>>>")])
    with pytest.raises(vb.BrokerError) as ei:
        b.get_account()
    assert SECRET not in str(ei.value)


def test_positions_orders_and_listing(tmp_path):
    f = fx("positions_before")
    b, t, _ = make(tmp_path, [(f["status"], f["body"])])
    ps = b.get_positions()
    spy = [p for p in ps if p.symbol == "SPY"][0]
    assert isinstance(spy.qty, str) and spy.qty == "33.221113325" and spy.asset_class == "us_equity" and spy.market_value > 20000
    f = fx("list_orders_all_today")
    b, t, _ = make(tmp_path, [(f["status"], f["body"])])
    after = dt.datetime(2026, 11, 16, 14, 30, tzinfo=dt.timezone.utc)
    orders, complete = b.list_orders(after, symbol="TQQQ")
    assert complete is True and len(orders) == 10 and len({o.id for o in orders}) == 10
    q = t.calls[0]["params"]
    assert q["status"] == "all" and int(q["limit"]) == 500 and q["direction"] == "asc" and str(q["nested"]).lower() == "false"
    assert q["symbols"] == "TQQQ" and "2026-11-16T14:30:00" in q["after"]
    big = [dict(f["body"][0], id="id-%d" % i, client_order_id="c-%d" % i) for i in range(500)]
    b, t, _ = make(tmp_path, [(200, big)])
    orders, complete = b.list_orders(after)
    assert complete is False and len(orders) == 500
    dup = f["body"] + f["body"][:3]
    b, t, _ = make(tmp_path, [(200, dup)])
    orders, complete = b.list_orders(after)
    assert len(orders) == 10 and complete is True


def test_market_data_calls_name_their_feed_and_page(tmp_path):
    f = fx("daily_bars_QQQ_sip_all")
    bars = f["body"]["bars"]["QQQ"]
    page1 = {"bars": {"QQQ": bars[:200]}, "next_page_token": "TOKEN-2"}
    page2 = {"bars": {"QQQ": bars[200:]}, "next_page_token": None}
    b, t, _ = make(tmp_path, [(200, page1), (200, page2)])
    got = b.get_daily_bars("QQQ", dt.date(2025, 9, 22))
    assert len(got) == 291 and got[-1].t.astimezone(vc.ET).date() == dt.date(2026, 11, 16)
    assert abs(got[-2].c - 679.89) < 1e-9
    p1, p2 = t.calls[0]["params"], t.calls[1]["params"]
    assert p1["feed"] == "sip" and p1["adjustment"] == "all" and p1["timeframe"] == "1Day" and p1["symbols"] == "QQQ"
    assert p2["page_token"] == "TOKEN-2" and t.calls[0]["url"].startswith("https://data.alpaca.markets")
    lt = fx("latest_trade_QQQ_iex")
    b, t, _ = make(tmp_path, [(lt["status"], lt["body"])])
    tr = b.get_latest_trade("QQQ")
    assert abs(tr.price - 667.62) < 1e-9 and tr.t.tzinfo is not None and t.calls[0]["params"]["feed"] == "iex"
    b, t, _ = make(tmp_path, [(200, {"bars": {}, "next_page_token": None})])
    assert b.get_minute_bars("QQQ", dt.datetime(2026, 11, 16, 14, 30, tzinfo=dt.timezone.utc)) == []
    assert t.calls[0]["params"]["feed"] == "iex" and t.calls[0]["params"]["timeframe"] == "1Min"
    ck = fx("clock")
    b, t, _ = make(tmp_path, [(ck["status"], ck["body"])])
    c = b.get_clock()
    assert c.is_open is True and c.timestamp.tzinfo is not None and c.next_close.astimezone(vc.ET).hour == 16
    assert c.timestamp.astimezone(dt.timezone.utc).replace(microsecond=0) == dt.datetime(2026, 11, 16, 16, 10, 41, tzinfo=dt.timezone.utc)


def test_live_broker_uses_live_host_and_live_keys(tmp_path):
    acc = fx("account")
    seen = {}

    def transport(method, url, params, json_body, timeout):
        seen["url"] = url
        return acc["status"], json.dumps(acc["body"])
    settings = vcfg.load_settings(env=env(TRADING_MODE="live"), app_dir=tmp_path)
    vb.AlpacaBroker(settings, transport=transport, sleep=lambda s: None).get_account()
    assert seen["url"].startswith("https://api.alpaca.markets/")


# ----------------------------------------------------------------------------- review findings (R1 Level 10)
import requests


class Boom(Exception):
    """An exception type the gateway has never heard of."""


AFTER = dt.datetime(2026, 11, 16, 14, 30, tzinfo=dt.timezone.utc)


def _order_body(**over):
    body = dict(fx("buy_notional_20_TQQQ")["body"])
    body.update(over)
    return body


@pytest.mark.parametrize("outcome", [
    requests.exceptions.ChunkedEncodingError("connection broken: IncompleteRead"),
    requests.exceptions.ContentDecodingError("bad gzip"),
    requests.exceptions.TooManyRedirects("loop"),
    requests.exceptions.RequestException("generic"),
    OSError("socket closed"), RuntimeError("boom"), Boom("unknown failure"),
    (200, "<<<not json>>>"), (200, ""), (201, "[]"), (200, "null"), (200, {"id": "only-an-id"}),
    (302, ""), (100, ""), (408, {"message": "request timeout"}), (425, {"message": "too early"}),
    (599, "x"), (204, ""),
])
def test_submit_never_reports_a_definite_failure_when_the_outcome_is_unknown(tmp_path, outcome):
    """After the request has left, only a definite refusal (4xx other than 408/425/429) may be reported as a
    refusal. Everything else is 'unknown outcome' and must say so, exactly once, with no retry."""
    b, t, sleeps = make(tmp_path, [outcome])
    with pytest.raises(vb.AmbiguousSubmit):
        b.submit_order("TQQQ", "buy", "v5-paper-20261116-dec-1", notional="20.00")
    assert len(t.calls) == 1 and sleeps == []


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422])
def test_submit_definite_refusals_stay_definite(tmp_path, status):
    b, t, _ = make(tmp_path, [(status, {"code": 12345, "message": "no"})])
    with pytest.raises(vb.BrokerRejected) as ei:
        b.submit_order("TQQQ", "buy", "v5-paper-20261116-dec-1", notional="20.00")
    assert not isinstance(ei.value, vb.BrokerUnavailable) and ei.value.status == status and len(t.calls) == 1


@pytest.mark.parametrize("exc", [
    requests.exceptions.ChunkedEncodingError("cut"), requests.exceptions.RequestException("generic"),
    OSError("socket"), RuntimeError("boom"), Boom("unknown"),
])
def test_reads_only_ever_raise_broker_errors(tmp_path, exc):
    b, t, sleeps = make(tmp_path, [exc] * 5)
    with pytest.raises(vb.BrokerUnavailable):
        b.get_account()
    assert len(t.calls) == 5 and sleeps == [0.5, 1, 2, 4]
    acc = fx("account")
    b, t, sleeps = make(tmp_path, [exc, (acc["status"], acc["body"])])
    assert b.get_account().status == "ACTIVE" and len(t.calls) == 2
    b, t, _ = make(tmp_path, [exc])
    with pytest.raises(vb.BrokerUnavailable):
        b.cancel_order("abc")
    assert len(t.calls) == 1


@pytest.mark.parametrize("body", ["null", "[]", "7", "\"text\"", "{}", "{\"id\": 1}"])
def test_wrong_shaped_answers_are_broker_errors_not_crashes(tmp_path, body):
    calls = [lambda b: b.get_account(), lambda b: b.get_order("x"), lambda b: b.get_clock(),
             lambda b: b.get_order_by_client_id("v5-x")]
    if body not in ("{}", "{\"id\": 1}"):          # an object without the key legitimately means "nothing there"
        calls += [lambda b: b.get_latest_trade("QQQ"), lambda b: b.get_daily_bars("QQQ", dt.date(2026, 1, 2)),
                  lambda b: b.get_minute_bars("QQQ", AFTER)]
    if body != "[]":
        calls += [lambda b: b.get_positions(), lambda b: b.get_open_orders(), lambda b: b.list_orders(AFTER),
                  lambda b: b.get_calendar(dt.date(2026, 11, 1), dt.date(2026, 11, 30))]
    for call in calls:
        b, t, _ = make(tmp_path, [(200, body)])
        with pytest.raises(vb.BrokerError):
            call(b)


@pytest.mark.parametrize("rows", ["[null]", "[7]", "[\"x\"]", "[{}]", "[[]]"])
def test_wrong_shaped_rows_are_broker_errors(tmp_path, rows):
    for call in (lambda b: b.get_positions(), lambda b: b.get_open_orders(), lambda b: b.list_orders(AFTER)):
        b, t, _ = make(tmp_path, [(200, rows)])
        with pytest.raises(vb.BrokerError):
            call(b)
    if rows != "[{}]":
        b, t, _ = make(tmp_path, [(200, rows)])
        with pytest.raises(vb.BrokerError):
            b.get_calendar(dt.date(2026, 11, 1), dt.date(2026, 11, 30))
    b, t, _ = make(tmp_path, [(200, "{\"bars\": {\"QQQ\": " + rows + "}, \"next_page_token\": null}")])
    with pytest.raises(vb.BrokerError):
        b.get_daily_bars("QQQ", dt.date(2026, 1, 2))


def test_positions_list_404_is_not_an_empty_account(tmp_path):
    """A 'not found' for the positions LIST must never read as 'flat': flat is 200 with an empty list."""
    b, t, _ = make(tmp_path, [(404, {"code": 40410000, "message": "position does not exist"})])
    with pytest.raises(vb.BrokerError):
        b.get_positions()
    b, t, _ = make(tmp_path, [(200, [])])
    assert b.get_positions() == []
    b, t, _ = make(tmp_path, [(200, {"positions": []})])
    with pytest.raises(vb.BrokerError):
        b.get_positions()


def test_paging_is_bounded(tmp_path):
    page = {"bars": {"QQQ": [{"t": "2026-11-16T14:30:00Z", "o": 1, "h": 2, "l": 1, "c": 2, "v": 3}]}, "next_page_token": "SAME"}
    calls = []

    def transport(method, url, params, json_body, timeout):
        calls.append(params)
        if len(calls) > 1000:
            raise AssertionError("unbounded paging")
        return 200, json.dumps(page)
    settings = vcfg.load_settings(env=env(), app_dir=tmp_path)
    b = vb.AlpacaBroker(settings, transport=transport, sleep=lambda s: None)
    with pytest.raises(vb.BrokerError):
        b.get_minute_bars("QQQ", AFTER)
    assert len(calls) <= 200


@pytest.mark.parametrize("kwargs", [{"qty": "1e3"}, {"notional": "1E2"}, {"qty": "+5"}, {"qty": " 5"}, {"qty": "5 "}, {"qty": "5_0"},
                                    {"notional": "٥"}, {"notional": "0"}, {"notional": "0.00"}, {"qty": "0.000000000"},
                                    {"qty": "5."}, {"qty": ".5"}, {"notional": 20.0}, {"qty": 1}, {"notional": "Infinity"},
                                    {"notional": ""}, {"qty": "1,5"}, {"qty": "5\n"}])
def test_sizes_must_be_plain_decimal_strings(tmp_path, kwargs):
    b, t, _ = make(tmp_path, [])
    with pytest.raises(ValueError):
        b.submit_order("TQQQ", "buy", "v5-paper-20261116-dec-1", **kwargs)
    assert t.calls == []


@pytest.mark.parametrize("kwargs", [{"notional": "1"}, {"notional": "1.5"}, {"notional": "20.00"}, {"notional": "0.01"},
                                    {"qty": "0.000000001"}, {"qty": "33.221113325"}, {"qty": "7"}, {"notional": "490.12"}])
def test_plain_sizes_are_sent_verbatim(tmp_path, kwargs):
    f = fx("buy_notional_20_TQQQ")
    b, t, _ = make(tmp_path, [(f["status"], f["body"])])
    b.submit_order("TQQQ", "sell" if "qty" in kwargs else "buy", "v5-paper-20261116-dec-1", **kwargs)
    key, value = next(iter(kwargs.items()))
    assert t.calls[0]["json"][key] == value and isinstance(t.calls[0]["json"][key], str)


@pytest.mark.parametrize("symbol,client_id", [("TQQQ", ""), ("TQQQ", None), ("TQQQ", "x" * 129), ("TQQQ", 12345), ("TQQQ", "has space"),
                                               ("TQQQ", "tab\tid"), ("TQQQ", "new\nline"),
                                               ("", "v5-paper-20261116-dec-1"), (None, "v5-paper-20261116-dec-1"), ("TQ QQ", "v5-paper-20261116-dec-1")])
def test_an_order_must_always_carry_our_id_and_a_symbol(tmp_path, symbol, client_id):
    b, t, _ = make(tmp_path, [])
    with pytest.raises(ValueError):
        b.submit_order(symbol, "buy", client_id, notional="5.00")
    assert t.calls == []


def test_fake_broker_validates_like_the_real_gateway():
    from tests_v5.fakebroker import FakeBroker
    fake = FakeBroker(cash="100", prices={"TQQQ": "10"})
    for kwargs in ({"qty": "1e1"}, {"notional": "+5"}, {"notional": 5.0}, {"qty": "5."}):
        with pytest.raises(ValueError):
            fake.submit_order("TQQQ", "buy", "v5-paper-20261116-dec-1", **kwargs)
    for symbol, client_id in (("TQQQ", ""), ("TQQQ", None), ("", "ok-id"), ("TQQQ", "x" * 129)):
        with pytest.raises(ValueError):
            fake.submit_order(symbol, "buy", client_id, notional="5.00")
    assert fake.orders == []


def test_null_fill_fields_do_not_become_text(tmp_path):
    b, t, _ = make(tmp_path, [(200, _order_body(filled_qty=None, filled_avg_price=None))])
    o = b.get_order("abc")
    assert o.filled_qty == "0" and o.filled_avg_price is None
    b, t, _ = make(tmp_path, [(200, [_order_body(id="a1", client_order_id="c1", qty=None, notional=None)])])
    assert b.get_open_orders()[0].qty is None


def test_default_transport_never_follows_redirects_and_sends_keys_only_in_headers(tmp_path, monkeypatch):
    seen = {}

    class Resp:
        status_code, text = 200, json.dumps(fx("account")["body"])

    def fake_request(self, method=None, url=None, **kw):
        seen.update(kw, method=method, url=url)
        return Resp()
    monkeypatch.setattr(requests.sessions.Session, "request", fake_request)      # nothing may reach the network
    settings = vcfg.load_settings(env=env(), app_dir=tmp_path)
    vb.AlpacaBroker(settings).get_account()
    assert seen["allow_redirects"] is False and seen["timeout"] == 15
    assert seen["headers"]["APCA-API-KEY-ID"] == KEY and seen["headers"]["APCA-API-SECRET-KEY"] == SECRET
    assert SECRET not in seen["url"] and KEY not in seen["url"] and not seen.get("params")


def test_ntfy_settings_are_normalised_and_the_topic_is_not_printed(tmp_path):
    s = vcfg.load_settings(env=env(NTFY_TOPIC="  my-private-topic \n", NTFY_BASE_URL=""), app_dir=tmp_path)
    assert s.ntfy_topic == "my-private-topic" and s.ntfy_base_url == "https://ntfy.sh"
    assert "my-private-topic" not in repr(s) and "my-private-topic" not in str(s)
    s = vcfg.load_settings(env=env(NTFY_TOPIC="   ", NTFY_BASE_URL="https://ntfy.example.org/"), app_dir=tmp_path)
    assert s.ntfy_topic == "" and s.ntfy_base_url == "https://ntfy.example.org"
    for bad in ("ntfy.sh", "ftp://ntfy.sh", "https://", "javascript:alert(1)"):
        with pytest.raises(vcfg.ConfigError):
            vcfg.load_settings(env=env(NTFY_BASE_URL=bad), app_dir=tmp_path)
    for bad in ("a/b", "topic with space", "t?x=1", "t#frag", "x" * 65):
        with pytest.raises(vcfg.ConfigError):
            vcfg.load_settings(env=env(NTFY_TOPIC=bad), app_dir=tmp_path)


def test_state_refuses_non_finite_numbers_both_ways(tmp_path):
    p = tmp_path / "rails.json"
    vs.atomic_write_json(p, {"peak": 491.08})
    for bad in ({"peak": float("nan")}, {"peak": float("inf")}, {"deep": [1, {"x": float("-inf")}]}):
        with pytest.raises(ValueError):
            vs.atomic_write_json(p, bad)
        assert vs.read_json(p) == {"peak": 491.08}            # the old content survives
    assert [x.name for x in tmp_path.iterdir()] == ["rails.json"]   # no temporary file left behind
    for text in ('{"peak": NaN}', '{"peak": Infinity}', '{"peak": -Infinity}'):
        q = tmp_path / "hand.json"
        q.write_text(text, encoding="utf-8")
        with pytest.raises(vs.StateCorrupt):
            vs.read_json(q)
        assert q.read_text(encoding="utf-8") == text


def test_atomic_write_refuses_what_json_cannot_hold_and_keeps_the_old_file(tmp_path):
    p = tmp_path / "x.json"
    vs.atomic_write_json(p, {"v": 1})
    for bad in ({"when": dt.datetime(2026, 11, 16)}, {"s": {1, 2}}, {"b": b"bytes"}):
        with pytest.raises(TypeError):
            vs.atomic_write_json(p, bad)
        assert vs.read_json(p) == {"v": 1}
    assert [x.name for x in tmp_path.iterdir()] == ["x.json"]


def test_lock_context_manager_refuses_to_run_unlocked(tmp_path):
    root = str(Path(__file__).resolve().parents[1])
    lock_path = tmp_path / "process.lock"
    holder = subprocess.Popen([sys.executable, "-c", LOCK_CHILD, str(lock_path), root, "60"], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "ACQUIRED"
        entered = False
        with pytest.raises(vs.LockBusy):
            with vs.ProcessLock(lock_path):
                entered = True
        assert entered is False
    finally:
        holder.kill()
        holder.wait(timeout=30)
    with vs.ProcessLock(lock_path) as lock:
        assert lock.acquired is True
        assert vs.ProcessLock(lock_path).acquire() is False      # a second handle in the same process is excluded too
    last = vs.ProcessLock(lock_path)
    assert last.acquire() is True
    last.release()


def test_same_process_double_acquire_and_release_are_safe(tmp_path):
    lock = vs.ProcessLock(tmp_path / "l.lock")
    assert lock.acquire() is True and lock.acquire() is True
    lock.release()
    lock.release()
    other = vs.ProcessLock(tmp_path / "l.lock")
    assert other.acquire() is True
    other.release()


def test_calendar_cache_rejects_garbage(tmp_path):
    for bad in (None, {}, "x", [1], [{}], [{"date": "2026-11-16", "open": "2026-11-16T14:30:00", "close": "2026-11-16T21:00:00"}],
                [{"date": "2026-11-16", "open": "2026-11-16T21:00:00+00:00", "close": "2026-11-16T14:30:00+00:00"}]):
        with pytest.raises(ValueError):
            vc.SessionCalendar.from_json(bad)


def test_clock_skew_formats(tmp_path):
    local = dt.datetime(2026, 11, 16, 16, 0, 0, tzinfo=dt.timezone.utc)
    for stamp in ("2026-11-16T11:00:00-05:00", "2026-11-16T16:00:00Z", "2026-11-16T16:00:00.000000000Z",
                  "2026-11-16T11:00:00.5-05:00", "2026-11-16T16:00:00.123456789+00:00"):
        assert abs(vc.clock_skew_seconds(stamp, local)) < 1.0, stamp
    for bad in ("", "not a time", "2026-11-16T16:00:00", None, 12345):
        with pytest.raises(ValueError):
            vc.clock_skew_seconds(bad, local)
    with pytest.raises(ValueError):
        vc.clock_skew_seconds("2026-11-16T16:00:00Z", dt.datetime(2026, 11, 16, 16, 0, 0))


# ----------------------------------------------------------------------------- reviewer hardening after fix round 1
@pytest.mark.parametrize("field,value", [("trading_blocked", None), ("trading_blocked", "false"), ("trading_blocked", 0),
                                          ("account_blocked", None), ("account_blocked", "true"), ("account_blocked", 1)])
def test_block_flags_must_be_real_booleans(tmp_path, field, value):
    """A null flag must never read as 'not blocked'."""
    body = dict(fx("account")["body"])
    assert isinstance(body[field], bool)
    body[field] = value
    b, t, _ = make(tmp_path, [(200, body)])
    with pytest.raises(vb.BrokerError):
        b.get_account()


@pytest.mark.parametrize("value", [None, "true", "false", 1, 0])
def test_market_open_flag_must_be_a_real_boolean(tmp_path, value):
    body = dict(fx("clock")["body"])
    assert isinstance(body["is_open"], bool)
    body["is_open"] = value
    b, t, _ = make(tmp_path, [(200, body)])
    with pytest.raises(vb.BrokerError):
        b.get_clock()


@pytest.mark.parametrize("bad", ["", "a/b", "../account", "a b", "x?y=1", "x#y", None, 5, "a" * 65, "id\n"])
def test_ids_and_symbols_cannot_reshape_the_request_path(tmp_path, bad):
    for call in (lambda b: b.get_order(bad), lambda b: b.cancel_order(bad), lambda b: b.get_latest_trade(bad)):
        b, t, _ = make(tmp_path, [])
        with pytest.raises(ValueError):
            call(b)
        assert t.calls == []
    done = fx("buy_notional_20_TQQQ_final")
    b, t, _ = make(tmp_path, [(200, done["body"])])
    assert b.get_order(done["body"]["id"]).status == "filled"                    # a real broker id still passes
