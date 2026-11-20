"""Owner-held symbols (plan Revision 10): the owner's own shares and their orders live beside the engine in
the same account. The engine must leave them alone, must not mistake them for a foreign asset, and must size
its own orders on the money that is really its to use."""
import datetime as dt
from decimal import Decimal

import pytest

from tests_v5.fakebroker import FakeBroker
from v5 import broker as vb
from v5 import clock as vc
from v5 import config as vcfg
from v5 import executor as ex
from v5 import notify as vn
from v5 import rails as vr
from v5 import state as vs
from v5.packages import get_package
from v5.strategy import READY, Decision

UTC = dt.timezone.utc
DAY = dt.date(2026, 11, 20)
SESSION = vc.Session(DAY, dt.datetime(2026, 11, 20, 14, 30, tzinfo=UTC), dt.datetime(2026, 11, 20, 21, 0, tzinfo=UTC))
WINDOW = vc.window_for(SESSION)
T0 = WINDOW.decide_from + dt.timedelta(seconds=30)
PARAMS = get_package("high")
PRODUCT = PARAMS.product_symbol


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += dt.timedelta(seconds=float(seconds))


def env(**over):
    base = {"TRADING_MODE": "paper", "ALPACA_PAPER_API_KEY": "k", "ALPACA_PAPER_SECRET_KEY": "s", "NTFY_TOPIC": "t"}
    base.update(over)
    return base


def settings_for(tmp_path, owners="ANT"):
    s = vcfg.load_settings(env=env(V5_OWNER_SYMBOLS=owners), app_dir=tmp_path)
    vs.ensure_state_dir(s.state_dir)
    return s


def account(cash="290", product="2", ant="1"):
    positions, prices = {}, {PRODUCT: "50", "ANT": "100"}
    if product:
        positions[PRODUCT] = product
    if ant:
        positions["ANT"] = ant
    return FakeBroker(cash=cash, positions=positions, prices=prices)


def decision(core_new, ovl_new, core_held=0.0, ovl_held=0.0):
    total = core_new + ovl_new
    return Decision(status=READY, reason_code=None, trend_score=1.0, vol=0.2, exposure=1.5, core_target=core_new,
                    ibs=0.5, wanted=ovl_new > 0, overlay_data_missing=False,
                    core_trade=abs(core_new - core_held) > 1e-12, core_held=core_held, ovl_held=ovl_held,
                    core_new=core_new, ovl_new=ovl_new, total_new=total, trade=True,
                    overlay_fraction_new=(ovl_new / total if total > 0 else 0.0), reason_keys=("TREND_UP",))


def test_owner_symbols_setting(tmp_path):
    assert vcfg.load_settings(env=env(), app_dir=tmp_path).owner_symbols == ()
    assert vcfg.load_settings(env=env(V5_OWNER_SYMBOLS=""), app_dir=tmp_path).owner_symbols == ()
    assert vcfg.load_settings(env=env(V5_OWNER_SYMBOLS=" ant, xyz ,ANT,, brk.b "), app_dir=tmp_path).owner_symbols == ("ANT", "XYZ", "BRK.B")
    for bad in ("A B", "TQQQ", "ANT,QQQ", "tqqq", "ANT$", "A,B,C,D,E,F", "TOOLONGSYMBOL", "ANT;rm"):
        with pytest.raises(vcfg.ConfigError):
            vcfg.load_settings(env=env(V5_OWNER_SYMBOLS=bad), app_dir=tmp_path)
    assert "ANT" in repr(vcfg.load_settings(env=env(V5_OWNER_SYMBOLS="ANT"), app_dir=tmp_path))


