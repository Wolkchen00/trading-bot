"""Regression tests for fix round R5e."""

import datetime as dt
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests_v5.fakebroker import reject
from tests_v5.test_r5_saldirgan import D1, D2, D3, ET, PRODUCT, Sim, load_tool, window
from tests_v5.test_r5b_inceleme import Death, break_call, rows
from v5 import broker as vb
from v5 import config as vcfg
from v5 import executor as ex
from v5 import health as vh
from v5 import notify as vn
from v5 import ops as vo
from v5 import rails as vr
from v5 import runner as vrun
from v5 import state as vs
from v5.strategy import Decision, READY


SECONDS = dt.timedelta(seconds=1)


def test_h1_one_after_close_print_never_raises_the_peak(tmp_path):
    sim = Sim(tmp_path)
    w1 = window(sim, D1)

    def print_once(moment, runner):
        if moment == w1.reconcile_from + 10 * SECONDS:
            sim.fb.set_price(PRODUCT, "310")
        elif moment == w1.reconcile_from + 70 * SECONDS:
            sim.fb.set_price(PRODUCT, "50")

    runner = sim.run_day(D1, hook=print_once)
    assert sim.rails().peak() == 490.0
    before = len(sim.orders())
    sim.run_day(D2, runner)
    assert sim.rails().peak() == 490.0
    assert sim.rails().floor_state() == "OK"
    assert all(order.side != "sell" for order in sim.orders()[before:])


def test_h1_named_official_close_corrects_the_provisional_row(tmp_path):
    sim = Sim(tmp_path)
    w1 = window(sim, D1)

    def print_once(moment, runner):
        if moment == w1.reconcile_from + 10 * SECONDS:
            sim.fb.set_price(PRODUCT, "310")
        elif moment == w1.reconcile_from + 70 * SECONDS:
            sim.fb.set_price(PRODUCT, "50")

    runner = sim.run_day(D1, hook=print_once)
    sim.fb.balance_asof = D1
    sim.run_day(D2, runner)
    assert rows(sim)[D1.isoformat()] == 490.0
    assert sum("Dünkü özetteki hesap değeri yanlış okunmuş" in text for text in sim.alerts()) == 1
    assert not any(text.startswith("Hesap değeri tutarsız göründü") for text in sim.alerts())
    info = sim.rails().breaker_info()
    assert not info or info.get("session") != D2.isoformat()


def test_h1_normal_close_is_confirmed_on_the_next_session(tmp_path):
    sim = Sim(tmp_path, cash="520")
    runner = sim.run_day(D1, hook=lambda moment, runner: setattr(sim.fb, "last_equity", 490.0))
    state = sim.rails().state()
    row = next(row for row in state["equity_history"] if row["session"] == D1.isoformat())
    assert row["equity"] == 520.0 and row["provisional"] is True
    assert sim.rails().peak() == 490.0
    sim.run_day(D2, runner, stop=sim.session(D2).open + 30 * dt.timedelta(minutes=1))
    row = next(row for row in sim.rails().state()["equity_history"] if row["session"] == D1.isoformat())
    assert row == {"session": D1.isoformat(), "equity": 520.0}
    assert sim.rails().peak() == 520.0


def test_h1_peak_sanity_bound_and_unnamed_disagreement(tmp_path):
    sim = Sim(tmp_path)
    rails = sim.rails()
    assert rails.record_close(D1, 1359.0, raise_peak=True, current_equity=490.0) == "stored_peak_refused"
    assert rails.peak() == 490.0

    other = Sim(tmp_path / "other").rails()
    assert other.record_close(D1, 490.0, raise_peak=False) == "stored"
    assert other.confirm_close(D1, 1359.0, named=False, current_equity=490.0) is None
    assert other.state()["equity_history"] == [
        {"session": D1.isoformat(), "equity": 490.0, "provisional": True}
    ]
    assert other.peak() == 490.0


def test_j1_skewed_tick_books_nothing_and_the_real_session_trades(tmp_path):
    sim = Sim(tmp_path)
    runner = sim.runner()
    session = sim.session(D1)
    real = session.open + 150 * dt.timedelta(minutes=1)
    sim.clock.now = real + 7 * dt.timedelta(hours=1)
    sim.fb.skew = -7 * 60 * 60
    runner.tick()
    assert sim.record(D1) is None
    assert sim.digests() == []
    state_path = sim.settings.state_dir / "runner.json"
    assert not state_path.exists() or runner._load_state()["closed_sessions"] == []

    sim.fb.skew = 0
    sim.clock.now = real + 20 * SECONDS
    sim.run_day(D1, runner, start=sim.clock.now)
    assert sim.record(D1).is_final
    assert len(sim.digests()) == 1


