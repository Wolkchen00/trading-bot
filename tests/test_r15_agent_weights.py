"""R15 , ajan maskeleme, cokme duzeltmesi ve olcek korumasi.

Kanit maddeleri RF-PLAN-4.md R15 PROOF bolumunden birebir:
  (a) SocialAgent kapaliyken decide() IndexError firlatmiyor; RiskAgent ada gore
      bulunuyor; RiskAgent oyu 0 ya da 2 oldugunda fail-closed.
  (b) IKI invaryant birden: active_sum dogru VE her etkin ajanin agirligi
      maskeleme oncesiyle BIREBIR ayni (yeniden dagitim yakalaniyor).
  (c) DONMUS ALTIN CIKTI regresyonu, R15 ONCESI davranisa karsi.
  (d) MIN_TRADES_FOR_EVAL = 5 gecis siniri (4 ve 5 cozumlenmis ornek).
  (e) analyze_social cagri sayaci 0 VE time.sleep cagri sayaci 0.
  (f) DISABLED_BY_POLICY != SOURCE_UNAVAILABLE, ajan_raporu ciktisinda ayri.
  (g) Eski semali agent_stats.json goc ediyor, cokmuyor.
  (h) Entegrasyon: gercek stock_bot agirlik atama yolundan gecen test.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

import config
from core import agent_enable
from core.agent_coordinator import AgentCoordinator, AgentVote, RiskAgent
from core.agent_enable import (
    ALL_AGENTS,
    enabled_agents,
    is_agent_enabled,
    mask_weights,
    masked_weight_total,
)
from core.agent_performance import AgentPerformanceTracker
from core.agent_stats import AgentStats, SCHEMA_VERSION

GOLDEN = json.loads(
    (Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "r15_golden.json")
    .read_text(encoding="utf-8")
)

# Uretimde SocialAgent HER ZAMAN HOLD/0 oyu veriyor (olculdu: Reddit HTTP 403,
# Nitter yok -> social_score 0 -> HOLD, confidence min(0*2,100) = 0). Bu yuzden
# ajanin GERCEK uretim etkisi olan senaryolar bunlar; maskeleme bunlarda karar
# alanlarini DEGISTIRMEMELIDIR.
SOCIAL_HOLD_SENARYOLARI = tuple(
    ad for ad, s in GOLDEN["senaryolar"].items()
    if s["votes"]["SocialAgent"]["signal"] == "HOLD"
)

# Karar alanlari: bunlar degisirse davranis kaymistir. hold_count bilincli olarak
# DISARIDA: kapali ajan HOLD oyu atmiyor, ama hold_count'un coordinator disinda
# tuketicisi yok (yalniz log ozeti ve sonuc sozlugu) ve cogunluk buy/sell
# sayaclarina bakiyor.
KARAR_ALANLARI = (
    "signal", "confidence", "weighted_score", "majority", "risk_veto",
    "buy_count", "sell_count",
)


@pytest.fixture
def social(monkeypatch):
    """SocialAgent'i acip kapatan yardimci."""
    def _set(enabled: bool):
        monkeypatch.setitem(config.AGENT_CONFIG, "social_agent_enabled", enabled)
    return _set


def _vote(name, signal, confidence, short_boost=0):
    v = AgentVote(name, signal, confidence, "test")
    if short_boost:
        v.short_boost = short_boost
    return v


class _Stub:
    def __init__(self, vote):
        self._vote = vote
        self.calls = 0

    def analyze(self, _data):
        self.calls += 1
        return self._vote


def _coordinator(weights, votes):
    c = AgentCoordinator()
    c.WEIGHTS = dict(weights)
    c.tech_agent = _Stub(votes["TechAgent"])
    c.fund_agent = _Stub(votes["FundAgent"])
    c.sent_agent = _Stub(votes["SentAgent"])
    c.social_agent = _Stub(votes["SocialAgent"])
    c.risk_agent = _Stub(votes["RiskAgent"])
    return c


def _senaryo_votes(ad):
    v = GOLDEN["senaryolar"][ad]["votes"]
    return {
        name: _vote(name, d["signal"], d["confidence"], d.get("short_boost", 0))
        for name, d in v.items()
    }


def _senaryo_weights(ad):
    return GOLDEN["senaryolar"][ad]["weights"]


