"""R5 adversarial suite (written by Claude from the contract, before reading the implementation).

A whole fake market driven by a fake clock, and the real runner on top of it. The questions are the ones the
previous bot failed: does it ever do nothing without telling the owner, does it ever act on something it did
not verify, and does it ever send an order twice.
"""
import datetime as dt
import json
import os
from decimal import Decimal
from pathlib import Path

import pytest

from tests_v5.fakebroker import FakeBroker, partial
from v5 import broker as vb
from v5 import clock as vc
from v5 import config as vcfg
from v5 import executor as ex
from v5 import health as vh
from v5 import notify as vn
from v5 import ops as vo
from v5 import rails as vr
from v5 import runner as vrun
from v5 import state as vs
from v5.packages import get_package

UTC = dt.timezone.utc
ET = vc.ET
PARAMS = get_package("high")
PRODUCT, SIGNAL = PARAMS.product_symbol, PARAMS.signal_symbol
ROOT = Path(__file__).resolve().parents[1]
D1, D2, D3 = dt.date(2026, 11, 16), dt.date(2026, 11, 17), dt.date(2026, 11, 18)
HOLIDAYS = {dt.date(2025, 9, 1), dt.date(2025, 11, 27), dt.date(2025, 12, 25), dt.date(2026, 1, 1), dt.date(2026, 1, 19),
            dt.date(2026, 2, 16), dt.date(2026, 4, 3), dt.date(2026, 5, 25), dt.date(2026, 6, 19), dt.date(2026, 7, 3),
            dt.date(2026, 9, 7), dt.date(2026, 11, 26), dt.date(2026, 12, 25), dt.date(2027, 1, 1), dt.date(2027, 1, 18),
            dt.date(2027, 2, 15)}
EARLY = {dt.date(2026, 11, 27)}
PAUSED_TEXT = "Bot duraklatılmış durumda. Elimdekiler duruyor, yeni işlem yapmıyorum."
MISSED_TEXT = "Bugünkü karar saatini kaçırdım. Elimdekilere dokunmadım. Sonraki işlem gününde normal devam ederim."
UNRESOLVED_TEXT = "Son emrimin akıbetini öğrenemedim. Netleşene kadar yeni işlem yapmıyorum. Claude'a 'trading botuna bak' yazarsan inceler."
ERROR_TEXT = "Bot beklenmeyen bir sorunla karşılaştı. Claude'a 'trading botuna bak' yaz."


def hold_text(cause):
    return "Bugün karar veremedim, çünkü %s. Elimdekilere dokunmadım. Sonraki işlem gününde yeniden denerim." % cause


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += dt.timedelta(seconds=float(seconds))


class SimBroker(FakeBroker):
    """FakeBroker whose clock, calendar and market data follow the simulation."""

    def __init__(self, sim, **kw):
        self.sim = sim
        self.down = False            # every call fails
        self.skew = 0.0              # broker clock minus local clock, seconds
        self.force_closed = False    # the broker says the market is closed
        self.no_calendar = False
        self.no_bars = False
        self.no_price = False
        super().__init__(**kw)

    @property
    def clock(self):
        now = self.sim.clock.now
        session = self.sim.session(now.astimezone(ET).date())
        is_open = bool(session and session.open <= now < session.close) and not self.force_closed
        return vb.BrokerClock(now + dt.timedelta(seconds=self.skew), is_open, now + dt.timedelta(hours=1), now + dt.timedelta(hours=2))

    @clock.setter
    def clock(self, value):
        pass

    def _gate(self):
        if self.down:
            raise vb.BrokerUnavailable("down")

    def get_account(self):
        self._gate()
        return super().get_account()

    def get_positions(self):
        self._gate()
        return super().get_positions()

    def get_open_orders(self):
        self._gate()
        return super().get_open_orders()

    def list_orders(self, after, symbol=None):
        self._gate()
        return super().list_orders(after, symbol)

    def submit_order(self, symbol, side, client_order_id, notional=None, qty=None):
        if self.down:
            raise vb.AmbiguousSubmit("down")
        return super().submit_order(symbol, side, client_order_id, notional=notional, qty=qty)

    def cancel_order(self, order_id):
        self._gate()
        return super().cancel_order(order_id)

    def get_order(self, order_id):
        self._gate()
        return super().get_order(order_id)

    def get_order_by_client_id(self, client_order_id):
        self._gate()
        return super().get_order_by_client_id(client_order_id)

    def get_clock(self):
        self.calls.append(("get_clock", {}))
        self._gate()
        return self.clock

    def get_calendar(self, start, end):
        self._gate()
        if self.no_calendar:
            raise vb.BrokerUnavailable("calendar down")
        return super().get_calendar(start, end)

    def get_daily_bars(self, symbol, start, adjustment="all", feed="sip"):
        self.calls.append(("get_daily_bars", {"symbol": symbol, "start": start, "adjustment": adjustment, "feed": feed}))
        self._gate()
        if self.no_bars:
            raise vb.BrokerUnavailable("data down")
        return self.sim.daily_bars(start)

    def get_minute_bars(self, symbol, start, feed="iex"):
        self.calls.append(("get_minute_bars", {"symbol": symbol, "start": start, "feed": feed}))
        self._gate()
        return [] if self.no_price else self.sim.minute_bars()

    def get_latest_trade(self, symbol, feed="iex"):
        self.calls.append(("get_latest_trade", {"symbol": symbol, "feed": feed}))
        self._gate()
        return None if self.no_price else vb.Trade(t=self.sim.clock.now - dt.timedelta(seconds=5), price=self.sim.px(self.sim.today()))