def test_snapshot_keeps_the_owners_shares_apart():
    fb = account()
    fb.script("never_fill")
    fb.submit_order("ANT", "sell", "owner-ant-20261119-1", qty="1")                   # the owner's resting sell order
    plain = ex.take_snapshot(fb, PARAMS, set())
    assert len(plain.foreign_positions) == 1 and len(plain.foreign_orders) == 1       # unknown to the engine: foreign
    assert plain.equity == 490.0 and plain.owner_value == 0.0
    s = ex.take_snapshot(fb, PARAMS, set(), ("ANT",))
    assert s.foreign == () and s.foreign_product_orders == ()
    assert s.account_equity == 490.0 and s.owner_value == 100.0 and s.equity == 390.0
    assert s.product_weight == pytest.approx(100.0 / 390.0) and s.product_qty == "2"
    assert len(s.owner_positions) == 1 and s.owner_positions[0].split(":")[1] == "ANT"
    assert len(s.owner_orders) == 1 and "owner-ant-20261119-1" in s.owner_orders[0]
    assert ex.take_snapshot(fb, PARAMS, set(), ("ant",)).foreign == ()                # case does not matter
    # a symbol the owner did not announce is still foreign
    fb.positions["SPY"] = Decimal("0.5")
    fb.prices["SPY"] = Decimal("600")
    mixed = ex.take_snapshot(fb, PARAMS, set(), ("ANT",))
    assert len(mixed.foreign_positions) == 1 and "SPY" in mixed.foreign_positions[0]
    # the product can never be declared an owner symbol through the back door
    sneaky = ex.take_snapshot(account(), PARAMS, set(), (PRODUCT, "ANT"))
    assert sneaky.product_qty == "2" and sneaky.owner_value == 100.0


class Odd(FakeBroker):
    over = {}
    nan_symbol = None

    def get_account(self):
        a = super().get_account()
        return vb.Account(**{**a.__dict__, **self.over})

    def get_positions(self):
        out = []
        for p in super().get_positions():
            if p.symbol == self.nan_symbol:
                p = vb.Position(**{**p.__dict__, "market_value": float("nan")})
            out.append(p)
        return out


def test_snapshot_refuses_what_it_cannot_size_on():
    fb = Odd(cash="290", positions={PRODUCT: "2", "ANT": "1"}, prices={PRODUCT: "50", "ANT": "100"})
    fb.nan_symbol = "ANT"
    with pytest.raises(ex.SnapshotInvalid):
        ex.take_snapshot(fb, PARAMS, set(), ("ANT",))                                 # the owner's share has no value
    only_owner = Odd(cash="0", positions={"ANT": "1"}, prices={PRODUCT: "50", "ANT": "100"})
    with pytest.raises(ex.SnapshotInvalid):
        ex.take_snapshot(only_owner, PARAMS, set(), ("ANT",))                         # nothing is left for the engine


def test_an_open_order_of_the_owner_reserves_money_the_engine_must_not_spend():
    fb = Odd(cash="490", prices={PRODUCT: "50"})
    fb.over = {"buying_power": 387.05}                                                # the owner's pending buy holds 103
    s = ex.take_snapshot(fb, PARAMS, set(), ("ANT",))
    assert s.cash == 490.0 and s.spendable == 387.05
    plan = ex.plan_order(s, 0.99, PARAMS)
    assert plan.kind == "buy" and plan.capped is True and Decimal(plan.notional) == Decimal("385.11")
    free = ex.take_snapshot(FakeBroker(cash="490", prices={PRODUCT: "50"}), PARAMS, set())
    assert free.spendable == 490.0 and ex.plan_order(free, 0.99, PARAMS).capped is False
    margin = Odd(cash="490", prices={PRODUCT: "50"})
    margin.over = {"buying_power": 980.0}                                             # never more than the cash
    assert ex.take_snapshot(margin, PARAMS, set()).spendable == 490.0