def test_j1_first_future_tick_never_writes_future_started_at(tmp_path):
    sim = Sim(tmp_path)
    runner = sim.runner()
    real = sim.session(D1).open - 40 * dt.timedelta(minutes=1)
    sim.clock.now = real + 3 * dt.timedelta(days=1)
    sim.fb.skew = -3 * 24 * 60 * 60
    runner.tick()
    path = sim.settings.state_dir / "runner.json"
    assert not path.exists()

    sim.fb.skew = 0
    sim.clock.now = real + 20 * SECONDS
    for day in (D1, D2, D3):
        runner = sim.run_day(day, runner, start=sim.clock.now)
    assert dt.datetime.fromisoformat(runner._load_state()["started_at"]) <= sim.clock.now
    assert all(sim.record(day) is not None for day in (D1, D2, D3))


def test_j1_future_cooldown_does_not_silence_a_later_alert(tmp_path):
    sim = Sim(tmp_path)
    notifier = vn.Notifier(sim.settings, vn.Outbox(sim.settings.state_dir), sim.post, sim.clock)
    real = sim.clock.now
    sim.clock.now = real + 2 * dt.timedelta(days=1)
    assert notifier.alert("ERROR") is not None
    sim.clock.now = real + dt.timedelta(hours=1)
    assert notifier.alert("ERROR") is not None


def test_m1_chain_under_way_measures_the_floor_again(tmp_path):
    sim = Sim(tmp_path, cash="245", qty="4.9", price="60", peak=1300.0)
    sim.ibs[D1] = 0.0
    w = window(sim, D1)
    undo = break_call(sim, "get_open_orders", after_calls=1)

    def fall_after_decision(moment, runner):
        if moment == w.decide_from + 20 * SECONDS:
            undo()
            assert sim.record(D1).data["target"] is not None and sim.submits() == []
            sim.fb.set_price(PRODUCT, "55")

    sim.run_day(D1, hook=fall_after_decision)
    record = sim.record(D1)
    assert sim.submits() == []
    assert record.status == ex.NOT_SUBMITTED
    assert record.data["status_detail"] in {"RAIL_CONFIRMING", "FLOOR_HALT"}


def test_h4_readings_are_stamped_when_taken_and_slow_clock_is_valid(tmp_path):
    sim = Sim(tmp_path, cash="330", qty="3.4", price="50", peak=900.0)
    w = window(sim, D1)
    start = w.decide_from
    bad_from, bad_to = start + 40 * SECONDS, start + 70 * SECONDS
    sim.fb.no_price = True
    sim.fb.last_equity = sim.fb.equity
    real_account, real_clock = sim.fb.get_account, sim.fb.get_clock

    def account():
        sim.fb.set_price(PRODUCT, "0.01" if bad_from <= sim.clock.now < bad_to else "50")
        return real_account()

    def slow_clock():
        if sim.clock.now == start:
            sim.clock.sleep(45)
        return real_clock()

    sim.fb.get_account, sim.fb.get_clock = account, slow_clock
    runner = sim.runner()
    sim.clock.now = start - 40 * SECONDS
    runner.run_forever(tick_seconds=20, max_ticks=9)
    assert sim.rails().floor_state() == "OK"
    assert sim.orders() == []

    other = Sim(tmp_path / "clock")
    w2 = window(other, D1)
    original = other.fb.get_clock

    def seventy_seconds():
        if other.clock.now == w2.decide_from:
            other.clock.sleep(70)
        return original()

    other.fb.get_clock = seventy_seconds
    runner = other.runner()
    other.clock.now = w2.decide_from - 20 * SECONDS
    runner.run_forever(tick_seconds=20, max_ticks=2)
    assert runner.clock_ok is True


def test_h3_clock_doubt_cannot_book_a_mid_session_close(tmp_path):
    sim = Sim(tmp_path, cash="323", qty="3.34", price="50", peak=490.0)
    sim.fb.skew = -1800.0
    original = sim.fb.get_clock

    def true_time_clock():
        clock = original()
        true_now = sim.clock.now + dt.timedelta(seconds=sim.fb.skew)
        session = sim.session(true_now.astimezone(ET).date())
        return vb.BrokerClock(
            true_now,
            bool(session and session.open <= true_now < session.close),
            clock.next_open,
            clock.next_close,
        )

    sim.fb.get_clock = true_time_clock
    runner = sim.runner()
    close = sim.session(D1).close
    for moment in sim.times(D1):
        true_now = moment + dt.timedelta(seconds=sim.fb.skew)
        if true_now >= close - 20 * dt.timedelta(minutes=1):
            break
        sim.clock.now = moment
        runner.tick()
    assert D1.isoformat() not in rows(sim)
    assert sim.digests() == []

    sim.fb.skew = 0
    sim.fb.get_clock = original
    for moment in sim.times(D1):
        if moment < close - 20 * dt.timedelta(minutes=1):
            continue
        sim.clock.now = moment
        runner.tick()
    assert len(sim.digests()) == 1
    assert rows(sim)[D1.isoformat()] == float(sim.fb.equity)