# ======================================================================
# (a) COKME DUZELTMESI , eski `votes[4]` her kararda IndexError firlatirdi
# ======================================================================

def test_a_kapaliyken_indexerror_yok(social):
    """SocialAgent oy kumesinden cikinca liste 4'e duser.

    R15 oncesi `risk_vote = votes[4]` bu noktada IndexError firlatiyordu ve bot
    HER KARARDA cokuyordu. Hicbir invaryant degerlendirilmeden.
    """
    social(False)
    for ad in SOCIAL_HOLD_SENARYOLARI:
        c = _coordinator(_senaryo_weights(ad), _senaryo_votes(ad))
        r = c.decide("T", {}, {}, {}, {}, {})  # IndexError firlatmamali
        assert r["signal"] in ("BUY", "SELL", "HOLD")
        assert len(r["votes"]) == 4, "kapali ajan oy kumesinde olmamali"


def test_a_riskagent_ada_gore_bulunuyor(social):
    """RiskAgent konumu degisse bile veto calismali."""
    social(False)
    votes = {
        "TechAgent": _vote("TechAgent", "BUY", 90),
        "FundAgent": _vote("FundAgent", "BUY", 80),
        "SentAgent": _vote("SentAgent", "HOLD", 0),
        "SocialAgent": _vote("SocialAgent", "HOLD", 0),
        "RiskAgent": _vote("RiskAgent", "SELL", 30),
    }
    c = _coordinator({n: 0.2 for n in ALL_AGENTS}, votes)
    r = c.decide("T", {}, {}, {}, {}, {})
    assert r["risk_veto"] is True
    assert r["signal"] == "HOLD", "RiskAgent SELL, BUY'i veto etmeli"


def test_a_riskagent_yoksa_fail_closed(social):
    """RiskAgent oyu KAYBOLURSA karar sessizce surdurulemez.

    Veto, BUY'i durduran tek mekanizma. Yoklugu 'veto yok' demek DEGILDIR.
    """
    social(False)
    c = _coordinator(
        {n: 0.2 for n in ALL_AGENTS},
        {n: _vote(n, "HOLD", 0) for n in ALL_AGENTS},
    )
    c.risk_agent = _Stub(_vote("BaskaAjan", "HOLD", 0))  # RiskAgent adi yok
    with pytest.raises(RuntimeError, match="RiskAgent"):
        c.decide("T", {}, {}, {}, {}, {})


def test_a_riskagent_iki_taneyse_fail_closed(social):
    """Iki RiskAgent oyu da belirsizlik; hangisinin vetosu gecerli?"""
    social(False)
    c = _coordinator(
        {n: 0.2 for n in ALL_AGENTS},
        {n: _vote(n, "HOLD", 0) for n in ALL_AGENTS},
    )
    # Tech'i de RiskAgent adiyla dondur -> iki RiskAgent oyu
    c.tech_agent = _Stub(_vote(RiskAgent.NAME, "SELL", 50))
    with pytest.raises(RuntimeError, match="RiskAgent"):
        c.decide("T", {}, {}, {}, {}, {})


# ======================================================================
# (b) IKI INVARYANT , biri tek basina yetmez
# ======================================================================

@pytest.mark.parametrize("normalized", [
    {"TechAgent": 0.25, "FundAgent": 0.20, "SentAgent": 0.20,
     "SocialAgent": 0.15, "RiskAgent": 0.20},
    # Performansa gore kaymis vektor: SocialAgent payi 0.15 DEGIL.
    # Sabit 0.85 varsayimi burada COKER.
    {"TechAgent": 0.2366231853, "FundAgent": 0.20, "SentAgent": 0.20,
     "SocialAgent": 0.1633768147, "RiskAgent": 0.20},
    {"TechAgent": 0.30, "FundAgent": 0.18, "SentAgent": 0.22,
     "SocialAgent": 0.12, "RiskAgent": 0.18},
])
def test_b_invaryant_1_aktif_toplam(social, normalized):
    """active_sum == 1.0 - maskeleme oncesi kapali agirlik. SABIT 0.85 DEGIL."""
    social(False)
    masked = mask_weights(normalized)
    dusen = masked_weight_total(normalized)
    assert dusen == pytest.approx(normalized["SocialAgent"], abs=1e-12)
    assert sum(masked.values()) == pytest.approx(1.0 - dusen, abs=1e-9)