class Sim:
    def __init__(self, tmp_path, mode="paper", enabled=False, topic="sim-topic", cash="490", qty=None, peak=490.0, price="50",
                 owners=""):
        self.rows, self.sessions = [], {}
        day = dt.date(2025, 8, 1)
        while day <= dt.date(2027, 2, 26):
            if day.weekday() < 5 and day not in HOLIDAYS:
                close = "13:00" if day in EARLY else "16:00"
                self.rows.append({"date": day.isoformat(), "open": "09:30", "close": close})
            day += dt.timedelta(days=1)
        for s in vc.parse_calendar(self.rows):
            self.sessions[s.date] = s
        self.days = sorted(self.sessions)
        self.day_return = {}                  # date -> the signal's return on that day (overrides the pattern)
        self.ibs = {}                         # date -> where the price sits in the day's range (default 0.5)
        self._closes = None
        self.clock = Clock(dt.datetime(2026, 11, 16, 13, 0, tzinfo=UTC))
        env = {"TRADING_MODE": mode, "ALPACA_PAPER_API_KEY": "k", "ALPACA_PAPER_SECRET_KEY": "s", "ALPACA_LIVE_API_KEY": "lk",
               "ALPACA_LIVE_SECRET_KEY": "ls", "NTFY_TOPIC": topic}
        if enabled:
            env["LIVE_TRADING_ENABLED"] = "true"
        if owners:
            env["V5_OWNER_SYMBOLS"] = owners
        self.settings = vcfg.load_settings(env=env, app_dir=tmp_path)
        vs.ensure_state_dir(self.settings.state_dir)
        self.dry = not self.settings.orders_enabled
        positions = {} if qty is None else {PRODUCT: qty}
        self.fb = SimBroker(self, cash=cash, positions=positions, prices={PRODUCT: price}, calendar=self.rows)
        self.posts, self.post_status = [], 200
        if peak is not None:
            self.rails().init_peak(peak, "test")

    # ---- market
    def session(self, day):
        return self.sessions.get(day)

    def today(self):
        return self.clock.now.astimezone(ET).date()

    def closes(self):
        if self._closes is None:
            value, out = 500.0, {}
            for i, day in enumerate(self.days):
                r = self.day_return.get(day, 0.019 if i % 2 == 0 else -0.017)
                value *= 1.0 + r
                out[day] = value
            self._closes = out
        return self._closes

    def set_return(self, day, r):
        self.day_return[day] = r
        self._closes = None

    def px(self, day):
        return self.closes()[day]

    def daily_bars(self, start):
        first = start if isinstance(start, dt.date) and not isinstance(start, dt.datetime) else dt.date.fromisoformat(str(start)[:10])
        out = []
        for day in self.days:
            if first <= day <= self.today():
                c = self.px(day)
                t = dt.datetime.combine(day, dt.time(0, 0), ET).astimezone(UTC)
                out.append(vb.Bar(t=t, o=c, h=c * 1.01, l=c * 0.99, c=c, v=1e6))
        return out

    def minute_bars(self):
        session = self.session(self.today())
        if session is None:
            return []
        px = self.px(self.today())
        ibs = self.ibs.get(self.today(), 0.5)
        lo = px - ibs * 0.02 * px
        hi = lo + 0.02 * px
        out, t = [], session.open
        while t < min(self.clock.now, session.close):
            out.append(vb.Bar(t=t, o=px, h=hi, l=lo, c=px, v=100.0))
            t += dt.timedelta(minutes=1)
        return out

    # ---- plumbing
    def post(self, url, data, headers, timeout):
        self.posts.append({"url": url, "text": data.decode("utf-8"), "headers": dict(headers), "time": self.clock.now})
        return self.post_status

    def texts(self):
        return [p["text"] for p in self.posts]

    def digests(self):
        return [t for t in self.texts() if "Gün sonu özeti (" in t]

    def label(self):
        if self.settings.mode == "paper":
            return "DENEME HESABI. "
        return "DENEME, gerçek emir gönderilmedi. " if self.dry else ""

    def alerts(self):
        """Alert texts without the account label; every alert must carry the label of its account."""
        out = []
        for text in self.texts():
            if "Gün sonu özeti (" in text:
                continue
            assert text.startswith(self.label()), text
            out.append(text[len(self.label()):])
        return out

    def runner(self):
        return vrun.Runner(self.settings, self.fb, self.clock, self.clock.sleep, self.post)

    def rails(self):
        return vr.Rails(self.settings, self.settings.state_dir, self.clock, self.clock.sleep)

    def record(self, day, purpose="dec"):
        return ex.SessionRecord.load(self.settings.state_dir, day, purpose=purpose, dry_run=self.dry)

    def heartbeat(self):
        return vh.read_heartbeat(self.settings.state_dir)

    def orders(self):
        return [o for o in self.fb.orders if o.symbol == PRODUCT]

    def submits(self):
        return [c for c in self.fb.calls if c[0] == "submit_order"]

    def weight(self):
        eq = float(self.fb.equity)
        return float(self.fb.positions.get(PRODUCT, Decimal(0)) * self.fb.prices[PRODUCT]) / eq if eq else 0.0

    def times(self, day):
        s = self.session(day)
        w = vc.window_for(s)
        out = [s.open - dt.timedelta(minutes=40), s.open + dt.timedelta(minutes=30), s.open + dt.timedelta(hours=2),
               w.decide_from - dt.timedelta(minutes=30), w.decide_from - dt.timedelta(seconds=20)]
        t = w.decide_from
        while t < s.close:
            out.append(t)
            t += dt.timedelta(seconds=20)
        out += [s.close + dt.timedelta(seconds=10), w.reconcile_from + dt.timedelta(seconds=10),
                w.reconcile_from + dt.timedelta(seconds=70), w.reconcile_until + dt.timedelta(minutes=1)]
        return out

    def run_day(self, day, runner=None, hook=None, start=None, stop=None):
        """Tick the runner through one session; `hook(t, runner)` may return a replacement runner."""
        runner = runner or self.runner()
        self.fb.last_equity = self.fb.equity                  # the broker's "equity at the previous close"
        for t in self.times(day):
            if (start and t < start) or (stop and t > stop):
                continue
            self.clock.now = t
            if hook:
                runner = hook(t, runner) or runner
            runner.tick()
        return runner


def window(sim, day):
    return vc.window_for(sim.session(day))


def assert_only_known_sentences(sim):
    """Everything the owner received is a sentence of the plan: never an invented or half-filled text."""
    for text in sim.alerts():
        assert "{" not in text and "}" not in text and "None" not in text and "nan" not in text.lower().split(), text
        assert any(text == t or text.startswith(t.split("{")[0]) for t in vn.TEMPLATES.values()), text
    for text in sim.digests():
        lines = text.split("\n")
        assert len(lines) == 7 and "{" not in text and "None" not in text, text
        assert lines[1].startswith(("Hesap: ", "Hesap şu an ")) and lines[2].startswith(("Elimde: yüzde ", "Elimde şu an: yüzde ")) and lines[3].startswith("Bugün ne yaptım: ")
        assert lines[4].startswith("Neden: ") and lines[5].startswith("Bu ay: ") and lines[6].startswith("Durum: ")
        reasons = lines[4][len("Neden: "):]
        rest = reasons
        for sentence in sorted(vn.REASON_SENTENCES.values(), key=len, reverse=True):
            rest = rest.replace(sentence, "")
        assert rest.strip() in ("", "Bugün karar veremedim.") or reasons in vn.OUTCOME_SENTENCES.values(), reasons


# ============================================================================ the ordinary days
def test_first_day_buys_once_and_tells_the_owner_once(tmp_path):
    sim = Sim(tmp_path)
    sim.run_day(D1)
    rec = sim.record(D1)
    assert rec is not None and rec.status == ex.DONE and rec.is_final
    orders = sim.orders()
    assert len(orders) == 1 and orders[0].client_order_id == "v5-paper-20261116-dec-1" and orders[0].side == "buy"
    w = window(sim, D1)
    assert w.decide_from <= orders[0].submitted_at <= w.decide_until
    assert 0.25 < sim.weight() < 0.45                                                 # about E/3 of a calm uptrend
    assert sim.alerts() == [] and len(sim.digests()) == 1
    lines = sim.digests()[0].split("\n")
    bought = float(Decimal(orders[0].filled_qty)) * orders[0].filled_avg_price
    assert lines[0] == "DENEME HESABI. Gün sonu özeti (16.11.2026)"
    assert lines[1] == "Hesap: 490,00 dolar. Bugün değişmedi."
    assert lines[2] == "Elimde: yüzde %s Nasdaq fonu, yüzde %s nakit." % (vn.fmt_weight(sim.weight()), 100 - int(vn.fmt_weight(sim.weight())))
    assert lines[3] == "Bugün ne yaptım: %s dolarlık Nasdaq fonu aldım." % vn.fmt_money(bought)
    assert lines[5] == "Bu ay: değişim yok. Başlangıçtan beri: değişim yok."
    assert lines[6] == "Durum: Her şey normal."
    assert_only_known_sentences(sim)
    hb = sim.heartbeat()
    assert hb["phase"] == "after_close" and hb["mode"] == "paper" and hb["ntfy_configured"] is True
    assert hb["last_decision"]["session"] == "2026-11-16" and hb["last_decision"]["status"] == ex.DONE
    assert vh.assess(hb, sim.clock.now)[0] == 0
    assert sim.rails().state()["equity_history"][-1]["session"] == "2026-11-16"
    assert rec.data["inputs"] and rec.data["decision"]["trade"] is True                # the bundle is kept for replay


