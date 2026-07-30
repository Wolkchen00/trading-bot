from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

import stock_bot as stock_bot_module
from core.executor import OrderExecutor
from core.trade_gates import TradeGates, plan_exit_pcts
from stock_bot import StockBot
from tools.olcum_raporu import (
    Fill, broker_trading_days, phantom_count, reconstruct_closed_trades,
)


def _entry_bot(queue_result: bool):
    calls = {"queue": 0, "buy": 0}
    bot = StockBot.__new__(StockBot)
    bot.index_parking = SimpleNamespace(is_parking_symbol=lambda _symbol: False)
    bot._get_technical_analysis = lambda _symbol, _config: {
        "price": 100.0, "atr": 1.0, "rsi": 70,
        "bb_position": "MIDDLE", "vwap_signal": "NEUTRAL",
    }
    bot._get_agent_decision = lambda _symbol, _analysis, _config: {
        "signal": "BUY", "confidence": 80, "reasoning": "test",
        "weighted_score": 20,
    }
    bot._bear_breadth = {}
    bot.positions = {}
    bot.short_positions = {}
    bot._market_regime = "BULL"
    bot._options_enabled = False
    bot.bear_brain = SimpleNamespace(short_conf_relief=lambda: 0)
    bot.sector_rotator = SimpleNamespace(
        should_buy=lambda _symbol: True,
        get_weight_multiplier=lambda _symbol: 1.0,
        current_regime="NORMAL",
    )
    bot.trade_gates = SimpleNamespace(
        check_all_gates=lambda _symbol, _analysis, _config: (True, "")
    )

    def add_signal(*_args):
        calls["queue"] += 1
        return queue_result

    def execute_buy(*_args):
        calls["buy"] += 1
        return False

    bot.signal_queue = SimpleNamespace(add_signal=add_signal)
    bot.executor = SimpleNamespace(execute_buy=execute_buy)
    return bot, calls


def test_e1_already_queued_extended_entry_never_market_buys(monkeypatch):
    monkeypatch.setattr(stock_bot_module, "BOT_MODE", "both")
    bot, calls = _entry_bot(queue_result=False)
    bot._analyze_and_trade("AMD", {
        "min_confidence_score": 30,
        "pullback_queue_enabled": True,
    })
    assert calls == {"queue": 1, "buy": 0}


def test_e1_queue_disabled_path_is_unchanged(monkeypatch):
    monkeypatch.setattr(stock_bot_module, "BOT_MODE", "both")
    bot, calls = _entry_bot(queue_result=False)
    bot._analyze_and_trade("AMD", {
        "min_confidence_score": 30,
        "pullback_queue_enabled": False,
    })
    assert calls == {"queue": 0, "buy": 1}


@pytest.mark.parametrize(
    ("analysis", "config", "expected"),
    [
        (
            {"fundamental_data_ok": True, "fundamental_score": -1},
            {"fundamental_gate_enabled": True, "fundamental_gate_min_score": 0},
            (False, "FUND_NEGATIVE"),
        ),
        (
            {"fundamental_data_ok": False, "fundamental_score": 0},
            {"fundamental_gate_enabled": True, "fundamental_gate_min_score": 0},
            (False, "FUND_NO_DATA"),
        ),
        (
            {"fundamental_data_ok": False, "fundamental_score": -30},
            {"fundamental_gate_enabled": False, "fundamental_gate_min_score": 0},
            (True, ""),
        ),
    ],
)
def test_e2_gate_negative_missing_and_disabled(analysis, config, expected):
    config = {
        **config,
        "ema200_trend_gate": False,
        "earnings_gate_enabled": False,
        "loss_streak_enabled": False,
        "coin_filter_enabled": False,
        "rr_gate_enabled": False,
        "multi_tf_enabled": False,
        "volatility_filter_enabled": False,
    }
    assert TradeGates(SimpleNamespace()).check_all_gates(
        "AMD", {"confidence": 80, **analysis}, config
    ) == expected


@pytest.mark.parametrize(
    ("fund_result", "score", "data_ok"),
    [
        (
            {"fundamental_score": 0, "metrics": {"pe_ratio": 0, "eps": 0}},
            0, True,
        ),
        (
            {"fundamental_score": 0, "metrics": {}},
            0, False,
        ),
    ],
)
def test_e2_data_ok_wiring_uses_analyzer_return_shape(fund_result, score, data_ok):
    captured = {}
    bot = SimpleNamespace(
        fundamental_analyzer=SimpleNamespace(
            analyze_fundamentals=lambda _symbol: fund_result
        ),
        news_analyzer=SimpleNamespace(
            analyze_stock_news=lambda _symbol: {"news_score": 0}
        ),
        social_analyzer=SimpleNamespace(
            analyze_social=lambda _symbol: {"social_score": 0}
        ),
        agent_perf=SimpleNamespace(get_dynamic_weights=lambda: {}),
        coordinator=SimpleNamespace(
            WEIGHTS={},
            decide=lambda symbol, tech, fund, sent, social, risk:
                captured.update(analysis=tech, fund=fund) or {"signal": "HOLD"},
        ),
        _build_risk_data=lambda _analysis, _config: {},
    )
    analysis = {}
    StockBot._get_agent_decision(bot, "AMD", analysis, {})
    assert analysis["fundamental_score"] == score
    assert analysis["fundamental_data_ok"] is data_ok
    assert captured["analysis"] is analysis