@pytest.mark.parametrize("normalized", [
    {"TechAgent": 0.25, "FundAgent": 0.20, "SentAgent": 0.20,
     "SocialAgent": 0.15, "RiskAgent": 0.20},
    {"TechAgent": 0.2366231853, "FundAgent": 0.20, "SentAgent": 0.20,
     "SocialAgent": 0.1633768147, "RiskAgent": 0.20},
    {"TechAgent": 0.30, "FundAgent": 0.18, "SentAgent": 0.22,
     "SocialAgent": 0.12, "RiskAgent": 0.18},
])
def test_b_invaryant_2_her_ajan_payi_korunuyor(social, normalized):
    """Her etkin ajanin agirligi maskeleme oncesiyle BIREBIR ayni olmali.

    Invaryant 1 tek basina YETMEZ: toplam korunurken paylar kendi aralarinda
    yeniden dagitilabilir (Tech 0.25 -> 0.30, Fund 0.20 -> 0.15, toplam yine
    0.85). Bu test onu kapatir.
    """
    social(False)
    masked = mask_weights(normalized)
    assert "SocialAgent" not in masked
    for name, before in normalized.items():
        if name == "SocialAgent":
            continue
        assert masked[name] == pytest.approx(before, abs=1e-12), (
            f"{name} agirligi degismis: {before} -> {masked[name]}"
        )


def test_b_sabit_085_varsayimi_yanlis_olurdu(social):
    """Performansa gore kaymis vektorde toplam 0.85 DEGIL.

    Bu test, plandaki ilk (yanlis) invaryantin neden reddedildigini dondurur.
    """
    social(False)
    normalized = {
        "TechAgent": 0.2366231853, "FundAgent": 0.20, "SentAgent": 0.20,
        "SocialAgent": 0.1633768147, "RiskAgent": 0.20,
    }
    toplam = sum(mask_weights(normalized).values())
    assert toplam != pytest.approx(0.85, abs=1e-6)
    assert toplam == pytest.approx(0.8366231853, abs=1e-9)


def test_b_renormalizasyon_YAPILMIYOR(social):
    """Maskelenmis vektorun toplami 1.0'a SISIRILMEMELI.

    Sisirilirse ws x1.176 buyur ve `ws > 15` esigi gizlice gevser.
    """
    social(False)
    normalized = {
        "TechAgent": 0.25, "FundAgent": 0.20, "SentAgent": 0.20,
        "SocialAgent": 0.15, "RiskAgent": 0.20,
    }
    assert sum(mask_weights(normalized).values()) != pytest.approx(1.0, abs=1e-6)


def test_b_acikken_hicbir_sey_suzulmuyor(social):
    social(True)
    normalized = {
        "TechAgent": 0.25, "FundAgent": 0.20, "SentAgent": 0.20,
        "SocialAgent": 0.15, "RiskAgent": 0.20,
    }
    assert mask_weights(normalized) == normalized
    assert masked_weight_total(normalized) == 0.0
    assert enabled_agents() == ALL_AGENTS


# ======================================================================
# (c) DONMUS ALTIN CIKTI REGRESYONU
# ======================================================================

@pytest.mark.parametrize("ad", sorted(GOLDEN["senaryolar"]))
def test_c_acikken_altin_cikti_birebir(social, ad):
    """Ajan ACIKKEN her senaryo R15 ONCESI degerlere birebir esit olmali.

    Karsilastirma iki YENI yol arasinda degil, degisiklikten ONCEKI donmus
    davranisa karsi yapilir; aksi halde ikisi birden kaydiginda test yesil yanar.
    """
    social(True)
    c = _coordinator(_senaryo_weights(ad), _senaryo_votes(ad))
    r = c.decide("T", {}, {}, {}, {}, {})
    beklenen = GOLDEN["senaryolar"][ad]["beklenen"]
    for alan in ("signal", "confidence", "weighted_score", "majority",
                 "risk_veto", "buy_count", "sell_count", "hold_count"):
        assert r[alan] == beklenen[alan], f"{ad}.{alan} kaydi"