def test_second_day_holds_and_still_reports(tmp_path):
    sim = Sim(tmp_path)
    runner = sim.run_day(D1)
    sim.run_day(D2, runner)
    rec = sim.record(D2)
    assert rec.status == ex.NO_TRADE and rec.is_final and len(sim.orders()) == 1
    assert rec.data["decision"] and rec.data["decision"]["trade"] is False and rec.data["inputs"]
    assert len(sim.digests()) == 2 and sim.alerts() == []
    lines = sim.digests()[1].split("\n")
    assert lines[0] == "DENEME HESABI. Gün sonu özeti (17.11.2026)" and lines[3] == "Bugün ne yaptım: İşlem yapmadım."
    assert "Ana pay hedefe yeterince yakın." in lines[4] and lines[6] == "Durum: Her şey normal."
    assert_only_known_sentences(sim)


def test_trend_break_sells_everything(tmp_path):
    sim = Sim(tmp_path)
    runner = sim.run_day(D1)
    sim.set_return(D2, -0.20)                                                         # far below every average
    sim.run_day(D2, runner)
    rec = sim.record(D2)
    assert rec.status == ex.DONE and PRODUCT not in sim.fb.positions
    assert [o.side for o in sim.orders()] == ["buy", "sell"] and sim.orders()[1].client_order_id == "v5-paper-20261117-dec-1"
    lines = sim.digests()[1].split("\n")
    assert lines[3] == "Bugün ne yaptım: Elimdeki fonun tamamını sattım." and lines[2] == "Elimde: yüzde 0 Nasdaq fonu, yüzde 100 nakit."
    assert "Nasdaq düşüş eğiliminde, bu yüzden ana para nakitte bekliyor." in lines[4]
    assert_only_known_sentences(sim)


def test_dip_day_adds_the_overlay_and_removes_it_the_next_day(tmp_path):
    sim = Sim(tmp_path)
    runner = sim.run_day(D1)
    w1 = sim.weight()
    sim.ibs[D2] = 0.0                                                                 # the price sits at the day's low
    runner = sim.run_day(D2, runner)
    assert sim.record(D2).status == ex.DONE and sim.weight() > w1 + 0.25
    assert "Bugün sert bir düşüş oldu, sonraki işlem günü için küçük bir ek alım yaptım." in sim.digests()[1].split("\n")[4]
    sim.run_day(D3, runner)                                                           # no fresh signal: it expires
    assert sim.record(D3).status == ex.DONE and abs(sim.weight() - w1) < 0.03
    assert "Düşüş için tuttuğum ek payı sattım." in sim.digests()[2].split("\n")[4]
    assert [o.side for o in sim.orders()] == ["buy", "buy", "sell"]
    assert_only_known_sentences(sim)


def test_early_close_day_decides_before_one_o_clock(tmp_path):
    sim = Sim(tmp_path)
    day = dt.date(2026, 11, 27)
    sim.clock.now = dt.datetime(2026, 11, 27, 13, 0, tzinfo=UTC)
    sim.run_day(day)
    rec = sim.record(day)
    assert rec.status == ex.DONE and len(sim.orders()) == 1
    at = sim.orders()[0].submitted_at.astimezone(ET)
    assert (at.hour, at.minute) >= (12, 50) and (at.hour, at.minute) < (12, 57)
    assert len(sim.digests()) == 1 and sim.digests()[0].startswith("DENEME HESABI. Gün sonu özeti (27.11.2026)")


def test_weekend_and_holiday_are_silent(tmp_path):
    for day in (dt.date(2026, 11, 21), dt.date(2026, 11, 22), dt.date(2026, 11, 26)):
        sim = Sim(tmp_path / day.isoformat())
        runner = sim.runner()
        for hour in (14, 18, 20, 21, 22):
            sim.clock.now = dt.datetime.combine(day, dt.time(hour, 55), UTC)
            runner.tick()
        assert sim.heartbeat()["phase"] == "closed", day
        assert sim.submits() == [] and sim.posts == [], day


# ============================================================================ restarts
@pytest.mark.parametrize("fault", [None, "timeout_after_accept", "timeout_before_accept"])
@pytest.mark.parametrize("restart_at", [0, 1, 2, 3, 5, 8, 13, 21, 29])
def test_restart_inside_the_window_never_trades_twice(tmp_path, fault, restart_at):
    sim = Sim(tmp_path)
    if fault:
        sim.fb.script(fault)
    w = window(sim, D1)
    moment = w.decide_from + dt.timedelta(seconds=20 * restart_at)

    def hook(t, runner):
        if t == moment or t == moment + dt.timedelta(seconds=20):
            return sim.runner()                                                       # a brand-new process
    sim.run_day(D1, hook=hook)
    rec = sim.record(D1)
    assert rec.is_final and rec.status == ex.DONE, (fault, restart_at, rec.status)
    assert len(sim.orders()) == 1 and len({c[1]["client_order_id"] for c in sim.submits()}) == 1
    assert 0.25 < sim.weight() < 0.45 and len(sim.digests()) == 1 and sim.alerts() == []


def test_restart_after_the_close_sends_no_second_digest(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, D1)

    def hook(t, runner):
        if t >= w.reconcile_from:
            return sim.runner()
    sim.run_day(D1, hook=hook)
    for minutes in (10, 30, 90, 600):
        sim.clock.now = sim.session(D1).close + dt.timedelta(minutes=minutes)
        sim.runner().tick()
    assert len(sim.digests()) == 1 and len(sim.orders()) == 1
    history = sim.rails().state()["equity_history"]                                   # the day itself, and before it the
    assert [row["session"] for row in history] == ["2026-11-13", "2026-11-16"]        # broker's close of the session before


def test_process_down_at_the_close_reports_late(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, D1)
    sim.run_day(D1, stop=w.decide_from + dt.timedelta(minutes=2))                     # dies after trading
    assert sim.record(D1).status == ex.DONE and sim.digests() == []
    sim.clock.now = sim.session(D1).close + dt.timedelta(hours=3)                     # comes back in the evening
    runner = sim.runner()
    runner.tick()
    runner.tick()
    assert len(sim.digests()) == 1 and sim.digests()[0].startswith("DENEME HESABI. Gün sonu özeti (16.11.2026)")
    sim.run_day(D2, runner)
    assert len(sim.digests()) == 2 and sim.record(D2).status == ex.NO_TRADE


