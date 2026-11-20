"""The reviewer's own tests for fix round R5e, written from the two contracts (`rf6/fix-r5e.md`,
`rf6/fix-r5e-2.md`) and not from the builder's code or tests. Scenarios and numbers are the reviewer's own."""

import dataclasses
import datetime as dt
import json
import random
import re
from decimal import Decimal

import pytest

from tests_v5.test_r5_saldirgan import (
    D1, D2, D3, ET, PRODUCT, UTC, Sim, assert_only_known_sentences, load_tool, window,
)
from tests_v5.test_r5b_inceleme import FRIDAY, break_call, rows, watchdog
from v5 import broker as vb
from v5 import config as vcfg
from v5 import executor as ex
from v5 import notify as vn
from v5 import runner as vrun

SECONDS = dt.timedelta(seconds=1)
MINUTES = dt.timedelta(minutes=1)
HOURS = dt.timedelta(hours=1)
D4, D5 = dt.date(2026, 11, 19), dt.date(2026, 11, 20)
SOLD_FUND = "Elimdeki Nasdaq fonunun tamamını sattım ve durdum."
WAITING = "Güvenlik satışını henüz başlatamadım, çünkü "
DOUBT = "Hesap değeri tutarsız göründü."
CLOCK_CAUSE = "saat bilgisi tutarsız göründü"


def one_print(sim, day, price, at=None, seconds=60):
    """A hook that shows the fund at `price` for one reading after the close of `day` and then puts it back."""
    w = window(sim, day)
    start = at or w.reconcile_from + 10 * SECONDS
    normal = {}

    def hook(t, runner):
        if t == start:
            normal["price"] = sim.fb.prices[PRODUCT]
            sim.fb.set_price(PRODUCT, str(price))
        elif "price" in normal and t >= start + seconds * SECONDS and sim.fb.prices[PRODUCT] != normal["price"]:
            sim.fb.set_price(PRODUCT, str(normal["price"]))
        return None
    return hook


# ============================================================================ H1: the peak and one reading
@pytest.mark.parametrize("factor", [1.03, 1.4, 2.6, 6.2])
def test_no_closing_print_of_any_size_moves_the_peak_or_sells_the_next_day(tmp_path, factor):
    sim = Sim(tmp_path)
    runner = sim.run_day(D1, hook=one_print(sim, D1, 50 * factor))
    true_close = float(sim.fb.equity)
    stored = [row for row in sim.rails().state()["equity_history"] if row["session"] == D1.isoformat()]
    assert len(stored) == 1 and stored[0].get("provisional") is True                # one reading: provisional
    assert stored[0]["equity"] > true_close                                          # the print did get into the row
    assert sim.rails().peak() == 490.0                                               # and never into the peak
    sim.run_day(D2, runner)                                                          # the account is worth the same
    assert sim.rails().floor_state() == "OK" and sim.record(D2, "flr") is None
    assert [o for o in sim.orders() if "-flr-" in o.client_order_id] == []
    assert sim.rails().peak() <= max(490.0, true_close) * 1.0001
    assert_only_known_sentences(sim)


def test_a_named_official_close_replaces_the_bad_row_and_the_owner_is_told_once(tmp_path):
    sim = Sim(tmp_path)
    runner = sim.run_day(D1, hook=one_print(sim, D1, 310))
    true_close = float(sim.fb.equity)
    sim.fb.balance_asof = D1                                                         # the broker names the session
    sim.run_day(D2, runner)
    row = [row for row in sim.rails().state()["equity_history"] if row["session"] == D1.isoformat()][0]
    assert row["equity"] == pytest.approx(true_close) and "provisional" not in row and row["corrected_from"] > 1000
    told = [text for text in sim.alerts() if text.startswith("Gün sonu özetindeki hesap değeri yanlış okunmuş (16.11.2026).")]
    assert len(told) == 1 and told[0].endswith("Doğru kapanış değeri %s dolar." % vn.fmt_money(true_close))
    assert not [text for text in sim.alerts() if text.startswith(DOUBT)]             # no doubt: the broker settled it
    info = sim.rails().breaker_info()
    assert not info or info.get("session") != D2.isoformat()                         # so the brake is not on
    assert sim.rails().peak() == 490.0 and sim.rails().floor_state() == "OK"