@pytest.mark.parametrize("ad", sorted(SOCIAL_HOLD_SENARYOLARI))
def test_c_kapaliyken_karar_alanlari_degismiyor(social, ad):
    """Uretimdeki gercek durum: SocialAgent HOLD/0 oyu veriyor.

    Maskeleme bu senaryolarda signal/confidence/weighted_score/majority/
    risk_veto/buy_count/sell_count alanlarini DEGISTIRMEMELIDIR. Degisirse
    olcek kaymistir ve esikler gevsemistir.
    """
    social(False)
    c = _coordinator(_senaryo_weights(ad), _senaryo_votes(ad))
    r = c.decide("T", {}, {}, {}, {}, {})
    beklenen = GOLDEN["senaryolar"][ad]["beklenen"]
    for alan in KARAR_ALANLARI:
        assert r[alan] == beklenen[alan], (
            f"{ad}.{alan}: {beklenen[alan]} -> {r[alan]} (olcek kaymis)"
        )


def test_c_ws_tam_sinir_HOLD_kalmali(social):
    """En kritik tek test: ws = 15.0 TAM SINIR.

    Kod `weighted_score > 15` istiyor, yani 15.0 HOLD'dur. Renormalizasyon
    yapilsaydi ws 17.65 olur, BUY'a doner ve confidence 35.3 ile paper esigi
    30'u gecerdi. Ayni kanit, farkli karar , gizli gevseme tam olarak budur.
    """
    for acik in (True, False):
        social(acik)
        c = _coordinator(_senaryo_weights("ws_tam_arti_15"),
                         _senaryo_votes("ws_tam_arti_15"))
        r = c.decide("T", {}, {}, {}, {}, {})
        assert r["weighted_score"] == 15.0, f"acik={acik}"
        assert r["signal"] == "HOLD", f"acik={acik}: sinir gevsemis"


def test_c_ws_eksi_tam_sinir_HOLD_kalmali(social):
    for acik in (True, False):
        social(acik)
        c = _coordinator(_senaryo_weights("ws_tam_eksi_15"),
                         _senaryo_votes("ws_tam_eksi_15"))
        r = c.decide("T", {}, {}, {}, {}, {})
        assert r["weighted_score"] == -15.0
        assert r["signal"] == "HOLD"


# ======================================================================
# (d) MIN_TRADES_FOR_EVAL = 5 GECIS SINIRI
# ======================================================================

def _tracker_with(n):
    t = AgentPerformanceTracker()
    now = datetime.now()
    t.predictions = {
        name: [
            {
                "timestamp": (now - timedelta(days=1)).isoformat(),
                "correct": (i % 2 == 0),
                "pnl": 10.0 if i % 2 == 0 else -5.0,
                "signal": "BUY",
            }
            for i in range(n)
        ]
        for name in AgentPerformanceTracker.DEFAULT_WEIGHTS
    }
    return t


@pytest.mark.parametrize("n", [4, 5])
def test_d_gecis_siniri_altin_cikti(social, n):
    """4 ornekte DEFAULT_WEIGHTS dali, 5'te hesaplanan dal (agent_performance.py:177).

    Ajan ACIKKEN iki dal da R15 oncesi degerlere birebir esit olmali.
    """
    social(True)
    w = _tracker_with(n).get_dynamic_weights()
    beklenen = GOLDEN["agirlik_gecis_siniri"][f"cozumlenmis_{n}"]
    assert sorted(w) == sorted(beklenen)
    for name, deger in beklenen.items():
        assert w[name] == pytest.approx(deger, abs=1e-9)


@pytest.mark.parametrize("n", [4, 5])
def test_d_gecis_siniri_maskelenince_paylar_korunuyor(social, n):
    """Kapaliyken: SocialAgent suzulur, kalanlarin paylari AYNEN kalir."""
    social(False)
    w = _tracker_with(n).get_dynamic_weights()
    oncesi = GOLDEN["agirlik_gecis_siniri"][f"cozumlenmis_{n}"]
    assert "SocialAgent" not in w
    for name, deger in oncesi.items():
        if name == "SocialAgent":
            continue
        assert w[name] == pytest.approx(deger, abs=1e-9), f"{name} kaydi"
    assert sum(w.values()) == pytest.approx(
        1.0 - oncesi["SocialAgent"], abs=1e-9
    )


def test_d_gecis_sinirinda_sabit_085_tutmuyor(social):
    """5 ornekte SocialAgent payi 0.15 degil 0.1633; toplam 0.85 DEGIL."""
    social(False)
    w = _tracker_with(5).get_dynamic_weights()
    assert sum(w.values()) != pytest.approx(0.85, abs=1e-6)


