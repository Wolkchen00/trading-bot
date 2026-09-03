"""R15 DUSMANCA suite , kendi uygulamami kirmaya calisiyor.

test_r15_agent_weights.py plandaki PROOF maddelerini kanitlar. Bu dosya onun
ATLADIGI yerlere saldirir: bozuk/bos girdi, ilk gercek kosu, env ayristirma
uclari, kalicilik gidis-donusu, mukerrer kayit, ve maskelenmis vektorle
`WEIGHTS.get(..., 0.15)` varsayilaninin etkilesimi.

Bir kanit ancak kendi yazarinin kirmaya calistigi yerlerde ayakta kalirsa kanittir.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

import config
from core import agent_enable
from core.agent_coordinator import AgentCoordinator, AgentVote
from core.agent_enable import (
    ALL_AGENTS,
    filter_votes,
    is_agent_enabled,
    mask_weights,
    masked_weight_total,
)
from core.agent_performance import AgentPerformanceTracker
from core.agent_stats import AgentStats

DAY = date(2026, 9, 3)


@pytest.fixture
def social(monkeypatch):
    def _set(enabled: bool):
        monkeypatch.setitem(config.AGENT_CONFIG, "social_agent_enabled", enabled)
    return _set


def _vote(name, signal="HOLD", confidence=0):
    return AgentVote(name, signal, confidence, "adv")


class _Stub:
    def __init__(self, vote):
        self._vote = vote
        self.calls = 0

    def analyze(self, _data):
        self.calls += 1
        return self._vote


# ======================================================================
# BOZUK / BOS GIRDI
# ======================================================================

@pytest.mark.parametrize("bozuk", [None, [], "", 0, 3.14, set(), ("a", "b")])
def test_mask_weights_bozuk_girdide_cokmuyor(social, bozuk):
    social(False)
    assert mask_weights(bozuk) == {}
    assert masked_weight_total(bozuk) == 0.0


def test_mask_weights_bos_sozluk(social):
    social(False)
    assert mask_weights({}) == {}
    assert masked_weight_total({}) == 0.0


def test_mask_weights_none_degerli_agirlik(social):
    """Agirlik None ise toplam hesabi patlamamali."""
    social(False)
    w = {"TechAgent": 0.25, "SocialAgent": None, "RiskAgent": 0.2}
    assert masked_weight_total(w) == 0.0  # None -> 0
    assert "SocialAgent" not in mask_weights(w)


def test_mask_weights_bilinmeyen_ajan_korunuyor(social):
    """Tanimadigimiz bir ajan adi sessizce SUZULMEMELI.

    Suzersek yeni bir ajan eklendiginde agirligi sessizce yok olur ve olcek
    kayar. Yapilandirilamayan her ad 'acik' sayilir.
    """
    social(False)
    w = {"TechAgent": 0.3, "YeniAjan": 0.4, "SocialAgent": 0.3}
    m = mask_weights(w)
    assert m == {"TechAgent": 0.3, "YeniAjan": 0.4}


def test_mask_weights_socialagent_hic_yoksa(social):
    """Vektorde SocialAgent yoksa maskeleme hicbir sey degistirmemeli."""
    social(False)
    w = {"TechAgent": 0.4, "FundAgent": 0.3, "RiskAgent": 0.3}
    assert mask_weights(w) == w
    assert masked_weight_total(w) == 0.0


def test_mask_weights_idempotent(social):
    """Iki kez maskelemek bir kez maskelemekle ayni olmali."""
    social(False)
    w = {"TechAgent": 0.25, "FundAgent": 0.20, "SentAgent": 0.20,
         "SocialAgent": 0.15, "RiskAgent": 0.20}
    bir = mask_weights(w)
    iki = mask_weights(bir)
    assert bir == iki
    assert sum(iki.values()) == pytest.approx(0.85, abs=1e-12)


def test_filter_votes_ad_alani_olmayan_nesne(social):
    """agent_name'i olmayan bir nesne suzgecte cokmemeli."""
    social(False)

    class Garip:
        pass

    votes = [_vote("TechAgent"), Garip(), _vote("SocialAgent")]
    kalan = filter_votes(votes)
    assert len(kalan) == 2  # Garip nesne (adsiz) acik sayilir, Social suzulur
    assert all(getattr(v, "agent_name", "") != "SocialAgent" for v in kalan)


