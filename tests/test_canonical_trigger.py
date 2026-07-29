from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import stock_bot as stock_bot_module
from core.gap_scanner import GapScanner
from core.position_manager import PositionManager
from core.protection import (
    ProtectionOutcome,
    ProtectionResult,
    should_exit_locally,
)
from stock_bot import StockBot


def broker_position(
    symbol: str = "AAPL",
    qty: float = 10,
    entry: float = 100,
    current: float = 100,
):
    return SimpleNamespace(
        symbol=symbol,
        qty=str(qty),
        avg_entry_price=str(entry),
        current_price=str(current),
        unrealized_pl="0",
        asset_class="us_equity",
    )


class Recorder:
    def __init__(self):
        self.calls = []

    def execute_sell(self, *args, **kwargs):
        self.calls.append((args, kwargs))

    def execute_cover(self, *args, **kwargs):
        self.calls.append((args, kwargs))


class NeverParking:
    @staticmethod
    def is_parking_symbol(_symbol):
        return False


class ManageBot:
    def __init__(self, positions, long=None, short=None):
        self.client = SimpleNamespace(get_all_positions=lambda: positions)
        self.positions = long or {}
        self.short_positions = short or {}
        self.index_parking = NeverParking()
        self.sell_cooldown = {}
        self.consecutive_errors = 0
        self.executor = Recorder()
        self.short_executor = Recorder()
        self._exit_flag_cache = {}

    def _stash_exit_flags(self, symbol, data):
        self._exit_flag_cache[symbol] = dict(data)

    def _save_position_metadata(self):
        return True


def long_config():
    return {
        "min_position_close_usd": 5,
        "stop_loss_pct": 0.05,
        "take_profit_pct": 0.20,
        "breakeven_enabled": True,
        "breakeven_trigger_pct": 0.025,
        "breakeven_offset_pct": 0.003,
        "trailing_stop_pct": 0.03,
        "partial_profit_pct": 0.20,
    }


def short_config():
    return {
        "short_stop_loss_pct": 0.06,
        "short_take_profit_pct": 0.20,
        "short_breakeven_enabled": True,
        "short_breakeven_trigger_pct": 0.025,
        "short_breakeven_offset_pct": 0.003,
        "short_trailing_stop_pct": 0.04,
        "short_partial_profit_pct": 0.20,
    }


def verified(stop_price: float) -> ProtectionResult:
    return ProtectionResult(
        ProtectionOutcome.REPLACED_VERIFIED,
        "verified-stop",
        stop_price,
        10,
        "verified",
    )


def test_absolute_trigger_is_the_side_aware_local_comparator():
    assert should_exit_locally(95, 95, "LONG")
    assert not should_exit_locally(95.01, 95, "LONG")
    assert should_exit_locally(105, 105, "SHORT")
    assert not should_exit_locally(104.99, 105, "SHORT")
    assert not should_exit_locally(100, None, "LONG")


@pytest.mark.parametrize(
    ("side", "existing", "older_verified"),
    [
        ("LONG", 100.30, 95.0),
        ("SHORT", 100.30, 106.0),
    ],
)
def test_old_verified_server_stop_cannot_loosen_canonical_trigger(
    side, existing, older_verified
):
    symbol = "AAPL" if side == "LONG" else "TSLA"
    data = {
        "entry_price": 100,
        "stop_loss_price": existing,
    }
    bot = ManageBot(
        [],
        long={symbol: data} if side == "LONG" else {},
        short={symbol: data} if side == "SHORT" else {},
    )
    manager = PositionManager(bot)

    manager._apply_protection_result(symbol, side, verified(older_verified))

    assert data["stop_loss_price"] == existing


def test_long_break_even_sets_trigger_above_entry_only_after_verification():
    live = broker_position(current=102.5)
    bot = ManageBot(
        [live],
        long={
            "AAPL": {
                "entry_price": 100,
                "qty": 10,
                "highest_price": 102.5,
                "last_server_sl": 200,
                "stop_loss_pct": 0.05,
            }
        },
    )
    manager = PositionManager(bot)
    manager._update_server_stop_loss = (
        lambda _symbol, price, _qty, side: verified(round(price, 2))
    )

    manager.manage_positions(long_config())

    position = bot.positions["AAPL"]
    assert position["breakeven_set"] is True
    assert position["stop_loss_price"] == pytest.approx(100.30)
    assert position["stop_loss_price"] > position["entry_price"]
    assert position["stop_loss_pct"] == 0.05
    assert not bot.executor.calls