# ======================================================================
# (e) OLU UYKU , analyze_social VE time.sleep cagrilmamali
# ======================================================================

def test_e_kapaliyken_analyze_social_cagrilmiyor(social):
    """Skoru sifirlamak yetmez: asli maliyet 12 istek + 12 saniye uyku."""
    social(False)
    votes = {n: _vote(n, "HOLD", 0) for n in ALL_AGENTS}
    c = _coordinator({n: 0.2 for n in ALL_AGENTS}, votes)
    c.decide("T", {}, {}, {}, {}, {})
    assert c.social_agent.calls == 0, "kapali ajanin analyze()'i cagrildi"
    assert c.tech_agent.calls == 1
    assert c.risk_agent.calls == 1


def test_e_acikken_analyze_social_cagriliyor(social):
    social(True)
    votes = {n: _vote(n, "HOLD", 0) for n in ALL_AGENTS}
    c = _coordinator({n: 0.2 for n in ALL_AGENTS}, votes)
    c.decide("T", {}, {}, {}, {}, {})
    assert c.social_agent.calls == 1


def test_e_stock_bot_yolunda_uyku_yok(social, monkeypatch):
    """stock_bot'un cagri yerinde de ne analyze_social ne time.sleep kosmali."""
    social(False)
    import core.social_sentiment as ss

    uyku = {"n": 0}
    analiz = {"n": 0}
    monkeypatch.setattr(ss.time, "sleep", lambda *_: uyku.__setitem__("n", uyku["n"] + 1))
    monkeypatch.setattr(
        ss.SocialSentimentAnalyzer, "analyze_social",
        lambda self, symbol: analiz.__setitem__("n", analiz["n"] + 1) or {},
    )
    # stock_bot.py'deki kosul birebir bu; ajan kapaliyken dal HIC girilmemeli.
    from core.agent_enable import is_agent_enabled as gercek_kontrol
    social_data = {"social_score": 0}
    if gercek_kontrol("SocialAgent"):
        social_data = ss.SocialSentimentAnalyzer().analyze_social("AAPL")
    assert analiz["n"] == 0
    assert uyku["n"] == 0
    assert social_data == {"social_score": 0}


# ======================================================================
# (f) DISABLED_BY_POLICY != SOURCE_UNAVAILABLE
# ======================================================================

def _decision_without_social():
    """Kapali ajanin oy vermedigi gercekci bir karar sozlugu."""
    return {
        "signal": "HOLD",
        "confidence": 12.0,
        "weighted_score": 6.0,
        "majority": False,
        "risk_veto": False,
        "votes": [
            {"agent": "TechAgent", "signal": "BUY", "confidence": 40},
            {"agent": "FundAgent", "signal": "HOLD", "confidence": 0},
            {"agent": "SentAgent", "signal": "HOLD", "confidence": 0},
            {"agent": "RiskAgent", "signal": "HOLD", "confidence": 0},
        ],
    }


def test_f_kapali_ajan_disabled_sayiliyor(social, tmp_path):
    """Politika ile kapali != kaynak sustu. Ikisi ayri sayacta olmali."""
    social(False)
    st = AgentStats(path=str(tmp_path / "s.json"))
    st.record_decision(
        _decision_without_social(),
        data_ok={"TechAgent": True, "FundAgent": False,
                 "SentAgent": True, "RiskAgent": True},
        dynamic_weights={"TechAgent": 0.25},
        min_confidence_score=30,
    )
    gun = st.days[st._today()]
    social_ok = gun["agents"]["SocialAgent"]["data_ok"]
    fund_ok = gun["agents"]["FundAgent"]["data_ok"]

    assert social_ok["disabled"] == 1, "kapali ajan DISABLED sayilmali"
    assert social_ok["false"] == 0, "kapali ajan 'kaynak sustu' sayilmamali"
    # FundAgent kaynak sustu (veri yok) ,  bambaska bir durum
    assert fund_ok["false"] == 1
    assert fund_ok["disabled"] == 0


