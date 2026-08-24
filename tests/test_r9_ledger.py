from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import config as config_module
import core.executor as executor_module
import core.index_parking as parking_module
from core.executor import OrderExecutor
from core.fill_ledger import episode_realized_pnl, read_fills, record_fill
from core.index_parking import IndexParkingManager
from core.order_journal import bind, prepare, resolve, stale_prepared
from core.position_manager import PositionManager
from tools.ledger_backfill import backfill


@pytest.fixture(autouse=True)
def isolated_r9_state(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        config_module, "state_path", lambda name: str(tmp_path / name)
    )
    return tmp_path


def _fill_order(
    oid: str, qty: float, price: float, execution_id: str | None,
    *, symbol: str = "AAPL", client_order_id: str | None = None,
):
    return SimpleNamespace(
        id=oid,
        symbol=symbol,
        filled_qty=str(qty),
        filled_avg_price=str(price),
        status="filled",
        execution_id=execution_id,
        client_order_id=client_order_id,
    )


class _PartialClient:
    def __init__(self, avg_entry_price=100.0, qty=6.0):
        self.avg_entry_price = avg_entry_price
        self.qty = qty

    def get_all_positions(self):
        return [SimpleNamespace(
            symbol="AAPL",
            qty=str(self.qty),
            avg_entry_price=(
                None if self.avg_entry_price is None
                else str(self.avg_entry_price)
            ),
        )]


def _partial_manager(avg_entry_price=100.0):
    bot = SimpleNamespace(
        client=_PartialClient(avg_entry_price=avg_entry_price),
        positions={},
        short_positions={},
        sell_cooldown={},
        _save_position_metadata=lambda: True,
        _stash_exit_flags=lambda *_args: None,
    )
    return PositionManager(bot), bot


def _partial_state(entry_ts: str, *, target=4.0, attempt_qty=2.0):
    return {
        "entry_price": 999.0,  # PnL oracle bunu degil broker avg'yi kullanmali.
        "entry_time": entry_ts,
        "entry_time_utc": entry_ts,
        "episode_id": "episode-aapl",
        "partial_sold": False,
        "partial_intent": {
            "status": "SUBMITTED",
            "client_order_id": "cid-partial-1",
            "order_id": "partial-1",
            "target_qty": target,
            "filled_qty": 0.0,
            "attempt_qty": attempt_qty,
            "attempt_base_filled_qty": 0.0,
            "attempt_terminal": False,
        },
    }


class _CloseClient:
    def __init__(self, price=105.0, qty=6.0):
        self.price = price
        self.qty = qty
        self.closed = False
        self.close_order = _fill_order(
            "final-order", qty, price, "exec-final",
            client_order_id="cid-final",
        )

    def get_open_position(self, _symbol):
        return SimpleNamespace(
            symbol="AAPL",
            qty=str(self.qty),
            avg_entry_price="100",
            current_price=str(self.price),
            unrealized_pl=str((self.price - 100.0) * self.qty),
        )

    def get_orders(self, _request):
        return []

    def close_position(self, _symbol):
        self.closed = True
        return self.close_order

    def get_all_positions(self):
        return [] if self.closed else [self.get_open_position("AAPL")]

    def get_order_by_id(self, oid):
        assert str(oid) == "final-order"
        return self.close_order


class _Performance:
    def __init__(self):
        self.calls = []

    def record_trade(self, **kwargs):
        self.calls.append(kwargs)