def test_a_real_gain_reaches_the_peak_one_session_later(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, D1)

    def rally(t, runner):
        if t == w.decide_from + 2 * MINUTES:
            sim.fb.set_price(PRODUCT, "56")                                          # +12 % after the purchase
    runner = sim.run_day(D1, hook=rally)
    close = float(sim.fb.equity)
    assert close > 500 and sim.rails().peak() == 490.0                               # not on the evening itself
    sim.run_day(D2, runner, stop=sim.session(D2).open + 31 * MINUTES)                # the first trusted reading of D2
    assert sim.rails().peak() == pytest.approx(close)
    assert rows(sim)[D1.isoformat()] == pytest.approx(close)
    assert "provisional" not in [r for r in sim.rails().state()["equity_history"] if r["session"] == D1.isoformat()][0]


def test_the_bound_refuses_a_raise_that_would_put_the_account_at_the_alert_level(tmp_path):
    sim = Sim(tmp_path)
    rails = sim.rails()
    assert rails.record_close(D1, 1359.0, raise_peak=True, current_equity=490.0) == "stored_peak_refused"
    assert rails.peak() == 490.0 and rows(sim)[D1.isoformat()] == 1359.0
    assert rails.record_close(D2, 520.0, raise_peak=True, current_equity=515.0) == "stored"
    assert rails.peak() == 520.0
    assert rails.record_close(D2, 900.0, raise_peak=True, current_equity=890.0) is None      # the row exists already
    assert rails.record_close(D3, 530.0, raise_peak=False) == "stored" and rails.peak() == 520.0
    assert rails.confirm_close(D3, 529.0, False, 529.0) == "confirmed" and rails.peak() == 529.0
    assert rails.confirm_close(D3, 529.0, False, 529.0) is None                               # nothing left to confirm
    assert rails.record_close(D4, 531.0, raise_peak=False) == "stored"
    assert rails.confirm_close(D4, 1500.0, False, 531.0) is None and rows(sim)[D4.isoformat()] == 531.0
    assert rails.confirm_close(D4, 1500.0, True, 531.0) == "corrected" and rails.peak() == 529.0


@pytest.mark.parametrize("seed", [3, 11, 29])
def test_a_week_with_a_bad_print_keeps_the_peak_under_the_best_true_close(tmp_path, seed):
    rng = random.Random(seed)
    sim = Sim(tmp_path)
    days = [D1, D2, D3, D4, D5]
    bad_day = rng.choice(days[:-1])
    runner, best = None, 490.0
    for day in days:
        hook = one_print(sim, day, rng.choice([140, 220, 400])) if day == bad_day else None
        runner = sim.run_day(day, runner, hook=hook)
        best = max(best, float(sim.fb.equity))                                       # the true close of the day
        assert sim.rails().floor_state() == "OK", (seed, day)
    assert sim.rails().peak() <= best * 1.0001, (seed, sim.rails().peak(), best)
    assert [o for o in sim.orders() if "-flr-" in o.client_order_id] == []


# ============================================================================ R1: two clocks that disagree
def test_one_tick_with_the_host_clock_hours_ahead_books_nothing(tmp_path):
    sim = Sim(tmp_path)
    s, w = sim.session(D1), window(sim, D1)
    bad = w.decide_from - 20 * SECONDS

    def hook(t, runner):
        if t == bad:
            sim.clock.now = s.close + 3 * HOURS                                      # the host is wrong for one tick
            sim.fb.skew = (bad - sim.clock.now).total_seconds()                      # the broker still tells the truth
        else:
            sim.fb.skew = 0.0
    runner = sim.run_day(D1, hook=hook)
    record = sim.record(D1)
    assert record.status == ex.DONE and len(sim.submits()) == 1                      # the real decision still happened
    digests = sim.digests()
    assert len(digests) == 1 and digests[0].split("\n")[1].startswith("Hesap: ")     # one summary, after the real close
    assert sim.posts[[p["text"] for p in sim.posts].index(digests[0])]["time"] > s.close
    assert not [text for text in sim.alerts() if "karar saatini kaçırdım" in text or "karar veremedim" in text]
    state = runner._load_state()
    assert state["closed_sessions"].count(D1.isoformat()) == 1
    assert dt.datetime.fromisoformat(state["started_at"]) <= s.open


