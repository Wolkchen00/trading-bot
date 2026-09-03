"""R18 , golge toplayici: ekle-sadece niyet/olay kaydi.

Kanit maddeleri RF-PLAN-4.md R18 PROOF bolumunden birebir:
  (a) kilit kapaliyken kayit uretiliyor VE emir metodu cagri sayaci 0
  (b) niyet merkezi kilit reddinde yakalaniyor; asagi-akis elemeleri
      "gonderilecekti" diye YAZILMIYOR
  (c) intent_id kalici ve benzersiz, olay sirasi monoton, ekle-sadece
  (d) ortak ornek tek kayitta; sema surumu ve etiket referansi mevcut
  (e) commit_sha/profil hash degisince epoch sifirlaniyor; kimligi eksik kayit
      kapi-uygunsuz isaretleniyor
  (f) kayit yazma hatasi coordinator kararini DEGISTIRMIYOR
  (g) boyut tavani mevcut epoch icinde uygulaniyor, dusurulen sayi raporlaniyor
  (h) bozuk/eksik dosyada cokmuyor
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from core.shadow_ledger import (
    DEGERLENDIRILMEMIS_KONTROLLER,
    SCHEMA_VERSION,
    ShadowLedger,
    epoch_id,
)

T0 = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def _defter(tmp_path, max_bytes=8 * 1024 * 1024, now=T0):
    return ShadowLedger(
        path=str(tmp_path / "shadow.jsonl"),
        max_bytes=max_bytes,
        now_fn=lambda: now,
    )


def _karar():
    return {
        "signal": "BUY",
        "confidence": 42.0,
        "weighted_score": 21.0,
        "majority": False,
        "risk_veto": False,
        "buy_count": 2, "sell_count": 0, "hold_count": 2,
        "votes": [
            {"agent": "TechAgent", "signal": "BUY", "confidence": 60},
            {"agent": "FundAgent", "signal": "HOLD", "confidence": 0},
            {"agent": "SentAgent", "signal": "BUY", "confidence": 30},
            {"agent": "RiskAgent", "signal": "HOLD", "confidence": 0},
        ],
        "dynamic_weights": {"TechAgent": 0.25, "FundAgent": 0.20,
                            "SentAgent": 0.20, "RiskAgent": 0.20},
    }


def _analiz():
    return {
        "price": 250.0, "atr": 3.2, "confidence": 42, "rsi": 41,
        "fundamental_data_age_hours": 30.5,
        "fundamental_is_stale": True,
        "fundamental_data_source": "alpha_vantage",
        "fundamental_data_ok": True,
    }


# ======================================================================
# (a) KAYIT URETILIYOR ve EMIR METODU CAGRILMIYOR
# ======================================================================

def test_a_kayit_uretiliyor(tmp_path):
    d = _defter(tmp_path)
    iid = d.record_lock_rejection(
        symbol="AAPL", kind="stock_long", block_reason="LIVE_LOCK_R5",
        decision=_karar(), analysis=_analiz(),
    )
    assert iid, "niyet kaydedilmedi"
    kayitlar = d.read_all()
    assert len(kayitlar) == 1
    assert kayitlar[0]["symbol"] == "AAPL"
    assert kayitlar[0]["block_reason"] == "LIVE_LOCK_R5"


def test_a_hicbir_emir_metodu_cagrilmiyor(tmp_path, monkeypatch):
    """SALT GOZLEM. Golge defter broker'a DOKUNAMAZ."""
    import core.shadow_ledger as sl
    yasak = []
    for ad in ("submit_order", "close_position", "cancel_order"):
        monkeypatch.setattr(
            sl, ad, lambda *a, **k: yasak.append(ad), raising=False
        )
    d = _defter(tmp_path)
    d.record_lock_rejection(
        symbol="AAPL", kind="stock_long", block_reason="LIVE_LOCK_R5",
        decision=_karar(),
    )
    assert yasak == [], f"golge defter emir metodu cagirdi: {yasak}"


def test_a_kilit_kapaliyken_gercek_kapidan_kayit_geliyor(tmp_path, monkeypatch):
    """ENTEGRASYON: gercek risk_guard yolundan geciyor mu."""
    import core.risk_guard as rg
    import core.shadow_ledger as sl

    d = _defter(tmp_path)
    monkeypatch.setattr(sl, "_paylasilan", d)
    monkeypatch.setattr(rg, "_deny", lambda bot, reason: (False, reason))

    class _Bot:
        is_paper = False
        kill_switch = None
        positions = {}
        _son_karar = {"AAPL": _karar()}
        _son_analiz = {"AAPL": _analiz()}
        market_hours = None

    ok, sebep = rg.can_open_new_risk(
        _Bot(), {"live_entries_enabled": False}, kind="stock_long", symbol="AAPL"
    )
    assert ok is False and sebep == "LIVE_LOCK_R5"
    kayitlar = d.read_all()
    assert len(kayitlar) == 1, "gercek kapidan golge kaydi gelmedi"
    assert kayitlar[0]["decision"]["signal"] == "BUY"


