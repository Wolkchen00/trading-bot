"""R3 adversarial suite (written by Claude from the contract, before reading the implementation).

The executor moves real money, so these tests attack the ways an order can be sent twice, sent when it must not
be, or be believed to have failed when it did not: a process death at every single step, ambiguous submissions,
orders that surface late, refusals, foreign items, and the bookkeeping of the overlay fraction.
"""
import datetime as dt
import json
import math
import os
import random
from decimal import Decimal
from pathlib import Path

import pytest

from tests_v5.fakebroker import FakeBroker, invisible, partial, reject
from v5 import broker as vb
from v5 import clock as vc
from v5 import config as vcfg
from v5 import data as vd
from v5 import executor as ex
from v5 import state as vs
from v5.packages import get_package
from v5.strategy import READY, Decision, decide

UTC = dt.timezone.utc
FIX = Path(__file__).parent / "fixtures" / "alpaca"
DAY = dt.date(2026, 11, 16)
OPEN = dt.datetime(2026, 11, 16, 14, 30, tzinfo=UTC)
CLOSE = dt.datetime(2026, 11, 16, 21, 0, tzinfo=UTC)
SESSION = vc.Session(DAY, OPEN, CLOSE)
WINDOW = vc.window_for(SESSION)
T0 = WINDOW.decide_from + dt.timedelta(seconds=30)
PARAMS = get_package("high")
PRODUCT = PARAMS.product_symbol


def fx(name):
    return json.loads((FIX / (name + ".json")).read_text(encoding="utf-8"))


class Clock:
    def __init__(self, now=T0):
        self.now = now
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += dt.timedelta(seconds=float(seconds))

    def advance(self, seconds):
        self.now += dt.timedelta(seconds=seconds)


def settings_for(tmp_path, mode="paper", enabled=False):
    env = {"TRADING_MODE": mode, "ALPACA_PAPER_API_KEY": "k", "ALPACA_PAPER_SECRET_KEY": "s",
           "ALPACA_LIVE_API_KEY": "lk", "ALPACA_LIVE_SECRET_KEY": "ls", "NTFY_TOPIC": "t"}
    if enabled:
        env["LIVE_TRADING_ENABLED"] = "true"
    s = vcfg.load_settings(env=env, app_dir=tmp_path)
    vs.ensure_state_dir(s.state_dir)
    return s


def world(tmp_path, cash="490", qty=None, price="50", now=T0, mode="paper", enabled=False, extra=None, broker=None):
    positions = {} if qty is None else {PRODUCT: qty}
    prices = {PRODUCT: price}
    for sym, (q, p) in (extra or {}).items():
        positions[sym] = q
        prices[sym] = p
    fb = broker or FakeBroker(cash=cash, positions=positions, prices=prices)
    clock = Clock(now)
    s = settings_for(tmp_path, mode, enabled)
    return fb, clock, executor(fb, s, clock), s


def executor(fb, s, clock):
    return ex.Executor(fb, s, s.state_dir, PARAMS, clock, clock.sleep)


def decision(core_new, ovl_new, core_held=0.0, ovl_held=0.0):
    total = core_new + ovl_new
    return Decision(status=READY, reason_code=None, trend_score=1.0, vol=0.2, exposure=1.5, core_target=core_new,
                    ibs=0.5, wanted=ovl_new > 0, overlay_data_missing=False,
                    core_trade=abs(core_new - core_held) > 1e-12, core_held=core_held, ovl_held=ovl_held,
                    core_new=core_new, ovl_new=ovl_new, total_new=total, trade=True,
                    overlay_fraction_new=(ovl_new / total if total > 0 else 0.0), reason_keys=("TREND_UP",))


def inputs():
    closes = tuple(600.0 + i * 0.1 for i in range(260))
    dates = tuple(dt.date(2025, 1, 1) + dt.timedelta(days=i) for i in range(260))
    return vd.DecisionInputs(closes=closes, close_dates=dates, px=630.0, px_time=T0 - dt.timedelta(seconds=10),
                             px_source="iex_trade", session_high=632.0, session_low=625.0, range_known=True,
                             range_reason=None, range_bars=380, range_coverage=1.0)


def new_record(e, s, core_new, ovl_new, day=DAY, purpose="dec", dry_run=False):
    rec = ex.SessionRecord.new(s.state_dir, day, s.mode, purpose=purpose, dry_run=dry_run)
    if purpose == "dec":
        e.attach_decision(rec, inputs(), decision(core_new, ovl_new))
    else:
        e.attach_floor(rec)
    return rec


def drive(e, rec, clock, window=WINDOW, ticks=40, step=20):
    """Call run_chain like the runner would: every tick until the record is final."""
    for _ in range(ticks):
        rec = e.run_chain(rec, window)
        if rec.is_final:
            return rec
        clock.advance(step)
    return rec


def weight(fb):
    eq = float(fb.equity)
    return float(fb.positions.get(PRODUCT, Decimal(0)) * fb.prices[PRODUCT]) / eq if eq else 0.0


def product_orders(fb):
    return [o for o in fb.orders if o.symbol == PRODUCT]


def submits(fb):
    return [c for c in fb.calls if c[0] == "submit_order"]


def all_client_ids(rec):
    return [i["client_order_id"] for i in rec.data["intents"] if i.get("client_order_id")]


# ============================================================================ sizing (pure)
def snap(equity, cash, qty, price, weight_=None):
    w = (float(Decimal(qty) * Decimal(str(price))) / equity) if weight_ is None else weight_
    return ex.Snapshot(equity=equity, cash=cash, product_qty=qty, product_qty_available=qty,
                       product_price=float(price) if Decimal(qty) > 0 else None, product_weight=w,
                       foreign_positions=(), foreign_orders=(), foreign_product_orders=(), own_open_orders=())


def test_sizing_kinds_and_rounding():
    p = ex.plan_order(snap(490.0, 490.0, "0", 50), 0.30, PARAMS)
    assert p.kind == "buy" and p.side == "buy" and Decimal(p.notional) == Decimal("147") and p.qty is None
    assert abs(p.desired_usd - 147.0) < 1e-9 and p.capped is False
    p = ex.plan_order(snap(490.0, 490.0, "0", 50), 0.3333333, PARAMS)           # floors to cents, never rounds up
    assert Decimal(p.notional) == Decimal("163.33") and p.desired_usd > 163.33
    p = ex.plan_order(snap(500.0, 250.0, "5", 50), 0.20, PARAMS)
    assert p.kind == "sell" and p.side == "sell" and Decimal(p.qty) == Decimal("3") and p.notional is None
    p = ex.plan_order(snap(500.0, 250.0, "5", 50), 0.3333333, PARAMS)           # 9-decimal floor
    exact = Decimal("5") * (Decimal("0.5") - Decimal("0.3333333")) / Decimal("0.5")
    assert Decimal(p.qty) <= exact and exact - Decimal(p.qty) < Decimal("0.000000001")
    assert abs(float(p.desired_qty) - float(exact)) < 1e-9
    for plan in (p,):
        vb.validate_order_request(PRODUCT, plan.side, "v5-paper-20261116-dec-1", plan.notional, plan.qty)


def test_sizing_full_exit_is_exact_and_always_sent():
    for qty in ("5", "0.367004479", "0.000000001", "33.221113325"):
        p = ex.plan_order(snap(500.0, 250.0, qty, 50), 0.0, PARAMS)
        assert p.kind == "sell_all" and p.side == "sell" and p.qty == qty      # the broker's own string, verbatim
    p = ex.plan_order(snap(500.0, 500.0, "0", 50), 0.0, PARAMS)                 # flat and target 0: nothing to do
    assert p.kind == "none"


def test_sizing_sub_dollar_and_cash_cap():
    p = ex.plan_order(snap(500.0, 250.0, "5", 50), 0.5 + 0.9 / 500, PARAMS)     # +$0.90 wanted
    assert p.kind == "none" and p.capped is False
    p = ex.plan_order(snap(500.0, 250.0, "5", 50), 0.5 - 0.9 / 500, PARAMS)     # -$0.90 wanted
    assert p.kind == "none" and p.capped is False
    p = ex.plan_order(snap(500.0, 250.0, "5", 50), 0.5 + 1.1 / 500, PARAMS)
    assert p.kind == "buy" and Decimal(p.notional) == Decimal("1.10") or Decimal(p.notional) == Decimal("1.09")
    p = ex.plan_order(snap(500.0, 250.0, "5", 50), 0.5 - 1.1 / 500, PARAMS)
    assert p.kind == "sell" and Decimal(p.qty) > 0
    # the cash cap: wants $245 but only $100 cash -> 99.50 and marked capped
    p = ex.plan_order(snap(500.0, 100.0, "5", 50, weight_=0.5), 0.99, PARAMS)
    assert p.kind == "buy" and Decimal(p.notional) == Decimal("99.50") and p.capped is True
    assert abs(p.desired_usd - 245.0) < 1e-6
    # capped below one dollar: nothing can be sent
    p = ex.plan_order(snap(500.0, 0.60, "5", 50, weight_=0.5), 0.99, PARAMS)
    assert p.kind == "none" and p.capped is True
    p = ex.plan_order(snap(500.0, -3.0, "5", 50, weight_=0.5), 0.99, PARAMS)
    assert p.kind == "none" and p.capped is True


