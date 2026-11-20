"""R22 PROOF: durust giris akisi , tikali hat asla "saglikli" degil.

Vaat: esigi gecen aday GIRISE donmuyorsa, saglik bunu ADIYLA soyler.
Eski kod ayni girdide "SAGLIKLI" diyordu ve Ihsan aylarca botu calisiyor sandi.
"""
from __future__ import annotations

import json

import pytest

from core.funnel import DailyFunnel
from core.health_status import (
    CIKIS_KODLARI,
    Durum,
    EntryFlow,
    ProfilSagligi,
    giris_akisi_durumu,
)


def _saglikli(sebep="ok"):
    from core.health_status import BoyutDurumu
    return BoyutDurumu(Durum.SAGLIKLI, sebep)


def gun(
    *, scanned=10, eligible=0, entries=0, sector=0, queued=0, queue_dup=0,
    gate=None, executor=None, margin=None,
) -> dict:
    """Tek bir funnel gunu uret (denklik varsayilan olarak TUTAR)."""
    gate = dict(gate or {})
    executor = dict(executor or {})
    d = DailyFunnel._empty_day()
    d["scanned"] = scanned
    d["eligible_buy"] = eligible
    d["entries"] = entries
    d["sector_block"] = sector
    d["queued_pullback"] = queued
    d["queue_dup"] = queue_dup
    d["gate_block"] = sum(gate.values())
    d["gate_block_reasons"] = gate
    d["executor_block"] = sum(executor.values())
    d["executor_block_reasons"] = executor
    d["max_margin"] = margin
    return d


def akis(gunler, **kw) -> EntryFlow:
    return EntryFlow(giris_akisi_durumu(gunler, **kw).ayrinti["entry_flow"])


# ============================================================ ANA VAAT
def test_tikali_hat_SAGLIKLI_demez():
    """R22'nin var olma sebebi: zarar serisi tikaniklikta TIKALI + cikis 3."""
    gunler = {"2026-09-11": gun(eligible=7, gate={"LOSS_STREAK_WARN": 7})}
    durum = giris_akisi_durumu(gunler)
    assert durum.ayrinti["entry_flow"] == EntryFlow.TIKALI.value
    assert durum.durum is Durum.DEGRADED
    assert CIKIS_KODLARI[durum.durum] == 3
    assert "LOSS_STREAK_WARN" in durum.sebep


def test_tikali_boyut_ozette_gizlenemez():
    p = ProfilSagligi(
        "live", _saglikli(), _saglikli(), _saglikli(),
        giris_akisi_durumu({"2026-09-11": gun(eligible=4, gate={"STOCK_FILTER": 4})}),
    )
    assert "entry_flow=DEGRADED" in p.ozet_metni()
    assert p.en_kotu() is Durum.DEGRADED


# ============================================================ SINIFLAR
def test_akiyor():
    gunler = {"2026-09-11": gun(eligible=3, entries=1, gate={"MTF": 2})}
    assert akis(gunler) is EntryFlow.AKIYOR
    assert CIKIS_KODLARI[giris_akisi_durumu(gunler).durum] == 0


def test_kilitli_R5():
    """Pazartesi beklenen okuma: kilit kapali, aday var, KILITLI (ariza DEGIL)."""
    gunler = {"2026-09-11": gun(eligible=5, gate={"LIVE_LOCK_R5": 5})}
    durum = giris_akisi_durumu(gunler)
    assert durum.ayrinti["entry_flow"] == EntryFlow.KILITLI.value
    assert CIKIS_KODLARI[durum.durum] == 0, "kasitli kilit ariza sayildi"


def test_filtreli_tasarim_kapisi():
    gunler = {"2026-09-11": gun(eligible=6, gate={"EARNINGS": 4, "MTF": 2})}
    durum = giris_akisi_durumu(gunler)
    assert durum.ayrinti["entry_flow"] == EntryFlow.FILTRELI.value
    assert CIKIS_KODLARI[durum.durum] == 0
    assert "EARNINGS" in durum.sebep


def test_sessiz_marji_gosterir():
    m = {"symbol": "NVDA", "confidence": 41.0, "threshold": 45.0, "margin": -4.0}
    durum = giris_akisi_durumu({"2026-09-11": gun(eligible=0, margin=m)})
    assert durum.ayrinti["entry_flow"] == EntryFlow.SESSIZ.value
    assert CIKIS_KODLARI[durum.durum] == 0
    assert "NVDA" in durum.sebep and "41" in durum.sebep


@pytest.mark.parametrize("sebep", ["SIZER_ZERO", "EQUITY_FLOOR", "CASH_RESERVE"])
def test_emir_imkansizken_AKIYOR_demez(sebep):
    """Executor terminal redleri DURUM blokeridir, tasarim seciciligi degil."""
    gunler = {"2026-09-11": gun(eligible=3, executor={sebep: 3})}
    assert akis(gunler) is EntryFlow.TIKALI


