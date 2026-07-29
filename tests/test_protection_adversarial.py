"""Koruma degismezine KARSI yazilmis testler (Claude, R0-B dogrulamasi).

Codex'in kendi suite'i mutlu yollari kaniti; bu dosya onun KACIRDIGI vakalar icin.
Ozellikle: tasarim geregi korumasiz olan index-parking pozisyonu alarm kanalini
bogar mi? SPY park kolu equity'nin ~%70'i ve kalici olarak stop'suz ,  bunun her
mutabakat turunda alarm uretmesi, kanali bir gunde ise yaramaz hale getirir
(alarm yorgunlugu). Gercek bir korumasiz pozisyon o gurultunun icinde kaybolur.
"""
from __future__ import annotations

from types import SimpleNamespace

from alpaca.trading.enums import OrderSide, TimeInForce

from core.position_manager import PositionManager
from core.protection import (
    ProtectionOutcome,
    classify_covering_order,
    deterministic_client_order_id,
)

from tests.test_protection_invariant import Bot, order, position


class QueryClient:
    """Sabit pozisyon/emir listesi donen minimal istemci."""

    def __init__(self, positions, orders=()):
        self._positions = list(positions)
        self._orders = list(orders)
        self.submitted = []
        self.cancelled = []

    def get_all_positions(self):
        return list(self._positions)

    def get_orders(self, _request=None):
        return list(self._orders)

    def get_order_by_id(self, oid):
        for o in self._orders:
            if str(o.id) == str(oid):
                return o
        raise KeyError(oid)

    def submit_order(self, request):
        self.submitted.append(request)
        return order("new-submitted", symbol=getattr(request, "symbol", "SPY"))

    def cancel_order_by_id(self, oid):
        self.cancelled.append(str(oid))


def _config():
    return {
        "stop_loss_pct": 0.04,
        "breakeven_offset_pct": 0.003,
        "index_parking_symbol": "SPY",
    }


def _alarm_count(bot):
    """protection_alarm kac KEZ gercekten atesledi (dedupe sonrasi)."""
    return len(bot.notifier.messages)


# ---------------------------------------------------------------------------
# 1. Park kolu alarm yorgunlugu
# ---------------------------------------------------------------------------

def test_uncovered_parking_does_not_alarm_repeatedly():
    """Kapsanmamis park pozisyonu TEKRAR TEKRAR alarm uretmemeli.

    SPY parki tasarim geregi stop'suz. Mutabakat 5 dakikada bir kosuyor;
    dedupe 15 dakika. Yani bu kosulda kanal 7/24, iki hesapta, 15 dakikada bir
    alarm alir ve gercek alarmlar bogulur.
    """
    pos = position(symbol="SPY", qty=58.12, entry=746.9, current=740.0)
    bot = Bot(QueryClient([pos]), parking=("SPY",))
    pm = PositionManager(bot)

    # 4 ardisik mutabakat turu; her turda dedupe penceresini gecmis gibi davran
    for _ in range(4):
        pm.ensure_protective_stops(_config())
        # zamani ilerlet: dedupe cache'ini bosalt (15 dk gecmis gibi)
        bot._protection_alarm_cache = {}

    assert _alarm_count(bot) == 0, (
        f"Park kolu {_alarm_count(bot)} kez alarm uretti. Beklenen: 0 "
        "(tasarim geregi korumasiz olan bir durum alarm degil, kayit olmali)"
    )


def test_summary_ok_when_only_parking_is_uncovered():
    """Yalniz park kolu kapsanmamissa tur BASARILI sayilmali.

    Aksi halde `summary.ok` kalici olarak False olur ve cagiranlar icin
    "her sey bozuk" sinyali sabitlenir ,  hicbir sey ifade etmez.
    """
    pos = position(symbol="SPY", qty=58.12, entry=746.9, current=740.0)
    bot = Bot(QueryClient([pos]), parking=("SPY",))
    pm = PositionManager(bot)

    summary = pm.ensure_protective_stops(_config())

    assert summary.ok, (
        f"Yalniz park kapsanmamisken summary.ok False dondu "
        f"(failed={summary.failed}, detail={summary.detail})"
    )


