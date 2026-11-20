import json
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from tests_v5.fakebroker import FakeBroker
from tests_v5.fakebroker import invisible, partial
from v5.broker import BrokerClock
from v5.clock import Window
from v5.executor import Executor, SessionRecord, take_snapshot
from v5.packages import PACKAGES
from v5.strategy import READY, decide


UTC = timezone.utc


def fast_e2e_state(monkeypatch):
    def write(path, obj):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as handle:
            json.dump(obj, handle, allow_nan=False)

    monkeypatch.setattr("v5.executor.atomic_write_json", write)
    monkeypatch.setattr(SessionRecord, "save", lambda self: None)


def drive_with_restart(executor, record, window, state_dir, day):
    for _ in range(10):
        try:
            return executor.run_chain(record, window)
        except PermissionError:
            record = SessionRecord.load(state_dir, day)
    pytest.fail("state replacement remained unavailable")


def save_with_retry(record):
    for _ in range(10):
        try:
            record.save()
            return
        except PermissionError:
            pass
    pytest.fail("state replacement remained unavailable")


@pytest.mark.parametrize("filename", ["golden_high.csv.gz", "golden_high_rt.csv.gz"])
def test_ideal_executor_matches_complete_golden(filename, tmp_path, monkeypatch):
    fast_e2e_state(monkeypatch)
    frame = pd.read_csv(f"tests_v5/golden/{filename}", index_col=0, parse_dates=True)
    with open("tests_v5/golden/golden_meta.json", encoding="utf-8") as handle:
        meta = json.load(handle)
    first = pd.Timestamp(meta["files"][filename]["first_decision"])
    first_position = frame.index.get_loc(first)
    history = list(frame.iloc[:first_position]["c"])
    broker = FakeBroker(cash=1_000_000, prices={"TQQQ": 100})
    now = [datetime(2010, 1, 1, 19, 50, tzinfo=UTC)]
    executor = Executor(
        broker, SimpleNamespace(orders_enabled=True), tmp_path,
        PACKAGES["high"], lambda: now[0], lambda _: None,
    )
    trade_days = []

    for position in range(first_position, len(frame)):
        index, row = frame.index[position], frame.iloc[position]
        if position > first_position:
            product_return = 0.0 if pd.isna(row["r_T"]) else float(row["r_T"])
            broker.set_price("TQQQ", float(broker.prices["TQQQ"]) * (1 + product_return))
        before = take_snapshot(broker, PACKAGES["high"], set())
        decision = decide(
            history, row["px"], row["h"], row["l"], before.product_weight,
            executor.load_overlay_fraction(), PACKAGES["high"],
        )
        assert decision.status == READY
        assert decision.trade == bool(row["trade"]), str(index)
        if decision.trade:
            trade_days.append(index)
            day = index.date()
            now[0] = datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=19, minutes=50)
            broker.clock = BrokerClock(now[0], True, now[0] + timedelta(days=1), now[0] + timedelta(minutes=10))
            window = Window(
                now[0] - timedelta(minutes=1), now[0] + timedelta(minutes=1),
                now[0] + timedelta(minutes=3), now[0] + timedelta(minutes=2),
                now[0] + timedelta(minutes=11), now[0] + timedelta(minutes=15),
            )
            record = SessionRecord.new(tmp_path, day, "paper")
            record.data["target"] = {
                "core_new": decision.core_new, "ovl_new": decision.ovl_new,
                "total_new": decision.total_new,
            }
            save_with_retry(record)
            record = drive_with_restart(executor, record, window, tmp_path, day)
            assert record.is_final
        after = take_snapshot(broker, PACKAGES["high"], set())
        assert abs(after.product_weight - float(row["total"])) <= 1e-6, str(index)
        history.append(row["c"])
    expected = list(frame.index[first_position:][frame.iloc[first_position:]["trade"].astype(bool)])
    assert trade_days == expected


def independent_allocate(intent):
    core_b, ovl_b = intent["core_b"], intent["ovl_b"]
    core_new, ovl_new, theta = intent["core_new"], intent["ovl_new"], intent["theta"]
    dc, do = core_new - core_b, ovl_new - ovl_b
    net = dc + do
    if dc * do < 0:
        if abs(dc) >= abs(do):
            ovl_x, core_x = ovl_new, core_b - do + theta * net
        else:
            core_x, ovl_x = core_new, ovl_b - dc + theta * net
    else:
        core_x, ovl_x = core_b + theta * dc, ovl_b + theta * do
    core_x, ovl_x = max(0, core_x), max(0, ovl_x)
    if ovl_x * intent["equity_before"] < 1:
        core_x, ovl_x = core_x + ovl_x, 0
    elif core_x * intent["equity_before"] < 1:
        core_x, ovl_x = 0, ovl_x + core_x
    return ovl_x / (core_x + ovl_x) if core_x + ovl_x > 0 else 0