def test_sizing_never_sells_more_than_held_and_never_negative():
    rng = random.Random(7)
    for _ in range(4000):
        price = round(rng.uniform(1, 400), 2)
        qty = Decimal(str(round(rng.uniform(0, 30), rng.choice([0, 3, 9]))))
        cash = round(rng.uniform(0, 600), 2)
        equity = cash + float(qty) * price
        if equity <= 0:
            continue
        s = snap(equity, cash, format(qty, "f"), price)
        target = rng.choice([0.0, rng.uniform(0, 0.99), s.product_weight, 0.99])
        p = ex.plan_order(s, target, PARAMS)
        if p.kind in ("sell", "sell_all"):
            assert Decimal(p.qty) > 0 and Decimal(p.qty) <= qty and p.notional is None
            vb.validate_order_request(PRODUCT, "sell", "v5-paper-20261116-dec-1", None, p.qty)
        elif p.kind == "buy":
            assert Decimal(p.notional) >= Decimal("1.00") and float(p.notional) <= cash * 0.995 + 1e-9
            assert float(p.notional) <= (target - s.product_weight) * equity + 1e-9
            vb.validate_order_request(PRODUCT, "buy", "v5-paper-20261116-dec-1", p.notional, None)
        else:
            assert p.kind == "none" and p.qty is None and p.notional is None


def test_client_order_ids():
    assert ex.client_order_id("live", DAY, "dec", 1) == "v5-live-20261116-dec-1"
    assert ex.client_order_id("paper", DAY, "flr", 3) == "v5-paper-20261116-flr-3"
    for bad in (("live", DAY, "dec", 0), ("live", DAY, "dec", 4), ("live", DAY, "brk", 1), ("live", DAY, "dec", "1")):
        with pytest.raises(ValueError):
            ex.client_order_id(*bad)


# ============================================================================ snapshot and ownership
def test_snapshot_ownership_and_foreign_items(tmp_path):
    fb = FakeBroker(cash="300", positions={PRODUCT: "2", "SPY": "0.5", "DUST": "0.001"},
                    prices={PRODUCT: "50", "SPY": "600", "DUST": "100"})
    fb.script("never_fill", "never_fill", "never_fill")
    fb.submit_order(PRODUCT, "buy", "v5-paper-20261116-dec-1", notional="5.00")      # ours (if persisted)
    fb.submit_order(PRODUCT, "buy", "v5-paper-20261116-dec-9", notional="5.00")      # v5 prefix, but not persisted
    fb.submit_order("SPY", "sell", "someone-else", qty="0.1")
    s = ex.take_snapshot(fb, PARAMS, {"v5-paper-20261116-dec-1"})
    assert [o.client_order_id for o in s.own_open_orders] == ["v5-paper-20261116-dec-1"]
    assert len(s.foreign_positions) == 1 and "SPY" in s.foreign_positions[0]          # DUST (10 cents) is ignored
    assert len(s.foreign_orders) == 2 and len(s.foreign_product_orders) == 1
    assert "v5-paper-20261116-dec-9" in " ".join(s.foreign_product_orders) or PRODUCT in " ".join(s.foreign_product_orders)
    assert len(s.foreign) == 3
    assert s.product_qty == "2" and abs(s.product_weight - 100 / float(fb.equity)) < 1e-9
    none = ex.take_snapshot(fb, PARAMS, set())
    assert none.own_open_orders == () and len(none.foreign_product_orders) == 2
    flat = ex.take_snapshot(FakeBroker(cash="490", prices={PRODUCT: "50"}), PARAMS, set())
    assert flat.product_qty == "0" and flat.product_weight == 0.0 and flat.foreign == ()


class Account(FakeBroker):
    """FakeBroker whose account answer can be overridden (status, flags, numbers)."""
    over = {}

    def get_account(self):
        a = super().get_account()
        return vb.Account(**{**a.__dict__, **self.over})


@pytest.mark.parametrize("over", [{"status": "ACCOUNT_UPDATED"}, {"trading_blocked": True}, {"account_blocked": True},
                                   {"equity": 0.0}, {"equity": -5.0}, {"equity": float("nan")}, {"equity": float("inf")},
                                   {"equity": 10.0}])                                 # 100 of product on 10 equity
def test_snapshot_refuses_an_unusable_account(over):
    fb = Account(cash="300", positions={PRODUCT: "2"}, prices={PRODUCT: "50"})
    fb.over = over
    with pytest.raises(ex.SnapshotInvalid):
        ex.take_snapshot(fb, PARAMS, set())


# ============================================================================ plain chains
def test_buy_from_flat_ends_done_with_one_order(tmp_path):
    fb, clock, e, s = world(tmp_path)
    rec = drive(e, new_record(e, s, 0.30, 0.0), clock)
    assert rec.status == ex.DONE and rec.is_final
    assert len(product_orders(fb)) == 1 and product_orders(fb)[0].client_order_id == "v5-paper-20261116-dec-1"
    assert abs(weight(fb) - 0.30) < 0.001 and e.load_overlay_fraction() == 0.0
    intent = rec.data["intents"][-1]
    assert intent["submit_started"] is True and abs(intent["theta"] - 1.0) < 1e-6 and intent["order_status"] == "filled"
    again = e.run_chain(ex.SessionRecord.load(s.state_dir, DAY), WINDOW)             # a final record never trades again
    assert again.status == ex.DONE and len(product_orders(fb)) == 1


def test_overlay_entry_and_exit_bookkeeping(tmp_path):
    fb, clock, e, s = world(tmp_path)
    rec = drive(e, new_record(e, s, 0.30, 1 / 3), clock)                              # core 0.30 + overlay 1/3
    assert rec.status == ex.DONE and abs(weight(fb) - (0.30 + 1 / 3)) < 0.002
    f1 = e.load_overlay_fraction()
    assert abs(f1 - (1 / 3) / (0.30 + 1 / 3)) < 1e-6
    # next session: overlay expires, core stays
    day2 = dt.date(2026, 11, 17)
    w = weight(fb)
    core_held, ovl_held = w * (1 - f1), w * f1
    rec2 = ex.SessionRecord.new(s.state_dir, day2, s.mode)
    e.attach_decision(rec2, inputs(), decision(core_held, 0.0, core_held, ovl_held))
    win2 = vc.window_for(vc.Session(day2, OPEN + dt.timedelta(days=1), CLOSE + dt.timedelta(days=1)))
    clock.now = win2.decide_from + dt.timedelta(seconds=30)
    rec2 = drive(e, rec2, clock, win2)
    assert rec2.status == ex.DONE and abs(weight(fb) - core_held) < 0.002
    assert e.load_overlay_fraction() == 0.0                                           # exactly: no rounding dust
    assert [o.client_order_id for o in product_orders(fb)] == ["v5-paper-20261116-dec-1", "v5-paper-20261117-dec-1"]


def test_full_exit_sells_the_exact_quantity_including_dust(tmp_path):
    fb, clock, e, s = world(tmp_path, cash="10", qty="0.367004479", price="54.47")
    rec = drive(e, new_record(e, s, 0.0, 0.0), clock)
    assert rec.status == ex.DONE and PRODUCT not in fb.positions and product_orders(fb)[0].qty == "0.367004479"
    fb2, clock2, e2, s2 = world(tmp_path / "b", cash="490", qty="0.000000007", price="54.47")   # worth nothing
    rec = drive(e2, new_record(e2, s2, 0.0, 0.0), clock2)
    assert rec.status == ex.DONE and PRODUCT not in fb2.positions and len(product_orders(fb2)) == 1


def test_small_change_is_netted_without_an_order(tmp_path):
    fb, clock, e, s = world(tmp_path, cash="250", qty="5", price="50")               # w = 0.5
    rec = drive(e, new_record(e, s, 0.30, 0.2009), clock)                            # +$0.45 net: bookkeeping only
    assert rec.status == ex.NETTED and rec.is_final and submits(fb) == []
    assert abs(e.load_overlay_fraction() - 0.2009 / 0.5009) < 1e-9


def test_opposing_change_reassigns_the_holding_without_a_trade(tmp_path):
    fb, clock, e, s = world(tmp_path, cash="333.5", qty="3.33", price="50")          # w = 0.333 held as core
    rec = drive(e, new_record(e, s, 0.0, 1 / 3), clock)                              # core exits, overlay enters
    assert rec.status == ex.NETTED and submits(fb) == []
    assert e.load_overlay_fraction() == pytest.approx(1.0)


def test_capped_when_cash_cannot_cover_one_dollar(tmp_path):
    fb = Account(cash="250", positions={PRODUCT: "5"}, prices={PRODUCT: "50"})
    fb.over = {"cash": 0.60}                                                          # the broker says: no cash
    fb2, clock, e, s = world(tmp_path, broker=fb)
    rec = drive(e, new_record(e, s, 0.99, 0.0), clock)
    assert rec.status == ex.CAPPED and rec.is_final and submits(fb) == []
    assert rec.data["intents"][-1]["theta"] == 0 and e.load_overlay_fraction() == 0.0


def test_nothing_is_started_at_or_after_the_submit_cutoff(tmp_path):
    for late in (WINDOW.submit_until, WINDOW.submit_until + dt.timedelta(seconds=1), WINDOW.cancel_at, CLOSE + dt.timedelta(minutes=2)):
        fb, clock, e, s = world(tmp_path / str(late.timestamp()), now=late)
        rec = drive(e, new_record(e, s, 0.30, 0.0), clock, ticks=5)
        assert submits(fb) == [] and product_orders(fb) == [], late
        assert rec.is_final and rec.status == ex.NOT_SUBMITTED, (late, rec.status)
    fb, clock, e, s = world(tmp_path / "just-in-time", now=WINDOW.submit_until - dt.timedelta(seconds=2))
    rec = drive(e, new_record(e, s, 0.30, 0.0), clock)
    assert rec.status == ex.DONE and len(product_orders(fb)) == 1