def test_real_naked_position_still_alarms():
    """Gercek strateji pozisyonu korumasizsa alarm KESINLIKLE atesleme.

    Yukaridaki iki testin park gurultusunu susturuken bu yolu da
    susturmadigini garanti eder (fail-loud yonu korunmali).
    """
    pos = position(symbol="AAPL", qty=10, entry=100.0, current=105.0)
    bot = Bot(QueryClient([pos]), parking=("SPY",))
    pm = PositionManager(bot)

    pm.ensure_protective_stops(_config())

    assert _alarm_count(bot) >= 1, "Korumasiz AAPL icin alarm atesle(n)medi"


# ---------------------------------------------------------------------------
# 2. Siniflandirma kenar durumlari
# ---------------------------------------------------------------------------

def test_pending_cancel_stop_is_not_coverage():
    """Iptal edilmekte olan bir stop koruma sayilmamali."""
    pos = position(symbol="AAPL", qty=10, entry=100.0, current=105.0)
    o = order("o1", symbol="AAPL", qty=10, status="pending_cancel")
    res = classify_covering_order(o, pos, "LONG")
    assert res.outcome is ProtectionOutcome.FAILED_NAKED
    assert not res.verified


def test_order_without_qty_is_not_coverage():
    """Notional/qty'siz emir sessizce koruma sayilmamali, cokmemeli de."""
    pos = position(symbol="AAPL", qty=10, entry=100.0, current=105.0)
    o = order("o1", symbol="AAPL", qty=10, status="new")
    o.qty = None
    res = classify_covering_order(o, pos, "LONG")
    assert res.outcome is ProtectionOutcome.FAILED_NAKED
    assert not res.verified


def test_long_stop_limit_elected_but_unmarketable():
    """Boslukla dusen piyasada limit asili kalirsa ELECTED_UNFILLED olmali."""
    # stop 95, limit 94.5, piyasa 90 → tetiklendi ama limitin altinda
    pos = position(symbol="AAPL", qty=10, entry=100.0, current=90.0)
    o = order("o1", symbol="AAPL", qty=10, status="new", stop=95.0, limit=94.5)
    res = classify_covering_order(o, pos, "LONG")
    assert res.outcome is ProtectionOutcome.ELECTED_UNFILLED, res.detail
    assert not res.verified


def test_wrong_side_stop_is_not_coverage():
    """LONG pozisyonu BUY stop korumaz."""
    pos = position(symbol="AAPL", qty=10, entry=100.0, current=105.0)
    o = order("o1", symbol="AAPL", qty=10, side=OrderSide.BUY, status="new")
    res = classify_covering_order(o, pos, "LONG")
    assert res.outcome is ProtectionOutcome.FAILED_NAKED


def test_partial_quantity_stop_is_not_coverage():
    """Pozisyonun tamamini kapsamayan stop koruma sayilmamali."""
    pos = position(symbol="AAPL", qty=10, entry=100.0, current=105.0)
    o = order("o1", symbol="AAPL", qty=4, status="new")
    res = classify_covering_order(o, pos, "LONG")
    assert res.outcome is ProtectionOutcome.FAILED_NAKED
    assert "yetersiz" in res.detail


# ---------------------------------------------------------------------------
# 3. Retry korelasyonu
# ---------------------------------------------------------------------------

def test_deterministic_client_order_id_is_stable_and_short():
    """Ayni niyet ayni ID uretmeli (korelasyon), Alpaca 48 karakter siniri."""
    a = deterministic_client_order_id("AAPL", "SELL", 95.0, 10.0)
    b = deterministic_client_order_id("AAPL", "SELL", 95.0, 10.0)
    c = deterministic_client_order_id("AAPL", "SELL", 95.5, 10.0)
    assert a == b
    assert a != c
    assert len(a) < 48, f"client_order_id cok uzun: {len(a)}"