# ============================================== DENKLIK INVARYANTI
def test_aciklanamayan_fark_UNKNOWN():
    """Olculmemis bir `return False` yolu -> gun aciklanamaz, yesil sayilamaz."""
    gunler = {"2026-09-11": gun(eligible=9, gate={"MTF": 2})}   # 9 != 2
    durum = giris_akisi_durumu(gunler)
    assert durum.ayrinti["entry_flow"] == EntryFlow.UNKNOWN.value
    assert CIKIS_KODLARI[durum.durum] == 2
    assert durum.ayrinti["denklik"]["aciklanamayan"] == 7


def test_denklik_tutunca_siniflanir():
    gunler = {"2026-09-11": gun(eligible=5, gate={"MTF": 2}, executor={"SIZER_ZERO": 3})}
    assert akis(gunler) is EntryFlow.TIKALI     # SIZER_ZERO=3 > MTF=2


def test_sebepsiz_terminal_red_UNCLASSIFIED_ve_TIKALI():
    f = DailyFunnel(enabled=True, path=":yok:", today_fn=lambda: "2026-09-11")
    f._persist = lambda force=False: False
    f.bump("scanned", symbol="AAA")
    f.bump("eligible_buy", symbol="AAA")
    f.bump("executor_block")                     # sebep YOK
    d = f.days["2026-09-11"]
    assert d["executor_block_reasons"] == {"UNCLASSIFIED": 1}
    assert akis({"2026-09-11": d}) is EntryFlow.TIKALI


# ==================================================== PENCERE / MASKELEME
def test_eski_gunun_girisi_yeni_gunun_blokerini_maskeleyemez():
    gunler = {
        "2026-09-09": gun(eligible=3, entries=3),                     # eski: AKIYOR
        "2026-09-11": gun(eligible=4, gate={"LOSS_STREAK_WARN": 4}),  # yeni: TIKALI
    }
    assert akis(gunler) is EntryFlow.TIKALI, "eski giris yeni blokeri maskeledi"


def test_taranmayan_gun_atlanir_hafta_sonu_ariza_degil():
    gunler = {
        "2026-09-12": gun(scanned=0),                                  # hafta sonu
        "2026-09-11": gun(eligible=2, entries=2),
    }
    assert akis(gunler) is EntryFlow.AKIYOR


def test_pencere_disindaki_gun_karari_belirlemez():
    gunler = {
        "2026-09-11": gun(eligible=0),
        "2026-09-10": gun(eligible=0),
        "2026-09-09": gun(eligible=0),
        "2026-09-08": gun(eligible=5, gate={"LOSS_STREAK_WARN": 5}),   # pencere disi
    }
    assert akis(gunler, pencere=3) is EntryFlow.SESSIZ


# ================================================= FAIL-CLOSED / BOZUK
@pytest.mark.parametrize("bozuk", [None, {}, [], "metin", 42, {"g": "bozuk"}])
def test_bozuk_funnel_UNKNOWN(bozuk):
    durum = giris_akisi_durumu(bozuk)
    assert durum.ayrinti["entry_flow"] == EntryFlow.UNKNOWN.value
    assert CIKIS_KODLARI[durum.durum] == 2


def test_okuma_hatasi_UNKNOWN():
    durum = giris_akisi_durumu({"2026-09-11": gun()}, okuma_hatasi="izin yok")
    assert durum.ayrinti["entry_flow"] == EntryFlow.UNKNOWN.value


def test_tarama_hic_yoksa_UNKNOWN():
    assert akis({"2026-09-11": gun(scanned=0)}) is EntryFlow.UNKNOWN


def test_olculmemis_boyut_varsayilan_UNKNOWN():
    """ProfilSagligi entry_flow verilmeden kurulursa YESIL sayilmaz."""
    p = ProfilSagligi("live", _saglikli(), _saglikli(), _saglikli())
    assert p.entry_flow.durum is Durum.UNKNOWN
    assert p.en_kotu() is Durum.UNKNOWN


def test_bilinmeyen_bloker_durum_blokeri_sayilir():
    """Yeni bir red sebebi sessizce 'tasarim' kovasina DUSEMEZ."""
    gunler = {"2026-09-11": gun(eligible=3, executor={"YEPYENI_SEBEP": 3})}
    durum = giris_akisi_durumu(gunler)
    assert durum.ayrinti["entry_flow"] == EntryFlow.TIKALI.value
    assert "siniflandirilmamis" in durum.sebep