class _AgentPerformance:
    def __init__(self):
        self.calls = []

    def record_outcome(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def _closing_bot(entry_ts: str, *, with_performance=True):
    client = _CloseClient()
    performance = _Performance()
    agent_perf = _AgentPerformance()
    bot = SimpleNamespace(
        client=client,
        positions={"AAPL": {
            "entry_price": 100.0,
            "qty": 6.0,
            "entry_time": entry_ts,
            "entry_time_utc": entry_ts,
            "episode_id": "episode-aapl",
            "provenance": "strategy",
            "close_in_progress": False,
        }},
        short_positions={},
        sell_cooldown={},
        consecutive_errors=0,
        trades_today=[],
        last_trade_time={},
        _exit_flag_cache={},
        _consecutive_losses=2,
        _symbol_consecutive_losses={"AAPL": 2},
        position_manager=SimpleNamespace(_verify_attempts=1),
        _stash_exit_flags=lambda *_args: None,
        _save_position_metadata=lambda: True,
        agent_perf=agent_perf,
    )
    if with_performance:
        bot.performance = performance
    return bot, performance, agent_perf


def test_two_distinct_partials_plus_final_books_full_episode_once(monkeypatch):
    """a) Oracle sabittir; production episode helper'i oracle tarafinda kullanilmaz."""
    entry_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    manager, _ = _partial_manager(avg_entry_price=100.0)
    state = _partial_state(entry_ts)

    manager._finish_partial_attempt(
        "AAPL", state,
        _fill_order("partial-1", 2, 110, "exec-partial-1", client_order_id="cid-partial-1"),
        True, "ilk dolum",
    )
    state["partial_intent"].update({
        "client_order_id": "cid-partial-2",
        "order_id": "partial-2",
        "attempt_qty": 2.0,
        "attempt_base_filled_qty": 2.0,
        "attempt_terminal": False,
    })
    manager._finish_partial_attempt(
        "AAPL", state,
        _fill_order("partial-2", 2, 115, "exec-partial-2", client_order_id="cid-partial-2"),
        True, "ikinci dolum",
    )

    streak_calls = []
    monkeypatch.setattr(
        executor_module,
        "update_loss_streak",
        lambda _bot, symbol, pnl: streak_calls.append((symbol, pnl)),
    )
    bot, performance, agent_perf = _closing_bot(entry_ts)
    assert OrderExecutor(bot).execute_sell("AAPL", "TAKE_PROFIT") is True

    fixed_oracle = (110.0 - 100.0) * 2 + (115.0 - 100.0) * 2 + (105.0 - 100.0) * 6
    assert fixed_oracle == pytest.approx(80.0)
    assert len(performance.calls) == 1
    assert performance.calls[0]["pnl"] == pytest.approx(fixed_oracle)
    assert streak_calls == [("AAPL", pytest.approx(fixed_oracle))]
    assert len(agent_perf.calls) == 1
    assert agent_perf.calls[0][0][2] == pytest.approx(fixed_oracle)
    sells = [fill for fill in read_fills("AAPL") if fill["side"] == "SELL"]
    assert len(sells) == 3
    assert sum(fill["pnl_usd"] for fill in sells) == pytest.approx(fixed_oracle)


def test_duplicate_replay_does_not_change_rows_or_total():
    """b) Ayni execution dizisi iki kez oynatilinca ledger ayni kalir."""
    sequence = [
        dict(execution_id="exec-1", order_id="order-a", qty=2, price=110, pnl_usd=20),
        dict(execution_id="exec-2", order_id="order-b", qty=2, price=115, pnl_usd=30),
        dict(execution_id="exec-3", order_id="order-c", qty=6, price=105, pnl_usd=30),
    ]
    for _ in range(2):
        for item in sequence:
            assert record_fill(
                symbol="AAPL", side="SELL", provenance="strategy", **item
            ) is True
    fills = read_fills("AAPL")
    assert len(fills) == 3
    assert sum(fill["pnl_usd"] for fill in fills) == pytest.approx(80.0)


def test_same_order_id_two_execution_ids_are_two_rows():
    """c) order_id fill kimligi degildir; iki execution yutulmaz."""
    for execution_id, price in (("exec-a", 101), ("exec-b", 102)):
        record_fill(
            symbol="PLTR", side="SELL", qty=1, price=price,
            pnl_usd=price - 100, provenance="strategy",
            execution_id=execution_id, order_id="same-order",
        )
    fills = read_fills("PLTR")
    assert [fill["dedupe_key"] for fill in fills] == ["exec-a", "exec-b"]


def test_missing_execution_id_uses_composite_key_and_is_degraded():
    """d) Execution yoksa bilesik anahtar acik degraded kaydidir."""
    record_fill(
        symbol="SMCI", side="SELL", qty=2, price=51.25,
        pnl_usd=2.5, provenance="strategy", order_id="order-legacy",
    )
    fill = read_fills("SMCI")[0]
    assert fill["dedupe_key"] == "order-legacy|SELL|2.0|51.25"
    assert fill["degraded"] is True


def test_restart_sees_unbound_prepared_and_keeps_provenance():
    """e) PREPARED crash penceresi restart uzlastirmasinda gorunur."""
    prepare("cid-crash", "AAPL", "BUY", "strategy", qty=3)
    stale = stale_prepared()
    assert len(stale) == 1
    assert stale[0]["client_order_id"] == "cid-crash"
    assert stale[0]["provenance"] == "strategy"
    assert resolve("not-bound") == "UNKNOWN"

    bind("cid-crash", "broker-order-1")
    assert stale_prepared() == []
    assert resolve("broker-order-1") == "strategy"


def test_corrupt_ledger_logs_error_but_verified_exit_still_finishes(
    isolated_r9_state: Path, monkeypatch,
):
    """f) Muhasebe arizasi koruyucu cikisi fail-closed yapamaz."""
    ledger = isolated_r9_state / "fill_ledger.jsonl"
    ledger.write_text("{bozuk-json\n", encoding="utf-8")
    errors = []
    monkeypatch.setattr(executor_module.logger, "error", errors.append)
    entry_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    bot, performance, _agent = _closing_bot(entry_ts)

    assert OrderExecutor(bot).execute_sell("AAPL", "STOP_LOSS") is True
    assert "AAPL" not in bot.positions
    assert len(performance.calls) == 1
    assert any("DEFTER HATASI" in message for message in errors)


def test_index_parking_fills_are_tagged_and_excluded_from_strategy_episode(
    monkeypatch,
):
    """g) Parking BUY/SELL ledger'a girer ama strategy episode'ina sizmaz."""
    orders = {}

    class Client:
        def submit_order(self, _request):
            order = _fill_order(
                "park-buy", 2, 100, "exec-park-buy", symbol="SPY"
            )
            orders[order.id] = order
            return order

        def close_position(self, _symbol, close_options=None):
            order = _fill_order(
                "park-sell", 1, 103, "exec-park-sell", symbol="SPY"
            )
            orders[order.id] = order
            return order

        def get_order_by_id(self, oid):
            return orders[str(oid)]

    manager = object.__new__(IndexParkingManager)
    manager.bot = SimpleNamespace(client=Client())
    manager.config = {}
    manager.symbol = "SPY"
    manager._last_buy_date = None
    manager._save_dates = lambda: None
    manager._get_park_position = lambda: (5.0, 100.0, 500.0)
    monkeypatch.setattr(
        parking_module, "can_open_new_risk", lambda *_args, **_kwargs: (True, "")
    )

    manager._buy(200.0)
    manager._sell(100.0)

    fills = read_fills("SPY")
    assert [fill["side"] for fill in fills] == ["BUY", "SELL"]
    assert {fill["provenance"] for fill in fills} == {"index_parking"}
    old = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert episode_realized_pnl("SPY", old) == 0.0


def test_missing_broker_average_entry_records_none_degraded_and_finishes():
    """h) Giris ortalamasi bilinmiyorsa bacak kaybolmaz, PnL bilinmiyor kalir."""
    entry_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    manager, _ = _partial_manager(avg_entry_price=None)
    state = _partial_state(entry_ts, target=2.0, attempt_qty=2.0)

    manager._finish_partial_attempt(
        "AAPL", state,
        _fill_order("partial-no-avg", 2, 110, "exec-no-avg"),
        True, "terminal dolum",
    )

    fill = read_fills("AAPL")[0]
    assert fill["pnl_usd"] is None
    assert fill["degraded"] is True
    assert state["partial_intent"]["status"] == "FILLED"
    assert state["partial_sold"] is True


def test_backfill_defaults_are_dry_run_idempotent_and_unknown():
    activities = [
        {
            "id": "activity-1", "transaction_time": "2026-08-20T14:00:00Z",
            "symbol": "AAPL", "side": "buy", "qty": "1", "price": "100",
            "order_id": "same-order",
        },
        {
            "id": "activity-2", "transaction_time": "2026-08-20T14:00:01Z",
            "symbol": "AAPL", "side": "buy", "qty": "2", "price": "101",
            "order_id": "same-order",
        },
    ]
    summary = backfill(activities, apply=False)
    assert summary == {
        "to_add": 2,
        "added": 0,
        "skipped": 0,
        "unknown": 2,
        "symbols": {"AAPL": 2},
        "apply": False,
    }
    assert read_fills() == []


def test_backfill_never_guesses_pnl_or_provenance_for_unprovable_history():
    """R9 KARAR KILIDI (Claude, Level 10 devralma , 2026-08-24).

    Order journal deploy ONCESI her emir icin BOSTUR; dolayisiyla hicbir
    tarihsel dolumun stratejisi KANITLANAMAZ. Backfill'in isi defteri
    TAMAMLAMAKTIR, gecmise PnL ATFETMEK degil , tarihsel gerceklesen PnL
    R10'un isi (broker dolumlarindan yeniden kurulur ve bagimsiz olarak
    kurusu kurusuna dogrulandi).

    Bu test tahmin yasagini kilitler: ileride biri "yardim olsun diye"
    maliyet tabani uydurup PnL yazarsa buradan kirmizi doner. Kaldirmadan
    once RF-PLAN-3 K-maddesine bak.
    """
    activities = [
        {
            "id": "act-buy", "transaction_time": "2026-07-10T14:00:00Z",
            "symbol": "SMCI", "side": "buy", "qty": "10", "price": "30.00",
            "order_id": "legacy-buy-order",
        },
        {
            "id": "act-sell", "transaction_time": "2026-07-16T18:30:00Z",
            "symbol": "SMCI", "side": "sell", "qty": "10", "price": "48.95",
            "order_id": "legacy-sell-order",
        },
    ]

    summary = backfill(activities, apply=True)
    assert summary["added"] == 2, summary
    assert summary["unknown"] == 2, "journal'siz gecmis UNKNOWN olmali"

    written = read_fills(symbol="SMCI")
    assert len(written) == 2

    for row in written:
        assert row["provenance"] == "UNKNOWN", (
            f"provenance TAHMIN EDILMIS: {row['provenance']}"
        )
        assert row["pnl_usd"] is None, (
            f"PnL TAHMIN EDILMIS: {row['pnl_usd']} , maliyet tabani uydurulamaz"
        )

    # Ve kritik sonuc: tahmin edilmedigi icin bu gecmis, strateji episode
    # toplamasina SIZAMAZ (yoksa $189'luk hayali kar 4/4 kapisini yesile boyardi).
    assert episode_realized_pnl("SMCI", "2026-07-01T00:00:00+00:00") == 0.0