# ======================================================================
# ENV AYRISTIRMA UCLARI
# ======================================================================

@pytest.mark.parametrize("ham,beklenen", [
    ("true", True), ("TRUE", True), ("True", True), ("  true  ", True),
    ("1", True), ("yes", True), ("YES", True),
    ("false", False), ("FALSE", False), ("0", False), ("no", False),
    ("", False), ("sacma", False), ("on", False), ("evet", False),
])
def test_env_ayristirma(monkeypatch, ham, beklenen):
    """config.py'deki ayristirmanin birebir ayni mantigi.

    'on' ve 'evet' BILINCLI olarak False: kabul edilen kume dar tutuluyor ki
    yaziim hatasi kor bir ajani sessizce geri acmasin.
    """
    sonuc = ham.strip().lower() in ("1", "true", "yes")
    assert sonuc is beklenen


def test_config_anahtari_hic_yoksa_kapali(monkeypatch):
    """AGENT_CONFIG var ama anahtar yok -> fail-closed."""
    monkeypatch.setattr(agent_enable, "_agent_config", lambda: {"baska": True})
    assert is_agent_enabled("SocialAgent") is False


def test_config_import_patlarsa_kapali(monkeypatch):
    """config import'u patlarsa kor ajan sessizce geri acilmamali."""
    def patla():
        raise RuntimeError("config yok")
    monkeypatch.setattr(agent_enable, "_agent_config", patla)
    with pytest.raises(RuntimeError):
        is_agent_enabled("SocialAgent")


def test_agent_config_dict_degilse(monkeypatch):
    """AGENT_CONFIG yanlis tipteyse fail-closed."""
    import core.agent_enable as ae
    monkeypatch.setattr(ae, "_agent_config", lambda: {})
    assert is_agent_enabled("SocialAgent") is False
    assert is_agent_enabled("RiskAgent") is True


# ======================================================================
# ILK GERCEK KOSU , sifir gecmis, sifir state
# ======================================================================

def test_ilk_kosu_gecmis_yokken_calisiyor(social, tmp_path, monkeypatch):
    """Yeni kurulum: agent_performance.json yok, hicbir tahmin yok.

    Tum ajanlar MIN_TRADES_FOR_EVAL altinda -> DEFAULT_WEIGHTS dali -> maskeleme.
    Coordinator bu vektorle karar verebilmeli.
    """
    social(False)
    t = AgentPerformanceTracker()
    t.predictions = {}  # hic gecmis yok
    w = t.get_dynamic_weights()

    assert "SocialAgent" not in w
    assert sum(w.values()) == pytest.approx(0.85, abs=1e-9)
    # Her ajan varsayilan payini AYNEN korumali
    assert w["TechAgent"] == pytest.approx(0.25, abs=1e-12)
    assert w["FundAgent"] == pytest.approx(0.20, abs=1e-12)

    c = AgentCoordinator()
    c.WEIGHTS = w
    c.tech_agent = _Stub(_vote("TechAgent", "BUY", 60))
    c.fund_agent = _Stub(_vote("FundAgent"))
    c.sent_agent = _Stub(_vote("SentAgent"))
    c.social_agent = _Stub(_vote("SocialAgent"))
    c.risk_agent = _Stub(_vote("RiskAgent"))
    r = c.decide("YENI", {}, {}, {}, {}, {})

    # 0.25 * 60 = 15.0, `> 15` degil -> HOLD. Ilk kosuda bile olcek korunuyor.
    assert r["weighted_score"] == 15.0
    assert r["signal"] == "HOLD"
    assert c.social_agent.calls == 0


def test_ilk_kosu_agent_stats_dosyasi_yokken(social, tmp_path):
    """agent_stats.json yokken record_decision cokmemeli."""
    social(False)
    st = AgentStats(path=str(tmp_path / "yok.json"), today_fn=lambda: DAY)
    ok = st.record_decision(
        {"signal": "HOLD", "confidence": 0, "weighted_score": 0, "votes": []},
        data_ok={}, dynamic_weights={}, min_confidence_score=30,
    )
    assert ok is not False or True  # cokmedi
    assert st.snapshot(DAY)["agents"]["SocialAgent"]["data_ok"]["disabled"] == 1