def test_window_missed_is_reported_and_the_next_day_is_normal(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, D1)
    runner = sim.run_day(D1, start=w.decide_until + dt.timedelta(seconds=20))         # the process starts too late
    rec = sim.record(D1)
    assert rec.status == ex.MISSED and rec.is_final and sim.submits() == []
    assert sim.alerts() == [MISSED_TEXT]
    assert sim.digests()[0].split("\n")[3] == "Bugün ne yaptım: İşlem yapmadım."
    assert sim.digests()[0].split("\n")[6] == "Durum: " + MISSED_TEXT
    sim.run_day(D2, runner)
    assert sim.record(D2).status == ex.DONE and len(sim.orders()) == 1 and sim.alerts() == [MISSED_TEXT]


def test_whole_day_down_is_reported_the_next_morning(tmp_path):
    sim = Sim(tmp_path)
    friday = dt.date(2026, 11, 13)
    sim.clock.now = sim.session(friday).close + dt.timedelta(minutes=30)              # the engine exists since Friday evening
    sim.runner().tick()
    assert sim.posts == [] and sim.record(friday) is None                             # Friday itself was before its time
    sim.clock.now = sim.session(D2).open - dt.timedelta(minutes=50)                   # nothing ran on D1 at all
    runner = sim.runner()
    runner.tick()
    runner.tick()
    rec = sim.record(D1)
    assert rec is not None and rec.status == ex.MISSED
    # Told once, in the summary that carries the day's date: on the next morning a separate message that says
    # "today" would be wrong (review of 2026-11-19).
    assert sim.alerts() == [] and len(sim.digests()) == 1
    assert sim.digests()[0].startswith("DENEME HESABI. Gün sonu özeti (16.11.2026)")
    assert sim.digests()[0].split("\n")[6] == "Durum: " + MISSED_TEXT
    sim.run_day(D2, runner)
    assert sim.record(D2).status == ex.DONE and len(sim.orders()) == 1


def test_a_new_installation_does_not_report_days_before_it_existed(tmp_path):
    sim = Sim(tmp_path)
    sim.clock.now = sim.session(D2).open - dt.timedelta(minutes=50)                   # first start ever: Tuesday morning
    runner = sim.runner()
    runner.tick()
    runner.tick()
    assert sim.posts == [] and sim.record(D1) is None                                 # Monday is not "missed"
    sim.run_day(D2, runner)
    assert sim.record(D2).status == ex.DONE and len(sim.digests()) == 1 and sim.alerts() == []


def test_irregular_ticks_still_decide(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, D1)
    runner = sim.runner()
    for t in (sim.session(D1).open - dt.timedelta(minutes=30), w.decide_from - dt.timedelta(minutes=47),
              w.decide_from + dt.timedelta(seconds=7), w.decide_until - dt.timedelta(seconds=30), w.cancel_at + dt.timedelta(seconds=40),
              w.reconcile_from + dt.timedelta(seconds=15), w.reconcile_until + dt.timedelta(minutes=9)):
        sim.clock.now = t
        runner.tick()
    assert sim.record(D1).status == ex.DONE and len(sim.orders()) == 1 and len(sim.digests()) == 1


# ============================================================================ not ready: never an order, always a message
def blocked_day(sim, day=D1, runner=None):
    runner = sim.run_day(day, runner)
    rec = sim.record(day)
    assert rec is not None and rec.is_final and rec.status == ex.HOLD_NOT_READY, rec.status if rec else None
    return runner, rec


def test_calendar_unknown_holds_reports_once_and_recovers(tmp_path):
    sim = Sim(tmp_path)
    sim.fb.no_calendar = True
    runner = sim.runner()
    for hour, minute in ((13, 50), (15, 0), (18, 0), (20, 50), (20, 55), (20, 57), (20, 58), (21, 2), (21, 30)):
        sim.clock.now = dt.datetime(2026, 11, 16, hour, minute, tzinfo=UTC)
        runner.tick()
    assert sim.submits() == [] and sim.alerts() == [hold_text("bugünün borsa saatlerini öğrenemedim")]
    assert sim.heartbeat()["phase"] == "no_calendar"
    sim.fb.no_calendar = False
    sim.run_day(D2, runner)
    assert sim.record(D2).status == ex.DONE and len(sim.orders()) == 1
    assert len(sim.alerts()) == 1


def test_broker_down_all_day_holds_and_names_the_broker(tmp_path):
    sim = Sim(tmp_path)
    runner = sim.runner()
    sim.clock.now = sim.session(D1).open - dt.timedelta(minutes=60)
    runner.tick()                                                                     # the calendar is cached while all is well
    sim.fb.down = True
    runner, rec = blocked_day(sim, D1, runner)
    assert sim.submits() == []
    assert hold_text("Alpaca'ya bağlanamadım") in sim.alerts()
    assert all("tutarsız" not in t for t in sim.alerts())                             # an outage is not a "data doubt"
    assert len([t for t in sim.alerts() if t.startswith("Bugün karar veremedim")]) == 1
    sim.fb.down = False
    sim.run_day(D2, runner)
    assert sim.record(D2).status == ex.DONE and len(sim.orders()) == 1
    assert_only_known_sentences(sim)


@pytest.mark.parametrize("fault,cause", [("no_bars", "Alpaca'ya bağlanamadım"), ("no_price", "fiyat bilgisine ulaşamadım"),
                                          ("skew", "saat bilgisi tutarsız göründü"), ("closed", "bugünün borsa saatlerini öğrenemedim"),
                                          ("no_topic", None), ("no_peak", "başlangıç değeri kayıtlı değil")])
def test_each_doubt_blocks_the_order_and_is_named(tmp_path, fault, cause):
    sim = Sim(tmp_path, topic="" if fault == "no_topic" else "sim-topic", peak=None if fault == "no_peak" else 490.0)
    if fault == "no_bars":
        sim.fb.no_bars = True
    elif fault == "no_price":
        sim.fb.no_price = True
    elif fault == "skew":
        sim.fb.skew = 95.0
    elif fault == "closed":
        sim.fb.force_closed = True
    runner, rec = blocked_day(sim)
    assert sim.submits() == [] and sim.orders() == []
    if fault == "no_topic":
        assert sim.posts == []                                                        # nothing can be sent ...
        box = vn.Outbox(sim.settings.state_dir)
        assert box.last("HOLD_NOT_READY")["text"] == "DENEME HESABI. " + hold_text("bildirim kanalı tanımlı değil")
        assert vh.assess(sim.heartbeat(), sim.clock.now)[0] >= 1
    else:
        assert hold_text(cause) in sim.alerts()
        assert len([t for t in sim.alerts() if t.startswith("Bugün karar veremedim")]) == 1
        assert sim.digests()[0].split("\n")[6] == "Durum: " + hold_text(cause)
        assert sim.digests()[0].split("\n")[4] == "Neden: Bugün karar veremedim."
    assert_only_known_sentences(sim)


def test_data_back_before_the_cutoff_still_trades(tmp_path):
    sim = Sim(tmp_path)
    sim.fb.no_bars = True
    w = window(sim, D1)

    def hook(t, runner):
        if t >= w.decide_until - dt.timedelta(seconds=60):
            sim.fb.no_bars = False
    sim.run_day(D1, hook=hook)
    assert sim.record(D1).status == ex.DONE and len(sim.orders()) == 1 and sim.alerts() == []
    assert sim.orders()[0].submitted_at <= w.decide_until