def test_break_even_does_not_advance_when_target_is_not_verified():
    live = broker_position(current=102.5)
    bot = ManageBot(
        [live],
        long={
            "AAPL": {
                "entry_price": 100,
                "qty": 10,
                "highest_price": 102.5,
                "last_server_sl": 200,
                "stop_loss_pct": 0.05,
                "stop_loss_price": 95.0,
            }
        },
    )
    manager = PositionManager(bot)
    manager._update_server_stop_loss = (
        lambda *_args, **_kwargs: verified(95.0)
    )

    manager.manage_positions(long_config())

    position = bot.positions["AAPL"]
    assert position.get("breakeven_set", False) is False
    assert position["stop_loss_price"] == 95.0


def test_unarmed_long_at_minus_point_three_percent_does_not_exit():
    live = broker_position(current=99.7)
    bot = ManageBot(
        [live],
        long={
            "AAPL": {
                "entry_price": 100,
                "qty": 10,
                "highest_price": 100,
                "stop_loss_pct": 0.05,
                "breakeven_set": False,
            }
        },
    )

    PositionManager(bot).manage_positions(long_config())

    assert bot.positions["AAPL"]["stop_loss_price"] == 95.0
    assert not bot.executor.calls


def test_short_break_even_keeps_intentional_negative_pnl_offset_semantics():
    live = broker_position(symbol="TSLA", qty=-10, current=97.5)
    bot = ManageBot(
        [live],
        short={
            "TSLA": {
                "entry_price": 100,
                "qty": 10,
                "lowest_price": 97.5,
                "stop_loss_pct": 0.06,
            }
        },
    )
    manager = PositionManager(bot)
    manager._update_server_stop_loss = (
        lambda _symbol, price, _qty, side: verified(round(price, 2))
    )

    manager.manage_short_positions(long_config(), short_config())

    position = bot.short_positions["TSLA"]
    assert position["breakeven_set"] is True
    # Existing SHORT convention deliberately exits at P&L == -be_offset.
    assert position["stop_loss_price"] == pytest.approx(100.30)
    assert should_exit_locally(100.30, position["stop_loss_price"], "SHORT")
    assert position["stop_loss_pct"] == 0.06
    assert not bot.short_executor.calls


@pytest.mark.parametrize(
    ("side", "current", "old_trigger", "expected"),
    [
        ("LONG", 98.0, 94.0, 97.02),
        ("SHORT", 102.0, 108.0, 103.02),
    ],
)
def test_gap_tighten_uses_current_price_and_verified_server_path(
    side, current, old_trigger, expected
):
    symbol = "AAPL" if side == "LONG" else "TSLA"
    position = {
        "entry_price": 100,
        "qty": 10,
        "stop_loss_pct": 0.05,
        "stop_loss_price": old_trigger,
    }
    calls = []

    class Server:
        def _update_server_stop_loss(self, got_symbol, price, qty, side):
            calls.append((got_symbol, price, qty, side))
            return verified(price)

    bot = SimpleNamespace(
        positions={symbol: position} if side == "LONG" else {},
        short_positions={symbol: position} if side == "SHORT" else {},
        position_manager=Server(),
        _stash_exit_flags=lambda *_args: None,
        _save_position_metadata=lambda: True,
    )
    alert = {
        "symbol": symbol,
        "side": side,
        "action": "TIGHTEN_STOP",
        "current_price": current,
        "gap_pct": -2 if side == "LONG" else 2,
    }

    GapScanner().execute_gap_actions(bot, [alert])

    assert calls == [(symbol, expected, 10.0, side)]
    assert position["stop_loss_price"] == expected
    assert position["stop_loss_pct"] == 0.05


@pytest.mark.parametrize(
    ("side", "current", "armed_trigger"),
    [
        ("LONG", 98.0, 100.30),
        ("SHORT", 102.0, 100.30),
    ],
)
def test_gap_tighten_never_loosens_armed_break_even(
    side, current, armed_trigger
):
    symbol = "AAPL" if side == "LONG" else "TSLA"
    position = {
        "entry_price": 100,
        "qty": 10,
        "stop_loss_pct": 0.05,
        "stop_loss_price": armed_trigger,
        "breakeven_set": True,
    }
    calls = []
    bot = SimpleNamespace(
        positions={symbol: position} if side == "LONG" else {},
        short_positions={symbol: position} if side == "SHORT" else {},
        position_manager=SimpleNamespace(
            _update_server_stop_loss=lambda *args, **kwargs: calls.append(
                (args, kwargs)
            )
        ),
    )

    GapScanner().execute_gap_actions(
        bot,
        [{
            "symbol": symbol,
            "side": side,
            "action": "TIGHTEN_STOP",
            "current_price": current,
            "gap_pct": -2 if side == "LONG" else 2,
        }],
    )

    assert calls == []
    assert position["stop_loss_price"] == armed_trigger
    assert position["stop_loss_pct"] == 0.05