def test_engine_trades_beside_the_owners_share_and_never_touches_it(tmp_path):
    s = settings_for(tmp_path)
    fb = account(cash="390", product=None, ant="1")                                   # 390 cash + one owner share
    fb.script("never_fill")
    owners_order = fb.submit_order("ANT", "sell", "owner-ant-20261119-1", qty="1")
    clock = Clock()
    e = ex.Executor(fb, s, s.state_dir, PARAMS, clock, clock.sleep)
    rec = ex.SessionRecord.new(s.state_dir, DAY, s.mode)
    from v5 import data as vd
    inputs = vd.DecisionInputs(closes=tuple(600.0 + i for i in range(260)),
                               close_dates=tuple(dt.date(2025, 1, 1) + dt.timedelta(days=i) for i in range(260)),
                               px=860.0, px_time=T0, px_source="iex_trade", session_high=865.0, session_low=855.0,
                               range_known=True, range_reason=None, range_bars=380, range_coverage=1.0)
    e.attach_decision(rec, inputs, decision(0.30, 0.0))
    for _ in range(10):
        rec = e.run_chain(rec, WINDOW)
        if rec.is_final:
            break
        clock.sleep(20)
    assert rec.status == ex.DONE and rec.data.get("hold") is None
    mine = [o for o in fb.orders if o.symbol == PRODUCT]
    assert len(mine) == 1 and Decimal(mine[0].notional) == Decimal("117")             # 30% of the engine's 390
    assert fb.get_order(owners_order.id).status == "pending_new"                      # the owner's order is untouched
    assert [c for c in fb.calls if c[0] == "cancel_order"] == []
    assert fb.positions["ANT"] == Decimal("1")
    after = ex.take_snapshot(fb, PARAMS, e.own_client_ids(), s.owner_symbols)
    assert after.product_weight == pytest.approx(0.30, abs=0.001) and after.owner_value == 100.0
    # the floor sale sells the fund only
    flr = ex.SessionRecord.new(s.state_dir, DAY, s.mode, purpose="flr")
    e.attach_floor(flr)
    for _ in range(10):
        flr = e.run_chain(flr, WINDOW)
        if flr.is_final:
            break
        clock.sleep(20)
    assert flr.status == ex.DONE and PRODUCT not in fb.positions and fb.positions["ANT"] == Decimal("1")
    assert fb.get_order(owners_order.id).status == "pending_new"


def test_without_the_setting_the_same_account_is_held(tmp_path):
    s = vcfg.load_settings(env=env(), app_dir=tmp_path)
    vs.ensure_state_dir(s.state_dir)
    fb = account(cash="390", product=None, ant="1")
    clock = Clock()
    e = ex.Executor(fb, s, s.state_dir, PARAMS, clock, clock.sleep)
    rec = ex.SessionRecord.new(s.state_dir, DAY, s.mode)
    rec.data["target"] = {"core_new": 0.30, "ovl_new": 0.0, "total_new": 0.30}
    rec.save()
    rec = e.run_chain(rec, WINDOW)
    assert not rec.is_final and rec.data["hold"] == "FOREIGN_ASSET" and "ANT" in rec.data["status_detail"]
    assert [c for c in fb.calls if c[0] == "submit_order"] == []


def test_rails_keep_watching_the_whole_account():
    fb = account()
    reading = vr.read_equity(fb, T0)
    assert reading.ok and reading.equity == 490.0 and reading.positions_value == 200.0


BASE = dict(date=DAY, prefix="", equity=490.0, day_usd=0.0, day_pct=0.0, product_weight=0.28, action="none",
            action_usd=0.0, reason_keys=["TREND_UP", "BAND_HOLD"], month_pct=None, total_pct=None, status_text=None)


def test_digest_names_the_owners_share():
    line = vn.build_digest({**BASE, "owner_weight": 0.2041, "owner_label": "ANT"}).split("\n")[2]
    assert line == "Elimde: yüzde 28 Nasdaq fonu, yüzde 20 senin aldığın hisse (ANT), yüzde 52 nakit."
    assert vn.build_digest(dict(BASE)).split("\n")[2] == "Elimde: yüzde 28 Nasdaq fonu, yüzde 72 nakit."
    tiny = vn.build_digest({**BASE, "owner_weight": 0.004, "owner_label": "ANT"}).split("\n")[2]
    assert tiny == "Elimde: yüzde 28 Nasdaq fonu, yüzde 72 nakit."
    none = vn.build_digest({**BASE, "owner_weight": None, "owner_label": None}).split("\n")[2]
    assert none == "Elimde: yüzde 28 Nasdaq fonu, yüzde 72 nakit."
    full = vn.build_digest({**BASE, "product_weight": 0.795, "owner_weight": 0.205, "owner_label": "ANT, XYZ"}).split("\n")[2]
    # 79.5 and 20.5 round to 80 and 21; since R5e (S1) the largest part gives way, so the line adds up to 100
    assert full == "Elimde: yüzde 79 Nasdaq fonu, yüzde 21 senin aldığın hisse (ANT, XYZ), yüzde 0 nakit."
    with pytest.raises(KeyError):
        vn.build_digest({**BASE, "owner_weight": 0.2})
    assert len(vn.build_digest({**BASE, "owner_weight": 0.2, "owner_label": "ANT"}).split("\n")) == 7