def test_readiness_problem_is_announced_before_the_open(tmp_path):
    sim = Sim(tmp_path, peak=None)
    runner = sim.runner()
    sim.clock.now = sim.session(D1).open - dt.timedelta(minutes=40)
    runner.tick()
    runner.tick()
    expected = "Bugün borsa açılmadan önce bir sorun gördüm: başlangıç değeri kayıtlı değil. Düzelmezse bugün işlem yapmayacağım."
    assert sim.alerts() == [expected]
    hb = sim.heartbeat()
    assert hb["phase"] == "readiness" and hb["readiness"]["ok"] is False and "PEAK_MISSING" in hb["readiness"]["problems"]
    sim.rails().init_peak(490.0, "fixed by the operator")
    sim.run_day(D1, runner)
    assert sim.record(D1).status == ex.DONE and len(sim.alerts()) == 1


def test_foreign_position_blocks_and_clears(tmp_path):
    sim = Sim(tmp_path)
    sim.fb.positions["SPY"] = Decimal("0.5")
    sim.fb.prices["SPY"] = Decimal("600")
    runner, rec = blocked_day(sim)
    assert sim.submits() == [] and sim.fb.positions["SPY"] == Decimal("0.5")
    foreign = [t for t in sim.alerts() if t.startswith("Hesapta benim almadığım bir varlık ya da emir var (")]
    assert len(foreign) == 1 and "SPY" in foreign[0] and foreign[0].endswith("Bunu sen yaptıysan Claude'a haber ver.")
    assert foreign[0] == "Hesapta benim almadığım bir varlık ya da emir var (SPY). Karışmamak için işlem yapmıyorum. Bunu sen yaptıysan Claude'a haber ver."
    assert sim.alerts() == foreign                                                    # no second, generic message
    assert sim.digests()[0].split("\n")[6] == "Durum: " + foreign[0]
    del sim.fb.positions["SPY"]                                                       # the owner sold it:
    sim.fb.cash += Decimal("300")                                                     # the money is cash again
    sim.run_day(D2, runner)
    assert sim.record(D2).status == ex.DONE and len(sim.orders()) == 1 and sim.alerts() == foreign


def test_paused_for_two_months_one_digest_a_day_and_nothing_else(tmp_path):
    sim = Sim(tmp_path, cash="250", qty="5")
    sim.rails().pause("owner", "tatil")
    runner = sim.runner()
    days = [d for d in sim.days if d >= D1][:44]
    for day in days:
        w = window(sim, day)
        sim.fb.last_equity = sim.fb.equity
        for t in (sim.session(day).open - dt.timedelta(minutes=40), w.decide_from + dt.timedelta(seconds=5),
                  w.decide_from + dt.timedelta(seconds=65), w.decide_until + dt.timedelta(seconds=25),
                  w.reconcile_from + dt.timedelta(seconds=20), w.reconcile_from + dt.timedelta(seconds=80)):
            sim.clock.now = t
            runner.tick()
    assert sim.submits() == [] and sim.fb.positions[PRODUCT] == Decimal("5")
    assert sim.alerts() == [] and len(sim.digests()) == 44
    assert all(d.split("\n")[6] == "Durum: " + PAUSED_TEXT for d in sim.digests())
    assert all(sim.record(day).status == ex.HOLD_NOT_READY for day in days)
    sim.rails().resume("owner", "geri")
    after = [d for d in sim.days if d > days[-1]][0]
    sim.run_day(after, runner)
    assert sim.record(after).is_final and sim.record(after).status in (ex.DONE, ex.NO_TRADE)
    assert sim.digests()[-1].split("\n")[6] == "Durum: Her şey normal."


# ============================================================================ orders that go wrong
def test_order_still_open_at_the_cancel_time(tmp_path):
    sim = Sim(tmp_path)
    sim.fb.script(partial("0.4"))
    sim.run_day(D1)
    rec = sim.record(D1)
    assert rec.status == ex.PARTIAL and rec.is_final and len(sim.orders()) == 1 and sim.orders()[0].status == "canceled"
    held = vn.fmt_weight(sim.weight())
    expected = "Emrimin tamamı gerçekleşmedi. Şu an elimde yüzde %s Nasdaq fonu var. Sonraki işlem gününde duruma yeniden bakarım." % held
    assert sim.digests()[0].split("\n")[6] == "Durum: " + expected
    assert 0.10 < sim.weight() < 0.20
    assert_only_known_sentences(sim)


def test_unresolved_order_blocks_everything_until_proven(tmp_path):
    sim = Sim(tmp_path)
    sim.fb.script("timeout_after_accept")
    real, real_list = sim.fb.get_order_by_client_id, sim.fb.list_orders
    sim.fb.get_order_by_client_id = lambda cid: None                                  # the broker never shows it by id
    sim.fb.list_orders = lambda after, symbol=None: ([], False)                       # and its listing is incomplete
    runner = sim.run_day(D1)
    rec = sim.record(D1)
    assert rec.status == ex.UNRESOLVED and not rec.is_final and len(sim.orders()) == 1
    assert UNRESOLVED_TEXT in sim.alerts()
    assert sim.digests()[0].split("\n")[6] == "Durum: " + UNRESOLVED_TEXT
    assert "UNRESOLVED" in sim.heartbeat()["states"] and vh.assess(sim.heartbeat(), sim.clock.now)[0] == 2
    before = len(sim.submits())
    sim.ibs[D2] = 0.0                                                                 # the rule would love to buy
    runner = sim.run_day(D2, runner)
    assert len(sim.submits()) == before and len(sim.orders()) == 1                    # but nothing may be sent
    assert sim.digests()[1].split("\n")[6] == "Durum: " + UNRESOLVED_TEXT
    sim.fb.get_order_by_client_id, sim.fb.list_orders = real, real_list               # the order finally shows up
    runner = sim.run_day(D3, runner)
    first = sim.record(D1)
    assert first.is_final and first.status in (ex.DONE, ex.CAPPED) and "UNRESOLVED" not in sim.heartbeat()["states"]
    day4 = dt.date(2026, 11, 19)
    sim.run_day(day4, runner)
    assert sim.record(day4).is_final and sim.digests()[-1].split("\n")[6] == "Durum: Her şey normal."
    assert_only_known_sentences(sim)