# ============================================================ MARJ
def test_marj_ayni_karar_aninda_yazilir():
    """Guven ve esik AYRI anlardan toplanirsa marj uydurma cikar."""
    f = DailyFunnel(enabled=True, path=":yok:", today_fn=lambda: "2026-09-11")
    f._persist = lambda force=False: False
    f.record_margin("AAA", 41.0, 45.0)     # marj -4
    f.record_margin("BBB", 30.0, 55.0)     # marj -25 (daha kotu, yazilmamali)
    f.record_margin("CCC", 52.0, 55.0)     # marj -3 (en iyi)
    m = f.days["2026-09-11"]["max_margin"]
    assert m["symbol"] == "CCC" and m["margin"] == pytest.approx(-3.0)
    assert m["confidence"] == 52.0 and m["threshold"] == 55.0


def test_marj_gidis_donus_bozulmaz():
    d = gun(margin={"symbol": "X", "confidence": 1, "threshold": 2, "margin": -1})
    geri = DailyFunnel._normalize_day(json.loads(json.dumps(d)))
    assert geri["max_margin"]["symbol"] == "X"
    assert geri["max_margin"]["margin"] == pytest.approx(-1.0)


def test_bozuk_marj_cokertmez():
    d = gun()
    d["max_margin"] = "bozuk"
    geri = DailyFunnel._normalize_day(json.loads(json.dumps(d)))
    assert geri["max_margin"] is None


# ============================================ ESKI FUNNEL GUNLERI
def test_bear_denemesi_strateji_hunisini_KIRLETMEZ():
    """BearBrain ayni execute_buy'i kullanir ama eligible_buy'a hic girmez.

    Bear redleri ayni kovaya yazilsaydi gunun denkligi tutmaz ve saglik
    sebepsiz yere UNKNOWN'a duserdi (gercek bir kusurdu, R22 incelemesinde
    yakalandi).
    """
    from core.executor import OrderExecutor

    cagrilar = []

    class _Bot:
        def _funnel_bump(self, stage, reason=None, symbol=None):
            cagrilar.append((stage, reason, symbol))

    bot = _Bot()
    OrderExecutor._terminal_block_safe(bot, "SH", "CASH_RESERVE", "bear_etf")
    OrderExecutor._terminal_block_safe(bot, "SQQQ", "SIZER_ZERO", "index_parking")
    assert cagrilar == [], "bear/parking reddi strateji hunisine yazildi"

    OrderExecutor._terminal_block_safe(bot, "AAPL", "CASH_RESERVE", "strategy")
    assert cagrilar == [("executor_block", "CASH_RESERVE", "AAPL")]


def test_bear_kirlenmesi_denkligi_bozmaz():
    """Strateji gunu denk; bear redi sayilsaydi UNKNOWN olurdu."""
    gunler = {"2026-09-11": gun(eligible=2, gate={"MTF": 2})}
    assert akis(gunler) is EntryFlow.FILTRELI


def test_eski_gun_aday_TURETILIR_ve_isaretlenir():
    """R22 oncesi gunde eligible_buy yok , kapi redlerinden ALT SINIR turetilir.

    Sifir saymak bu gunleri sessizce "aday yok" gosterirdi: gizlemek
    istedigimiz seyin ta kendisi.
    """
    eski = {"scanned": 12, "signal_buy": 3, "gate_block": 4,
            "gate_block_reasons": {"LOSS_STREAK_WARN": 4}}
    durum = giris_akisi_durumu({"2026-09-01": eski})
    assert durum.ayrinti["entry_flow"] == EntryFlow.TIKALI.value
    assert durum.ayrinti["turetilmis"] is True
    assert durum.ayrinti["eligible_buy"] == 4
    assert "TURETILMIS" in durum.sebep


def test_eski_gunde_wash_sale_adaya_SAYILMAZ():
    """wash_sale kapisi esikten ONCE kosar , turetilen adaya girmemeli."""
    eski = {"scanned": 9, "wash_sale_block": 6, "gate_block": 1,
            "gate_block_reasons": {"MTF": 1}}
    durum = giris_akisi_durumu({"2026-09-01": eski})
    assert durum.ayrinti["eligible_buy"] == 1, "wash_sale aday sayisini sisirdi"


def test_eski_gun_hic_kapi_reddi_yoksa_sessiz():
    eski = {"scanned": 12, "signal_buy": 3}
    assert akis({"2026-09-01": eski}) is EntryFlow.SESSIZ


def test_eski_gun_denklik_kontrolune_takilmaz():
    """Turetilmis gunde denklik tanim geregi tutar; UNKNOWN uretmemeli."""
    eski = {"scanned": 20, "gate_block": 3, "gate_block_reasons": {"EARNINGS": 3},
            "sector_block": 2, "entries": 0}
    durum = giris_akisi_durumu({"2026-09-01": eski})
    assert durum.ayrinti["entry_flow"] != EntryFlow.UNKNOWN.value
    assert durum.ayrinti["eligible_buy"] == 5