@pytest.mark.parametrize("seed", range(1, 6))
def test_small_account_stochastic_safety(seed, tmp_path, monkeypatch):
    fast_e2e_state(monkeypatch)
    frame = pd.read_csv("tests_v5/golden/golden_high.csv.gz", index_col=0, parse_dates=True)
    with open("tests_v5/golden/golden_meta.json", encoding="utf-8") as handle:
        meta = json.load(handle)
    first = pd.Timestamp(meta["files"]["golden_high.csv.gz"]["first_decision"])
    first_position = frame.index.get_loc(first)
    opposing_expected = set(meta["files"]["golden_high.csv.gz"]["opposing_component_days"])
    history = list(frame.iloc[:first_position]["c"])
    rng = random.Random(seed)
    broker = FakeBroker(cash=490, prices={"TQQQ": 100})
    now = [datetime(2010, 1, 1, 19, 50, tzinfo=UTC)]

    def sleep(seconds):
        now[0] += timedelta(seconds=seconds)

    executor = Executor(
        broker, SimpleNamespace(orders_enabled=True), tmp_path,
        PACKAGES["high"], lambda: now[0], sleep,
    )
    within = 0
    compared = 0
    opposing_seen = set()
    chains = {}

    for position in range(first_position, len(frame)):
        index, row = frame.index[position], frame.iloc[position]
        if position > first_position:
            product_return = 0.0 if pd.isna(row["r_T"]) else float(row["r_T"])
            broker.set_price("TQQQ", float(broker.prices["TQQQ"]) * (1 + product_return))
        snapshot = take_snapshot(broker, PACKAGES["high"], set())
        decision = decide(
            history, row["px"], row["h"], row["l"], snapshot.product_weight,
            executor.load_overlay_fraction(), PACKAGES["high"],
        )
        assert decision.status == READY
        if decision.trade:
            day = index.date()
            now[0] = datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=19, minutes=50)
            broker.clock = BrokerClock(now[0], True, now[0] + timedelta(days=1), now[0] + timedelta(minutes=10))
            broker.adverse_bps = Decimal(str(rng.uniform(0, 20)))
            roll = rng.random()
            if roll < 0.10:
                broker.script(partial(rng.uniform(0.30, 0.90)))
            elif roll < 0.20:
                broker.script(invisible(2))
            elif roll < 0.25:
                broker.script("timeout_after_accept" if rng.random() < 0.5 else "timeout_before_accept")
            window = Window(
                now[0] - timedelta(minutes=1), now[0] + timedelta(minutes=1),
                now[0] + timedelta(seconds=60), now[0] + timedelta(seconds=5),
                now[0] + timedelta(minutes=11), now[0] + timedelta(minutes=15),
            )
            record = SessionRecord.new(tmp_path, day, "paper")
            record.data["target"] = {
                "core_new": decision.core_new, "ovl_new": decision.ovl_new,
                "total_new": decision.total_new,
            }
            save_with_retry(record)
            for _ in range(6):
                record = drive_with_restart(executor, record, window, tmp_path, day)
                if record.is_final:
                    break
            assert record.is_final
            chains[record.status] = chains.get(record.status, 0) + 1
            if record.status == "DONE":
                # The executor's own promise: a finished chain holds what THIS decision asked for.
                reached = take_snapshot(broker, PACKAGES["high"], set())
                assert abs(reached.product_weight - decision.total_new) <= max(0.015, 2 / reached.equity), str(index)
            for intent in record.data["intents"]:
                if intent.get("theta") is not None:
                    assert intent["fraction_after"] == independent_allocate(intent)
                    if (intent["core_new"] - intent["core_b"]) * (intent["ovl_new"] - intent["ovl_b"]) < 0:
                        opposing_seen.add(index.date().isoformat())
            assert executor.load_overlay_fraction() == record.data["overlay_fraction_committed"]
        after = take_snapshot(broker, PACKAGES["high"], set())
        assert broker.cash >= 0
        assert all(qty >= 0 for qty in broker.positions.values())
        assert after.product_weight <= PACKAGES["high"].cap + 0.01
        compared += 1
        within += abs(after.product_weight - float(row["total"])) <= 0.03
        history.append(row["c"])
    ids = [order.client_order_id for order in broker.orders]
    assert len(ids) == len(set(ids))
    # Closeness to the golden path. A fill a few basis points off can flip a band-edge decision (held 0.6109
    # against 0.6107: one path trades, the other does not) and the two valid paths then differ by the band
    # width until the next common trade. Seeds 1 to 5 give 98.9% to 99.9%; the bound guards against drift.
    assert within / compared >= 0.98
    assert (chains.get("DONE", 0) + chains.get("NETTED", 0)) / sum(chains.values()) >= 0.99, chains
    assert 27000 <= float(broker.equity) <= 59000
    assert opposing_expected <= opposing_seen