def test_i1_floor_messages_only_claim_what_happened_to_the_fund(tmp_path):
    sold = Sim(tmp_path / "sold", cash="150", qty="5", price="50", peak=1300.0, owners="ANT")
    sold.fb.positions["ANT"] = Decimal("1")
    sold.fb.prices["ANT"] = Decimal("100")
    sold.run_day(D1)
    message = next(text for text in sold.alerts() if "tamamını sattım ve durdum" in text)
    assert "Nasdaq fonunun tamamını" in message and sold.fb.positions["ANT"] == Decimal("1")

    flat = Sim(tmp_path / "flat", cash="400", peak=1300.0, owners="ANT")
    flat.fb.positions["ANT"] = Decimal("1")
    flat.fb.prices["ANT"] = Decimal("100")
    flat.run_day(D1)
    assert flat.submits() == []
    assert sum("Elimde Nasdaq fonu yoktu" in text for text in flat.alerts()) == 1


@pytest.mark.parametrize(
    "case,cause",
    [
        ("foreign", "hesapta benim vermediğim bir Nasdaq fonu emri var"),
        ("dry", "gerçek emir gönderme kapalı"),
    ],
)
def test_i2_unsent_floor_sale_is_called_waiting(tmp_path, case, cause):
    sim = Sim(
        tmp_path,
        mode="live" if case == "dry" else "paper",
        cash="250",
        qty="5",
        price="50",
        peak=1300.0,
    )
    if case == "foreign":
        sim.fb.script("never_fill")
        sim.fb.submit_order(PRODUCT, "sell", "somebody-else", qty="1")
    sim.run_day(D1)
    assert [call for call in sim.submits() if call[1]["client_order_id"].startswith("v5-")] == []
    assert any("henüz başlatamadım" in text and cause in text for text in sim.alerts())
    assert not any("Satışa başladım" in text for text in sim.alerts())


def test_i2_a_submitted_unfilled_floor_sale_stays_pending(tmp_path):
    sim = Sim(tmp_path, cash="250", qty="5", price="50", peak=1300.0)
    sim.fb.script("never_fill")
    sim.run_day(D1)
    assert len(sim.submits()) == 1
    assert any("Satışa başladım ama henüz bitmedi" in text for text in sim.alerts())


def test_i3_flag_off_process_reports_the_real_order(tmp_path):
    live = Sim(tmp_path, mode="live", enabled=True)
    w = window(live, D1)
    runner = live.run_day(D1, stop=w.decide_from)
    assert live.record(D1).status == ex.DONE

    env = {
        "TRADING_MODE": "live",
        "ALPACA_LIVE_API_KEY": "lk",
        "ALPACA_LIVE_SECRET_KEY": "ls",
        "NTFY_TOPIC": "sim-topic",
    }
    dry_settings = vcfg.load_settings(env=env, app_dir=tmp_path)
    dry = vrun.Runner(dry_settings, live.fb, live.clock, live.clock.sleep, live.post)
    live.clock.now = w.reconcile_from + 10 * SECONDS
    dry.tick()
    digests = [text for text in live.digests() if "(16.11.2026)" in text]
    assert len(digests) == 1
    assert not digests[0].startswith(vn.DRY_RUN_LABEL)
    assert "Nasdaq fonu aldım" in digests[0]
    assert not any("karar saatini kaçırdım" in text for text in live.alerts())


def test_i4_error_claims_no_holding_fact_and_is_once_per_new_york_day(tmp_path):
    sim = Sim(tmp_path)
    notifier = vn.Notifier(sim.settings, vn.Outbox(sim.settings.state_dir), sim.post, sim.clock)
    for moment in (
        dt.datetime(2026, 11, 13, 23, 0, tzinfo=dt.timezone.utc),
        dt.datetime(2026, 11, 14, 5, 0, tzinfo=dt.timezone.utc),
        dt.datetime(2026, 11, 15, 18, 0, tzinfo=dt.timezone.utc),
        dt.datetime(2026, 11, 16, 17, 0, tzinfo=dt.timezone.utc),
    ):
        sim.clock.now = moment
        notifier.alert("ERROR", state_key=moment.astimezone(ET).date().isoformat())
    texts = [text for text in sim.alerts() if "beklenmeyen bir sorunla" in text]
    assert len(texts) == 4
    assert all("dokunmadım" not in text for text in texts)


