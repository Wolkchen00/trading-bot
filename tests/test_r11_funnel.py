from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import core.fill_ledger
from core.executor import OrderExecutor
from core.funnel import DailyFunnel
from stock_bot import StockBot


TODAY = date(2026, 7, 30)


class StubNotifier:
    def __init__(self):
        self.calls = []

    def notify_critical(self, kind, message):
        self.calls.append((kind, message))
        return True


def _funnel(tmp_path, today=TODAY):
    return DailyFunnel(
        path=str(tmp_path / "funnel.json"), today_fn=lambda: today
    )


def _analysis_bot(tmp_path, decision):
    bot = StockBot.__new__(StockBot)
    bot.funnel = _funnel(tmp_path)
    bot.index_parking = SimpleNamespace(is_parking_symbol=lambda _symbol: False)
    bot._get_technical_analysis = lambda _symbol, _config: {
        "price": 100.0,
        "atr": 1.0,
    }
    bot._get_agent_decision = lambda _symbol, _analysis, _config: dict(decision)
    bot._bear_breadth = {}
    bot._market_regime = "NORMAL"
    bot.bear_brain = SimpleNamespace(short_conf_relief=lambda: 0)
    bot._options_enabled = False
    bot.positions = {}
    bot.short_positions = {}
    return bot


def _buy_config(live_entries_enabled=True):
    return {
        "cash_reserve_pct": 0,
        "max_position_pct": 1,
        "min_trade_value": 10,
        "default_tier_weight": 1,
        "tier_weights": {},
        "stop_loss_pct": 0.05,
        "stop_loss_max_pct": 0.08,
        "atr_stop_multiplier": 2,
        "take_profit_pct": 0.10,
        "min_risk_reward": 2,
        "live_entries_enabled": live_entries_enabled,
    }


def test_buy_short_conf_rejections_split_and_total_preserved(tmp_path):
    buy_bot = _analysis_bot(
        tmp_path, {"signal": "BUY", "confidence": 10, "weighted_score": 1}
    )
    buy_bot._analyze_and_trade("AAPL", {"min_confidence_score": 50})

    short_bot = buy_bot
    short_bot._get_agent_decision = lambda *_args: {
        "signal": "SHORT",
        "confidence": 10,
        "weighted_score": -1,
    }
    short_bot._analyze_and_trade("TSLA", {"min_confidence_score": 50})

    data = buy_bot.funnel.snapshot(TODAY.isoformat())
    assert data["conf_below_min"] == 2
    assert data["conf_below_min_buy"] == 1
    assert data["conf_below_min_short"] == 1
    assert data["stage_symbols"]["conf_below_min"] == ["AAPL", "TSLA"]


def test_same_symbol_100_events_has_one_unique_symbol(tmp_path):
    funnel = _funnel(tmp_path)
    for _ in range(100):
        funnel.bump("gate_block", reason="LIVE_LOCK_R5", symbol="AAPL")

    data = funnel.snapshot(TODAY.isoformat())
    assert data["gate_block"] == 100
    assert data["stage_symbols"]["gate_block"] == ["AAPL"]
    assert "olay=100 (benzersiz sembol=1)" in "\n".join(
        funnel.report_lines(TODAY.isoformat())
    )


def test_three_symbols_have_three_events_and_three_unique_symbols(tmp_path):
    funnel = _funnel(tmp_path)
    for symbol in ("AAPL", "MSFT", "NVDA"):
        funnel.bump("sector_block", symbol=symbol)

    data = funnel.snapshot(TODAY.isoformat())
    assert data["sector_block"] == 3
    assert data["stage_symbols"]["sector_block"] == ["AAPL", "MSFT", "NVDA"]
    assert "olay=3 (benzersiz sembol=3)" in "\n".join(
        funnel.report_lines(TODAY.isoformat())
    )