def test_gap_tighten_does_not_update_local_state_when_target_fails():
    position = {
        "entry_price": 100,
        "qty": 10,
        "stop_loss_pct": 0.05,
        "stop_loss_price": 94.0,
    }
    saved = []
    bot = SimpleNamespace(
        positions={"AAPL": position},
        short_positions={},
        position_manager=SimpleNamespace(
            _update_server_stop_loss=lambda *_args, **_kwargs: verified(94.0)
        ),
        _save_position_metadata=lambda: saved.append(True),
    )

    GapScanner().execute_gap_actions(
        bot,
        [{
            "symbol": "AAPL",
            "side": "LONG",
            "action": "TIGHTEN_STOP",
            "current_price": 98.0,
            "gap_pct": -2,
        }],
    )

    assert position["stop_loss_price"] == 94.0
    assert saved == []


def bare_stock_bot(state_file: Path) -> StockBot:
    bot = StockBot.__new__(StockBot)
    bot.POSITIONS_FILE = str(state_file)
    bot.positions = {}
    bot.short_positions = {}
    bot.options_positions = {}
    bot.last_trade_time = {}
    bot._consecutive_losses = 0
    bot._symbol_consecutive_losses = {}
    bot._daily_buys_count = 0
    bot.trades_today = []
    return bot


def test_restart_persists_canonical_trigger_for_both_sides(tmp_path):
    state_file = tmp_path / "positions.json"
    original = bare_stock_bot(state_file)
    original.positions = {
        "AAPL": {
            "entry_price": 100,
            "stop_loss_pct": 0.05,
            "stop_loss_price": 100.30,
        }
    }
    original.short_positions = {
        "TSLA": {
            "entry_price": 100,
            "stop_loss_pct": 0.06,
            "stop_loss_price": 103.02,
        }
    }
    assert original._save_position_metadata() is True

    restarted = bare_stock_bot(state_file)
    restarted.positions = {"AAPL": {"entry_price": 100}}
    restarted.short_positions = {"TSLA": {"entry_price": 100}}
    restarted._load_position_metadata()

    assert restarted.positions["AAPL"]["stop_loss_price"] == 100.30
    assert restarted.short_positions["TSLA"]["stop_loss_price"] == 103.02


def test_alpaca_resync_restores_cached_canonical_trigger(monkeypatch, tmp_path):
    monkeypatch.setattr(stock_bot_module, "BOT_MODE", "both")
    bot = bare_stock_bot(tmp_path / "unused.json")
    bot.client = SimpleNamespace(
        get_all_positions=lambda: [
            broker_position("AAPL", 10, 100, 101),
            broker_position("TSLA", -5, 100, 99),
        ]
    )
    bot.index_parking = NeverParking()
    bot._exit_flag_cache = {
        "AAPL": {"stop_loss_pct": 0.05, "stop_loss_price": 100.30},
        "TSLA": {"stop_loss_pct": 0.06, "stop_loss_price": 103.02},
    }

    bot._sync_positions_from_alpaca()

    assert bot.positions["AAPL"]["stop_loss_price"] == 100.30
    assert bot.short_positions["TSLA"]["stop_loss_price"] == 103.02


def test_legacy_breakeven_positions_migrate_without_none_injection(
    monkeypatch, tmp_path
):
    monkeypatch.setitem(
        stock_bot_module.STOCK_CONFIG, "breakeven_offset_pct", 0.003
    )
    monkeypatch.setitem(
        stock_bot_module.SHORT_CONFIG, "short_breakeven_offset_pct", 0.003
    )
    state_file = tmp_path / "legacy.json"
    state_file.write_text(
        json.dumps({
            "positions": {
                "AAPL": {
                    "entry_price": 100,
                    "breakeven_set": True,
                    "stop_loss_pct": 0.05,
                    "stop_loss_price": None,
                }
            },
            "short_positions": {
                "TSLA": {
                    "entry_price": 100,
                    "breakeven_set": True,
                    "stop_loss_pct": 0.06,
                    "stop_loss_price": None,
                }
            },
        }),
        encoding="utf-8",
    )
    bot = bare_stock_bot(state_file)
    bot.positions = {"AAPL": {"entry_price": 100}}
    bot.short_positions = {"TSLA": {"entry_price": 100}}

    bot._load_position_metadata()

    assert bot.positions["AAPL"]["stop_loss_price"] == 100.30
    assert bot.short_positions["TSLA"]["stop_loss_price"] == 100.30
    assert bot.positions["AAPL"]["stop_loss_pct"] == 0.05
    assert bot.short_positions["TSLA"]["stop_loss_pct"] == 0.06