def _ready_decision(**changes):
    values = dict(
        status=READY, reason_code=None, trend_score=1.0, vol=0.2, exposure=1.0,
        core_target=0.99, ibs=0.5, wanted=False, overlay_data_missing=False,
        core_trade=True, core_held=0.5, ovl_held=0.0, core_new=0.99, ovl_new=0.0,
        total_new=0.99, trade=True, overlay_fraction_new=0.0, reason_keys=("TREND_UP",),
    )
    values.update(changes)
    return Decision(**values)


def test_h6_reduce_only_never_invents_a_sale():
    idle = _ready_decision(
        trade=False, core_trade=False, core_held=1.0227, core_new=0.99,
        total_new=0.99,
    )
    reduced = vr.reduce_only(idle)
    assert reduced.trade is False and reduced.core_trade is False
    wanted_sale = _ready_decision(core_held=0.8, core_new=0.5, total_new=0.5)
    assert vr.reduce_only(wanted_sale) == wanted_sale
    buy = vr.reduce_only(_ready_decision())
    assert buy.trade is False and "BREAKER_REDUCE_ONLY" in buy.reason_keys


def test_h2_baseline_doubt_uses_the_doubt_reason(tmp_path):
    sim = Sim(tmp_path)
    sim.rails().record_close(D1, 490.0)

    def baseline(moment, runner):
        sim.fb.last_equity = 475.3

    sim.run_day(D2, hook=baseline)
    digest = sim.digests()[-1]
    assert "Hesap değerini doğrulayamadığım için bugün yeni alım yapmadım." in digest
    assert "sert düşüş olduğu için yeni alım yapmadım" not in digest


def test_k1_filled_order_rechecks_a_lagging_position_list(tmp_path):
    sim = Sim(tmp_path)
    fresh_positions, fresh_account = sim.fb.get_positions, sim.fb.get_account
    stale = {"positions": None, "account": None, "armed": False}

    def positions():
        current = fresh_positions()
        if stale["armed"] and stale["positions"] is not None:
            result, stale["positions"] = stale["positions"], None
            return result
        return current

    def account():
        current = fresh_account()
        if stale["armed"] and stale["account"] is not None:
            result, stale["account"] = stale["account"], None
            return result
        return current

    submit = sim.fb.submit_order

    def lagged_submit(*args, **kwargs):
        stale["positions"], stale["account"], stale["armed"] = fresh_positions(), fresh_account(), True
        return submit(*args, **kwargs)

    sim.fb.get_positions, sim.fb.get_account, sim.fb.submit_order = positions, account, lagged_submit
    sim.run_day(D1)
    assert sim.record(D1).status == ex.DONE
    assert "Emrimin tamamı gerçekleşmedi" not in sim.digests()[0]


def test_i7_partial_order_replaces_the_decision_reasons(tmp_path):
    from tests_v5.fakebroker import partial

    sim = Sim(tmp_path)
    sim.fb.script(partial("0.4"), "never_fill")
    sim.run_day(D1)
    assert sim.record(D1).status == ex.PARTIAL
    assert sim.digests()[0].split("\n")[4] == "Neden: Karar verdim ama emrimin yalnız bir kısmı gerçekleşti."


def test_i8_fallback_shares_name_owner_foreign_and_cash(tmp_path):
    owner = Sim(tmp_path / "owner", cash="255", qty="2.7", price="50", owners="ANT")
    owner.fb.positions["ANT"], owner.fb.prices["ANT"] = Decimal("1"), Decimal("100")
    break_call(owner, "get_open_orders", after_calls=1)
    owner.run_day(D1)
    assert owner.digests()[0].split("\n")[2] == (
        "Elimde: yüzde 28 Nasdaq fonu, yüzde 20 senin aldığın hisse (ANT), yüzde 52 nakit."
    )

    foreign = Sim(tmp_path / "foreign", cash="310")
    foreign.fb.positions["SPY"], foreign.fb.prices["SPY"] = Decimal("1"), Decimal("190")
    break_call(foreign, "get_open_orders", after_calls=1)
    foreign.run_day(D1)
    line = foreign.digests()[0].split("\n")[2]
    assert "yüzde 38 benim almadığım varlık" in line and "yüzde 62 nakit" in line