def test_a_disagreement_that_lasts_all_day_is_told_and_summarised(tmp_path):
    sim = Sim(tmp_path)
    sim.fb.skew = 1800.0                                                             # the broker is half an hour ahead
    sim.run_day(D1)
    assert sim.submits() == []                                                       # no order on a doubtful clock
    held = [text for text in sim.alerts() if text.startswith("Bugün karar veremedim, çünkü " + CLOCK_CAUSE)]
    assert len(held) == 1
    digests = sim.digests()
    assert len(digests) == 1 and digests[0].split("\n")[1].startswith("Hesap şu an ")  # no closing value is claimed
    assert D1.isoformat() not in rows(sim)


def test_under_the_floor_with_a_lasting_disagreement_the_halt_is_set_and_told_then_sold(tmp_path):
    sim = Sim(tmp_path, cash="250", qty="5", price="50", peak=1300.0)                 # 61.5 % under the peak
    sim.fb.skew = 95.0
    runner = sim.run_day(D1)
    assert sim.rails().floor_state() == "HALT" and sim.submits() == []
    waiting = [text for text in sim.alerts() if WAITING + CLOCK_CAUSE in text]
    assert len(waiting) == 1 and "Şu an elimde yüzde 50 Nasdaq fonu var." in waiting[0]
    assert not [text for text in sim.alerts() if "Satışa başladım" in text]          # no sale was started
    sim.fb.skew = 0.0
    sim.run_day(D2, runner)
    assert PRODUCT not in sim.fb.positions                                           # sold once the clocks agree
    done = [text for text in sim.alerts() if SOLD_FUND in text]
    assert len(done) == 1 and sim.rails().liquidating() is False
    assert_only_known_sentences(sim)


def test_three_days_of_disagreement_are_never_silent(tmp_path):
    sim = Sim(tmp_path)
    sim.fb.skew = -7200.0                                                            # the host runs two hours ahead
    runner = sim.runner()
    for day in (D1, D2, D3):
        sim.fb.last_equity = sim.fb.equity
        for t in sim.times(day):                                                     # the engine's own schedule, as the
            sim.clock.now = t + 2 * HOURS                                            # earlier of the two clocks sees it
            runner.tick()
    assert sim.submits() == []
    for day in ("16.11.2026", "17.11.2026", "18.11.2026"):
        assert len([d for d in sim.digests() if "(%s)" % day in d]) == 1, day
    held = [text for text in sim.alerts() if text.startswith("Bugün karar veremedim, çünkü " + CLOCK_CAUSE)]
    assert len(held) == 3


# ============================================================================ R2: once per key, but per event
def test_two_safety_sales_on_two_days_are_both_told(tmp_path):
    sim = Sim(tmp_path, cash="250", qty="5", price="50", peak=1300.0)
    runner = sim.run_day(D1)
    assert len([t for t in sim.alerts() if SOLD_FUND in t]) == 1 and PRODUCT not in sim.fb.positions
    sim.clock.now = sim.session(D2).open - 2 * HOURS
    sim.rails().clear_halt("owner", "devam", 500.0)
    runner = sim.run_day(D2, runner)                                                 # an ordinary day: the engine buys
    assert sim.fb.positions.get(PRODUCT, Decimal(0)) > 0 and sim.rails().floor_state() == "OK"
    sim.clock.now = sim.session(D3).open - 2 * HOURS
    sim.rails().init_peak(1300.0, "test", force=True)                                # the same fall again
    sim.run_day(D3, runner)
    assert sim.rails().floor_state() == "HALT" and PRODUCT not in sim.fb.positions
    assert len([t for t in sim.alerts() if SOLD_FUND in t]) == 2                     # the second one is told as well


def test_a_foreign_asset_is_told_once_a_day_and_again_the_next_day(tmp_path):
    sim = Sim(tmp_path)
    sim.fb.prices["SPY"] = Decimal("100")
    sim.fb.positions["SPY"] = Decimal("1")
    runner = sim.run_day(D1)
    sim.run_day(D2, runner)
    told = [text for text in sim.alerts() if "(SPY)" in text and text.startswith("Hesapta benim almadığım")]
    assert len(told) == 2 and sim.submits() == []


