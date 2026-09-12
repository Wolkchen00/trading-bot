"""R20 PROOF: zarar serisi sonmesi, profil sahipligi ve broker zamani."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
from types import MethodType, SimpleNamespace

import pytest

import core.run_profile as run_profile
import core.trade_gates as trade_gates_module
from core.executor import OrderExecutor
from core.streak import decay_loss_streaks, parse_utc, update_loss_streak
from core.trade_gates import TradeGates
import stock_bot as stock_bot_module
from stock_bot import StockBot


UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "canlandirma_2026_09_11"


def gate_config() -> dict:
    return {
        "ema200_trend_gate": False,
        "fundamental_gate_enabled": False,
        "earnings_gate_enabled": False,
        "loss_streak_enabled": True,
        "loss_streak_warn": 2,
        "loss_streak_halt": 4,
        "loss_streak_halt_hours": 24,
        "loss_streak_decay_hours": 24,
        "loss_streak_elevated_conf": 70,
        "coin_filter_enabled": True,
        "coin_max_consecutive_losses": 3,
        "symbol_loss_decay_hours": 24,
        "rr_gate_enabled": False,
        "multi_tf_enabled": False,
        "volatility_filter_enabled": False,
    }


def set_profile(monkeypatch, profile: str) -> None:
    monkeypatch.setattr(run_profile, "aktif_profil", lambda: profile)


def bare_bot(path: Path, *, is_paper: bool = False) -> StockBot:
    bot = StockBot.__new__(StockBot)
    bot.POSITIONS_FILE = str(path)
    bot.is_paper = is_paper
    bot.positions = {}
    bot.short_positions = {}
    bot.options_positions = {}
    bot.last_trade_time = {}
    bot._consecutive_losses = 0
    bot._last_loss_at = None
    bot._symbol_consecutive_losses = {}
    bot._symbol_last_loss_at = {}
    bot._streaks_by_profile = {}
    bot._daily_buys_count = 0
    bot.trades_today = []
    bot._exit_flag_cache = {}
    return bot


def write_state(
    path: Path,
    profile: str,
    losses: int,
    last_loss_at,
    *,
    symbol: str = "META",
    symbol_losses: int = 0,
    symbol_last_loss_at=None,
    extra_profiles: dict | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    profiles = dict(extra_profiles or {})
    profiles[profile] = {
        "consecutive_losses": losses,
        "last_loss_at": last_loss_at,
        "symbols": {
            symbol: {
                "losses": symbol_losses,
                "last_loss_at": symbol_last_loss_at,
            }
        },
    }
    path.write_text(
        json.dumps({
            "positions": {}, "short_positions": {}, "options_positions": {},
            "streaks_by_profile": profiles,
        }),
        encoding="utf-8",
    )


def freeze_gate_clock(monkeypatch, value: datetime) -> None:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return value.replace(tzinfo=None)
            return value.astimezone(tz)

    monkeypatch.setattr(trade_gates_module, "datetime", FrozenDateTime)


def read_profile(path: Path, profile: str = "live") -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["streaks_by_profile"][profile]


def test_a_25_saatte_warn_soner_47_gecer_ve_disk_sifir(monkeypatch, tmp_path):
    set_profile(monkeypatch, "live")
    now = datetime.now(UTC)
    path = tmp_path / "state_live" / "bot_positions.json"
    old = now - timedelta(hours=25)
    write_state(path, "live", 2, old.isoformat())
    bot = bare_bot(path)
    bot._load_position_metadata()
    freeze_gate_clock(monkeypatch, now)

    assert TradeGates(bot)._check_loss_streak(
        "META", {"confidence": 47}, gate_config()
    ) == (False, "")
    assert bot._consecutive_losses == 0
    assert read_profile(path)["consecutive_losses"] == 0
    assert read_profile(path)["last_loss_at"] is None


def test_b_23_saatte_warn_60_bloklu_70_gecer(monkeypatch, tmp_path):
    set_profile(monkeypatch, "live")
    now = datetime.now(UTC)
    path = tmp_path / "state_live" / "bot_positions.json"
    write_state(path, "live", 2, (now - timedelta(hours=23)).isoformat())
    bot = bare_bot(path)
    bot._load_position_metadata()
    freeze_gate_clock(monkeypatch, now)
    gates = TradeGates(bot)

    assert gates._check_loss_streak("META", {"confidence": 60}, gate_config()) == (
        True, "LOSS_STREAK_WARN"
    )
    assert gates._check_loss_streak("META", {"confidence": 70}, gate_config()) == (
        False, ""
    )


def test_c_halt_24_saatte_biter_ve_10_saat_restart_uzatmaz(monkeypatch, tmp_path):
    set_profile(monkeypatch, "live")
    real_now = datetime.now(UTC)
    last = real_now - timedelta(hours=10)
    path = tmp_path / "state_live" / "bot_positions.json"
    write_state(path, "live", 4, last.isoformat())

    restarted = bare_bot(path)
    restarted._load_position_metadata()
    assert parse_utc(restarted._last_loss_at) == last
    freeze_gate_clock(monkeypatch, real_now)
    assert TradeGates(restarted)._check_loss_streak(
        "META", {"confidence": 99}, gate_config()
    ) == (True, "LOSS_STREAK_HALT")
    # Ayni diskten ikinci gercek nesne: HALT saati restart anina tasinmadi.
    restarted_again = bare_bot(path)
    restarted_again._load_position_metadata()
    assert parse_utc(restarted_again._last_loss_at) == last
    freeze_gate_clock(monkeypatch, last + timedelta(hours=25))
    assert TradeGates(restarted_again)._check_loss_streak(
        "META", {"confidence": 1}, gate_config()
    ) == (False, "")


def test_d_sembol_serisi_23_saat_blok_25_saat_gecer(monkeypatch, tmp_path):
    set_profile(monkeypatch, "live")
    now = datetime.now(UTC)
    path = tmp_path / "state_live" / "bot_positions.json"
    write_state(
        path, "live", 0, None, symbol_losses=3,
        symbol_last_loss_at=(now - timedelta(hours=23)).isoformat(),
    )
    bot = bare_bot(path)
    bot._load_position_metadata()
    freeze_gate_clock(monkeypatch, now)
    assert TradeGates(bot).check_all_gates(
        "META", {"confidence": 99}, gate_config()
    ) == (False, "STOCK_FILTER")
    freeze_gate_clock(monkeypatch, now + timedelta(hours=2))
    assert TradeGates(bot).check_all_gates(
        "META", {"confidence": 99}, gate_config()
    ) == (True, "")


def test_e_eski_live_history_sondurur_history_yoksa_yuklemede_baslar(
    monkeypatch, tmp_path
):
    set_profile(monkeypatch, "live")
    state_dir = tmp_path / "state_live"
    state_dir.mkdir()
    path = state_dir / "bot_positions.json"
    path.write_bytes((FIXTURES / "live_bot_positions.json").read_bytes())
    (state_dir / "trade_history.json").write_bytes(
        (FIXTURES / "live_trade_history.json").read_bytes()
    )
    bot = bare_bot(path)
    bot._load_position_metadata()
    assert bot._consecutive_losses == 0
    assert read_profile(path)["consecutive_losses"] == 0
    assert "consecutive_losses" not in json.loads(path.read_text(encoding="utf-8"))

    no_history = tmp_path / "no_history" / "state_live" / "bot_positions.json"
    no_history.parent.mkdir(parents=True)
    no_history.write_text(json.dumps({
        "positions": {}, "short_positions": {},
        "consecutive_losses": 2, "symbol_consecutive_losses": {},
    }), encoding="utf-8")
    before = datetime.now(UTC)
    fresh = bare_bot(no_history)
    fresh._load_position_metadata()
    after = datetime.now(UTC)
    assert fresh._consecutive_losses == 2
    assert before <= parse_utc(fresh._last_loss_at) <= after


def test_f_eski_paper_agresife_gocer_live_config_temizdir(monkeypatch, tmp_path):
    set_profile(monkeypatch, "paper_live_config")
    path = tmp_path / "state_paper" / "bot_positions.json"
    path.parent.mkdir()
    path.write_text(json.dumps({
        "positions": {}, "short_positions": {},
        "consecutive_losses": 2,
        "symbol_consecutive_losses": {"META": 2},
    }), encoding="utf-8")
    bot = bare_bot(path, is_paper=True)
    bot._load_position_metadata()
    disk = json.loads(path.read_text(encoding="utf-8"))["streaks_by_profile"]
    assert bot._consecutive_losses == 0
    assert disk["paper_live_config"]["consecutive_losses"] == 0
    assert disk["paper_aggressive"]["consecutive_losses"] == 2


@pytest.mark.parametrize("owner", ["paper_aggressive", None])
@pytest.mark.parametrize("pnl", [-5.0, 5.0])
def test_g_farkli_ve_etiketsiz_pozisyon_kar_zarar_seriye_dokunmaz(
    monkeypatch, owner, pnl
):
    set_profile(monkeypatch, "paper_live_config")
    stamp = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    bot = SimpleNamespace(
        _consecutive_losses=2,
        _last_loss_at=stamp,
        _symbol_consecutive_losses={"META": 2},
        _symbol_last_loss_at={"META": stamp},
    )
    update_loss_streak(
        bot, "META", pnl, entry_profile=owner, persist=False
    )
    assert bot._consecutive_losses == 2
    assert bot._symbol_consecutive_losses["META"] == 2


def test_h_entry_profile_stash_broker_sync_long_short_restore(monkeypatch, tmp_path):
    set_profile(monkeypatch, "live")

    def broker(symbol, qty):
        return SimpleNamespace(
            symbol=symbol, qty=str(qty), avg_entry_price="100",
            current_price="100", unrealized_pl="0", asset_class="us_equity",
        )

    path = tmp_path / "state_live" / "bot_positions.json"
    bot = bare_bot(path)
    bot.client = SimpleNamespace(
        get_all_positions=lambda: [broker("AAPL", 2), broker("TSLA", -2)]
    )
    bot.index_parking = SimpleNamespace(is_parking_symbol=lambda _symbol: False)
    bot._exit_flag_cache = {
        "AAPL": {"entry_price": 100, "entry_profile": "live"},
        "TSLA": {"entry_price": 100, "entry_profile": "live"},
    }
    monkeypatch.setattr(stock_bot_module, "BOT_MODE", "both")
    bot._sync_positions_from_alpaca()
    assert bot.positions["AAPL"]["entry_profile"] == "live"
    assert bot.short_positions["TSLA"]["entry_profile"] == "live"

    bot._stash_exit_flags("AAPL", bot.positions["AAPL"])
    bot._stash_exit_flags("TSLA", bot.short_positions["TSLA"])
    assert bot._exit_flag_cache["AAPL"]["entry_profile"] == "live"
    assert bot._exit_flag_cache["TSLA"]["entry_profile"] == "live"
    assert bot._save_position_metadata() is True

    restarted = bare_bot(path)
    restarted.positions = {"AAPL": {"entry_price": 100}}
    restarted.short_positions = {"TSLA": {"entry_price": 100}}
    restarted._load_position_metadata()
    assert restarted.positions["AAPL"]["entry_profile"] == "live"
    assert restarted.short_positions["TSLA"]["entry_profile"] == "live"


def test_i_long_zarar_kapanisi_tek_yazimda_restartta_seri_ve_saat(
    monkeypatch, tmp_path
):
    set_profile(monkeypatch, "live")
    path = tmp_path / "state_live" / "bot_positions.json"
    path.parent.mkdir()
    filled_at = datetime.now(UTC) - timedelta(minutes=3)
    fill = SimpleNamespace(
        id="close-1", filled_avg_price="98", filled_qty="2",
        filled_at=filled_at, client_order_id="cid", execution_id="exec",
    )

    class Client:
        def get_open_position(self, _symbol):
            return SimpleNamespace(current_price="98", unrealized_pl="-4")
        def get_orders(self, _request):
            return []
        def close_position(self, _symbol):
            return fill
        def get_all_positions(self):
            return []
        def get_order_by_id(self, _order_id):
            return fill

    bot = bare_bot(path)
    bot.client = Client()
    bot.positions = {"AAPL": {
        "entry_price": 100, "qty": 2,
        "entry_time": (datetime.now() - timedelta(days=1)).isoformat(),
        "entry_profile": "live", "close_in_progress": False,
    }}
    bot.sell_cooldown = {}
    bot.consecutive_errors = 0
    bot.position_manager = SimpleNamespace(_verify_attempts=1)
    bot.pdt_tracker = SimpleNamespace(
        should_hold_overnight=lambda *_args: (False, ""),
        is_same_day_position=lambda *_args: False,
    )
    bot._stash_exit_flags = MethodType(StockBot._stash_exit_flags, bot)
    executor = OrderExecutor(bot)
    monkeypatch.setattr(executor, "_record_fill_safe", lambda *_a, **_k: True)
    assert executor.execute_sell("AAPL", "STOP_LOSS") is True

    restarted = bare_bot(path)
    restarted._load_position_metadata()
    assert restarted._consecutive_losses == 1
    assert parse_utc(restarted._last_loss_at) == filled_at
    assert "AAPL" not in restarted.positions


def test_j_dis_kapanis_broker_filled_at_kullanir(monkeypatch, tmp_path):
    set_profile(monkeypatch, "live")
    path = tmp_path / "state_live" / "bot_positions.json"
    path.parent.mkdir()
    filled_at = datetime.now(UTC) - timedelta(hours=5)
    order = SimpleNamespace(
        id="ext-1", side=stock_bot_module.OrderSide.SELL,
        filled_qty="1", filled_avg_price="90", order_type="stop",
        filled_at=filled_at,
    )
    bot = bare_bot(path)
    bot.positions = {"META": {
        "entry_price": 100, "qty": 1,
        "entry_time": (datetime.now() - timedelta(days=1)).isoformat(),
        "entry_profile": "live",
    }}
    bot.client = SimpleNamespace(get_orders=lambda _request: [order])
    bot.wash_sale_tracker = SimpleNamespace(record_loss_sale=lambda *_a: None)
    bot.pdt_tracker = SimpleNamespace(is_same_day_position=lambda *_a: False)
    bot.performance = SimpleNamespace(record_trade=lambda **_k: None)
    bot.agent_perf = SimpleNamespace(record_outcome=lambda *_a: None)
    bot._reconcile_external_exit("META", side="LONG")
    assert parse_utc(bot._last_loss_at) == filled_at
    assert parse_utc(read_profile(path)["last_loss_at"]) == filled_at


def test_k_canlandirma_fixturu_meta_53_9_loss_streakte_bloklanmaz(
    monkeypatch, tmp_path
):
    set_profile(monkeypatch, "live")
    state_dir = tmp_path / "state_live"
    state_dir.mkdir()
    path = state_dir / "bot_positions.json"
    path.write_bytes((FIXTURES / "live_bot_positions.json").read_bytes())
    (state_dir / "trade_history.json").write_bytes(
        (FIXTURES / "live_trade_history.json").read_bytes()
    )
    bot = bare_bot(path)
    bot._load_position_metadata()
    passed, reason = TradeGates(bot).check_all_gates(
        "META", {"confidence": 53.9}, gate_config()
    )
    assert (passed, reason) == (True, "")


def test_l_gelecek_bozuk_naive_aware_damgalar_kilitlemez(monkeypatch, tmp_path):
    set_profile(monkeypatch, "live")
    now = datetime.now(UTC).replace(microsecond=0)
    path = tmp_path / "state_live" / "bot_positions.json"
    write_state(path, "live", 2, (now + timedelta(days=30)).isoformat())
    future = bare_bot(path)
    future._load_position_metadata()
    assert parse_utc(future._last_loss_at) <= datetime.now(UTC)
    oversized = gate_config()
    oversized["loss_streak_decay_hours"] = 999
    oversized["symbol_loss_decay_hours"] = 999
    future._symbol_consecutive_losses["META"] = 3
    future._symbol_last_loss_at["META"] = now.isoformat()
    decay_loss_streaks(
        future, oversized, now=now + timedelta(hours=24, seconds=1)
    )
    assert future._consecutive_losses == 0
    assert future._symbol_consecutive_losses["META"] == 0

    bad_path = tmp_path / "bad" / "state_live" / "bot_positions.json"
    write_state(bad_path, "live", 2, "not-a-date")
    bad = bare_bot(bad_path)
    bad._load_position_metadata()
    assert bad._consecutive_losses == 0

    naive_path = tmp_path / "naive" / "state_live" / "bot_positions.json"
    write_state(
        naive_path, "live", 2,
        (now - timedelta(hours=23)).replace(tzinfo=None).isoformat(),
    )
    naive = bare_bot(naive_path)
    naive._load_position_metadata()
    freeze_gate_clock(monkeypatch, now)
    assert TradeGates(naive)._check_loss_streak(
        "META", {"confidence": 70}, gate_config()
    ) == (False, "")


def test_m_parity_harness_baseline_davranisi_degismedi():
    completed = subprocess.run(
        [sys.executable, "tools/parity_harness.py"],
        cwd=ROOT,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        timeout=120,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 1
    assert "SONUC: SAPMA VAR" in output
    assert "yeni regresyon" not in output.lower()