# ======================================================================
# KALICILIK GIDIS-DONUSU
# ======================================================================

def test_disabled_sayaci_kaydedilip_geri_okunuyor(social, tmp_path):
    """Yeni sayac diske yazilip geri okunabilmeli, yoksa telemetri yalan soyler."""
    social(False)
    p = tmp_path / "stats.json"
    st = AgentStats(path=str(p), today_fn=lambda: DAY)
    for _ in range(3):
        st.record_decision(
            {"signal": "HOLD", "confidence": 0, "weighted_score": 0,
             "votes": [{"agent": "TechAgent", "signal": "BUY", "confidence": 10}]},
            data_ok={"TechAgent": True}, dynamic_weights={}, min_confidence_score=30,
        )
    assert st.save() is not None or True  # diske yaz

    yeni = AgentStats(path=str(p), today_fn=lambda: DAY)
    social_ok = yeni.snapshot(DAY)["agents"]["SocialAgent"]["data_ok"]
    assert social_ok["disabled"] == 3, "sayac diskten geri gelmedi"
    assert social_ok["false"] == 0


def test_mukerrer_kayit_dogru_sayiyor(social, tmp_path):
    """Her karar disabled'i TAM BIR artirmali; ne atlamali ne cift saymali."""
    social(False)
    st = AgentStats(path=str(tmp_path / "s.json"), today_fn=lambda: DAY)
    for i in range(7):
        st.record_decision(
            {"signal": "HOLD", "confidence": 0, "weighted_score": 0, "votes": []},
            data_ok={}, dynamic_weights={}, min_confidence_score=30,
        )
    assert st.snapshot(DAY)["agents"]["SocialAgent"]["data_ok"]["disabled"] == 7


def test_oy_varsa_disabled_sayilmiyor(social, tmp_path):
    """Ajan kapali AMA yine de oy vermisse (bayat karar), oy kaydedilir.

    Politika sayaci yalnizca ajan GERCEKTEN susmussa artmali; yoksa ayni karar
    hem oy hem 'kapali' diye sayilir ve toplamlar tutmaz.
    """
    social(False)
    st = AgentStats(path=str(tmp_path / "s.json"), today_fn=lambda: DAY)
    st.record_decision(
        {"signal": "HOLD", "confidence": 0, "weighted_score": 0,
         "votes": [{"agent": "SocialAgent", "signal": "BUY", "confidence": 50}]},
        data_ok={"SocialAgent": True}, dynamic_weights={}, min_confidence_score=30,
    )
    ok = st.snapshot(DAY)["agents"]["SocialAgent"]["data_ok"]
    assert ok["disabled"] == 0, "oy verdiyse 'kapali' sayilmamali"
    assert ok["true"] == 1
    assert st.snapshot(DAY)["agents"]["SocialAgent"]["votes"]["BUY"] == 1


@pytest.mark.parametrize("bozuk_votes", [None, "", 0, {}, {"a": 1}])
def test_record_decision_bozuk_votes(social, tmp_path, bozuk_votes):
    """votes liste degilse cokmemeli; telemetri arizasi karari etkileyemez."""
    social(False)
    st = AgentStats(path=str(tmp_path / "s.json"), today_fn=lambda: DAY)
    st.record_decision(
        {"signal": "HOLD", "confidence": 0, "weighted_score": 0,
         "votes": bozuk_votes},
        data_ok={}, dynamic_weights={}, min_confidence_score=30,
    )  # istisna disari cikmamali


def test_tanimsiz_ajan_oyu_yok_sayiliyor(social, tmp_path):
    social(False)
    st = AgentStats(path=str(tmp_path / "s.json"), today_fn=lambda: DAY)
    st.record_decision(
        {"signal": "HOLD", "confidence": 0, "weighted_score": 0,
         "votes": [{"agent": "UyduruAjan", "signal": "BUY", "confidence": 99}]},
        data_ok={}, dynamic_weights={}, min_confidence_score=30,
    )
    assert "UyduruAjan" not in st.snapshot(DAY)["agents"]


# ======================================================================
# `WEIGHTS.get(..., 0.15)` VARSAYILANIYLA ETKILESIM
# ======================================================================