def test_a_kilit_ACIKKEN_golge_kaydi_YOK(tmp_path, monkeypatch):
    """Kilit acikken golge kaydi anlamsiz , gercek islem zaten olusur."""
    import core.risk_guard as rg
    import core.shadow_ledger as sl
    d = _defter(tmp_path)
    monkeypatch.setattr(sl, "_paylasilan", d)

    class _Bot:
        is_paper = False
        kill_switch = None
        positions = {}

    rg.can_open_new_risk(
        _Bot(), {"live_entries_enabled": True}, kind="stock_long", symbol="AAPL"
    )
    assert d.read_all() == []


# ======================================================================
# (b) ASIRI SAYIM KORUMASI
# ======================================================================

def test_b_asagi_akis_degerlendirilmemis_isaretleniyor(tmp_path):
    """Kilit reddi aninda equity floor / nakit rezerv / boyutlandirma HENUZ
    KOSMAMISTIR. Bunlari 'gonderilecekti' saymak asiri sayimdir."""
    d = _defter(tmp_path)
    d.record_lock_rejection(
        symbol="AAPL", kind="stock_long", block_reason="LIVE_LOCK_R5",
        decision=_karar(),
    )
    k = d.read_all()[0]
    assert k["downstream_evaluated"] is False
    assert set(k["downstream_unevaluated_checks"]) == set(
        DEGERLENDIRILMEMIS_KONTROLLER
    )
    assert "ANLAMINA GELMEZ" in k["downstream_note"]


def test_b_boyut_hesaplanmamis_olarak_isaretli(tmp_path):
    d = _defter(tmp_path)
    d.record_lock_rejection(
        symbol="AAPL", kind="stock_long", block_reason="LIVE_LOCK_R5",
        order_params={"side": "buy", "size_usd": None, "qty": None},
    )
    op = d.read_all()[0]["order_params"]
    assert op["size_usd"] is None and op["qty"] is None


def test_b_iddia_edilmeyenler_acikca_yazili(tmp_path):
    """R18 sonuc/getiri/dolum/cikis/benchmark IDDIA ETMEZ."""
    d = _defter(tmp_path)
    d.record_lock_rejection(symbol="AAPL", kind="stock_long",
                            block_reason="LIVE_LOCK_R5")
    iddia_edilmeyen = d.read_all()[0]["not_claimed"]
    for alan in ("outcome", "return", "fill", "exit", "benchmark",
                 "portfolio_state", "closed_episode"):
        assert alan in iddia_edilmeyen


def test_b_ozet_sonuc_iddia_etmiyor(tmp_path):
    d = _defter(tmp_path)
    d.record_lock_rejection(symbol="AAPL", kind="stock_long",
                            block_reason="LIVE_LOCK_R5")
    o = d.ozet()
    assert "iddia_edilmeyen" in o
    for yasak in ("pnl", "getiri", "alpha", "kazanc"):
        assert yasak not in json.dumps(o).lower().replace("iddia_edilmeyen", "")


# ======================================================================
# (c) INTENT_ID, SIRA, EKLE-SADECE
# ======================================================================

def test_c_intent_id_benzersiz_ve_sira_monoton(tmp_path):
    d = _defter(tmp_path)
    idler = [
        d.record_lock_rejection(symbol=f"S{i}", kind="stock_long",
                                block_reason="LIVE_LOCK_R5")
        for i in range(5)
    ]
    assert len(set(idler)) == 5, "intent_id benzersiz degil"
    siralar = [k["seq"] for k in d.read_all()]
    assert siralar == sorted(siralar) == [1, 2, 3, 4, 5]


def test_c_ekle_sadece_onceki_kayitlar_korunuyor(tmp_path):
    d = _defter(tmp_path)
    d.record_lock_rejection(symbol="AAPL", kind="stock_long",
                            block_reason="LIVE_LOCK_R5")
    d2 = _defter(tmp_path)     # yeni nesne = restart
    d2.record_lock_rejection(symbol="MSFT", kind="stock_long",
                             block_reason="LIVE_LOCK_R5")
    semboller = [k["symbol"] for k in d2.read_all()]
    assert semboller == ["AAPL", "MSFT"], "ekle-sadece bozuldu"


def test_c_intent_id_kalici_alan_olarak_yaziliyor(tmp_path):
    d = _defter(tmp_path)
    iid = d.record_lock_rejection(symbol="AAPL", kind="stock_long",
                                  block_reason="LIVE_LOCK_R5")
    assert d.read_all()[0]["intent_id"] == iid


