"""R1'e KARSI yazilmis testler (Claude) ,  KOK-1 ve KOK-2 gercekten oldu mu?

Bu testler soyut degil: uretimde gerceklesmis UC cikisi birebir yeniden kuruyor.
  paper NVDA   "STOP_LOSS (-0.3% / limit -0.3%)"    -> KOK-1
  paper GOOGL  "STOP_LOSS (-0.4% / limit -0.3%)"    -> KOK-1
  paper RIVN   "STOP_LOSS (-4.2% / limit -1.0%)"    -> KOK-2
Bir daha olamayacagini kanitlamak icin, eski kodun kesin olarak YAPTIGI seyi
test ediyoruz ,  yeni kodun yapabileceklerini degil.
"""
from __future__ import annotations

from core.gap_scanner import GapScanner
from core.protection import (
    ProtectionOutcome,
    ProtectionResult,
    should_exit_locally,
)

import pytest


# ===========================================================================
# KOK-1 ,  break-even armliyken tetik girisin USTUNDE olmali
# ===========================================================================

def test_armed_breakeven_exits_above_entry_not_below():
    """NVDA/GOOGL vakasi: +%2.5'e cikip -%0.3'te satan davranis olmeli.

    Giris 100, break-even armli -> kanonik tetik 100.30.
    Eski kod stop_loss_pct=0.003 yazip "pnl <= -0.003" ile kiyasliyordu, yani
    pozisyonu 99.70'e kadar tasiyordu. Yeni kod 100.30'da cikmali.
    """
    entry = 100.0
    trigger = round(entry * 1.003, 2)  # 100.30

    # tetigin uzerinde: cikis YOK
    assert not should_exit_locally(100.50, trigger, "LONG")
    assert not should_exit_locally(100.31, trigger, "LONG")

    # tetikte ve altinda: CIKIS (hala kârda -> +%0.3 kilitlendi)
    assert should_exit_locally(100.30, trigger, "LONG")
    assert should_exit_locally(100.20, trigger, "LONG")

    # ESKI KODUN SATTIGI YER: -%0.3. Buraya inmis olmasi zaten cok gec demek;
    # yukaridaki 100.20 assert'i bu senaryonun onune geciyor.
    assert should_exit_locally(round(entry * 0.997, 2), trigger, "LONG")


def test_unarmed_position_uses_real_stop_distance_not_breakeven_offset():
    """Armlanmamis pozisyonun tetigi %4 asagida olmali, %0.3 degil."""
    entry = 100.0
    trigger = round(entry * (1 - 0.04), 2)  # 96.00
    assert not should_exit_locally(99.70, trigger, "LONG"), \
        "armlanmamis pozisyon -%0.3'te satiyor ,  KOK-1 geri gelmis"
    assert not should_exit_locally(96.01, trigger, "LONG")
    assert should_exit_locally(96.00, trigger, "LONG")


def test_short_trigger_is_mirrored():
    """SHORT'ta tetik YUKARIDA ve karsilastirma ters yonde olmali."""
    entry = 100.0
    trigger = round(entry * 1.04, 2)  # 104.00
    assert not should_exit_locally(103.99, trigger, "SHORT")
    assert should_exit_locally(104.00, trigger, "SHORT")
    assert should_exit_locally(105.00, trigger, "SHORT")
    # LONG mantigi SHORT'a uygulanirsa ters sonuc verirdi:
    assert not should_exit_locally(95.0, trigger, "SHORT")


def test_missing_or_broken_trigger_does_not_sell():
    """Tetik yok/bozuksa 'armli degil' say ,  yanlislikla satmak en kotu hata."""
    for bad in (None, "", "abc", 0, -5, float("nan")):
        assert not should_exit_locally(100.0, bad, "LONG"), f"bozuk tetik satti: {bad!r}"
    for bad in (None, "", "abc", 0, -5):
        assert not should_exit_locally(bad, 96.0, "LONG"), f"bozuk fiyat satti: {bad!r}"


def test_unknown_side_raises_rather_than_guessing():
    """Bilinmeyen yonde sessizce LONG varsayma ,  patla."""
    with pytest.raises(ValueError):
        should_exit_locally(100.0, 96.0, "BOTH")


# ===========================================================================
# KOK-2 ,  gap sikistirmasi mevcut fiyattan, ve ASLA gevsetmez
# ===========================================================================

class _FakePM:
    """Sunucu guncellemesini taklit eder: istenen fiyati DOGRULANMIS dondurur.

    Gercek yol R0 testlerinde ayrica kaniti; burada sinanan sey gap
    sikistirmasinin HANGI FIYATI istedigi ve gevsetmeyi reddedip reddetmedigi.
    """

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def _update_server_stop_loss(self, symbol, price, qty, side="LONG"):
        self.calls.append((symbol, price, qty, side))
        if self.fail:
            return ProtectionResult(
                ProtectionOutcome.FAILED_NAKED, None, None, 0.0, "test: basarisiz"
            )
        return ProtectionResult(
            ProtectionOutcome.REPLACED_VERIFIED, "oid", float(price), float(qty),
            "test: dogrulandi",
        )


class _Bot:
    def __init__(self, positions=None, shorts=None, fail=False):
        self.positions = positions or {}
        self.short_positions = shorts or {}
        self.notifier = None
        self.client = None
        self.position_manager = _FakePM(fail=fail)