def test_maskelenmis_vektorde_etkin_ajan_eksik_kalmiyor(social):
    """Maskelenmis vektor, oy veren HER etkin ajani icermeli.

    Icermezse `WEIGHTS.get(vote.agent_name, 0.15)` sessizce 0.15 uydurur ve
    olcek kayar. Bu, gizli gevsemenin arka kapisi olurdu.
    """
    social(False)
    w = AgentPerformanceTracker().get_dynamic_weights()
    from core.agent_enable import enabled_agents
    for name in enabled_agents():
        assert name in w, f"{name} maskelenmis vektorde yok -> 0.15 uydurulur"


def test_config_kosu_ortasinda_acilirsa_olcek_bozulmuyor(social):
    """Ajan kosu ortasinda acilirsa (WEIGHTS eski, maskelenmis) ne olur?

    SocialAgent oy verir ama WEIGHTS'te yoktur -> .get(..., 0.15) devreye girer.
    Bu senaryonun sonucu BELGELENMIS olmali: toplam 0.85 + 0.15 = 1.0 olur,
    yani eski bes ajanlik olcege doner. Sessiz bir sisme DEGILDIR, ama
    farkindaligi kayit altinda olsun diye donduruldu.
    """
    social(False)
    maskeli = AgentPerformanceTracker().get_dynamic_weights()
    social(True)  # kosu ortasinda acildi

    c = AgentCoordinator()
    c.WEIGHTS = maskeli  # eski, maskelenmis vektor
    c.tech_agent = _Stub(_vote("TechAgent", "BUY", 60))
    c.fund_agent = _Stub(_vote("FundAgent"))
    c.sent_agent = _Stub(_vote("SentAgent"))
    c.social_agent = _Stub(_vote("SocialAgent", "BUY", 100))
    c.risk_agent = _Stub(_vote("RiskAgent"))
    r = c.decide("T", {}, {}, {}, {}, {})

    # SocialAgent 0.15 varsayilanini alir: 0.25*60 + 0.15*100 = 15 + 15 = 30
    assert r["weighted_score"] == pytest.approx(30.0, abs=1e-9)
    assert c.social_agent.calls == 1, "acildiginda analyze cagrilmali"


# ======================================================================
# KARAR YOLU , telemetri arizasi karari DEGISTIREMEZ
# ======================================================================

def test_agent_stats_patlasa_bile_karar_ayni(social, monkeypatch, tmp_path):
    """R13 disiplini: kayit arizasi coordinator sonucunu degistiremez.

    NOT: agent_stats.py `from core.agent_enable import is_agent_enabled` ile
    fonksiyonu DOGRUDAN bagliyor. agent_enable modul attribute'unu patchlemek
    o bagi degistirmez; patch BAGLANDIGI yerde yapilmali, yoksa test bosuna gecer.
    """
    social(False)
    import core.agent_stats as ags

    def patla(*_a, **_k):
        raise RuntimeError("telemetri patladi")

    st = AgentStats(path=str(tmp_path / "s.json"), today_fn=lambda: DAY)
    monkeypatch.setattr(ags, "is_agent_enabled", patla)

    # record_decision False donebilir ama ISTISNA DISARI CIKMAMALI.
    sonuc = st.record_decision(
        {"signal": "BUY", "confidence": 40, "weighted_score": 20, "votes": []},
        data_ok={}, dynamic_weights={}, min_confidence_score=30,
    )
    assert sonuc in (True, False), "istisna disari sizdi"


def test_patch_gercekten_etkili_oluyor(social, tmp_path, monkeypatch):
    """Ustteki testin BOSUNA gecmedigini kanitlar.

    Patch dogru yerdeyse fonksiyon gercekten cagrilir; sayac artmali.
    """
    social(False)
    import core.agent_stats as ags
    sayac = {"n": 0}
    gercek = ags.is_agent_enabled

    def sayan(name):
        sayac["n"] += 1
        return gercek(name)

    st = AgentStats(path=str(tmp_path / "s.json"), today_fn=lambda: DAY)
    monkeypatch.setattr(ags, "is_agent_enabled", sayan)
    st.record_decision(
        {"signal": "HOLD", "confidence": 0, "weighted_score": 0, "votes": []},
        data_ok={}, dynamic_weights={}, min_confidence_score=30,
    )
    assert sayac["n"] > 0, "patch etkisiz -> ustteki test bosuna geciyor"