# ======================================================================
# (d) ORTAK ORNEK TEK KAYITTA
# ======================================================================

def test_d_ortak_ornek_tek_kayitta(tmp_path):
    """Mevcut agent_stats MARJINAL histogram tutuyor; ondan kalibrasyon
    yapilamaz. Burada oy + guven + agirlik BIR ARADA."""
    d = _defter(tmp_path)
    d.record_lock_rejection(
        symbol="AAPL", kind="stock_long", block_reason="LIVE_LOCK_R5",
        decision=_karar(), analysis=_analiz(),
    )
    k = d.read_all()[0]
    kar = k["decision"]
    assert kar["weighted_score"] == 21.0
    assert len(kar["votes"]) == 4
    assert kar["votes"][0]["agent"] == "TechAgent"
    assert kar["votes"][0]["confidence"] == 60
    assert kar["dynamic_weights"]["TechAgent"] == 0.25
    # Ayni kayitta veri yasi da var (R17'den)
    assert k["analysis"]["fundamental_is_stale"] is True
    assert k["analysis"]["fundamental_data_age_hours"] == 30.5


def test_d_sema_surumu_ve_etiket_referansi(tmp_path):
    d = _defter(tmp_path)
    d.record_lock_rejection(symbol="AAPL", kind="stock_long",
                            block_reason="LIVE_LOCK_R5")
    k = d.read_all()[0]
    assert k["identity"]["schema_version"] == SCHEMA_VERSION
    assert "label_contract_version" in k["identity"]
    assert "label_ref" in k, "etiket baglanti noktasi yok , sonra yeniden yazim gerekir"


def test_d_degismez_fiyat_kaynagi_adlandirilmis(tmp_path):
    """Giris ani bid/ask TEK BASINA sonraki tekrar icin yetmez."""
    d = _defter(tmp_path)
    d.record_lock_rejection(symbol="AAPL", kind="stock_long",
                            block_reason="LIVE_LOCK_R5")
    k = d.read_all()[0]
    assert k["quote_source"], "degismez fiyat kaynagi adlandirilmamis"
    assert "alpaca" in k["quote_source"].lower()


# ======================================================================
# (e) KIMLIK ve EPOCH
# ======================================================================

def test_e_epoch_commit_ve_profil_ile_degisiyor():
    e1 = epoch_id("sha_a", "profil_a")
    assert epoch_id("sha_b", "profil_a") != e1, "commit degisince epoch degismedi"
    assert epoch_id("sha_a", "profil_b") != e1, "profil degisince epoch degismedi"
    assert epoch_id("sha_a", "profil_a") == e1, "epoch deterministik degil"


def test_e_kimligi_eksik_kayit_kapi_uygunsuz(tmp_path, monkeypatch):
    """Kimliksiz gozlem KANIT DEGILDIR."""
    import core.shadow_ledger as sl
    monkeypatch.setattr(sl, "commit_sha", lambda: "UNKNOWN")
    monkeypatch.setattr(sl, "profil_hash", lambda: "UNKNOWN")
    d = ShadowLedger(path=str(tmp_path / "s.jsonl"), now_fn=lambda: T0)
    d.record_lock_rejection(symbol="AAPL", kind="stock_long",
                            block_reason="LIVE_LOCK_R5")
    assert d.read_all()[0]["identity"]["kapi_uygunsuz"] is True


def test_e_kimlik_tamsa_kapi_uygun(tmp_path, monkeypatch):
    import core.shadow_ledger as sl
    monkeypatch.setattr(sl, "commit_sha", lambda: "abc123")
    monkeypatch.setattr(sl, "profil_hash", lambda: "def456")
    d = ShadowLedger(path=str(tmp_path / "s.jsonl"), now_fn=lambda: T0)
    d.record_lock_rejection(symbol="AAPL", kind="stock_long",
                            block_reason="LIVE_LOCK_R5")
    assert d.read_all()[0]["identity"]["kapi_uygunsuz"] is False


def test_e_farkli_epochlar_karismiyor(tmp_path, monkeypatch):
    import core.shadow_ledger as sl
    yol = str(tmp_path / "s.jsonl")

    monkeypatch.setattr(sl, "commit_sha", lambda: "sha_eski")
    monkeypatch.setattr(sl, "profil_hash", lambda: "p")
    d1 = ShadowLedger(path=yol, now_fn=lambda: T0)
    d1.record_lock_rejection(symbol="A", kind="stock_long", block_reason="LIVE_LOCK_R5")

    monkeypatch.setattr(sl, "commit_sha", lambda: "sha_yeni")
    d2 = ShadowLedger(path=yol, now_fn=lambda: T0)
    d2.record_lock_rejection(symbol="B", kind="stock_long", block_reason="LIVE_LOCK_R5")

    ozet = d2.ozet()
    assert len(ozet["epochlar"]) == 2, "epochlar ayrismiyor"