def test_i10_late_first_delivery_is_dated_after_the_account_label(tmp_path):
    sim = Sim(tmp_path)
    outbox = vn.Outbox(sim.settings.state_dir)
    notifier = vn.Notifier(sim.settings, outbox, sim.post, sim.clock)
    monday = dt.datetime(2026, 11, 16, 14, 30, tzinfo=dt.timezone.utc)
    sim.clock.now = monday
    sim.post_status = 500
    notifier.alert("BREAKER", state_key=D1.isoformat(), pct=0.1)
    sim.clock.now = dt.datetime(2026, 11, 17, 13, 45, tzinfo=dt.timezone.utc)
    sim.post_status = 200
    notifier.flush()
    assert sim.posts[-1]["text"].startswith("DENEME HESABI. Gecikmiş mesaj (16.11.2026): ")


def test_j3_successful_post_is_not_repeated_when_marks_fail(tmp_path):
    sim = Sim(tmp_path)
    outbox = vn.Outbox(sim.settings.state_dir)
    identifier = outbox.add("ERROR", vn.build_alert("ERROR"), sim.clock.now)
    notifier = vn.Notifier(sim.settings, outbox, sim.post, sim.clock)
    outbox.mark_delivered = lambda *args: (_ for _ in ()).throw(OSError("disk full"))
    outbox.mark_attempt = lambda *args: (_ for _ in ()).throw(OSError("disk full"))
    for _ in range(30):
        notifier.flush()
        sim.clock.sleep(20)
    assert identifier in notifier._posted_successfully and len(sim.posts) == 1


def test_j2_owner_unreachable_covers_missing_stale_and_expired_messages(tmp_path):
    sim = Sim(tmp_path)
    outbox = vn.Outbox(sim.settings.state_dir)
    now = sim.clock.now
    outbox.add("ERROR", vn.build_alert("ERROR"), now - dt.timedelta(minutes=121))
    assert outbox.owner_unreachable(now) is True
    heartbeat = {
        "time": now.isoformat(), "states": [], "readiness": {"ok": True, "problems": []},
        "ntfy_configured": False,
    }
    level, lines = vh.assess(heartbeat, now)
    assert level > 0 and "Owner notifications are not configured." in lines


def test_j4_corrupt_overlay_is_visible_and_closes_not_ready(tmp_path):
    sim = Sim(tmp_path)
    (sim.settings.state_dir / "overlay.json").write_text('{"fraction": 1.2}', encoding="utf-8")
    sim.run_day(D1)
    record = sim.record(D1)
    assert record.status == ex.HOLD_NOT_READY and record.data["status_detail"] == "STATE_CORRUPT"
    assert "STATE_CORRUPT" in sim.heartbeat()["states"]
    assert any("kayıt dosyam bozuk" in text for text in sim.alerts())


def test_j4_process_started_after_open_runs_readiness(tmp_path):
    sim = Sim(tmp_path)
    real = sim.fb.get_account
    sim.fb.get_account = lambda: replace(real(), trading_blocked=True)
    runner = sim.runner()
    sim.clock.now = sim.session(D1).open + dt.timedelta(minutes=30)
    runner.tick()
    assert sim.heartbeat()["readiness"]["problems"] == ["BLOCKED"]


def test_l3_init_peak_checks_the_account_and_prints_floor(tmp_path, capsys):
    sim = Sim(tmp_path, peak=None)
    reading = vr.read_equity(sim.fb, sim.clock.now)
    assert vo.cmd_init_peak(
        sim.settings.state_dir, sim.settings, 4910.8, "test", reading=reading, no_check=False,
        now_fn=sim.clock, sleep_fn=sim.clock.sleep,
    ) == 1
    assert sim.rails().peak() is None
    assert vo.cmd_init_peak(
        sim.settings.state_dir, sim.settings, 490.0, "test", reading=reading, no_check=False,
        now_fn=sim.clock, sleep_fn=sim.clock.sleep,
    ) == 0
    assert "floor level: 196.00" in capsys.readouterr().out


def test_i11_status_tool_uses_health_not_an_empty_state_list(tmp_path):
    durum = load_tool("v5_durum")
    now = dt.datetime(2026, 11, 20, 20, 0, tzinfo=dt.timezone.utc)
    base = {
        "time": now.isoformat(), "states": [], "readiness": {"ok": True, "problems": []},
        "ntfy_configured": True, "outbox_oldest_minutes": None,
    }
    cases = [
        {**base, "readiness": {"ok": False, "problems": ["ACCOUNT_STATUS"]}},
        {**base, "outbox_oldest_minutes": 1560.0},
        {**base, "ntfy_configured": False},
    ]
    for index, heartbeat in enumerate(cases):
        folder = tmp_path / str(index)
        folder.mkdir()
        (folder / "heartbeat.json").write_text(json.dumps(heartbeat), encoding="utf-8")
        text = durum.render(folder, now)
        assert "Bot çalışıyor." in text
        assert "Durum: Her şey normal." not in text
        assert "Durum: Normal değil" not in text