def test_the_daily_brake_is_told_once_although_it_is_measured_on_every_tick(tmp_path):
    sim = Sim(tmp_path, cash="250", qty="5", price="50", peak=500.0)
    s = sim.session(D1)

    def fall(t, runner):
        if t == s.open + 30 * MINUTES:
            sim.fb.set_price(PRODUCT, "30")                                          # the account is 20 % down
    sim.run_day(D1, hook=fall)
    told = [text for text in sim.alerts() if text.startswith("Hesap bugün yüzde ")]
    assert len(told) == 1 and "20,0" in told[0]
    assert [o for o in sim.orders() if o.side == "buy"] == []                        # and nothing was bought


def test_an_alert_without_a_key_keeps_the_four_hour_cooldown(tmp_path):
    sim = Sim(tmp_path)
    notifier = sim.runner().notifier
    assert notifier.alert("NOT_STARTED") is not None
    sim.clock.now += 3 * HOURS
    assert notifier.alert("NOT_STARTED") is None
    sim.clock.now += 2 * HOURS
    assert notifier.alert("NOT_STARTED") is not None
    assert notifier.alert("UNRESOLVED", state_key="2026-11-16") is not None
    sim.clock.now += 9 * HOURS
    assert notifier.alert("UNRESOLVED", state_key="2026-11-16") is None              # with a key: once per key
    assert notifier.alert("UNRESOLVED", state_key="2026-11-17") is not None


# ============================================================================ M1: a chain under way and the floor
def test_a_purchase_still_to_be_sent_stops_under_the_floor_and_the_fund_is_sold_instead(tmp_path):
    sim = Sim(tmp_path, cash="486", qty="1", price="50", peak=1300.0)                 # worth 536, floor at 520
    w = window(sim, D1)
    undo = break_call(sim, "get_open_orders", after_calls=1)                         # the first step ends without an order

    def hook(t, runner):
        if t == w.decide_from + 20 * SECONDS:
            undo()
            sim.fb.set_price(PRODUCT, "20")                                          # now 506: under the floor
    runner = sim.run_day(D1, hook=hook)
    assert sim.orders() == []                                                        # the purchase never went out
    assert sim.rails().floor_state() in ("CONFIRMING", "HALT")                       # the fall was measured, not remembered
    assert sim.record(D1).status in (ex.NOT_SUBMITTED, ex.HOLD_NOT_READY)
    # Accepted limit 26 (a): once the day's record is closed the floor is looked at again in the next session.
    sim.run_day(D2, runner)
    assert [o for o in sim.orders() if o.side == "buy"] == []
    sales = [o for o in sim.orders() if o.side == "sell"]
    assert len(sales) == 1 and "-flr-" in sales[0].client_order_id and sales[0].status == "filled"
    assert sim.rails().floor_state() == "HALT" and PRODUCT not in sim.fb.positions
    assert len([t for t in sim.alerts() if SOLD_FUND in t]) == 1
    assert_only_known_sentences(sim)


# ============================================================================ I3: a real order and a flag switched off
def test_after_the_flag_is_switched_off_the_days_real_purchase_is_still_reported_as_real(tmp_path):
    live = Sim(tmp_path, mode="live", enabled=True)
    w = window(live, D1)
    live.run_day(D1, stop=w.decide_from + 40 * SECONDS)
    bought = [o for o in live.orders() if o.side == "buy" and o.status == "filled"]
    assert len(bought) == 1 and live.record(D1).status == ex.DONE
    env = {"TRADING_MODE": "live", "ALPACA_LIVE_API_KEY": "lk", "ALPACA_LIVE_SECRET_KEY": "ls",
           "NTFY_TOPIC": "sim-topic"}
    live.settings = vcfg.load_settings(env=env, app_dir=tmp_path)                    # recreated without the flag
    live.dry = True
    dry = vrun.Runner(live.settings, live.fb, live.clock, live.clock.sleep, live.post)
    for t in live.times(D1):
        if t > w.decide_from + 40 * SECONDS:
            live.clock.now = t
            dry.tick()
    assert len(live.orders()) == 1                                                   # the dry process sent nothing
    digests = [text for text in live.digests() if "(16.11.2026)" in text]
    assert len(digests) == 1 and digests[0].startswith("Gün sonu özeti (16.11.2026)")  # no dry-run label
    assert digests[0].split("\n")[3] == "Bugün ne yaptım: %s dolarlık Nasdaq fonu aldım." % vn.fmt_money(
        float(bought[0].filled_qty) * float(bought[0].filled_avg_price))
    assert ex.SessionRecord.load(live.settings.state_dir, D1, purpose="dec", dry_run=True) is None
    assert not [p["text"] for p in live.posts if "karar saatini kaçırdım" in p["text"]]