def _alert(symbol, side, current_price, action="TIGHTEN_STOP", gap_pct=-3.0):
    return {
        "symbol": symbol, "side": side, "action": action,
        "current_price": current_price, "gap_pct": gap_pct,
        "reason": "test", "prev_close": current_price * 1.03,
    }


def test_gap_tighten_computes_from_current_price_not_entry():
    """RIVN vakasi: '%1 limit' derken -%4.2 gerceklesen davranis olmeli.

    Giris 18.22, gap sonrasi fiyat 17.90. Eski kod stop_loss_pct=0.01 yaziyordu,
    bu GIRISIN %1 alti = 18.04 demekti; fiyat 17.90 zaten onun altinda oldugu icin
    tetik anlamini yitiriyor ve gercek cikis -%4.2'de olabiliyordu.
    Yeni kod MEVCUT fiyattan hesaplamali: 17.90 * 0.99 = 17.72.
    """
    entry, current = 18.22, 17.90
    bot = _Bot({"RIVN": {
        "entry_price": entry, "qty": 96,
        "stop_loss_pct": 0.04,
        "stop_loss_price": round(entry * 0.96, 2),  # 17.49
    }})
    GapScanner().execute_gap_actions(bot, [_alert("RIVN", "LONG", current)])

    trigger = bot.positions["RIVN"]["stop_loss_price"]
    assert abs(trigger - round(current * 0.99, 2)) < 0.02, \
        f"tetik mevcut fiyattan hesaplanmamis: {trigger}"
    assert abs(trigger - round(entry * 0.99, 2)) > 0.05, \
        "tetik hala GIRISTEN hesaplaniyor ,  KOK-2 geri gelmis"


def test_gap_tighten_refuses_to_loosen_an_armed_breakeven():
    """En sinsi hali: gap sikistirmasi armli break-even'i GEVSETMEMELI.

    Giris 100, break-even armli -> tetik 100.30. Fiyat 100.50.
    Aday: 100.50 * 0.99 = 99.50, yani 100.30'dan DAHA GEVSEK.
    Kabul edilirse bot kilitledigi kâri geri verir.
    """
    bot = _Bot({"AAPL": {
        "entry_price": 100.0, "qty": 10,
        "stop_loss_pct": 0.04,
        "stop_loss_price": 100.30,
        "breakeven_set": True,
    }})
    GapScanner().execute_gap_actions(bot, [_alert("AAPL", "LONG", 100.50)])

    assert bot.positions["AAPL"]["stop_loss_price"] == 100.30, \
        f"armli break-even gevsetildi: {bot.positions['AAPL']['stop_loss_price']}"


def test_gap_tighten_accepts_a_genuinely_tighter_trigger():
    """Gevsetme yasagi, gercek sikistirmayi da engellemesin."""
    bot = _Bot({"AAPL": {
        "entry_price": 100.0, "qty": 10,
        "stop_loss_pct": 0.04,
        "stop_loss_price": 96.00,
    }})
    GapScanner().execute_gap_actions(bot, [_alert("AAPL", "LONG", 110.0)])

    trigger = bot.positions["AAPL"]["stop_loss_price"]
    assert trigger > 96.00, "gercek sikistirma reddedildi"
    assert abs(trigger - 108.90) < 0.02, f"beklenen 108.90, gelen {trigger}"


def test_gap_tighten_short_side_mirrors():
    """SHORT'ta sikistirma AŞAGI dogru olmali (current * 1.01)."""
    bot = _Bot(shorts={"TSLA": {
        "entry_price": 200.0, "qty": 5,
        "stop_loss_pct": 0.04,
        "stop_loss_price": 208.00,
    }})
    GapScanner().execute_gap_actions(bot, [_alert("TSLA", "SHORT", 190.0)])

    trigger = bot.short_positions["TSLA"]["stop_loss_price"]
    assert abs(trigger - 191.90) < 0.02, f"beklenen 191.90, gelen {trigger}"
    assert trigger < 208.00, "SHORT sikistirmasi yanlis yone gitti"


def test_gap_tighten_on_unknown_symbol_is_a_noop():
    """Pozisyon defterinde olmayan sembol icin sessizce hicbir sey yapma."""
    bot = _Bot({})
    GapScanner().execute_gap_actions(bot, [_alert("GHOST", "LONG", 50.0)])
    assert bot.positions == {}


def test_gap_tighten_does_not_move_local_trigger_when_server_fails():
    """Sunucu guncellemesi basarisizsa yerel tetik OLDUGU GIBI kalmali.

    Aksi halde yerel "sikilastim" derken sunucuda eski gevsek stop durur —
    tum hasara yol acan split-brain'in ta kendisi.
    """
    bot = _Bot({"AAPL": {
        "entry_price": 100.0, "qty": 10,
        "stop_loss_pct": 0.04,
        "stop_loss_price": 96.00,
    }}, fail=True)
    GapScanner().execute_gap_actions(bot, [_alert("AAPL", "LONG", 110.0)])

    assert bot.positions["AAPL"]["stop_loss_price"] == 96.00,         "sunucu basarisizken yerel tetik yine de degistirildi"
    assert bot.position_manager.calls, "sunucu guncellemesi hic denenmedi"