# ============================================================================ rails
def test_breaker_day_buys_nothing_but_still_sells(tmp_path):
    sim = Sim(tmp_path, cash="49", qty="8.82", price="50", peak=490.0)                # 90% in the product
    runner = sim.runner()

    def crash(t, r):
        if t == sim.session(D1).open + dt.timedelta(minutes=30):
            sim.fb.set_price(PRODUCT, "40")                                           # -20% on the product: -18% on the account
    sim.ibs[D1] = 0.0                                                                 # the rule wants the overlay
    runner = sim.run_day(D1, runner, hook=crash)
    breaker = [t for t in sim.alerts() if t.startswith("Hesap bugün yüzde ")]
    assert len(breaker) == 1 and breaker[0].endswith("Bugün yeni alım yapmıyorum. Sonraki işlem gününde normal çalışmaya dönerim. Senden bir şey beklemiyorum.")
    assert breaker[0].startswith("Hesap bugün yüzde 18,0 düştü.")
    assert all(c[1]["side"] == "sell" for c in sim.submits())                         # never a buy today
    assert "BREAKER_TODAY" in sim.heartbeat()["states"] or sim.heartbeat()["phase"] == "after_close"
    assert sim.rails().floor_state() == "OK"
    buys_before = len([c for c in sim.submits() if c[1]["side"] == "buy"])
    sim.ibs[D2] = 0.0
    sim.run_day(D2, runner)                                                           # the next session is normal again
    assert len(breaker) == 1 and len([t for t in sim.alerts() if t.startswith("Hesap bugün yüzde ")]) == 1
    assert sim.rails().check_breaker(vr.read_equity(sim.fb, sim.clock.now), D2) is False
    assert_only_known_sentences(sim)


def test_breaker_lets_the_rules_own_exit_through(tmp_path):
    sim = Sim(tmp_path, cash="49", qty="8.82", price="50", peak=490.0)
    sim.set_return(D1, -0.20)                                                         # trend broken: the rule exits

    def crash(t, r):
        if t == sim.session(D1).open + dt.timedelta(minutes=30):
            sim.fb.set_price(PRODUCT, "40")
    sim.run_day(D1, hook=crash)
    assert sim.record(D1).status == ex.DONE and PRODUCT not in sim.fb.positions
    assert [c[1]["side"] for c in sim.submits()] == ["sell"]


def test_floor_confirms_then_sells_everything_and_stays_stopped(tmp_path):
    sim = Sim(tmp_path, cash="250", qty="5", price="50", peak=1300.0)                 # 500 is 61.5% below 1300
    runner = sim.run_day(D1)
    assert sim.rails().floor_state() == "HALT" and PRODUCT not in sim.fb.positions
    assert [o.client_order_id for o in sim.orders()] == ["v5-paper-20261116-flr-1"] and sim.orders()[0].qty == "5"
    w = window(sim, D1)
    assert sim.orders()[0].submitted_at >= w.decide_from + dt.timedelta(seconds=60)   # only after the second reading
    done = [t for t in sim.alerts() if "Elimdeki Nasdaq fonunun tamamını sattım ve durdum." in t]
    assert len(done) == 1 and done[0].startswith("Hesap en yüksek değerinden yüzde 61,5 düştü, şu an 500,00 dolar.")
    assert sim.record(D1, "flr").status == ex.DONE
    dec = sim.record(D1)
    assert dec is None or dec.status == ex.HOLD_NOT_READY                             # never a strategy trade
    assert sim.rails().liquidating() is False
    assert "FLOOR_HALT" in sim.heartbeat()["states"] and vh.assess(sim.heartbeat(), sim.clock.now)[0] == 2
    assert sim.digests()[0].split("\n")[6] == "Durum: " + done[0]
    for day in (D2, D3):                                                              # stopped until the owner says go
        sim.ibs[day] = 0.0
        runner = sim.run_day(day, runner)
        assert len(sim.submits()) == 1 and sim.digests()[-1].split("\n")[6].startswith("Durum: Hesap en yüksek değerinden")
    assert len([t for t in sim.alerts() if "Elimdeki Nasdaq fonunun tamamını sattım ve durdum." in t]) <= 3   # at most one a day, never a flood
    sim.rails().clear_halt("owner", "devam", 500.0)
    day4 = dt.date(2026, 11, 19)
    sim.run_day(day4, runner)
    assert sim.record(day4).status == ex.DONE and sim.orders()[-1].side == "buy"
    assert_only_known_sentences(sim)


def test_single_bad_reading_never_sells(tmp_path):
    sim = Sim(tmp_path, cash="330", qty="3.4", price="50", peak=900.0)                # 500: fine (the floor is at 360)
    w = window(sim, D1)

    def glitch(t, r):
        if t == w.decide_from:
            sim.fb.set_price(PRODUCT, "0.01")                                         # one absurd print
        elif t == w.decide_from + dt.timedelta(seconds=20):
            sim.fb.set_price(PRODUCT, "50")
    sim.run_day(D1, hook=glitch)
    assert sim.rails().floor_state() == "OK" and sim.fb.positions[PRODUCT] == Decimal("3.4")
    assert sim.submits() == [] and sim.record(D1, "flr") is None
    assert sim.record(D1).status == ex.NO_TRADE                                       # and the day went on normally


def test_floor_waits_while_the_clock_is_in_doubt(tmp_path):
    sim = Sim(tmp_path, cash="250", qty="5", price="50", peak=1300.0)
    sim.fb.skew = 95.0
    runner = sim.run_day(D1)
    assert sim.rails().floor_state() == "HALT" and sim.submits() == [] and sim.fb.positions[PRODUCT] == Decimal("5")
    pending = [t for t in sim.alerts() if "Güvenlik satışını henüz başlatamadım" in t]
    assert len(pending) >= 1 and "saat bilgisi tutarsız göründü" in pending[0] and "Şu an elimde yüzde 50 Nasdaq fonu var." in pending[0]
    assert sim.rails().liquidating() is True
    sim.fb.skew = 0.0
    sim.run_day(D2, runner)                                                           # retried the next session
    assert PRODUCT not in sim.fb.positions and sim.orders()[0].client_order_id == "v5-paper-20261117-flr-1"
    assert sim.rails().liquidating() is False
    assert_only_known_sentences(sim)


def test_floor_sells_even_without_a_notification_channel(tmp_path):
    sim = Sim(tmp_path, topic="", cash="250", qty="5", price="50", peak=1300.0)
    sim.run_day(D1)
    assert sim.rails().floor_state() == "HALT" and PRODUCT not in sim.fb.positions and sim.posts == []
    assert vn.Outbox(sim.settings.state_dir).last("FLOOR_DONE") is not None           # waiting to be delivered


def test_drawdown_alert_changes_nothing(tmp_path):
    sim = Sim(tmp_path, cash="250", qty="5", price="50", peak=800.0)                  # 37.5% below the peak
    sim.run_day(D1)
    alert = [t for t in sim.alerts() if t.startswith("Hesap en yüksek değerinden yüzde 37,5 aşağıda, şu an 500,00 dolar.")]
    assert len(alert) == 1 and sim.rails().floor_state() == "OK" and sim.record(D1).is_final
    assert sim.record(D1).status in (ex.DONE, ex.NO_TRADE)
    assert_only_known_sentences(sim)


# ============================================================================ dry run
def test_live_without_the_flag_never_sends_an_order(tmp_path):
    sim = Sim(tmp_path, mode="live")
    assert sim.dry is True
    runner = None
    for day in (D1, D2, D3):
        sim.ibs[day] = 0.0
        runner = sim.run_day(day, runner)
    assert sim.submits() == [] and [c for c in sim.fb.calls if c[0] == "cancel_order"] == []
    assert len(sim.digests()) == 3
    assert all(d.startswith("DENEME, gerçek emir gönderilmedi. Gün sonu özeti (") for d in sim.digests())
    for day in (D1, D2, D3):
        assert ex.SessionRecord.load(sim.settings.state_dir, day) is None             # no live record ...
        assert ex.SessionRecord.load(sim.settings.state_dir, day, dry_run=True).is_final   # ... only dry-run ones
    assert "DRY_RUN" in sim.heartbeat()["states"] and sim.heartbeat()["orders_enabled"] is False
    assert_only_known_sentences(sim)