def test_run_chain_needs_a_target_and_guards_its_inputs(tmp_path):
    fb, clock, e, s = world(tmp_path)
    rec = ex.SessionRecord.new(s.state_dir, DAY, s.mode)
    with pytest.raises(ValueError):
        e.run_chain(rec, WINDOW)
    not_ready = decide([1.0] * 5, 1.0, 1.0, 1.0, 0.0, 0.0, PARAMS)                   # too short a history
    assert not_ready.status != READY
    with pytest.raises(ValueError):
        e.attach_decision(rec, inputs(), not_ready)
    hold = decision(0.3, 0.0, 0.3, 0.0)
    hold = Decision(**{**hold.__dict__, "trade": False})
    with pytest.raises(ValueError):
        e.attach_decision(rec, inputs(), hold)
    e.attach_decision(rec, inputs(), decision(0.3, 0.0))
    with pytest.raises(ValueError):
        e.attach_decision(rec, inputs(), decision(0.9, 0.0))                          # decided once per record
    assert rec.data["target"]["total_new"] == pytest.approx(0.3)
    with pytest.raises(ValueError):
        e.finalize(rec, ex.NO_TRADE)                                                  # a record with a target is not idle
    assert submits(fb) == []


def test_idle_records_can_be_marked_and_finalised(tmp_path):
    fb, clock, e, s = world(tmp_path)
    rec = ex.SessionRecord.new(s.state_dir, DAY, s.mode)
    assert rec.status == ex.NEW and not rec.is_final
    e.mark_not_ready(rec, "PRICE_STALE", "no fresh trade")
    assert ex.SessionRecord.load(s.state_dir, DAY).status == ex.NOT_READY and not rec.is_final
    e.finalize(rec, ex.HOLD_NOT_READY, "PRICE_STALE")
    loaded = ex.SessionRecord.load(s.state_dir, DAY)
    assert loaded.status == ex.HOLD_NOT_READY and loaded.is_final
    quiet = ex.SessionRecord.new(s.state_dir, dt.date(2026, 11, 18), s.mode)
    stay = decision(0.3, 0.0, 0.3, 0.0)
    stay = Decision(**{**stay.__dict__, "trade": False, "reason_keys": ("TREND_UP", "BAND_HOLD")})
    e.finalize(quiet, ex.NO_TRADE, "", inputs=inputs(), decision=stay)
    kept = ex.SessionRecord.load(s.state_dir, dt.date(2026, 11, 18))
    assert kept.status == ex.NO_TRADE and kept.is_final and kept.data["decision"]["reason_keys"] == ["TREND_UP", "BAND_HOLD"]
    assert vd.DecisionInputs.from_bundle(kept.data["inputs"]) == inputs() and not kept.data.get("target")
    with pytest.raises(ValueError):
        e.finalize(loaded, ex.NO_TRADE)
    assert ex.SessionRecord.load(s.state_dir, dt.date(2026, 11, 17)) is None
    assert submits(fb) == [] and not e.has_unresolved()


# ============================================================================ ambiguity
def test_timeout_before_accept_is_resubmitted_with_the_same_id(tmp_path):
    fb, clock, e, s = world(tmp_path)
    fb.script("timeout_before_accept")
    rec = drive(e, new_record(e, s, 0.30, 0.0), clock)
    assert rec.status == ex.DONE and len(product_orders(fb)) == 1
    ids = [c[1]["client_order_id"] for c in submits(fb)]
    assert ids == ["v5-paper-20261116-dec-1", "v5-paper-20261116-dec-1"]               # same id, never a new one
    assert sum(clock.sleeps) >= 19                                                    # it looked for about 20 s first
    assert abs(weight(fb) - 0.30) < 0.001


def test_timeout_after_accept_is_found_and_not_sent_again(tmp_path):
    fb, clock, e, s = world(tmp_path)
    fb.script("timeout_after_accept")
    rec = drive(e, new_record(e, s, 0.30, 0.0), clock)
    assert rec.status == ex.DONE and len(product_orders(fb)) == 1 and len(submits(fb)) == 1
    assert abs(weight(fb) - 0.30) < 0.001


def test_late_visible_order_is_never_duplicated(tmp_path):
    """Accepted, but lookups by client id see nothing for a long time."""
    fb, clock, e, s = world(tmp_path)
    fb.script("timeout_after_accept")
    real_lookup, hidden = fb.get_order_by_client_id, {"n": 10 ** 9}

    def lookup(cid):
        if hidden["n"] > 0:
            hidden["n"] -= 1
            fb.calls.append(("get_order_by_client_id", {"client_order_id": cid}))
            return None
        return real_lookup(cid)
    fb.get_order_by_client_id = lookup
    rec = drive(e, new_record(e, s, 0.30, 0.0), clock, ticks=8)
    assert rec.status == ex.UNRESOLVED and not rec.is_final and e.has_unresolved()
    assert len(product_orders(fb)) == 1                                                # duplicate-id refusals only
    assert 1 <= len(submits(fb)) <= 3
    assert {c[1]["client_order_id"] for c in submits(fb)} == {"v5-paper-20261116-dec-1"}
    hidden["n"] = 0                                                                   # the order surfaces
    rec = drive(e, ex.SessionRecord.load(s.state_dir, DAY), clock)
    assert rec.status == ex.DONE and len(product_orders(fb)) == 1 and not e.has_unresolved()
    assert abs(weight(fb) - 0.30) < 0.001 and abs(rec.data["intents"][-1]["theta"] - 1.0) < 1e-6


def test_unknown_outcome_at_the_cutoff_stays_unresolved_and_blocks_everything(tmp_path):
    fb, clock, e, s = world(tmp_path, now=WINDOW.submit_until - dt.timedelta(seconds=5))
    fb.script("timeout_before_accept")
    rec = drive(e, new_record(e, s, 0.30, 0.0), clock, ticks=3)
    assert rec.status == ex.UNRESOLVED and len(submits(fb)) == 1 and product_orders(fb) == []
    assert e.has_unresolved()
    # the next session may not trade, neither the strategy nor the floor
    day2 = dt.date(2026, 11, 17)
    win2 = vc.window_for(vc.Session(day2, OPEN + dt.timedelta(days=1), CLOSE + dt.timedelta(days=1)))
    clock.now = win2.decide_from + dt.timedelta(seconds=30)
    for purpose in ("dec", "flr"):
        rec2 = new_record(e, s, 0.30, 0.0, day=day2, purpose=purpose)
        rec2 = drive(e, rec2, clock, win2, ticks=3)
        assert not rec2.is_final and rec2.data.get("hold") == "UNRESOLVED", purpose
    assert len(submits(fb)) == 1
    # after the close: complete listing without the id and an unchanged quantity -> proven not sent
    clock.now = CLOSE + dt.timedelta(days=1, minutes=2)
    rec = e.reconcile(ex.SessionRecord.load(s.state_dir, DAY), SESSION)
    assert rec.status == ex.FAILED and rec.is_final and not e.has_unresolved()
    assert rec.data["intents"][-1]["theta"] == 0 and e.load_overlay_fraction() == 0.0


@pytest.mark.parametrize("doubt", ["incomplete_listing", "listing_down", "quantity_changed", "order_in_listing"])
def test_reconcile_never_assumes_absence(tmp_path, doubt):
    fb, clock, e, s = world(tmp_path, now=WINDOW.submit_until - dt.timedelta(seconds=5))
    fb.script("timeout_before_accept" if doubt != "order_in_listing" else "timeout_after_accept")
    real_lookup = fb.get_order_by_client_id
    fb.get_order_by_client_id = lambda cid: None                                      # lookups by id stay blind
    rec = drive(e, new_record(e, s, 0.30, 0.0), clock, ticks=3)
    assert rec.status == ex.UNRESOLVED
    clock.now = CLOSE + dt.timedelta(minutes=2)
    real_list = fb.list_orders
    if doubt == "incomplete_listing":
        fb.list_orders = lambda after, symbol=None: (real_list(after, symbol)[0], False)
    elif doubt == "listing_down":
        def down(after, symbol=None):
            raise vb.BrokerUnavailable("down")
        fb.list_orders = down
    elif doubt == "quantity_changed":
        fb.positions[PRODUCT] = Decimal("1")                                           # something did happen
    rec = e.reconcile(ex.SessionRecord.load(s.state_dir, DAY), SESSION)
    assert rec.status != ex.FAILED and rec.status != ex.NOT_SUBMITTED, doubt
    if doubt != "order_in_listing":
        assert rec.status == ex.UNRESOLVED and not rec.is_final and e.has_unresolved(), doubt
        again = e.reconcile(ex.SessionRecord.load(s.state_dir, DAY), SESSION)         # asking twice changes nothing
        assert again.status == ex.UNRESOLVED
    else:
        if not rec.is_final:
            assert rec.status == ex.UNRESOLVED
            fb.get_order_by_client_id = real_lookup                                   # later it is found by id
            rec = e.reconcile(ex.SessionRecord.load(s.state_dir, DAY), SESSION)
        assert rec.is_final and rec.status in (ex.DONE, ex.CAPPED, ex.PARTIAL) and not e.has_unresolved()
        assert abs(rec.data["intents"][-1]["theta"] - 1.0) < 1e-6
        assert abs(weight(fb) - 0.30) < 0.001 and len(product_orders(fb)) == 1


def test_slow_visibility_by_order_id_is_waited_out(tmp_path):
    fb, clock, e, s = world(tmp_path)
    fb.script(invisible(30))
    rec = drive(e, new_record(e, s, 0.30, 0.0), clock)
    assert rec.status == ex.DONE and len(product_orders(fb)) == 1 and len(submits(fb)) == 1