def test_option_b_paper_sweep_stays_in_band_and_rr_does_not_block():
    config_path = Path(__file__).resolve().parents[1] / "config.py"
    spec = importlib.util.spec_from_file_location("_r6_fresh_config", config_path)
    fresh = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(fresh)
    live = fresh.STOCK_CONFIG
    overrides = fresh.PAPER_AGGRESSIVE_CONFIG
    assert live["partial_profit_pct"] == 0.05
    assert live["take_profit_pct"] == 0.08
    assert live["take_profit_max_pct"] == 0.12
    assert live["fundamental_gate_enabled"] is False
    assert overrides["partial_profit_pct"] == 0.03
    assert overrides["take_profit_pct"] == 0.05
    assert overrides["take_profit_max_pct"] == 0.075
    assert overrides["min_rr_ratio"] == 1.25
    assert overrides["fundamental_gate_enabled"] is True

    paper = dict(live)
    paper.update(overrides)
    gates = TradeGates(SimpleNamespace())
    for atr_pct in (0.5, 1.0, 1.5, 2.0, 2.77, 3.0):
        sl, tp = plan_exit_pcts(atr_pct, 100.0, paper)
        assert 0.05 <= tp <= 0.075
        assert tp / sl + 1e-9 >= paper["min_rr_ratio"]
        assert gates._check_rr_gate(
            "TEST", {"atr": atr_pct, "price": 100.0}, paper
        ) == (False, "")


def test_verified_close_uses_real_fill_and_reconcile_cannot_double_book():
    entry_time = (datetime.now() - timedelta(days=1)).isoformat()
    position = {
        "entry_price": 100.0, "qty": 5.0, "entry_time": entry_time,
        "close_in_progress": False,
    }
    close_ack = SimpleNamespace(id="close-1", filled_avg_price=None, filled_qty=None)
    real_fill = SimpleNamespace(
        id="close-1", symbol="AAPL", side=stock_bot_module.OrderSide.SELL,
        filled_avg_price="98.25", filled_qty="5", order_type="market",
    )

    class Client:
        def get_open_position(self, _symbol):
            return SimpleNamespace(current_price="99.50", unrealized_pl="-2.50")

        def get_orders(self, request):
            if request.status == stock_bot_module.QueryOrderStatus.CLOSED:
                return [real_fill]
            return []

        def cancel_order_by_id(self, _order_id):
            raise AssertionError("no open orders should be cancelled")

        def close_position(self, _symbol):
            return close_ack

        def get_all_positions(self):
            return []

        def get_order_by_id(self, order_id):
            assert order_id == "close-1"
            return real_fill

    bot = SimpleNamespace(
        client=Client(),
        positions={"AAPL": dict(position)},
        short_positions={},
        sell_cooldown={},
        consecutive_errors=0,
        trades_today=[],
        last_trade_time={},
        _exit_flag_cache={},
        _consecutive_losses=0,
        _symbol_consecutive_losses={},
        position_manager=SimpleNamespace(_verify_attempts=1),
        _stash_exit_flags=lambda *_args: None,
        _save_position_metadata=lambda: True,
    )
    executor = OrderExecutor(bot)
    assert executor.execute_sell("AAPL", "STOP_LOSS") is True
    assert len(bot.trades_today) == 1
    booked = bot.trades_today[0]
    assert booked["price"] == 98.25
    assert booked["qty"] == 5.0
    assert booked["pnl"] == pytest.approx(-8.75)
    assert booked["exit_order_id"] == "close-1"

    # Stale yerel state aynı pozisyonu yeniden sunsa bile EXTERNAL_CLOSE yazılmamalı.
    bot.positions["AAPL"] = dict(position)
    bot._exit_already_recorded = MethodType(StockBot._exit_already_recorded, bot)
    StockBot._reconcile_external_exit(bot, "AAPL", side="LONG")
    assert len(bot.trades_today) == 1


def test_measurement_reconstructs_partial_and_flags_local_broker_mismatch():
    start = datetime(2026, 7, 1, 14, 0, tzinfo=timezone.utc)
    fills = [
        Fill("AMD", "BUY", 10, 100, start, "b1"),
        Fill("AMD", "SELL", 5, 103, start + timedelta(hours=1), "s1"),
        Fill("AMD", "SELL", 5, 105, start + timedelta(hours=2), "s2"),
    ]
    trades = reconstruct_closed_trades(fills)
    assert len(trades) == 1
    assert trades[0].pnl == pytest.approx(40)
    assert trades[0].profitable_partial_at_three()

    local = [
        {"action": "SELL", "symbol": "AMD", "qty": 5, "time": start.isoformat()},
        {
            "action": "SELL", "symbol": "AMD", "qty": 5,
            "time": (start + timedelta(minutes=1)).isoformat(),
        },
        {"action": "SELL", "symbol": "AMD", "qty": 5, "time": (start + timedelta(hours=2)).isoformat()},
    ]
    phantom, duplicates, unmatched = phantom_count(local, trades)
    assert duplicates == 1
    assert unmatched == 1
    assert phantom == 1

    calendar = SimpleNamespace(get_calendar=lambda request: [request] * 3)
    assert broker_trading_days(
        calendar, start.date(), (start + timedelta(days=4)).date()
    ) == 3