@pytest.mark.parametrize("damage", ["overlay", "old_record", "runner"])
def test_l1_floor_sale_survives_unneeded_corrupt_state(tmp_path, damage):
    sim = Sim(tmp_path, cash="250", qty="5", price="50", peak=1300.0)
    if damage == "overlay":
        (sim.settings.state_dir / "overlay.json").write_text('{"fraction": 0.3', encoding="utf-8")
    elif damage == "old_record":
        old = ex.SessionRecord.new(sim.settings.state_dir, dt.date(2026, 11, 13), "paper")
        old.data.update(status=ex.NO_TRADE, closed=True)
        old.save()
        old.path.write_text(old.path.read_text(encoding="utf-8")[:40], encoding="utf-8")
    else:
        (sim.settings.state_dir / "runner.json").write_text('{"closed_session": "2026', encoding="utf-8")
    sim.run_day(D1)
    assert len(sim.submits()) == 1 and PRODUCT not in sim.fb.positions
    text = "\n".join(sim.alerts())
    assert "karar saatini kaçırdım" not in text
    if damage in {"overlay", "runner"}:
        assert "kayıt dosyam bozuk" in text


def test_k2_no_order_commits_books_before_final_status(tmp_path):
    sim = Sim(tmp_path, peak=490.0)
    executor = sim.runner().executor
    record = ex.SessionRecord.new(sim.settings.state_dir, D1, "paper", purpose="flr")
    executor.attach_floor(record)
    seen = []
    original = executor._commit

    def watched(item, fraction):
        seen.append(item.status)
        return original(item, fraction)

    executor._commit = watched
    executor.run_chain(record, window(sim, D1))
    assert record.status == ex.DONE
    assert seen and all(status not in ex.FINAL_STATUSES for status in seen)
    assert json.loads((sim.settings.state_dir / "overlay.json").read_text(encoding="utf-8"))["fraction"] == 0.0


def test_k3_definite_refusal_is_not_resubmitted_after_restart(tmp_path):
    sim = Sim(tmp_path)
    runner = sim.runner()
    record = ex.SessionRecord.new(sim.settings.state_dir, D1, "paper")
    runner.executor.attach_decision(
        record,
        SimpleNamespace(to_bundle=lambda: {}),
        _ready_decision(core_new=0.4, total_new=0.4),
    )
    sim.fb.script(reject(422, 42210000, "refused"))
    original = runner.executor._commit
    died = {"once": False}

    def die_after_refusal(item, fraction):
        if item.status == ex.PARTIAL and not died["once"]:
            died["once"] = True
            raise Death()
        return original(item, fraction)

    runner.executor._commit = die_after_refusal
    with pytest.raises(Death):
        runner.executor.run_chain(record, window(sim, D1))
    restarted = sim.runner().executor
    loaded = ex.SessionRecord.load(sim.settings.state_dir, D1)
    restarted.run_chain(loaded, window(sim, D1))
    assert len(sim.submits()) == 1 and loaded.status == ex.FAILED


def test_l2_decide_now_names_engine_fault_and_plans_the_floor(tmp_path):
    sim = Sim(tmp_path, cash="250", qty="5", price="50", peak=1300.0)
    now = window(sim, D1).decide_from
    code, document = vo.cmd_decide_now(sim.settings, sim.fb, now)
    assert code == 1 and "ENGINE_FAULT" in document["blockers"]
    assert "RAIL_CONFIRMING" in document["blockers"]
    assert document["plan"]["kind"] == "sell_all" and document["plan"]["side"] == "sell"


def test_l4_cleared_halt_summary_never_says_holdings_were_untouched(tmp_path):
    sim = Sim(tmp_path, cash="250", qty="5", price="50", peak=1300.0)
    runner = sim.runner()
    w = window(sim, D1)
    runner = sim.run_day(D1, runner, stop=w.decide_from + 60 * SECONDS)
    assert PRODUCT not in sim.fb.positions
    sim.rails().clear_halt("owner", "continue", 500.0)
    sim.run_day(D1, runner, start=sim.session(D1).close + 10 * SECONDS)
    digest = sim.digests()[-1]
    assert "Elimdekilere dokunmadım" not in digest
    assert "sattım" in digest
    assert "durdum" not in digest


def test_h5_clear_halt_between_check_and_order_is_honoured(tmp_path):
    sim = Sim(tmp_path, cash="250", qty="5", price="50", peak=1300.0)
    runner = sim.runner()
    original = runner.rails.set_liquidating
    cleared = {"done": False}

    def clear_then_set(flag):
        if flag and not cleared["done"] and runner.rails.floor_state() == "HALT":
            cleared["done"] = True
            runner.rails.clear_halt("owner", "continue", 500.0)
        return original(flag)

    runner.rails.set_liquidating = clear_then_set
    sim.run_day(D1, runner, stop=window(sim, D1).decide_from + 60 * SECONDS)
    assert [call for call in sim.submits() if "-flr-" in call[1]["client_order_id"]] == []
    assert sim.rails().floor_state() == "OK"
    assert sim.rails().liquidating() is False
    assert not any("tamamını sattım" in text for text in sim.alerts())