def test_live_with_the_flag_trades_under_live_ids(tmp_path):
    sim = Sim(tmp_path, mode="live", enabled=True)
    sim.run_day(D1)
    assert sim.record(D1).status == ex.DONE and sim.orders()[0].client_order_id == "v5-live-20261116-dec-1"
    assert sim.digests()[0].startswith("Gün sonu özeti (16.11.2026)")


# ============================================================================ the loop never dies and never lies
def test_tick_never_raises_whatever_breaks(tmp_path, monkeypatch):
    sim = Sim(tmp_path)
    runner = sim.runner()
    w = window(sim, D1)
    sim.clock.now = w.decide_from - dt.timedelta(minutes=60)
    runner.tick()

    def explode(*a, **k):
        raise RuntimeError("boom")
    for name in ("get_account", "get_positions", "get_open_orders", "get_clock", "get_calendar", "get_daily_bars",
                 "get_minute_bars", "get_latest_trade", "list_orders", "get_order_by_client_id"):
        monkeypatch.setattr(sim.fb, name, explode)
    for i in range(12):
        sim.clock.now = w.decide_from + dt.timedelta(seconds=20 * i)
        runner.tick()
    assert sim.submits() == []
    hb = sim.heartbeat()
    assert hb is not None and dt.datetime.fromisoformat(hb["time"]) == sim.clock.now  # the heartbeat kept beating


def test_corrupt_rails_file_stops_trading_and_is_reported(tmp_path):
    sim = Sim(tmp_path)
    path = sim.settings.state_dir / "rails.json"
    path.write_text("{ broken", encoding="utf-8")
    sim.run_day(D1)
    assert sim.submits() == [] and path.read_text(encoding="utf-8") == "{ broken"
    assert ERROR_TEXT in sim.alerts()
    assert len([t for t in sim.alerts() if t == ERROR_TEXT]) <= 2                     # cooldown: never a flood
    assert "STATE_CORRUPT" in sim.heartbeat()["states"]
    assert vh.assess(sim.heartbeat(), sim.clock.now)[0] >= 1