# ============================================================================ refusals, partial fills, cancel
def test_buying_power_refusal_gets_one_resized_attempt(tmp_path):
    fb, clock, e, s = world(tmp_path)
    fb.script(reject(403, 40310000, "insufficient buying power"))
    rec = drive(e, new_record(e, s, 0.30, 0.0), clock)
    assert rec.status == ex.DONE and len(product_orders(fb)) == 1
    assert [c[1]["client_order_id"] for c in submits(fb)] == ["v5-paper-20261116-dec-1", "v5-paper-20261116-dec-2"]
    assert rec.data["intents"][0]["theta"] == 0 and len(rec.data["intents"]) == 2
    fb, clock, e, s = world(tmp_path / "twice")
    fb.script(reject(403, 40310000, "insufficient buying power"), reject(403, 40310000, "insufficient buying power"))
    rec = drive(e, new_record(e, s, 0.30, 0.0), clock)
    assert rec.status == ex.FAILED and rec.is_final and len(submits(fb)) == 2 and product_orders(fb) == []


@pytest.mark.parametrize("refusal", [reject(422, 42210000, "invalid something"), reject(403, 40310100, "account restricted"),
                                      reject(401, None, "unauthorized"), reject(400, 40010000, "bad request")])
def test_other_refusals_end_the_chain_at_once(tmp_path, refusal):
    fb, clock, e, s = world(tmp_path)
    fb.script(refusal)
    rec = drive(e, new_record(e, s, 0.30, 0.0), clock)
    assert rec.status == ex.FAILED and rec.is_final and len(submits(fb)) == 1 and product_orders(fb) == []
    assert rec.data["intents"][-1]["error"] and e.load_overlay_fraction() == 0.0


def test_partial_fill_then_remainder(tmp_path):
    fb, clock, e, s = world(tmp_path)
    fb.script(partial("0.5"))
    rec = new_record(e, s, 0.30, 0.0)
    rec = e.run_chain(rec, WINDOW)                                                    # half filled, order still open
    assert not rec.is_final and len(product_orders(fb)) == 1
    fb.cancel_order(product_orders(fb)[0].id)                                         # the broker ends the order
    clock.advance(20)
    rec = drive(e, rec, clock)
    assert rec.status == ex.DONE and len(product_orders(fb)) == 2 and abs(weight(fb) - 0.30) < 0.002
    first, second = rec.data["intents"][0], rec.data["intents"][1]
    assert abs(first["theta"] - 0.5) < 0.01 and abs(first["fill_ratio"] - 0.5) < 0.01 and first["order_status"] == "canceled"
    assert second["client_order_id"] == "v5-paper-20261116-dec-2" and abs(second["theta"] - 1.0) < 0.01
    assert abs(float(product_orders(fb)[1].notional) - 73.5) < 0.2                    # only the remainder was bought


def test_open_order_is_cancelled_at_the_cancel_time(tmp_path):
    fb, clock, e, s = world(tmp_path)
    fb.script(partial("0.4"))
    rec = new_record(e, s, 0.30, 1 / 3)                                               # total 0.6333
    rec = e.run_chain(rec, WINDOW)
    assert not rec.is_final
    clock.now = WINDOW.cancel_at - dt.timedelta(seconds=30)
    rec = e.run_chain(rec, WINDOW)
    assert not rec.is_final and product_orders(fb)[0].status == "partially_filled"     # too early to cancel
    clock.now = WINDOW.cancel_at + dt.timedelta(seconds=1)
    rec = drive(e, rec, clock, ticks=3)
    assert rec.status == ex.PARTIAL and rec.is_final and product_orders(fb)[0].status == "canceled"
    assert len(product_orders(fb)) == 1                                               # too late for a second attempt
    theta = rec.data["intents"][0]["theta"]
    assert abs(theta - 0.4) < 0.01
    core_x, ovl_x = theta * 0.30, theta * (1 / 3)
    assert e.load_overlay_fraction() == pytest.approx(ovl_x / (core_x + ovl_x), abs=1e-6)


def test_order_that_never_fills_ends_failed_without_touching_the_fraction(tmp_path):
    fb, clock, e, s = world(tmp_path)
    fb.script("never_fill")
    rec = new_record(e, s, 0.30, 0.0)
    rec = e.run_chain(rec, WINDOW)
    clock.now = WINDOW.cancel_at + dt.timedelta(seconds=1)
    rec = drive(e, rec, clock, ticks=3)
    assert rec.status == ex.FAILED and rec.is_final and PRODUCT not in fb.positions
    assert product_orders(fb)[0].status == "canceled" and e.load_overlay_fraction() == 0.0


def test_at_most_three_attempts(tmp_path):
    fb, clock, e, s = world(tmp_path)
    fb.script("never_fill", "never_fill", "never_fill", "never_fill")
    rec = new_record(e, s, 0.30, 0.0)
    for _ in range(12):
        rec = e.run_chain(rec, WINDOW)
        if rec.is_final:
            break
        for o in product_orders(fb):
            if not vb.is_terminal(o.status):
                fb.cancel_order(o.id)                                                 # the broker gives up on it
        clock.advance(5)
    assert rec.is_final and rec.status == ex.FAILED
    assert [o.client_order_id for o in product_orders(fb)] == ["v5-paper-20261116-dec-%d" % i for i in (1, 2, 3)]


# ============================================================================ foreign items, floor, dry-run
def test_foreign_position_holds_and_clears_by_itself(tmp_path):
    fb, clock, e, s = world(tmp_path, extra={"SPY": ("0.5", "600")})
    rec = new_record(e, s, 0.30, 0.0)
    rec = drive(e, rec, clock, ticks=3)
    assert not rec.is_final and rec.data.get("hold") == "FOREIGN_ASSET" and submits(fb) == []
    assert "SPY" in json.dumps(rec.data)
    del fb.positions["SPY"]                                                           # the owner removed it
    rec = drive(e, rec, clock)
    assert rec.status == ex.DONE and not rec.data.get("hold") and len(product_orders(fb)) == 1


def test_stray_orders_are_never_touched(tmp_path):
    fb, clock, e, s = world(tmp_path, cash="490", qty="1", price="50")
    fb.script("never_fill", "never_fill")
    stray_product = fb.submit_order(PRODUCT, "sell", "v5-paper-20261116-dec-2", qty="0.5")    # looks like ours, is not
    rec = drive(e, new_record(e, s, 0.30, 0.0), clock, ticks=3)
    assert not rec.is_final and rec.data.get("hold") == "FOREIGN_ASSET"
    assert fb.get_order(stray_product.id).status == "pending_new"                     # not cancelled
    assert [c for c in fb.calls if c[0] == "cancel_order"] == [] and len(submits(fb)) == 1
    floor = drive(e, new_record(e, s, 0.0, 0.0, purpose="flr"), clock, ticks=3)       # a floor sale is blocked too
    assert not floor.is_final and len(submits(fb)) == 1


def test_floor_chain_sells_everything_and_ignores_foreign_positions(tmp_path):
    fb, clock, e, s = world(tmp_path, cash="100", qty="7.123456789", price="50", extra={"SPY": ("0.5", "600")})
    rec = drive(e, new_record(e, s, 0.0, 0.0, purpose="flr"), clock)
    assert rec.status == ex.DONE and PRODUCT not in fb.positions and fb.positions["SPY"] == Decimal("0.5")
    order = product_orders(fb)[0]
    assert order.client_order_id == "v5-paper-20261116-flr-1" and order.qty == "7.123456789" and order.side == "sell"
    assert e.load_overlay_fraction() == 0.0
    assert ex.SessionRecord.load(s.state_dir, DAY, purpose="flr").status == ex.DONE
    assert ex.SessionRecord.load(s.state_dir, DAY) is None                            # the strategy record is separate
    fb2, clock2, e2, s2 = world(tmp_path / "flat")
    flat = drive(e2, new_record(e2, s2, 0.0, 0.0, purpose="flr"), clock2)
    assert flat.status == ex.DONE and submits(fb2) == []


def test_dry_run_sends_nothing_and_commits_nothing(tmp_path):
    fb, clock, e, s = world(tmp_path, mode="live")                                    # live, flag not set
    assert s.orders_enabled is False
    rec = new_record(e, s, 0.30, 1 / 3, dry_run=True)
    rec = drive(e, rec, clock)
    assert rec.status == ex.DRY_RUN and rec.is_final
    assert submits(fb) == [] and [c for c in fb.calls if c[0] == "cancel_order"] == []
    assert e.load_overlay_fraction() == 0.0 and not e.has_unresolved()
    assert ex.SessionRecord.load(s.state_dir, DAY) is None                            # nothing in the live records
    dry = ex.SessionRecord.load(s.state_dir, DAY, dry_run=True)
    plan = dry.data["intents"][-1]
    assert plan["side"] == "buy" and abs(float(plan["notional"]) - 490 * (0.30 + 1 / 3)) < 0.02
    assert e.own_client_ids() == set()
    # a record that is not marked dry-run must never be driven while orders are disabled
    live_rec = new_record(e, s, 0.30, 0.0, day=dt.date(2026, 11, 17))
    before = len(fb.calls)
    with pytest.raises(ValueError):
        e.run_chain(live_rec, WINDOW)
    assert submits(fb) == [] and len(fb.calls) == before
    fb3, clock3, e3, s3 = world(tmp_path / "paper")                                  # and the other way round
    wrong = new_record(e3, s3, 0.30, 0.0, dry_run=True)
    with pytest.raises(ValueError):
        e3.run_chain(wrong, WINDOW)
    assert submits(fb3) == []


def test_live_enabled_trades_and_uses_live_ids(tmp_path):
    fb, clock, e, s = world(tmp_path, mode="live", enabled=True)
    rec = drive(e, new_record(e, s, 0.30, 0.0), clock)
    assert rec.status == ex.DONE and product_orders(fb)[0].client_order_id == "v5-live-20261116-dec-1"