def test_h5_working_floor_order_is_cancelled_when_halt_is_cleared(tmp_path):
    sim = Sim(tmp_path, cash="250", qty="5", price="50", peak=1300.0)
    sim.fb.script("never_fill")
    w = window(sim, D1)
    runner = sim.run_day(D1, stop=w.decide_from + 60 * SECONDS)
    assert len(sim.submits()) == 1 and sim.orders()[0].status == "pending_new"
    sim.rails().clear_halt("owner", "continue", 500.0)
    sim.clock.now = w.decide_from + 80 * SECONDS
    runner.tick()
    cancels = [call for call in sim.fb.calls if call[0] == "cancel_order"]
    assert len(cancels) == 1 and sim.orders()[0].status == "canceled"


def test_l6_drawdown_alert_remains_pending_until_queued(tmp_path):
    sim = Sim(tmp_path, peak=1000.0)
    rails = sim.rails()
    reading = vr.EquityReading(sim.clock.now, 600.0, 600.0, 600.0, 0.0, True, None)
    assert rails.check_alert(reading) is not None
    assert rails.alert_pending() is True
    vn.Notifier(sim.settings, sim.outbox if hasattr(sim, "outbox") else vn.Outbox(sim.settings.state_dir), sim.post, sim.clock).alert(
        "DRAWDOWN_ALERT", state_key=D1.isoformat(), pct=0.4, equity=600.0,
    )
    rails.mark_alert_told()
    assert rails.alert_pending() is False


def test_l5_flatten_foreign_skips_the_engines_working_order(tmp_path, monkeypatch):
    drill_tool = load_tool("v5_tatbikat")
    sim = Sim(tmp_path)
    sim.fb.script("never_fill")
    order = sim.fb.submit_order(PRODUCT, "buy", "v5-paper-20261116-dec-1", notional="10")
    monkeypatch.setattr(drill_tool.tempfile, "mkdtemp", lambda prefix: str(tmp_path / "drill"))
    drill = drill_tool._Drill(sim.settings, sim.fb, sim.clock, sim.clock.sleep, sim.post, True)
    ok, evidence = drill.step_flatten_foreign()
    assert ok is True and "skipped 1 engine orders" in evidence
    assert sim.fb.get_order(order.id).status == "pending_new"


def test_j5_l7_watchdog_bounds_listings_and_has_stamp_fallback():
    script = (Path(__file__).resolve().parents[1] / "ops" / "vps" / "v5_watchdog.sh").read_text(encoding="utf-8")
    assert script.count("$LIMIT docker ps") == 2
    assert 'PROBLEM="docker does not answer"' in script
    assert "/dev/shm/v5_watchdog_" in script
    assert "cooldown stamp moved to /dev/shm" in script
    assert 'TEXT="DENEME HESABI. ${TEXT}"' in script


def test_r2_each_floor_completion_has_the_halts_date_in_its_key(tmp_path):
    sim = Sim(tmp_path, peak=1300.0)
    runner = sim.runner()
    for day in (D1, D2):
        sim.clock.now = sim.session(day).open + dt.timedelta(hours=1)
        first = vr.EquityReading(sim.clock.now, 490.0, 490.0, 490.0, 490.0, True, None)
        assert runner.rails.check_floor(first) == "CONFIRMING"
        sim.clock.sleep(61)
        second = replace(first, time=sim.clock.now)
        assert runner.rails.check_floor(second) == "HALT"
        assert runner.rails.set_liquidating(True) is True
        runner._floor_done(1.0 - 490.0 / 1300.0, 490.0)
        if day == D1:
            runner.rails.clear_halt("owner", "continue", 1300.0)
    keys = [
        row["state_key"] for row in vn.Outbox(sim.settings.state_dir)._messages()
        if row["kind"] == "FLOOR_FLAT"
    ]
    assert keys == [f"halt:{D1.isoformat()}", f"halt:{D2.isoformat()}"]


def test_r3_blind_floor_wait_never_invents_a_zero_holding(tmp_path):
    sim = Sim(tmp_path, cash="250", qty="5", price="50", peak=1300.0)
    break_call(sim, "get_open_orders")
    sim.run_day(D1)
    waiting = [text for text in sim.alerts() if "Güvenlik satışını henüz başlatamadım" in text]
    assert len(waiting) == 1
    assert "Şu an elimde" not in waiting[0]