# ============================================================================ L1: the safety sale and a damaged file
@pytest.mark.parametrize("damage", ["runner", "overlay"])
def test_a_damaged_bookkeeping_file_does_not_stop_the_safety_sale(tmp_path, damage):
    sim = Sim(tmp_path, cash="250", qty="5", price="50", peak=1300.0)
    w = window(sim, D1)
    name = "runner.json" if damage == "runner" else "overlay.json"

    def hook(t, runner):
        if t == w.decide_from - 20 * SECONDS:
            (sim.settings.state_dir / name).write_text("{ this is not json", encoding="utf-8")
    sim.run_day(D1, hook=hook)
    sales = [o for o in sim.orders() if o.side == "sell" and o.status == "filled"]
    assert len(sales) == 1 and PRODUCT not in sim.fb.positions                       # the fund was sold all the same
    assert len([t for t in sim.alerts() if SOLD_FUND in t]) == 1
    assert not [t for t in sim.alerts() if "karar saatini kaçırdım" in t]
    text = (sim.settings.state_dir / name).read_text(encoding="utf-8")
    if damage == "runner":
        assert text == "{ this is not json"                                          # never replaced by defaults
    else:
        assert json.loads(text)["fraction"] == 0.0                                   # nothing is held: zero is the truth


# ============================================================================ R6: READINESS only before the open
def test_a_process_that_starts_after_the_open_does_not_say_before_the_open(tmp_path):
    sim = Sim(tmp_path)
    real = sim.fb.get_account
    sim.fb.get_account = lambda: dataclasses.replace(real(), trading_blocked=True)
    s = sim.session(D1)
    sim.run_day(D1, start=s.open + 30 * MINUTES)
    assert not [text for text in sim.alerts() if text.startswith("Bugün borsa açılmadan önce")]
    held = [text for text in sim.alerts() if text.startswith("Bugün karar veremedim, çünkü Alpaca hesabı işleme kapalı")]
    assert len(held) == 1 and sim.submits() == []


# ============================================================================ I10: a message that arrives late
def test_an_alert_that_arrives_a_day_late_carries_its_date_and_the_summary_does_not(tmp_path):
    sim = Sim(tmp_path, cash="250", qty="5", price="50", peak=500.0)
    s = sim.session(D1)

    def fall(t, runner):
        if t == s.open + 30 * MINUTES:
            sim.fb.set_price(PRODUCT, "30")
    sim.post_status = 500                                                            # nothing reaches the phone
    runner = sim.run_day(D1, hook=fall)
    sim.post_status = 200
    sim.clock.now = sim.session(D2).open - 40 * MINUTES
    runner.tick()
    delivered = [p["text"] for p in sim.posts if p["time"] == sim.clock.now]
    brake = [text for text in delivered if "Hesap bugün yüzde " in text]
    assert len(brake) == 1 and brake[0].startswith("DENEME HESABI. Gecikmiş mesaj (16.11.2026): Hesap bugün yüzde ")
    summary = [text for text in delivered if "Gün sonu özeti (16.11.2026)" in text]
    assert len(summary) == 1 and summary[0].startswith("DENEME HESABI. Gün sonu özeti (16.11.2026)")


# ============================================================================ R7: line 3 always adds up
def test_the_shares_in_line_three_always_add_up_to_a_hundred():
    rng = random.Random(7)
    base = dict(date=D1, prefix="", equity=490.0, day_usd=0.0, day_pct=0.0, action="none", action_usd=0.0,
                reason_keys=["TREND_UP"], status_text=None, month_pct=0.0, total_pct=0.0, owner_label="ANT")
    for _ in range(400):
        product, owner, other = rng.random(), rng.random() * 0.4, rng.choice([0.0, 0.0, rng.random() * 0.3])
        scale = max(1.0, product + owner + other)
        ctx = dict(base, product_weight=product / scale, owner_weight=owner / scale, other_weight=other / scale,
                   cash_weight=max(0.0, 1.0 - (product + owner + other) / scale))
        line = vn.build_digest(ctx).split("\n")[2]
        numbers = [int(n) for n in re.findall(r"yüzde (\d+)", line)]
        assert sum(numbers) == 100 and min(numbers) >= 0, line
        assert line.endswith(" nakit.") and line.startswith("Elimde: yüzde ")