def test_old_schema_without_symbol_sets_reports_unknown(tmp_path):
    path = tmp_path / "funnel.json"
    path.write_text(
        json.dumps(
            {
                "days": {TODAY.isoformat(): {"gate_block": 7}},
                "last_entry_date": None,
                "last_no_trade_alarm_date": None,
            }
        ),
        encoding="utf-8",
    )

    funnel = DailyFunnel(path=str(path), today_fn=lambda: TODAY)
    data = funnel.snapshot(TODAY.isoformat())
    assert data["gate_block"] == 7
    assert data["stage_symbols"] is None
    assert "olay=7 (benzersiz sembol=UNKNOWN)" in "\n".join(
        funnel.report_lines(TODAY.isoformat())
    )


def test_wash_sale_block_is_counted_without_changing_rejection(tmp_path):
    bot = StockBot.__new__(StockBot)
    bot.funnel = _funnel(tmp_path)
    bot.wash_sale_tracker = SimpleNamespace(
        check_wash_sale=lambda _symbol: (True, "30 gun yasak")
    )

    assert bot._check_wash_sale("AAPL") == (True, "30 gun yasak")
    data = bot.funnel.snapshot(TODAY.isoformat())
    assert data["wash_sale_block"] == 1
    assert data["stage_symbols"]["wash_sale_block"] == ["AAPL"]


def test_index_and_inverse_etf_signals_are_counted(tmp_path):
    bot = _analysis_bot(
        tmp_path,
        {"signal": "BUY", "confidence": 90, "weighted_score": 50},
    )
    for symbol in ("SPY", "SQQQ"):
        bot._analyze_and_trade(symbol, {"min_confidence_score": 50})

    data = bot.funnel.snapshot(TODAY.isoformat())
    assert data["index_signal"] == 2
    assert data["stage_symbols"]["index_signal"] == ["SPY", "SQQQ"]


def test_live_bracket_rejection_counts_reason_and_never_falls_back(tmp_path):
    class Client:
        def __init__(self):
            self.submit_count = 0

        def get_account(self):
            return SimpleNamespace(cash="1000", equity="1000")

        def submit_order(self, _request):
            self.submit_count += 1
            raise RuntimeError("bracket rejected")

    funnel = _funnel(tmp_path)
    client = Client()

    def bump(stage, reason=None, symbol=None):
        funnel.bump(stage, reason=reason, symbol=symbol)

    bot = SimpleNamespace(
        client=client,
        is_paper=False,
        equity_floor=0,
        max_pos_usd=100,
        consecutive_errors=0,
        positions={},
        _funnel_bump=bump,
    )
    analysis = {"price": 10, "atr": 0, "confidence": 80, "reasons": []}

    assert OrderExecutor(bot).execute_buy(
        "AAPL", analysis, _buy_config()
    ) is False
    assert client.submit_count == 1
    assert bot.positions == {}
    data = funnel.snapshot(TODAY.isoformat())
    assert data["gate_block"] == 1
    assert data["gate_block_reasons"] == {"FRACTIONAL_NO_BRACKET": 1}


def test_reached_executor_is_zero_when_r5_locked_and_bumps_after_guard(tmp_path):
    class Client:
        def __init__(self):
            self.get_account_calls = 0

        def get_account(self):
            self.get_account_calls += 1
            return SimpleNamespace(cash="1000", equity="1000")

    funnel = _funnel(tmp_path)

    def bump(stage, reason=None, symbol=None):
        funnel.bump(stage, reason=reason, symbol=symbol)

    bot = SimpleNamespace(
        client=Client(),
        is_paper=False,
        equity_floor=10**9,
        consecutive_errors=0,
        _funnel_bump=bump,
    )
    analysis = {"price": 100, "confidence": 90}

    assert OrderExecutor(bot).execute_buy(
        "AMZN", analysis, _buy_config(False)
    ) is False
    assert funnel.snapshot(TODAY.isoformat())["reached_executor"] == 0
    assert bot.client.get_account_calls == 0

    assert OrderExecutor(bot).execute_buy(
        "AMZN", analysis, _buy_config(True)
    ) is False
    data = funnel.snapshot(TODAY.isoformat())
    assert data["reached_executor"] == 1
    assert data["stage_symbols"]["reached_executor"] == ["AMZN"]
    assert bot.client.get_account_calls == 1