# ======================================================================
# (f) KAYIT ARIZASI KARARI DEGISTIREMEZ
# ======================================================================

def test_f_yazma_hatasi_kapi_kararini_degistirmiyor(tmp_path, monkeypatch):
    """R13 disiplini: telemetri arizasi trading kararina geri beslenemez."""
    import core.risk_guard as rg
    import core.shadow_ledger as sl

    class _Patlayan:
        def record_lock_rejection(self, **kw):
            raise RuntimeError("golge defter patladi")

    monkeypatch.setattr(sl, "_paylasilan", _Patlayan())
    monkeypatch.setattr(rg, "_deny", lambda bot, reason: (False, reason))

    class _Bot:
        is_paper = False
        kill_switch = None
        positions = {}

    ok, sebep = rg.can_open_new_risk(
        _Bot(), {"live_entries_enabled": False}, kind="stock_long", symbol="AAPL"
    )
    assert ok is False and sebep == "LIVE_LOCK_R5", (
        "golge defter arizasi kapi kararini degistirdi"
    )


def test_f_kayit_metodu_istisna_sizdirmiyor(tmp_path, monkeypatch):
    d = _defter(tmp_path)
    monkeypatch.setattr(d, "_ekle", lambda k: (_ for _ in ()).throw(IOError("disk")))
    assert d.record_lock_rejection(
        symbol="AAPL", kind="stock_long", block_reason="LIVE_LOCK_R5"
    ) is None


# ======================================================================
# (g) BOYUT TAVANI + DETERMINISTIK BUDAMA
# ======================================================================

def test_g_boyut_tavani_mevcut_epoch_icinde_de_uygulaniyor(tmp_path):
    """'Mevcut epoch'a dokunma' kurali kendi tavanini ihlal ederdi."""
    d = _defter(tmp_path, max_bytes=6000)
    for i in range(200):
        d.record_lock_rejection(
            symbol=f"S{i:03d}", kind="stock_long", block_reason="LIVE_LOCK_R5",
            decision=_karar(), analysis=_analiz(),
        )
    boyut = os.path.getsize(d.path)
    assert boyut <= 6000 * 2, f"tavan asildi: {boyut}"
    assert d.dropped > 0, "budama olmadi ama tavan zorlandi"


def test_g_dusurulen_sayi_kaydediliyor(tmp_path):
    """Sessiz veri kaybi YOK."""
    d = _defter(tmp_path, max_bytes=5000)
    for i in range(150):
        d.record_lock_rejection(symbol=f"S{i}", kind="stock_long",
                                block_reason="LIVE_LOCK_R5", decision=_karar())
    assert d.ozet()["dusurulen"] == d.dropped
    meta = d.path + ".meta.json"
    if os.path.exists(meta):
        with open(meta, encoding="utf-8") as f:
            assert json.load(f)["dropped_total"] >= 1


def test_g_budama_DETERMINISTIK(tmp_path):
    """Rastgele budama tekrarlanabilirligi bozardi."""
    sonuclar = []
    for tur in range(2):
        yol = tmp_path / f"s{tur}.jsonl"
        d = ShadowLedger(path=str(yol), max_bytes=5000, now_fn=lambda: T0)
        for i in range(120):
            d.record_lock_rejection(symbol=f"S{i:03d}", kind="stock_long",
                                    block_reason="LIVE_LOCK_R5", decision=_karar())
        sonuclar.append([k["symbol"] for k in d.read_all()])
    assert sonuclar[0] == sonuclar[1], "budama deterministik degil"


# ======================================================================
# (h) BOZUK / EKSIK DOSYA
# ======================================================================

def test_h_bozuk_satir_atlaniyor_cokmuyor(tmp_path):
    yol = tmp_path / "s.jsonl"
    yol.write_text('{"intent_id":"a"}\nBOZUK SATIR\n{"intent_id":"b"}\n',
                   encoding="utf-8")
    d = ShadowLedger(path=str(yol), now_fn=lambda: T0)
    kayitlar = d.read_all()
    assert len(kayitlar) == 2


def test_h_dosya_yoksa_bos_liste(tmp_path):
    d = ShadowLedger(path=str(tmp_path / "yok.jsonl"), now_fn=lambda: T0)
    assert d.read_all() == []
    assert d.ozet()["toplam_niyet"] == 0


def test_h_bozuk_dosyaya_yazmaya_devam_edebiliyor(tmp_path):
    yol = tmp_path / "s.jsonl"
    yol.write_text("BOZUK\n", encoding="utf-8")
    d = ShadowLedger(path=str(yol), now_fn=lambda: T0)
    assert d.record_lock_rejection(
        symbol="AAPL", kind="stock_long", block_reason="LIVE_LOCK_R5"
    )
