"""R6'ya KARSI testler (Claude). Codex suite'inin sinir bosluklarini kapatir.

1. E2 sinir: skor TAM 0 GECMELI (Ihsan karari: yalniz 0'in alti blok).
2. E2 cop skor: parse edilemeyen skor fail-closed bloklanmali.
3. R:R siniri: oran TAM 1.25 iken kapi bloklamamali (epsilon yonu).
4. Asiri-dedup: ayni sembol ayni gun IKI gercek islem -> ikisi de kayda girmeli.
   Dedup hayaleti engellerken gercek islemi yutarsa muhasebe ters yonden bozulur.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.trade_gates import TradeGates, plan_exit_pcts


def _gates():
    return TradeGates(SimpleNamespace())


def _cfg(**extra):
    cfg = {
        "fundamental_gate_enabled": True,
        "fundamental_gate_min_score": 0,
        "ema200_trend_gate": False,
        "earnings_gate_enabled": False,
        "loss_streak_enabled": False,
        "coin_filter_enabled": False,
        "rr_gate_enabled": False,
        "multi_tf_enabled": False,
        "volatility_filter_enabled": False,
    }
    cfg.update(extra)
    return cfg


def _analysis(**extra):
    a = {"confidence": 50, "atr": 1.0, "price": 100.0,
         "above_ema200": True, "fundamental_data_ok": True,
         "fundamental_score": 5}
    a.update(extra)
    return a


def test_fund_gate_score_exactly_zero_passes():
    """Ihsan karari: 'skor 0 kabul, yalniz 0'in alti negatif'."""
    ok, reason = _gates().check_all_gates("AAPL", _analysis(fundamental_score=0), _cfg())
    assert ok, f"skor 0 bloklandi: {reason}"


def test_fund_gate_garbage_score_fails_closed():
    """Parse edilemeyen skor 'gecir' degil 'blokla' olmali."""
    ok, reason = _gates().check_all_gates(
        "AAPL", _analysis(fundamental_score="N/A"), _cfg())
    assert not ok and reason == "FUND_NEGATIVE"


def test_fund_gate_off_ignores_missing_data():
    """Canli davranisi: bayrak kapaliyken veri yoklugu bile bloklamaz."""
    ok, _ = _gates().check_all_gates(
        "AAPL",
        _analysis(fundamental_data_ok=False, fundamental_score=-30),
        _cfg(fundamental_gate_enabled=False))
    assert ok


def test_rr_gate_exactly_at_min_ratio_does_not_block():
    """Paper seti: SL floor 0.05, TP = SL*1.25 -> oran TAM 1.25 = min_rr.
    Kapi `rr + 1e-9 < min_rr` kullaniyor; esitlik bloklamamali."""
    cfg = {"atr_stop_multiplier": 1.8, "stop_loss_pct": 0.05,
           "stop_loss_max_pct": 0.06, "take_profit_pct": 0.05,
           "take_profit_max_pct": 0.075, "min_rr_ratio": 1.25,
           "rr_gate_enabled": True}
    for atr in (0.5, 1.0, 1.5, 2.0, 2.77, 3.0):
        sl, tp = plan_exit_pcts(atr, 100.0, cfg)
        assert 0.05 <= tp <= 0.075, f"ATR {atr}: TP {tp} bant disi"
        blocked, reason = _gates()._check_rr_gate(
            "T", {"atr": atr, "price": 100.0}, cfg)
        assert not blocked, f"ATR {atr}: R:R self-block ({reason})"


def test_dedup_does_not_swallow_second_real_roundtrip():
    """AAPL sabah alinip satildi, ogleden sonra TEKRAR alinip satildi.
    Iki cikis FARKLI emir ve FARKLI girisler -> ikisi de kayitta kalmali."""
    from stock_bot import StockBot

    bot = SimpleNamespace(trades_today=[
        {"action": "SELL", "symbol": "AAPL", "qty": 10.0,
         "entry_time": "2026-07-30T14:00:00", "exit_order_id": "ord-1",
         "price": 101.0, "pnl": 10.0},
    ])
    already = StockBot._exit_already_recorded

    # farkli emir + farkli giris zamani -> kayitli SAYILMAMALI
    assert not already(bot, "AAPL", "SELL", 10.0,
                       entry_time="2026-07-30T18:30:00",
                       exit_order_id="ord-2")

    # ayni emir ID -> kayitli sayilmali (hayalet onlenir)
    assert already(bot, "AAPL", "SELL", 10.0,
                   entry_time="2026-07-30T14:00:00",
                   exit_order_id="ord-1")

    # emir ID yok ama ayni qty + ayni giris -> kayitli sayilmali
    assert already(bot, "AAPL", "SELL", 10.0,
                   entry_time="2026-07-30T14:00:00",
                   exit_order_id="")

    # ayni qty ama giris zamani farkli ve emir ID yok -> gercek ikinci islem,
    # kayitli SAYILMAMALI
    assert not already(bot, "AAPL", "SELL", 10.0,
                       entry_time="2026-07-30T18:30:00",
                       exit_order_id="")