# ============================================================================ third part (rf6/fix-r5e-3.md)
def test_a_positions_call_that_fails_while_the_summary_is_built_is_tried_again_quietly(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, D1)
    bad = w.reconcile_from + 10 * SECONDS
    runner = sim.runner()
    armed = {"on": False}
    real_snapshot, real_positions = runner._snapshot, sim.fb.get_positions

    def snapshot(*args, **kwargs):
        if sim.clock.now == bad:
            armed["on"] = True                                                       # the open-orders call fails ...
            raise vb.BrokerUnavailable("open orders are down")
        return real_snapshot(*args, **kwargs)

    def positions():
        if armed["on"] and sim.clock.now == bad:
            raise vb.BrokerUnavailable("positions are down")                         # ... and then the plain look too
        return real_positions()
    runner._snapshot, sim.fb.get_positions = snapshot, positions
    sim.run_day(D1, runner)
    assert armed["on"] is True
    digests = sim.digests()
    assert len(digests) == 1 and sim.posts[sim.texts().index(digests[0])]["time"] > bad    # one summary, a tick later
    assert not [text for text in sim.alerts() if text.startswith("Bot beklenmeyen bir sorunla")]
    assert runner._load_state()["closed_sessions"][-1] == D1.isoformat()


def test_a_docker_that_does_not_answer_is_asked_once_and_both_services_are_told(tmp_path):
    import time
    work, run, containers = watchdog(tmp_path)
    (work / "topic").write_text("secret-topic-xyz\n", encoding="utf-8")
    (work / "bin" / "docker").write_bytes(b'#!/bin/sh\nif [ "$1" = "ps" ]; then sleep 100; fi\nexit 1\n')
    started = time.time()
    calls, log = run()
    assert time.time() - started < 75                                                # one wait of 60 s, not four
    assert len(calls) == 2 and "DENEME HESABI" in calls[1] and "DENEME HESABI" not in calls[0]
    assert len(log) == 2 and all("docker does not answer" in line for line in log)


def test_a_damaged_old_record_on_an_ordinary_day_is_not_called_a_missed_decision(tmp_path):
    sim = Sim(tmp_path)
    old = ex.SessionRecord.new(sim.settings.state_dir, FRIDAY, "paper")
    old.save()
    old.path.write_text("{ torn in the middle", encoding="utf-8")                    # a record of a finished session
    sim.run_day(D1)
    assert sim.submits() == []
    assert not [text for text in sim.alerts() if "karar saatini kaçırdım" in text]
    held = [text for text in sim.alerts() if text.startswith("Bugün karar veremedim, çünkü kayıt dosyam bozuk")]
    assert len(held) == 1 and sim.record(D1).status == ex.HOLD_NOT_READY
    assert "STATE_CORRUPT" in sim.heartbeat()["states"]
    assert old.path.read_text(encoding="utf-8") == "{ torn in the middle"


def test_the_drill_keeps_away_from_the_engines_decision_window(tmp_path):
    drill = load_tool("v5_tatbikat")
    sim = Sim(tmp_path, cash="10000")
    w = window(sim, D1)
    sim.clock.now = w.decide_from - 10 * MINUTES
    steps = ["inventory", "flatten_foreign", "round_trip", "duplicate_id", "restart"]
    result = drill.run_steps(sim.settings, sim.fb, steps, sim.clock, sim.clock.sleep, sim.post, confirm=True)
    assert result["inventory"][0] is True                                            # looking is always allowed
    for step in steps[1:]:
        assert result[step] == (False, "too close to the engine's decision window; run the drill earlier in the session")
    assert sim.submits() == []
    sim.clock.now = w.decide_from - 20 * MINUTES
    result = drill.run_steps(sim.settings, sim.fb, ["round_trip"], sim.clock, sim.clock.sleep, sim.post)
    assert result["round_trip"][0] is True, result
    sim.clock.now = sim.session(D1).close + 5 * MINUTES                              # after the close: allowed again
    result = drill.run_steps(sim.settings, sim.fb, ["duplicate_id"], sim.clock, sim.clock.sleep, sim.post)
    assert result["duplicate_id"][0] is True, result