def test_downstream_bottleneck_ignores_dominant_signal_hold(tmp_path):
    funnel = _funnel(tmp_path)
    for _ in range(100):
        funnel.bump("signal_hold", symbol="AAPL")
    for _ in range(4):
        funnel.bump("gate_block", reason="LOSS_STREAK_WARN", symbol="MSFT")

    assert funnel.dominant_stage(TODAY.isoformat()) == ("signal_hold", 100)
    assert funnel.downstream_bottleneck(TODAY.isoformat()) == (
        "gate_block",
        4,
    )


def test_downstream_bottleneck_returns_no_data_without_downstream_events(tmp_path):
    funnel = _funnel(tmp_path)
    funnel.bump("signal_buy", symbol="AAPL")
    funnel.bump("signal_hold", symbol="MSFT")
    assert funnel.downstream_bottleneck(TODAY.isoformat()) == ("veri_yok", 0)


def test_numeric_dominant_is_exact_dominant_stage_wrapper(tmp_path):
    funnel = _funnel(tmp_path)
    for _ in range(3):
        funnel.bump("gate_block", reason="LIVE_LOCK_R5")
        funnel.bump("signal_hold")
    assert funnel.numeric_dominant(TODAY.isoformat()) == funnel.dominant_stage(
        TODAY.isoformat()
    )


def test_no_trade_message_names_bottleneck_numeric_context_and_gate_reason(tmp_path):
    funnel = _funnel(tmp_path)
    funnel.last_entry_date = "2026-07-20"
    for _ in range(20):
        funnel.bump("signal_hold", symbol="AAPL")
    for _ in range(3):
        funnel.bump("gate_block", reason="LIVE_LOCK_R5", symbol="MSFT")
    notifier = StubNotifier()

    assert funnel.maybe_notify_no_trade(
        closed_day=TODAY,
        today=TODAY,
        threshold=3,
        is_paper=False,
        notifier=notifier,
        history_path=str(tmp_path / "missing.json"),
    )
    kind, message = notifier.calls[0]
    assert kind == "NO_TRADE"
    assert "Baskin downstream bloker: gate_block (3)" in message
    assert "Sayisal baskin asama: signal_hold (20)" in message
    assert "Baskin gate nedeni: LIVE_LOCK_R5 (3)" in message


def test_no_trade_uses_real_strategy_buy_date_from_fill_ledger(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        core.fill_ledger,
        "read_fills",
        lambda: [
            {
                "ts_utc": "2026-07-17T01:30:00+00:00",
                "side": "BUY",
                "provenance": "strategy",
            },
            {
                "ts_utc": "2026-07-25T15:00:00+00:00",
                "side": "BUY",
                "provenance": "index_parking",
            },
        ],
    )
    funnel = _funnel(tmp_path)
    # R4 migration'inin urettigi sahte "funnel olusma gunu" imzasi:
    funnel.last_entry_date = "2026-07-30"
    funnel.bump("gate_block", reason="LIVE_LOCK_R5", symbol="AAPL")
    notifier = StubNotifier()

    assert funnel.maybe_notify_no_trade(
        closed_day=TODAY,
        today=TODAY,
        threshold=3,
        is_paper=False,
        notifier=notifier,
        history_path=str(tmp_path / "missing.json"),
    )
    assert funnel.last_entry_date == "2026-07-16"
    assert "Son giris tarihi: 2026-07-16" in notifier.calls[0][1]


def test_no_trade_without_any_entry_reports_unknown_instead_of_fake_date(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(core.fill_ledger, "read_fills", lambda: [])
    funnel = _funnel(tmp_path, today=date(2026, 7, 31))
    for day_str in ("2026-07-28", "2026-07-29", "2026-07-30"):
        funnel.days[day_str] = funnel._empty_day()
    notifier = StubNotifier()

    assert funnel.maybe_notify_no_trade(
        closed_day="2026-07-30",
        today="2026-07-31",
        threshold=3,
        is_paper=False,
        notifier=notifier,
        history_path=str(tmp_path / "missing.json"),
    )
    assert funnel.last_entry_date is None
    assert "Son giris tarihi: bilinmiyor (funnel oncesi)" in notifier.calls[0][1]
    assert "2026-07-30" not in notifier.calls[0][1]