def test_r4_a_new_flat_halt_ignores_an_old_halts_fill(tmp_path):
    sim = Sim(tmp_path, peak=1300.0)
    old = ex.SessionRecord.new(sim.settings.state_dir, D1, "paper", purpose="flr")
    old.data.update(
        status=ex.DONE, closed=True,
        intents=[{"filled_qty": "1", "submit_count": 1}],
    )
    old.save()
    sim.run_day(D2)
    flat = [text for text in sim.alerts() if "Elimde Nasdaq fonu yoktu" in text]
    assert len(flat) == 1
    assert not any("tamamını sattım" in text for text in sim.alerts())


def test_r7_two_buying_power_refusals_are_not_tried_a_third_time_after_restart(tmp_path):
    sim = Sim(tmp_path)
    runner = sim.runner()
    record = ex.SessionRecord.new(sim.settings.state_dir, D1, "paper")
    runner.executor.attach_decision(
        record,
        SimpleNamespace(to_bundle=lambda: {}),
        _ready_decision(core_new=0.99, total_new=0.99),
    )
    sim.fb.script(
        reject(403, 40310000, "insufficient buying power"),
        reject(403, 40310000, "insufficient buying power"),
    )
    original = runner.executor._commit
    seen = {"refusals": 0}

    def die_after_second(item, fraction):
        if item.status == ex.PARTIAL:
            seen["refusals"] += 1
            if seen["refusals"] == 2:
                raise Death()
        return original(item, fraction)

    runner.executor._commit = die_after_second
    with pytest.raises(Death):
        runner.executor.run_chain(record, window(sim, D1))
    restarted = sim.runner().executor
    loaded = ex.SessionRecord.load(sim.settings.state_dir, D1)
    restarted.run_chain(loaded, window(sim, D1))
    assert len(sim.submits()) == 2
    assert loaded.status == ex.FAILED


def test_i5_a_late_summary_labels_the_current_holdings_as_now(tmp_path):
    sim = Sim(tmp_path, cash="90", qty="8", price="50")
    w1, w2 = window(sim, D1), window(sim, D2)
    sim.run_day(D1, stop=w1.decide_from + 2 * dt.timedelta(minutes=1))
    assert sim.digests() == []
    sim.set_return(D2, -0.20)
    sim.run_day(D2, start=w2.decide_from + 40 * SECONDS)
    first = sim.digests()[0].splitlines()
    assert first[0].endswith("Gün sonu özeti (16.11.2026)")
    assert first[2].startswith("Elimde şu an: ")


def test_i6_a_reassigned_overlay_is_never_called_an_extra_purchase(tmp_path):
    sim = Sim(tmp_path, cash="326.665", qty="3.2667", price="50", peak=490.0)
    sim.set_return(D1, -0.20)
    sim.ibs[D1] = 0.0
    runner = sim.run_day(D1)
    assert sim.record(D1).status == ex.NETTED
    assert sim.submits() == []
    sim.run_day(D2, runner)
    assert all("ek alımı" not in digest for digest in sim.digests())
    assert "Dünkü düşüş için tuttuğum ek payı sattım." in sim.digests()[1]


def test_i9_capped_fills_and_idle_days_are_told_truthfully(tmp_path):
    sim = Sim(tmp_path, owners="ANT")
    sim.fb.prices["ANT"] = Decimal("110")
    calm = [day for day in sim.days if day < D3][-45:] + [D1, D2, D3]
    for index, day in enumerate(calm):
        sim.set_return(day, 0.006 if index % 2 == 0 else -0.004)
    real_account = sim.fb.get_account

    def account():
        current = real_account()
        return vb.Account(**{**current.__dict__, "buying_power": max(0.0, current.cash - 100.0)})

    sim.fb.get_account = account
    sim.fb.script("never_fill")
    sim.fb.submit_order("ANT", "buy", "owner-ant-20261119-1", qty="1")
    runner = None
    for day in (D1, D2, D3):
        runner = sim.run_day(day, runner)
    d1 = sim.digests()[0]
    d3 = sim.digests()[2]
    assert sim.record(D1).status == ex.CAPPED
    assert "388,05 dolarlık Nasdaq fonu aldım" in d1
    assert vn.TEMPLATES["CAPPED_FILLED"] in d1
    assert sim.record(D3).status == ex.CAPPED
    assert "Bugün ne yaptım: İşlem yapmadım." in d3
    assert "Neden: " + vn.OUTCOME_SENTENCES["NO_CASH"] in d3
    assert vn.TEMPLATES["CAPPED_IDLE"] in d3
    assert sim.alerts() == []