# ============================================================================ process death at every step
class Crash(BaseException):
    """A process death: not an Exception, so nothing in the code under test may swallow it."""


class DyingBroker:
    """Delegates to a FakeBroker and dies before or after the n-th broker call."""

    def __init__(self, inner, die_at, when):
        self._inner, self._die_at, self._when, self.count = inner, die_at, when, 0

    def __getattr__(self, name):
        target = getattr(self._inner, name)
        if not callable(target) or name.startswith("_") or name in ("script", "set_price", "set_clock", "queue_fault"):
            return target

        def call(*args, **kwargs):
            self.count += 1
            if self.count == self._die_at and self._when == "before":
                raise Crash(name)
            try:
                return target(*args, **kwargs)
            finally:
                if self.count == self._die_at and self._when == "after":
                    raise Crash(name)
        return call


SCENARIOS = {
    "buy": dict(cash="490", qty=None, target=(0.30, 0.0), faults=()),
    "buy_with_overlay": dict(cash="490", qty=None, target=(0.30, 1 / 3), faults=()),
    "sell_part": dict(cash="250", qty="5", target=(0.20, 0.0), faults=()),
    "sell_all": dict(cash="250", qty="5", target=(0.0, 0.0), faults=()),
    "ambiguous_accepted": dict(cash="490", qty=None, target=(0.30, 0.0), faults=("timeout_after_accept",)),
    "ambiguous_lost": dict(cash="490", qty=None, target=(0.30, 0.0), faults=("timeout_before_accept",)),
    "slow_to_appear": dict(cash="490", qty=None, target=(0.30, 0.0), faults=(invisible(3),)),
}


def run_with_death(tmp_path, scenario, die_at, when, kind):
    sc = SCENARIOS[scenario]
    positions = {} if sc["qty"] is None else {PRODUCT: sc["qty"]}
    fb = FakeBroker(cash=sc["cash"], positions=positions, prices={PRODUCT: "50"})
    fb.script(*sc["faults"])
    s = settings_for(tmp_path)
    clock = Clock(T0)
    died = False
    if kind == "broker":
        dying = DyingBroker(fb, die_at, when)
        e = executor(dying, s, clock)
    else:
        e = executor(fb, s, clock)
    real_replace, n = os.replace, {"count": 0}

    def replace(src, dst):
        n["count"] += 1
        if kind == "disk" and n["count"] == die_at:
            if when == "before":
                raise Crash("replace")
            real_replace(src, dst)
            raise Crash("replace")
        return real_replace(src, dst)
    os.replace = replace
    try:
        rec = ex.SessionRecord.new(s.state_dir, DAY, s.mode)
        e.attach_decision(rec, inputs(), decision(*sc["target"]))
        for _ in range(40):
            rec = e.run_chain(rec, WINDOW)
            if rec.is_final:
                break
            clock.advance(20)
    except Crash:
        died = True
    finally:
        os.replace = real_replace
    return fb, s, clock, died


def finish_after_restart(fb, s, clock, target):
    """A new process: fresh objects, the same disk and the same broker."""
    clock = Clock(clock.now + dt.timedelta(seconds=7))
    e = executor(fb, s, clock)
    rec = ex.SessionRecord.load(s.state_dir, DAY)
    if rec is None:                                                                   # died before anything was saved
        rec = ex.SessionRecord.new(s.state_dir, DAY, s.mode)
    if not rec.data.get("target"):
        if rec.is_final:
            return e, rec, clock
        e.attach_decision(rec, inputs(), decision(*target))
    for _ in range(60):
        if rec.is_final:
            break
        rec = e.run_chain(rec, WINDOW)
        if rec.is_final:
            break
        clock.advance(20)
    if not rec.is_final:                                                              # still open: the after-close pass
        clock.now = CLOSE + dt.timedelta(minutes=2)
        rec = e.reconcile(rec, SESSION)
    return e, rec, clock


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
@pytest.mark.parametrize("kind,when", [("broker", "before"), ("broker", "after"), ("disk", "before"), ("disk", "after")])
def test_death_at_every_step_never_duplicates_and_always_converges(tmp_path, scenario, kind, when):
    sc = SCENARIOS[scenario]
    core_new, ovl_new = sc["target"]
    target = core_new + ovl_new
    start_w = 0.0 if sc["qty"] is None else 0.5
    deaths = 0
    for die_at in range(1, 400):
        fb, s, clock, died = run_with_death(tmp_path / ("%s-%s-%s-%d" % (scenario, kind, when, die_at)), scenario, die_at, when, kind)
        if not died:
            break                                                                     # the chain is shorter than die_at
        deaths += 1
        e, rec, clock = finish_after_restart(fb, s, clock, sc["target"])
        tag = (scenario, kind, when, die_at, rec.status)
        orders = product_orders(fb)
        ids = [o.client_order_id for o in orders]
        assert len(ids) == len(set(ids)), tag
        known = set(all_client_ids(rec))
        assert set(ids) <= known, tag                                                 # no order the record does not know
        assert rec.is_final, tag
        w = weight(fb)
        lo, hi = min(start_w, target) - 0.004, max(start_w, target) + 0.004
        assert lo <= w <= hi, tag + (w,)                                              # never beyond the target: no double fill
        filled = [o for o in orders if Decimal(o.filled_qty) > 0]
        assert len(filled) <= 1, tag                                                  # one decision, one executed order
        if rec.status in (ex.DONE, ex.NETTED):
            assert abs(w - target) <= 0.016, tag + (w,)
            expect = (ovl_new / target) if target > 0 else 0.0
            assert e.load_overlay_fraction() == pytest.approx(expect, abs=1e-6), tag
        else:
            assert rec.status in (ex.FAILED, ex.NOT_SUBMITTED, ex.PARTIAL, ex.CAPPED), tag
            if not filled:
                assert abs(w - start_w) < 1e-9 and e.load_overlay_fraction() == 0.0, tag
        assert not e.has_unresolved(), tag
    assert deaths >= 3, (scenario, kind, when, deaths)                                # the harness really killed something


def test_repeating_the_bookkeeping_after_a_crash_gives_the_same_fraction(tmp_path):
    fb, clock, e, s = world(tmp_path)
    fb.script(partial("0.5"))
    rec = new_record(e, s, 0.30, 1 / 3)
    rec = e.run_chain(rec, WINDOW)
    fb.cancel_order(product_orders(fb)[0].id)
    clock.now = WINDOW.submit_until + dt.timedelta(seconds=1)                         # no second attempt
    rec = drive(e, rec, clock, ticks=3)
    assert rec.status == ex.PARTIAL and rec.is_final
    committed = e.load_overlay_fraction()
    assert committed == pytest.approx((1 / 3) / (0.30 + 1 / 3), abs=1e-6)             # both parts moved by the same theta
    # pretend the process died right after the fill bookkeeping: the status is back to RECONCILING
    raw = vs.read_json(vs.session_record_path(s.state_dir, DAY))
    raw["status"] = ex.RECONCILING
    raw["closed"] = False
    vs.atomic_write_json(vs.session_record_path(s.state_dir, DAY), raw)
    rec = drive(executor(fb, s, clock), ex.SessionRecord.load(s.state_dir, DAY), clock, ticks=3)
    assert rec.status == ex.PARTIAL and rec.is_final and len(product_orders(fb)) == 1
    assert e.load_overlay_fraction() == pytest.approx(committed, abs=1e-12)


def test_corrupt_state_stops_everything(tmp_path):
    fb, clock, e, s = world(tmp_path)
    rec = new_record(e, s, 0.30, 0.0)
    path = vs.session_record_path(s.state_dir, DAY)
    path.write_text("{ broken", encoding="utf-8")
    with pytest.raises(vs.StateCorrupt):
        ex.SessionRecord.load(s.state_dir, DAY)
    with pytest.raises(vs.StateCorrupt):
        e.has_unresolved()
    assert path.read_text(encoding="utf-8") == "{ broken" and submits(fb) == []
    fb, clock, e, s = world(tmp_path / "ovl")
    (s.state_dir / "overlay.json").write_text("not json", encoding="utf-8")
    with pytest.raises(vs.StateCorrupt):
        e.load_overlay_fraction()
    rec = new_record(e, s, 0.30, 0.0)
    with pytest.raises(vs.StateCorrupt):
        e.run_chain(rec, WINDOW)
    assert submits(fb) == []
    for bad in ('{"fraction": 1.5}', '{"fraction": -0.1}', '{"fraction": "x"}', '[]'):
        (s.state_dir / "overlay.json").write_text(bad, encoding="utf-8")
        with pytest.raises(vs.StateCorrupt):
            e.load_overlay_fraction()