def test_f_acik_ajan_disabled_sayilmiyor(social, tmp_path):
    social(True)
    st = AgentStats(path=str(tmp_path / "s.json"))
    d = _decision_without_social()
    d["votes"].append({"agent": "SocialAgent", "signal": "HOLD", "confidence": 0})
    st.record_decision(
        d,
        data_ok={n: False for n in ALL_AGENTS},
        dynamic_weights={},
        min_confidence_score=30,
    )
    social_ok = st.days[st._today()]["agents"]["SocialAgent"]["data_ok"]
    assert social_ok["disabled"] == 0
    assert social_ok["false"] == 1, "acik ama veri yok -> 'false'"


# ======================================================================
# (g) ESKI SEMA GOCU
# ======================================================================

def test_g_eski_sema_goc_ediyor(tmp_path):
    """Surum 1 dosyasinda 'disabled' anahtari YOK; goc kayipsiz olmali."""
    eski = {
        "schema_version": 1,
        "days": {
            "2026-08-26": {
                "agents": {
                    "TechAgent": {
                        "votes": {"BUY": 3, "SELL": 1, "HOLD": 2},
                        "data_ok": {"true": 5, "false": 1},
                        "confidence_histogram": {},
                        "last_dynamic_weight": 0.25,
                    },
                },
                "coordinator": {"decisions": 6},
            }
        },
    }
    p = tmp_path / "agent_stats.json"
    p.write_text(json.dumps(eski), encoding="utf-8")

    st = AgentStats(path=str(p))  # cokmemeli
    tech = st.days["2026-08-26"]["agents"]["TechAgent"]
    assert tech["data_ok"]["true"] == 5, "eski sayac korunmali"
    assert tech["data_ok"]["false"] == 1
    assert tech["data_ok"]["disabled"] == 0, "eksik anahtar 0'a dusmeli"
    assert SCHEMA_VERSION == 2


def test_g_bozuk_dosyada_cokmuyor(tmp_path):
    p = tmp_path / "agent_stats.json"
    p.write_text("{bozuk json", encoding="utf-8")
    st = AgentStats(path=str(p))
    assert isinstance(st.days, dict)


# ======================================================================
# (h) ENTEGRASYON , gercek stock_bot agirlik atama yolu
# ======================================================================

def test_h_stock_bot_atama_yolu(social):
    """stock_bot.py:1298 `coordinator.WEIGHTS = get_dynamic_weights()` yolu.

    Coordinator sinif sabitine bakan bir test uretimde yanilir; gercek yol
    performans takipcisinden gecer ve sinif sabitini her sembolde ezer.
    """
    social(False)
    tracker = _tracker_with(5)
    c = AgentCoordinator()

    # Uretimdeki satirin birebir ayni hali:
    dynamic_weights = tracker.get_dynamic_weights()
    c.WEIGHTS = dynamic_weights

    assert "SocialAgent" not in c.WEIGHTS, (
        "gercek atama yolunda kapali ajan agirlik vektorunde kalmis"
    )
    assert sum(c.WEIGHTS.values()) < 1.0, "renormalizasyon sizmis"
    assert sum(c.WEIGHTS.values()) == pytest.approx(0.8366231853, abs=1e-9)


def test_h_atama_sonrasi_karar_olcegi_korunuyor(social):
    """Gercek atama yolundan sonra ws hala kalibre edildigi olcekte olmali."""
    social(False)
    tracker = _tracker_with(4)  # DEFAULT_WEIGHTS dali
    c = _coordinator(
        tracker.get_dynamic_weights(),
        {
            "TechAgent": _vote("TechAgent", "BUY", 60),
            "FundAgent": _vote("FundAgent", "HOLD", 0),
            "SentAgent": _vote("SentAgent", "HOLD", 0),
            "SocialAgent": _vote("SocialAgent", "HOLD", 0),
            "RiskAgent": _vote("RiskAgent", "HOLD", 0),
        },
    )
    r = c.decide("T", {}, {}, {}, {}, {})
    # Tech agirligi 0.25 kaldi -> ws = 15.0 -> `> 15` degil -> HOLD
    assert r["weighted_score"] == 15.0
    assert r["signal"] == "HOLD"


# ======================================================================
# EK , fail-closed config davranisi
# ======================================================================

def test_config_okunamazsa_kapali_sayiliyor(monkeypatch):
    """Config kaybolursa kor ajan sessizce geri acilmamali."""
    monkeypatch.setattr(agent_enable, "_agent_config", lambda: {})
    assert is_agent_enabled("SocialAgent") is False
    assert is_agent_enabled("TechAgent") is True, "yapilandirilamayan ajan hep acik"