def test_unwritable_disk_means_no_order(tmp_path, monkeypatch):
    sim = Sim(tmp_path)
    runner = sim.runner()
    w = window(sim, D1)
    sim.clock.now = w.decide_from - dt.timedelta(minutes=30)
    runner.tick()

    def full(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", full)
    for i in range(10):
        sim.clock.now = w.decide_from + dt.timedelta(seconds=20 * i)
        runner.tick()                                                                 # must not raise
    assert sim.submits() == [] and sim.orders() == []


def test_failed_posts_are_retried_not_lost(tmp_path):
    sim = Sim(tmp_path)
    sim.post_status = 500
    runner = sim.run_day(D1)
    assert sim.record(D1).status == ex.DONE and len(sim.digests()) >= 1               # it tried
    assert len(set(sim.digests())) == 1
    sim.post_status = 200
    sim.clock.now = sim.session(D1).close + dt.timedelta(hours=2)
    runner.tick()
    box = vn.Outbox(sim.settings.state_dir)
    assert box.pending(sim.clock.now + dt.timedelta(hours=3)) == []                   # delivered in the end
    assert box.oldest_undelivered_minutes(sim.clock.now) is None


def test_the_topic_never_reaches_a_log_or_a_heartbeat(tmp_path, caplog):
    sim = Sim(tmp_path, topic="very-secret-topic-123")
    with caplog.at_level("DEBUG"):
        sim.run_day(D1)
    assert "very-secret-topic-123" not in caplog.text
    assert "very-secret-topic-123" not in json.dumps(sim.heartbeat())
    for name in ("runner.json", "calendar.json", "heartbeat.json", "rails.json"):
        p = sim.settings.state_dir / name
        if p.exists():
            assert "very-secret-topic-123" not in p.read_text(encoding="utf-8")
    assert all(p["url"] == "https://ntfy.sh/very-secret-topic-123" for p in sim.posts)


# ============================================================================ operator commands and tools
def test_decide_now_is_read_only_and_replays_identically(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, D1)
    sim.clock.now = w.decide_from + dt.timedelta(minutes=1)                           # inside the decision window
    vh.write_heartbeat(sim.settings.state_dir, {"time": sim.clock.now.isoformat(), "states": []})
    before_files = sorted(p.name for p in sim.settings.state_dir.rglob("*"))
    out = tmp_path / "decision.json"
    code, doc = vo.cmd_decide_now(sim.settings, sim.fb, sim.clock.now, out_path=out)
    assert code == 0 and doc["decision"]["status"] == "READY" and doc["decision"]["trade"] is True
    assert doc["plan"]["side"] == "buy" and float(doc["plan"]["notional"]) > 100
    assert not [c for c in sim.fb.calls if c[0] in ("submit_order", "cancel_order")]
    assert sorted(p.name for p in sim.settings.state_dir.rglob("*")) == before_files   # nothing written to the state
    stored = json.loads(out.read_text(encoding="utf-8"))
    code, again = vo.cmd_replay(stored)
    assert code == 0 and again == stored["decision"]
    tampered = json.loads(json.dumps(stored))
    tampered["decision"]["core_new"] = 0.99
    assert vo.cmd_replay(tampered)[0] == 1
    text = json.dumps(stored)
    assert "ALPACA" not in text and "sim-topic" not in text
    sim.clock.now = sim.session(D1).close + dt.timedelta(hours=1)                     # outside the session
    code, doc = vo.cmd_decide_now(sim.settings, sim.fb, sim.clock.now)
    assert code == 1


def load_tool(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_drill_refuses_a_live_account_and_passes_on_paper(tmp_path):
    drill = load_tool("v5_tatbikat")
    live = Sim(tmp_path / "live", mode="live", enabled=True)
    with pytest.raises(ValueError):
        drill.run_steps(live.settings, live.fb, ["inventory", "round_trip"], live.clock, live.clock.sleep, live.post)
    assert live.fb.calls == []                                                        # not even a read
    sim = Sim(tmp_path / "paper", cash="10000")
    sim.clock.now = sim.session(D1).open + dt.timedelta(hours=1)
    result = drill.run_steps(sim.settings, sim.fb, ["inventory", "round_trip", "duplicate_id", "restart", "notify"],
                             sim.clock, sim.clock.sleep, sim.post)
    assert {k: v[0] for k, v in result.items()} == {"inventory": True, "round_trip": True, "duplicate_id": True, "restart": True, "notify": True}
    assert PRODUCT not in sim.fb.positions                                            # the round trip ended flat
    ids = [o.client_order_id for o in sim.fb.orders]
    assert len(ids) == len(set(ids)) and all(1990 <= int(i.split("-")[2][:4]) <= 2009 for i in ids if i.startswith("v5-"))
    assert sim.texts() == ["DENEME HESABI. Deneme bildirimi: bu mesajı görüyorsan bildirimler çalışıyor."]
    bot_state = sorted(p.name for p in sim.settings.state_dir.rglob("*") if p.is_file())
    assert not [n for n in bot_state if n.endswith(".json") and n[:4].isdigit()]      # no drill record in the bot's state
    assert not (sim.settings.state_dir / "overlay.json").exists()


def test_drill_flatten_needs_an_explicit_yes(tmp_path):
    drill = load_tool("v5_tatbikat")
    sim = Sim(tmp_path, cash="10000")
    sim.fb.positions["SPY"] = Decimal("2")
    sim.fb.prices["SPY"] = Decimal("600")
    sim.clock.now = sim.session(D1).open + dt.timedelta(hours=1)
    drill.run_steps(sim.settings, sim.fb, ["flatten_foreign"], sim.clock, sim.clock.sleep, sim.post)
    assert sim.fb.positions["SPY"] == Decimal("2") and sim.submits() == []
    result = drill.run_steps(sim.settings, sim.fb, ["flatten_foreign"], sim.clock, sim.clock.sleep, sim.post, confirm=True)
    sim.fb.get_account()
    assert result["flatten_foreign"][0] is True and "SPY" not in sim.fb.positions


# ============================================================================ packaging
def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_image_contains_only_the_new_engine():
    df = read("Dockerfile")
    lines = [l.strip() for l in df.splitlines() if l.strip() and not l.strip().startswith("#")]
    assert lines[0] == "FROM python:3.12-slim"
    assert 'CMD ["python", "-u", "-m", "v5.runner"]' in lines
    copies = [l for l in lines if l.upper().startswith("COPY")]
    assert copies and not any(l.split()[1] in (".", "./", "*") for l in copies)
    copied = " ".join(copies)
    for needed in ("requirements-v5.lock.txt", "v5", "tools/v5_durum.py", "tools/v5_tatbikat.py", "tests_v5", "ops"):
        assert needed in copied, needed
    for forbidden in ("stock_bot", "core/", "config.py", ".env", "models", "requirements.txt ", "HEALTHCHECK", "finbert", "transformers"):
        assert forbidden not in df, forbidden
    assert "pip install --no-cache-dir -r" in df and "requirements-v5.lock.txt" in df
    ignore = [l.strip() for l in read(".dockerignore").splitlines() if l.strip() and not l.strip().startswith("#")]
    assert ignore[0] == "*" and "!v5/" in ignore or "!v5" in ignore
    assert not any(l in ("!.env", "!*", "!.") for l in ignore)
    lock = [l for l in read("requirements-v5.lock.txt").splitlines() if l.strip() and not l.startswith("#")]
    assert lock and all("==" in l for l in lock) and not any("alpaca" in l.lower() for l in lock)


def test_compose_keeps_the_two_services_and_their_volumes():
    text = read("docker-compose.yml")
    assert "trading-live:" in text and "trading-paper-livecfg:" in text
    for forbidden in ("depends_on", "container_name", "version:", "trading-short", "BOT_MODE", "LIVE_TRADING_ENABLED=true",
                      "LIVE_TRADING_ENABLED: true", "healthcheck"):
        assert forbidden not in text, forbidden
    for needed in ("TRADING_MODE=live", "ALPACA_KEY_PREFIX=LIVE", "TRADING_MODE=paper", "ALPACA_KEY_PREFIX=PAPER",
                   "state-live:/app/state_live", "state-paper:/app/state_paper", "./logs/live:/app/logs", "./logs/livecfg:/app/logs",
                   "restart: unless-stopped", "image: trading-bot:latest", "env_file"):
        assert needed in text.replace('"', "").replace("'", ""), needed
    live = text[text.index("trading-live:"):text.index("trading-paper-livecfg:")]
    paper = text[text.index("trading-paper-livecfg:"):]
    assert "state-paper" not in live and "PAPER" not in live and "state-live:/app" not in paper and "ALPACA_KEY_PREFIX=LIVE" not in paper


def test_watchdog_script_only_watches():
    raw = (ROOT / "ops" / "vps" / "v5_watchdog.sh").read_bytes()
    assert b"\r" not in raw and raw.startswith(b"#!/bin/sh")
    text = raw.decode("utf-8")
    assert "v5.health" in text and "--json" in text and "heartbeat_age_seconds" in text
    assert "trading-live-" in text and "trading-paper-livecfg-" in text and "/root/.v5_watchdog_topic" in text
    assert "Trading botu çalışmıyor görünüyor. Claude'a 'trading botuna bak' yaz." in text
    for forbidden in ("docker restart", "docker stop", "docker rm", "docker kill", "docker compose", "docker start", "rm -rf", "set -x"):
        assert forbidden not in text, forbidden
    assert "echo $TOPIC" not in text and 'echo "$TOPIC"' not in text


# ============================================================================ the owner's own share
def test_the_owners_own_share_is_left_alone_and_named(tmp_path):
    sim = Sim(tmp_path, cash="390", owners="ANT")
    sim.fb.positions["ANT"] = Decimal("1")
    sim.fb.prices["ANT"] = Decimal("100")
    sim.fb.script("never_fill")
    owners_order = sim.fb.submit_order("ANT", "sell", "owner-ant-20261119-1", qty="1")   # his resting profit order
    runner = sim.run_day(D1)
    rec = sim.record(D1)
    assert rec.status == ex.DONE and sim.alerts() == []                               # not a "foreign asset"
    assert sim.fb.positions["ANT"] == Decimal("1") and sim.fb.get_order(owners_order.id).status == "pending_new"
    assert [c for c in sim.fb.calls if c[0] == "cancel_order"] == []
    mine = sim.orders()
    assert len(mine) == 1 and abs(float(mine[0].notional) - 0.35 * 390) < 12          # sized on the engine's 390
    line = sim.digests()[0].split("\n")[2]
    fund = int(vn.fmt_weight(float(sim.fb.positions[PRODUCT] * sim.fb.prices[PRODUCT]) / float(sim.fb.equity)))
    assert 25 <= fund <= 30
    assert line == "Elimde: yüzde %d Nasdaq fonu, yüzde 20 senin aldığın hisse (ANT), yüzde %d nakit." % (fund, 80 - fund)
    assert sim.digests()[0].split("\n")[1] == "Hesap: 490,00 dolar. Bugün değişmedi."  # the whole account
    sim.fb.set_price("ANT", "130")                                                    # his share jumps 30%
    sim.run_day(D2, runner)
    assert sim.record(D2).is_final and sim.fb.positions["ANT"] == Decimal("1")
    assert sim.digests()[1].split("\n")[1].startswith("Hesap: 520,00 dolar. Bugün yüzde 6,1 arttı (30,00 dolar).")


def test_without_the_owner_setting_the_same_share_stops_the_engine(tmp_path):
    sim = Sim(tmp_path, cash="390")
    sim.fb.positions["ANT"] = Decimal("1")
    sim.fb.prices["ANT"] = Decimal("100")
    sim.run_day(D1)
    assert sim.submits() == [] and sim.record(D1).status == ex.HOLD_NOT_READY
    assert sim.alerts() == ["Hesapta benim almadığım bir varlık ya da emir var (ANT). Karışmamak için işlem yapmıyorum. Bunu sen yaptıysan Claude'a haber ver."]