def test_unsaveable_record_means_no_order(tmp_path, monkeypatch):
    fb, clock, e, s = world(tmp_path)
    rec = new_record(e, s, 0.30, 0.0)

    def full(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", full)
    with pytest.raises(OSError):
        e.run_chain(rec, WINDOW)
    assert submits(fb) == [] and product_orders(fb) == []


def test_broker_trouble_during_a_step_changes_nothing_and_is_retried(tmp_path):
    fb, clock, e, s = world(tmp_path)
    rec = new_record(e, s, 0.30, 0.0)
    fb.script("http_5xx", "http_5xx", "http_429")
    rec = e.run_chain(rec, WINDOW)                                                    # the snapshot fails first
    assert not rec.is_final and submits(fb) == [] and rec.status in (ex.NEW, ex.PLANNED)
    clock.advance(20)
    rec = drive(e, rec, clock)
    assert rec.status == ex.DONE and len(product_orders(fb)) == 1


# ============================================================================ the overlay ledger against an independent book
def book_allocate(cb, ob, cn, on, theta, equity):
    """My own statement of the plan's Revision 9 rule, written without looking at the implementation."""
    core_move, overlay_move = cn - cb, on - ob
    if core_move * overlay_move < 0:                      # one part hands over to the other: no trade needed for that
        net = core_move + overlay_move
        if abs(core_move) >= abs(overlay_move):
            cx, ox = cb - overlay_move + theta * net, on
        else:
            cx, ox = cn, ob - core_move + theta * net
    else:
        cx, ox = cb + theta * core_move, ob + theta * overlay_move
    cx, ox = max(cx, 0.0), max(ox, 0.0)
    if ox * equity < 1.0:                                 # a part too small to ever trade joins the other part
        cx, ox = cx + ox, 0.0
    elif cx * equity < 1.0:
        cx, ox = 0.0, cx + ox
    total = cx + ox
    return cx, ox, (ox / total if total > 0 else 0.0)


def test_overlay_fraction_matches_an_independent_ledger(tmp_path):
    rng = random.Random(20261116)
    fb = FakeBroker(cash="490", prices={PRODUCT: "50"})
    s = settings_for(tmp_path)
    clock = Clock(T0)
    e = executor(fb, s, clock)
    book_fraction = 0.0
    day = DAY
    sessions = traded = 0
    while sessions < 220:
        day += dt.timedelta(days=1)
        if day.weekday() >= 5:
            continue
        sessions += 1
        sess = vc.Session(day, dt.datetime.combine(day, dt.time(14, 30), UTC), dt.datetime.combine(day, dt.time(21, 0), UTC))
        win = vc.window_for(sess)
        clock.now = win.decide_from + dt.timedelta(seconds=30)
        fb.set_price(PRODUCT, max(5.0, float(fb.prices[PRODUCT]) * (1 + rng.uniform(-0.08, 0.08))))
        w = weight(fb)
        core_held, ovl_held = w * (1 - book_fraction), w * book_fraction
        core_new = rng.choice([core_held, core_held, 0.0, rng.uniform(0.05, 0.6)])
        ovl_new = rng.choice([0.0, 0.0, ovl_held, min(1 / 3, max(0.0, 0.99 - core_new))])
        if abs(core_new - core_held) < 1e-12 and abs(ovl_new - ovl_held) < 1e-12:
            continue
        equity_before = float(fb.equity)
        cash_before = float(fb.cash)
        qty_before = fb.positions.get(PRODUCT, Decimal(0))
        fault = rng.choice([None, None, None, "partial", "reject", "ambiguous"])
        if fault == "partial":
            fb.script(partial(str(round(rng.uniform(0.3, 0.9), 2))))
        elif fault == "reject":
            fb.script(reject(422, 42210000, "refused for the test"))
        elif fault == "ambiguous":
            fb.script(rng.choice(["timeout_before_accept", "timeout_after_accept"]))
        rec = ex.SessionRecord.new(s.state_dir, day, s.mode)
        e.attach_decision(rec, inputs(), decision(core_new, ovl_new, core_held, ovl_held))
        for tick in range(40):
            rec = e.run_chain(rec, win)
            if rec.is_final:
                break
            if tick < 8:
                clock.advance(20)
            else:
                clock.now = max(clock.now + dt.timedelta(seconds=20), win.cancel_at + dt.timedelta(seconds=1))
        assert rec.is_final, (day, rec.status, fault)
        traded += 1
        # independent book: one attempt per session here (a partial is only cancelled after the submit cutoff)
        desired = (core_new + ovl_new - w) * equity_before
        qty_after = fb.positions.get(PRODUCT, Decimal(0))
        moved = qty_after - qty_before
        orders = [o for o in product_orders(fb) if o.client_order_id.startswith("v5-paper-%s-" % day.strftime("%Y%m%d"))]
        capped = desired > 0 and cash_before * 0.995 < desired
        if abs(desired) < 1.0 and core_new + ovl_new > 1e-12:
            theta = 1.0
            assert orders == [], (day, "a bookkeeping-only change sent an order")
        elif not orders or moved == 0:
            theta = 0.0
        elif orders[-1].status == "filled" and not capped:
            theta = 1.0                                                               # complete: only rounding is missing
        elif desired > 0:
            o = orders[-1]
            theta = min(1.0, float(Decimal(o.filled_qty)) * o.filled_avg_price / desired)
        else:
            want_qty = qty_before if core_new + ovl_new <= 1e-12 else qty_before * Decimal(repr((w - core_new - ovl_new) / w))
            theta = min(1.0, float(-moved / want_qty))
        core_x, ovl_x, book_fraction = book_allocate(core_held, ovl_held, core_new, ovl_new, theta, equity_before)
        got = e.load_overlay_fraction()
        assert got == pytest.approx(book_fraction, abs=2e-6), (day, fault, rec.status, theta, got, book_fraction)
        assert 0.0 <= got <= 1.0 and float(fb.cash) >= -1e-9 and qty_after >= 0
        assert weight(fb) <= 0.99 + 0.011, (day, weight(fb))
        assert not e.has_unresolved(), day
    assert traded > 120


# ============================================================================ decision data
def bars_from_fixture():
    rows = fx("daily_bars_QQQ_sip_all")["body"]["bars"]["QQQ"]
    return [vb.Bar(t=dt.datetime.fromisoformat(r["t"].replace("Z", "+00:00")), o=float(r["o"]), h=float(r["h"]),
                   l=float(r["l"]), c=float(r["c"]), v=float(r["v"])) for r in rows]


def calendar_from(bars):
    rows = [{"date": b.t.astimezone(vc.ET).date().isoformat(), "open": "09:30", "close": "16:00"} for b in bars]
    return vc.SessionCalendar(vc.parse_calendar(rows))


def minute_bars(start, count, step=1, price=667.0):
    out = []
    for i in range(count):
        t = start + dt.timedelta(minutes=i * step)
        out.append(vb.Bar(t=t, o=price, h=price + 1 + (i % 3) * 0.1, l=price - 1 - (i % 5) * 0.1, c=price + 0.2, v=100.0))
    return out


def data_world(now=None, drop=None):
    daily = bars_from_fixture()
    assert len(daily) == 291 and daily[-1].t.astimezone(vc.ET).date() == DAY
    cal = calendar_from(daily)
    now = now or (WINDOW.decide_from + dt.timedelta(seconds=40))
    fb = FakeBroker(cash="490", prices={PRODUCT: "50"})
    fb.daily_bars["QQQ"] = [b for i, b in enumerate(daily) if i not in (drop or ())]
    minutes = int((now - OPEN).total_seconds() // 60)
    fb.minute_bars["QQQ"] = minute_bars(OPEN, minutes)
    fb.latest_trades["QQQ"] = vb.Trade(t=now - dt.timedelta(seconds=20), price=667.62)
    return fb, cal, now


def test_inputs_happy_path_and_replay(tmp_path):
    fb, cal, now = data_world()
    got = vd.build_inputs(fb, cal, SESSION, now, PARAMS)
    assert isinstance(got, vd.DecisionInputs), got
    assert len(got.closes) == 260 and len(got.close_dates) == 260
    assert got.close_dates[-1] == dt.date(2026, 11, 13) and abs(got.closes[-1] - 679.89) < 1e-9   # today's bar dropped
    assert list(got.close_dates) == sorted(set(got.close_dates)) and DAY not in got.close_dates
    assert got.px == 667.62 and got.px_source == "iex_trade" and got.range_known and got.range_reason is None
    assert got.session_high == pytest.approx(668.2) and got.session_low == pytest.approx(665.6)
    back = vd.DecisionInputs.from_bundle(json.loads(json.dumps(got.to_bundle())))
    assert back == got
    d1 = decide(got.closes, got.px, got.session_high, got.session_low, 0.0, 0.0, PARAMS)
    d2 = decide(back.closes, back.px, back.session_high, back.session_low, 0.0, 0.0, PARAMS)
    assert d1 == d2 and d1.status == READY
    calls = [c[0] for c in fb.calls]
    assert calls.count("get_daily_bars") == 1 and calls.count("get_minute_bars") == 1
    daily_call = [c for c in fb.calls if c[0] == "get_daily_bars"][0][1]
    assert daily_call["feed"] == "sip" and daily_call["adjustment"] == "all" and daily_call["symbol"] == "QQQ"
    assert [c for c in fb.calls if c[0] == "get_minute_bars"][0][1]["feed"] == "iex"
    assert not [c for c in fb.calls if c[0] in ("submit_order", "cancel_order")]


def test_inputs_history_rules():
    fb, cal, now = data_world(drop=(289,))                                            # the previous session is missing
    got = vd.build_inputs(fb, cal, SESSION, now, PARAMS)
    assert isinstance(got, vd.InputsNotReady) and got.code == "HISTORY_STALE"
    fb, cal, now = data_world(drop=(200,))                                            # a hole inside the last 249
    assert vd.build_inputs(fb, cal, SESSION, now, PARAMS).code == "HISTORY_GAP"
    fb, cal, now = data_world(drop=(31, 33))                                          # holes older than the last 249
    got = vd.build_inputs(fb, cal, SESSION, now, PARAMS)
    assert isinstance(got, vd.DecisionInputs) and len(got.closes) == 258
    fb, cal, now = data_world()
    fb.daily_bars["QQQ"].insert(100, fb.daily_bars["QQQ"][100])                       # duplicate
    assert vd.build_inputs(fb, cal, SESSION, now, PARAMS).code == "HISTORY_GAP"
    fb, cal, now = data_world()
    bars = fb.daily_bars["QQQ"]
    bars[150], bars[151] = bars[151], bars[150]                                       # out of order
    assert vd.build_inputs(fb, cal, SESSION, now, PARAMS).code == "HISTORY_GAP"
    for bad in (float("nan"), 0.0, -3.0, float("inf")):
        fb, cal, now = data_world()
        b = fb.daily_bars["QQQ"][250]
        fb.daily_bars["QQQ"][250] = vb.Bar(t=b.t, o=b.o, h=b.h, l=b.l, c=bad, v=b.v)
        assert vd.build_inputs(fb, cal, SESSION, now, PARAMS).code == "HISTORY_GAP", bad
    fb, cal, now = data_world()
    short = vc.SessionCalendar([cal.get(d) for d in cal.previous(dt.date(2026, 11, 17), 200)])
    assert vd.build_inputs(fb, short, SESSION, now, PARAMS).code == "CALENDAR_MISSING"
    fb, cal, now = data_world()
    fb.daily_bars["QQQ"] = []
    assert vd.build_inputs(fb, cal, SESSION, now, PARAMS).code in ("HISTORY_STALE", "HISTORY_GAP")


def test_inputs_price_rules():
    fb, cal, now = data_world()
    fb.latest_trades["QQQ"] = vb.Trade(t=now - dt.timedelta(seconds=121), price=660.0)          # too old
    got = vd.build_inputs(fb, cal, SESSION, now, PARAMS)
    assert got.px_source == "iex_bar" and got.px == pytest.approx(667.2)
    fb.latest_trades["QQQ"] = vb.Trade(t=now - dt.timedelta(seconds=119), price=660.0)
    assert vd.build_inputs(fb, cal, SESSION, now, PARAMS).px == 660.0
    fb.latest_trades["QQQ"] = vb.Trade(t=now + dt.timedelta(seconds=6), price=1.0)              # from the future
    assert vd.build_inputs(fb, cal, SESSION, now, PARAMS).px_source == "iex_bar"
    for bad in (0.0, -1.0, float("nan"), float("inf")):
        fb.latest_trades["QQQ"] = vb.Trade(t=now - dt.timedelta(seconds=5), price=bad)
        assert vd.build_inputs(fb, cal, SESSION, now, PARAMS).px_source == "iex_bar", bad
    fb.latest_trades["QQQ"] = None
    assert vd.build_inputs(fb, cal, SESSION, now, PARAMS).px_source == "iex_bar"
    fb.minute_bars["QQQ"] = fb.minute_bars["QQQ"][:-6]                                          # last bar 6+ min old
    got = vd.build_inputs(fb, cal, SESSION, now, PARAMS)
    assert isinstance(got, vd.InputsNotReady) and got.code == "PRICE_STALE"
    fb.latest_trades["QQQ"] = vb.Trade(t=now - dt.timedelta(seconds=5), price=667.0)            # a fresh trade saves it
    got = vd.build_inputs(fb, cal, SESSION, now, PARAMS)
    assert isinstance(got, vd.DecisionInputs) and not got.range_known and got.range_reason == "RANGE_STALE"
    assert got.session_high is None and got.session_low is None


def test_inputs_range_rules():
    fb, cal, now = data_world()
    full = list(fb.minute_bars["QQQ"])
    fb.minute_bars["QQQ"] = []
    got = vd.build_inputs(fb, cal, SESSION, now, PARAMS)
    assert not got.range_known and got.range_reason == "RANGE_EMPTY" and got.range_bars == 0
    fb.minute_bars["QQQ"] = full[11:]
    assert vd.build_inputs(fb, cal, SESSION, now, PARAMS).range_reason == "RANGE_LATE_START"
    fb.minute_bars["QQQ"] = full[10:]
    assert vd.build_inputs(fb, cal, SESSION, now, PARAMS).range_known
    thin = [b for i, b in enumerate(full) if i % 10 < 6 or i < 3 or i > len(full) - 3]         # about 60% coverage
    fb.minute_bars["QQQ"] = thin
    got = vd.build_inputs(fb, cal, SESSION, now, PARAMS)
    assert not got.range_known and got.range_reason == "RANGE_THIN" and got.range_coverage < 0.7
    # bars outside the session and duplicates must not help or distort
    outside = [vb.Bar(t=OPEN - dt.timedelta(minutes=5), o=1, h=9999.0, l=0.01, c=1, v=1),
               vb.Bar(t=now + dt.timedelta(minutes=1), o=1, h=9999.0, l=0.01, c=1, v=1)]
    fb.minute_bars["QQQ"] = outside + full + full[:50]
    got = vd.build_inputs(fb, cal, SESSION, now, PARAMS)
    assert got.range_known and got.session_high == pytest.approx(668.2) and got.session_low == pytest.approx(665.6)
    assert got.range_bars == len(full)
    bad = [vb.Bar(t=full[40].t, o=1, h=float("nan"), l=600.0, c=1, v=1), vb.Bar(t=full[41].t, o=1, h=10.0, l=700.0, c=1, v=1),
           vb.Bar(t=full[42].t, o=1, h=700.0, l=-5.0, c=1, v=1)]
    fb.minute_bars["QQQ"] = full[:40] + bad + full[43:]
    got = vd.build_inputs(fb, cal, SESSION, now, PARAMS)
    assert got.session_high == pytest.approx(668.2) and got.session_low == pytest.approx(665.6) and got.range_bars == len(full) - 3


def test_inputs_never_raise_on_broker_trouble():
    for method, expected in (("get_daily_bars", None), ("get_minute_bars", None), ("get_latest_trade", None)):
        for exc, code in ((vb.BrokerUnavailable("down"), "BROKER_DOWN"), (vb.BrokerRejected(403, 40310000, "no", {}), "DATA_ERROR"),
                          (vb.BrokerError("garbled"), "DATA_ERROR")):
            fb, cal, now = data_world()

            def boom(*a, _exc=exc, **k):
                raise _exc
            setattr(fb, method, boom)
            got = vd.build_inputs(fb, cal, SESSION, now, PARAMS)
            assert isinstance(got, vd.InputsNotReady) and got.code == code, (method, code, got)


# ============================================================================ allocation bookkeeping (plan Revision 9)
def test_allocate_matches_my_book_everywhere():
    rng = random.Random(99)
    for _ in range(20000):
        cb, ob = rng.choice([0.0, rng.uniform(0, 0.7)]), rng.choice([0.0, 0.0, rng.uniform(0, 0.34)])
        cn, on = rng.choice([0.0, cb, rng.uniform(0, 0.99)]), rng.choice([0.0, ob, 1 / 3])
        theta = rng.choice([0.0, 1.0, rng.random()])
        equity = rng.choice([490.0, 5.0, 1_000_000.0])
        got = ex.allocate(cb, ob, cn, on, theta, equity)
        want = book_allocate(cb, ob, cn, on, theta, equity)
        assert got == pytest.approx(want, abs=1e-12), (cb, ob, cn, on, theta, equity)
        core_x, ovl_x, fraction = got
        assert core_x >= 0.0 and ovl_x >= 0.0 and 0.0 <= fraction <= 1.0
        assert ovl_x == 0.0 or ovl_x * equity >= 1.0, (ovl_x, equity)                 # the overlay: zero, or tradable
        assert ovl_x == 0.0 or core_x == 0.0 or core_x * equity >= 1.0, (core_x, ovl_x, equity)   # no crumb beside it
        if theta == 1.0 and min(cn, on) * equity >= 1.0:
            assert got[:2] == pytest.approx((cn, on), abs=1e-12)
        if (cn - cb) * (on - ob) >= 0 and theta == 0.0 and min(cb, ob) * equity >= 1.0:
            assert got[:2] == pytest.approx((cb, ob), abs=1e-12)                      # nothing filled, nothing moved
        expected_total = cb + ob + theta * ((cn - cb) + (on - ob))
        assert core_x + ovl_x == pytest.approx(max(expected_total, 0.0), abs=1e-9)    # holdings are never invented


def test_allocate_leaves_no_dust():
    # Codex's case: overlay exits while the core grows, the buy is floored to cents (theta just under 1)
    core_x, ovl_x, fraction = ex.allocate(0.20, 0.30, 0.56, 0.0, 0.9999998493294634, 1_000_000.0)
    assert ovl_x == 0.0 and fraction == 0.0 and core_x == pytest.approx(0.56, abs=1e-6)
    # an overlay exit by a sell floored to 9 decimals arrives as theta 1 (complete, uncapped): exactly gone
    assert ex.allocate(0.30, 0.3333, 0.30, 0.0, 1.0, 490.0) == (pytest.approx(0.30), 0.0, 0.0)
    # the core leaves while the overlay stays: the holding is all overlay, exactly
    core_x, ovl_x, fraction = ex.allocate(0.30, 0.3333, 0.0, 0.3333, 1.0, 490.0)
    assert core_x == 0.0 and fraction == 1.0
    # a nearly complete partial fill: the crumb left in the overlay is merged, not kept as "held"
    core_x, ovl_x, fraction = ex.allocate(0.30, 0.3333, 0.30, 0.0, 0.99999, 490.0)
    assert ovl_x == 0.0 and fraction == 0.0 and core_x == pytest.approx(0.30 + 0.3333 * 0.00001, abs=1e-9)
    # a real remainder stays what it is
    core_x, ovl_x, fraction = ex.allocate(0.30, 0.3333, 0.30, 0.0, 0.5, 490.0)
    assert ovl_x == pytest.approx(0.16665) and fraction == pytest.approx(0.16665 / 0.46665)
    # opposing moves, nothing filled at all: the hand-over still happened, the net did not
    core_x, ovl_x, fraction = ex.allocate(0.20, 0.3333, 0.50, 0.0, 0.0, 490.0)        # net -0.0333 (a sell)
    assert core_x == pytest.approx(0.50) and ovl_x == pytest.approx(0.0333) and core_x + ovl_x == pytest.approx(0.5333)
    core_x, ovl_x, fraction = ex.allocate(0.40, 0.0, 0.0, 1 / 3, 0.0, 490.0)          # core exits, overlay enters, sell refused
    assert ovl_x == pytest.approx(1 / 3) and core_x == pytest.approx(0.40 - 1 / 3) and core_x + ovl_x == pytest.approx(0.40)
    assert ex.allocate(0.0, 0.0, 0.0, 0.0, 1.0, 490.0) == (0.0, 0.0, 0.0)


def test_dust_never_blocks_the_next_overlay_entry(tmp_path):
    """End to end on Codex's finding: after an overlay exit that is netted against a core increase, the next
    dip day must ENTER a fresh overlay (the rule keeps an existing one instead, however small)."""
    fb, clock, e, s = world(tmp_path, cash="1000000")
    rec = drive(e, new_record(e, s, 0.20, 1 / 3), clock)                              # core 0.20 + overlay 1/3
    assert rec.status == ex.DONE
    f1 = e.load_overlay_fraction()
    day2 = dt.date(2026, 11, 17)
    win2 = vc.window_for(vc.Session(day2, OPEN + dt.timedelta(days=1), CLOSE + dt.timedelta(days=1)))
    clock.now = win2.decide_from + dt.timedelta(seconds=30)
    fb.set_price(PRODUCT, "50.37")                                                    # an awkward price for rounding
    w = weight(fb)
    core_held, ovl_held = w * (1 - f1), w * f1
    rec2 = ex.SessionRecord.new(s.state_dir, day2, s.mode)
    e.attach_decision(rec2, inputs(), decision(0.5644273497, 0.0, core_held, ovl_held))   # overlay out, core up: a net buy
    rec2 = drive(e, rec2, clock, win2)
    assert rec2.status == ex.DONE and product_orders(fb)[-1].side == "buy"
    assert e.load_overlay_fraction() == 0.0                                           # exactly
    closes, value = [], 500.0
    for i in range(260):                                                              # a choppy uptrend: a moderate core
        value *= 1.019 if i % 2 == 0 else 0.983
        closes.append(value)
    px = closes[-1] * 0.995
    nxt = decide(closes, px, px * 1.02, px, weight(fb), e.load_overlay_fraction(), PARAMS)   # the price sits at the low
    assert nxt.status == READY and nxt.wanted and "OVERLAY_ENTER" in nxt.reason_keys and nxt.ovl_held == 0.0
    assert nxt.ovl_new == pytest.approx(min(1 / 3, 0.99 - nxt.core_new))


def test_core_exit_under_a_kept_overlay_leaves_no_core_crumb(tmp_path):
    fb, clock, e, s = world(tmp_path)
    rec = drive(e, new_record(e, s, 0.30, 1 / 3), clock)
    f1 = e.load_overlay_fraction()
    day2 = dt.date(2026, 11, 17)
    win2 = vc.window_for(vc.Session(day2, OPEN + dt.timedelta(days=1), CLOSE + dt.timedelta(days=1)))
    clock.now = win2.decide_from + dt.timedelta(seconds=30)
    fb.set_price(PRODUCT, "47.13")
    w = weight(fb)
    core_held, ovl_held = w * (1 - f1), w * f1
    rec2 = ex.SessionRecord.new(s.state_dir, day2, s.mode)
    e.attach_decision(rec2, inputs(), decision(0.0, ovl_held, core_held, ovl_held))   # the core exits, the overlay stays a day
    rec2 = drive(e, rec2, clock, win2)
    assert rec2.status == ex.DONE and product_orders(fb)[-1].side == "sell"
    assert e.load_overlay_fraction() == 1.0                                           # exactly: all of it is overlay
    closes = [600.0 + i * 0.5 for i in range(260)]
    nxt = decide(closes, closes[-1] * 1.001, closes[-1] * 1.01, closes[-1] * 0.99, weight(fb), 1.0, PARAMS)
    assert nxt.status == READY and nxt.core_held == 0.0 and nxt.core_trade is True     # held is exactly zero: an entry


# ============================================================================ takeover review findings (R3)
class OddBroker(FakeBroker):
    """FakeBroker whose account cash or one position value can be made unusable."""
    bad_cash = None
    bad_value_symbol = None

    def get_account(self):
        a = super().get_account()
        return a if self.bad_cash is None else vb.Account(**{**a.__dict__, "cash": self.bad_cash})

    def get_positions(self):
        out = []
        for p in super().get_positions():
            if p.symbol == self.bad_value_symbol:
                p = vb.Position(**{**p.__dict__, "market_value": float("nan")})
            out.append(p)
        return out


def test_snapshot_never_overlooks_what_it_cannot_value():
    fb = OddBroker(cash="300", positions={PRODUCT: "2", "XYZ": "3"}, prices={PRODUCT: "50", "XYZ": "0.10"})
    assert ex.take_snapshot(fb, PARAMS, set()).foreign_positions == ()               # 30 cents of XYZ: dust
    fb.bad_value_symbol = "XYZ"
    s = ex.take_snapshot(fb, PARAMS, set())
    assert len(s.foreign_positions) == 1 and "XYZ" in s.foreign_positions[0]          # unknown value is not dust
    for bad in (float("nan"), float("inf"), float("-inf")):
        fb2 = OddBroker(cash="300", positions={PRODUCT: "2"}, prices={PRODUCT: "50"})
        fb2.bad_cash = bad
        with pytest.raises(ex.SnapshotInvalid):
            ex.take_snapshot(fb2, PARAMS, set())
    fb3 = OddBroker(cash="300", positions={PRODUCT: "2"}, prices={PRODUCT: "50"})
    fb3.bad_value_symbol = PRODUCT
    with pytest.raises(ex.SnapshotInvalid):
        ex.take_snapshot(fb3, PARAMS, set())


def test_foreign_item_names_its_symbol_second(tmp_path):
    """The runner shows the owner only the ticker: it is the second field of the item text."""
    fb = FakeBroker(cash="300", positions={"SPY": "0.5"}, prices={PRODUCT: "50", "SPY": "600"})
    fb.script("never_fill")
    fb.submit_order("QQQ" if False else PRODUCT, "buy", "someone-elses-order", notional="5.00")
    s = ex.take_snapshot(fb, PARAMS, set())
    assert s.foreign_positions[0].split(":")[1] == "SPY" and s.foreign_orders[0].split(":")[1] == PRODUCT


def test_unresolved_order_is_checked_once_per_call_without_waiting(tmp_path):
    fb, clock, e, s = world(tmp_path, now=WINDOW.submit_until - dt.timedelta(seconds=5))
    fb.script("timeout_before_accept")
    rec = drive(e, new_record(e, s, 0.30, 0.0), clock, ticks=3)
    assert rec.status == ex.UNRESOLVED
    for _ in range(5):
        clock.sleeps.clear()
        before = len([c for c in fb.calls if c[0] == "get_order_by_client_id"])
        rec = e.run_chain(rec, WINDOW)
        lookups = len([c for c in fb.calls if c[0] == "get_order_by_client_id"]) - before
        assert rec.status == ex.UNRESOLVED and lookups == 1 and sum(clock.sleeps) == 0
        clock.advance(20)


def test_a_long_outage_does_not_bloat_the_record(tmp_path):
    fb, clock, e, s = world(tmp_path)
    rec = new_record(e, s, 0.30, 0.0)
    for _ in range(60):
        fb.script("http_5xx")
        rec = e.run_chain(rec, WINDOW)
        assert not rec.is_final and submits(fb) == []
    assert len(rec.data["events"]) < 12 and rec.data["events"][-1].get("count", 1) >= 50
    fb.faults.clear()
    rec = drive(e, rec, clock)
    assert rec.status == ex.DONE and len(product_orders(fb)) == 1


class SlowBroker(FakeBroker):
    """The inspection takes so long that the submit cutoff passes before anything could be sent."""
    clock_to_push = None
    push_to = None

    def get_open_orders(self):
        if self.clock_to_push is not None:
            self.clock_to_push.now = self.push_to
        return super().get_open_orders()


def test_cutoff_reached_before_the_first_call_is_not_an_unknown_outcome(tmp_path):
    fb = SlowBroker(cash="490", prices={PRODUCT: "50"})
    fb2, clock, e, s = world(tmp_path, broker=fb, now=WINDOW.submit_until - dt.timedelta(seconds=3))
    fb.clock_to_push, fb.push_to = clock, WINDOW.submit_until + dt.timedelta(seconds=1)
    rec = drive(e, new_record(e, s, 0.30, 0.0), clock, ticks=3)
    assert rec.status == ex.NOT_SUBMITTED and rec.is_final                            # nothing was ever sent
    assert submits(fb) == [] and not e.has_unresolved() and e.load_overlay_fraction() == 0.0


def test_reconcile_compares_quantities_as_numbers(tmp_path):
    fb, clock, e, s = world(tmp_path, cash="250", qty="5", now=WINDOW.submit_until - dt.timedelta(seconds=5))
    fb.script("timeout_before_accept")
    rec = drive(e, new_record(e, s, 0.60, 0.0), clock, ticks=3)
    assert rec.status == ex.UNRESOLVED
    path = vs.session_record_path(s.state_dir, DAY)
    raw = vs.read_json(path)
    assert raw["intents"][-1]["before_qty"] == "5"
    raw["intents"][-1]["before_qty"] = "5.000"                                        # the same number, other text
    vs.atomic_write_json(path, raw)
    clock.now = CLOSE + dt.timedelta(minutes=2)
    rec = executor(fb, s, clock).reconcile(ex.SessionRecord.load(s.state_dir, DAY), SESSION)
    assert rec.status == ex.FAILED and rec.is_final
