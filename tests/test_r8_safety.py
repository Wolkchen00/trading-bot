"""R8 merkezi yeni-risk guard'i ve tasfiye yetkisi daraltma testleri."""
from types import SimpleNamespace

import pytest

from core.bear_brain import BearBrain
from core.executor import OrderExecutor
from core.index_parking import IndexParkingManager
from core.kill_switch import KillSwitch
from core.options_executor import OptionsExecutor
from core.risk_guard import can_open_new_risk, classify_error
from core.short_executor import ShortExecutor
from stock_bot import StockBot


class NoBrokerClient:
    """Guard reddinden sonra broker'a dokunulmadigini kanitlayan spy."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def forbidden(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            raise AssertionError(f"Guard sonrasi broker cagrisi: {name}")

        return forbidden


class GuardBot:
    def __init__(self, *, is_paper=False, killed=False, halted=False):
        self.is_paper = is_paper
        self.kill_switch = SimpleNamespace(
            is_active=killed,
            risk_halted=halted,
        )
        self.client = NoBrokerClient()
        self.funnel_calls = []

    def _funnel_bump(self, stage, reason=None):
        self.funnel_calls.append((stage, reason))


@pytest.mark.parametrize(
    "kind",
    ["stock_long", "stock_short", "option", "bear_etf"],
)
def test_live_r5_lock_blocks_every_strategy_kind_without_broker(kind):
    bot = GuardBot(is_paper=False)

    result = can_open_new_risk(
        bot, {"live_entries_enabled": False}, kind=kind, symbol="TEST"
    )

    assert result == (False, "LIVE_LOCK_R5")
    assert bot.client.calls == []
    assert ("gate_block", "LIVE_LOCK_R5") in bot.funnel_calls


def test_index_parking_bypasses_only_live_r5_lock():
    live_bot = GuardBot(is_paper=False)
    assert can_open_new_risk(
        live_bot,
        {"live_entries_enabled": False, "index_parking_allow_live": True},
        kind="index_parking",
        symbol="SPY",
    ) == (True, "")

    halted_bot = GuardBot(is_paper=False, halted=True)
    assert can_open_new_risk(
        halted_bot,
        {"live_entries_enabled": False, "index_parking_allow_live": True},
        kind="index_parking",
        symbol="SPY",
    ) == (False, "RISK_HALT")
    assert halted_bot.client.calls == []


def test_kill_switch_has_precedence_for_all_risk_kinds():
    for kind in ("stock_long", "stock_short", "option", "bear_etf", "index_parking"):
        bot = GuardBot(is_paper=False, killed=True, halted=True)
        assert can_open_new_risk(
            bot, {"live_entries_enabled": True}, kind=kind, symbol="TEST"
        ) == (False, "KILL_SWITCH")
        assert bot.client.calls == []


def test_guard_fails_closed_and_telemetry_cannot_change_decision():
    bot = GuardBot(is_paper=False)

    def broken_telemetry(*args, **kwargs):
        raise RuntimeError("telemetri bozuk")

    bot._funnel_bump = broken_telemetry
    assert can_open_new_risk(
        bot, object(), kind="stock_long", symbol="TEST"
    ) == (False, "GUARD_ERROR")
    assert can_open_new_risk(
        bot, {"live_entries_enabled": True}, kind="bilinmeyen", symbol="TEST"
    ) == (False, "GUARD_ERROR")
    assert bot.client.calls == []


def test_option_missing_live_key_reads_single_stock_config_source(monkeypatch):
    from config import OPTIONS_CONFIG, STOCK_CONFIG

    assert "live_entries_enabled" not in OPTIONS_CONFIG
    bot = GuardBot(is_paper=False)

    monkeypatch.setitem(STOCK_CONFIG, "live_entries_enabled", True)
    assert can_open_new_risk(
        bot, {}, kind="option", symbol="AAPL"
    ) == (True, "")

    monkeypatch.setitem(STOCK_CONFIG, "live_entries_enabled", False)
    assert can_open_new_risk(
        bot, {}, kind="option", symbol="AAPL"
    ) == (False, "LIVE_LOCK_R5")


def test_five_entry_paths_stop_before_any_order_when_risk_halted():
    config = {"live_entries_enabled": True}

    long_bot = GuardBot(halted=True)
    assert OrderExecutor(long_bot).execute_buy(
        "AAPL", {"price": 100, "confidence": 80}, config
    ) is False
    assert long_bot.client.calls == []

    short_bot = GuardBot(halted=True)
    assert ShortExecutor(short_bot).execute_short(
        "AAPL", {"price": 100}, config, {}
    ) is False
    assert short_bot.client.calls == []

    option_bot = GuardBot(halted=True)
    option_bot.options_positions = {}
    option_info = {
        "symbol": "AAPL260918C00100000",
        "underlying": "AAPL",
        "strike": 100,
        "expiry": "2026-09-18",
    }
    assert OptionsExecutor(option_bot)._execute_option(
        "CALL", option_info, {}, config
    ) is False
    assert option_bot.client.calls == []

    parking_bot = GuardBot(halted=True)
    parking = object.__new__(IndexParkingManager)
    parking.bot = parking_bot
    parking.config = config
    parking.symbol = "SPY"
    parking._buy(100)
    assert parking_bot.client.calls == []

    bear_bot = GuardBot(halted=True)
    bear_bot.executor = SimpleNamespace(
        execute_buy=lambda *args, **kwargs: pytest.fail(
            "BearBrain guard sonrasi executor cagrildi"
        )
    )
    bear = object.__new__(BearBrain)
    bear.bot = bear_bot
    bear.pick_instrument = lambda: "SH"
    bear._maybe_enter(config)
    assert bear_bot.client.calls == []


@pytest.mark.parametrize(
    "exc_type",
    [
        AttributeError,
        TypeError,
        KeyError,
        NameError,
        IndexError,
        ZeroDivisionError,
        ImportError,
        UnboundLocalError,
    ],
)
def test_classify_error_marks_programming_errors_as_code(exc_type):
    assert classify_error(exc_type("yapay")) == "code"


@pytest.mark.parametrize("exc", [RuntimeError("api"), TimeoutError("timeout")])
def test_classify_error_defaults_other_errors_to_broker(exc):
    assert classify_error(exc) == "broker"


@pytest.mark.parametrize("exc", [TypeError("kod"), TimeoutError("broker")])
def test_three_errors_halt_new_risk_but_never_liquidate(tmp_path, exc):
    callbacks = []
    kill_file = tmp_path / "kill.json"
    ks = KillSwitch(max_consecutive_errors=3, kill_file=str(kill_file))
    ks.set_callback(callbacks.append)

    assert ks.check_api_error(exc) is False
    assert ks.check_api_error(exc) is False
    assert ks.check_api_error(exc) is True

    assert ks.consecutive_errors == 3
    assert ks.risk_halted is True
    assert ks.risk_halt_reason
    assert ks.is_active is False
    assert callbacks == []
    assert not kill_file.exists()

    ks.reset_error_count()
    assert ks.consecutive_errors == 0
    assert ks.risk_halted is False
    assert ks.risk_halt_reason == ""


def test_only_daily_loss_and_manual_kill_use_liquidation_callback(tmp_path):
    callbacks = []
    ks = KillSwitch(kill_file=str(tmp_path / "kill.json"))
    ks.set_callback(callbacks.append)

    assert ks.check_daily_loss(94, 100) is True
    assert ks.is_active is True
    assert len(callbacks) == 1

    ks.reset()
    ks.manual_kill("test manuel")
    assert ks.is_active is True
    assert len(callbacks) == 2


def test_successful_main_loop_reset_clears_both_error_counters(tmp_path):
    bot = object.__new__(StockBot)
    bot.consecutive_errors = 3
    bot.kill_switch = KillSwitch(kill_file=str(tmp_path / "kill.json"))
    bot.kill_switch.consecutive_errors = 3
    bot.kill_switch.risk_halted = True
    bot.kill_switch.risk_halt_reason = "yapay"

    bot._reset_main_loop_error_counts()

    assert bot.consecutive_errors == 0
    assert bot.kill_switch.consecutive_errors == 0
    assert bot.kill_switch.risk_halted is False
