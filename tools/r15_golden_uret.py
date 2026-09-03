"""R15 DONMUS ALTIN CIKTI ureteci.

Bu script R15 DEGISIKLIGINDEN ONCE calistirilir ve mevcut uretim davranisini
`tests/fixtures/r15_golden.json` dosyasina dondurur. R15 sonrasi test suite'i
ayni senaryolari kosup bu donmus degerlere karsi karsilastirir.

NEDEN BOYLE: iki YENI yolu birbiriyle karsilastiran bir test, ikisi birden
kaydiginda yesil yanar. Karsilastirma degisiklikten ONCEKI davranisa karsi
yapilmalidir. (Codex Same Page Meeting Round 2 bulgusu.)

Kullanim:
    py tools/r15_golden_uret.py            # uretir ve yazar
    py tools/r15_golden_uret.py --goster   # yazmadan ekrana basar
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_coordinator import AgentCoordinator, AgentVote
from core.agent_performance import AgentPerformanceTracker

GOLDEN_PATH = ROOT / "tests" / "fixtures" / "r15_golden.json"

# Uretim varsayilan agirliklari (agent_performance.DEFAULT_WEIGHTS ile ayni).
DEFAULT_W = {
    "TechAgent": 0.25,
    "FundAgent": 0.20,
    "SentAgent": 0.20,
    "SocialAgent": 0.15,
    "RiskAgent": 0.20,
}

# Dinamik (performans sonrasi normalize) ornek bir agirlik vektoru.
# Toplami 1.0, ama paylar varsayilandan farkli ,  maskeleme sonrasi her ajanin
# payinin AYNEN korundugunu ispatlamak icin gerekli.
DYNAMIC_W = {
    "TechAgent": 0.30,
    "FundAgent": 0.18,
    "SentAgent": 0.22,
    "SocialAgent": 0.12,
    "RiskAgent": 0.18,
}


def _vote(name: str, signal: str, confidence: float, short_boost: float = 0):
    v = AgentVote(name, signal, confidence, f"golden:{signal}")
    if short_boost:
        v.short_boost = short_boost
    return v


class _StubAgent:
    """decide() icindeki gercek ajanin yerine gecer; scriptlenmis oyu doner."""

    def __init__(self, vote: AgentVote):
        self._vote = vote

    def analyze(self, _data):
        return self._vote


def _run(weights: dict, votes: dict) -> dict:
    """Verilen agirlik ve oylarla decide() kosar, sonucu dondurur."""
    c = AgentCoordinator()
    c.WEIGHTS = dict(weights)
    c.tech_agent = _StubAgent(votes["TechAgent"])
    c.fund_agent = _StubAgent(votes["FundAgent"])
    c.sent_agent = _StubAgent(votes["SentAgent"])
    c.social_agent = _StubAgent(votes["SocialAgent"])
    c.risk_agent = _StubAgent(votes["RiskAgent"])

    r = c.decide("GOLD", {}, {}, {}, {}, {})
    return {
        "signal": r["signal"],
        "confidence": r["confidence"],
        "weighted_score": r["weighted_score"],
        "majority": r["majority"],
        "risk_veto": r["risk_veto"],
        "buy_count": r["buy_count"],
        "sell_count": r["sell_count"],
        "hold_count": r["hold_count"],
    }


def _senaryolar() -> list:
    """Gizli gevsemeyi yakalayabilecek senaryo kumesi.

    Zorunlu kapsam (Codex Round 2/3): ws=+-15 TAM sinirlari, cogunluk var/yok,
    RiskAgent veto, VIX short boost, confidence 100 doygunlugu, ve SocialAgent'in
    uretimdeki gercek hali (HOLD/0) ile acik hali.
    """
    H = lambda n: _vote(n, "HOLD", 0)
    out = []

    # 1) ws = +15 TAM SINIR. Tech BUY conf 60, agirlik 0.25 -> ws = 15.
    #    Kod `> 15` istiyor, yani bu HOLD olmali. Renormalizasyon bu satiri
    #    BUY'a cevirirdi ,  testin kalbi burasi.
    out.append(("ws_tam_arti_15", DEFAULT_W, {
        "TechAgent": _vote("TechAgent", "BUY", 60),
        "FundAgent": H("FundAgent"), "SentAgent": H("SentAgent"),
        "SocialAgent": H("SocialAgent"), "RiskAgent": H("RiskAgent"),
    }))

    # 2) ws = -15 TAM SINIR (`< -15` istiyor) -> HOLD.
    out.append(("ws_tam_eksi_15", DEFAULT_W, {
        "TechAgent": _vote("TechAgent", "SELL", 60),
        "FundAgent": H("FundAgent"), "SentAgent": H("SentAgent"),
        "SocialAgent": H("SocialAgent"), "RiskAgent": H("RiskAgent"),
    }))

    # 3) Sinirin bir tik ustu -> BUY.
    out.append(("ws_sinir_ustu_buy", DEFAULT_W, {
        "TechAgent": _vote("TechAgent", "BUY", 61),
        "FundAgent": H("FundAgent"), "SentAgent": H("SentAgent"),
        "SocialAgent": H("SocialAgent"), "RiskAgent": H("RiskAgent"),
    }))

    # 4) Sinirin bir tik alti -> SELL.
    out.append(("ws_sinir_alti_sell", DEFAULT_W, {
        "TechAgent": _vote("TechAgent", "SELL", 61),
        "FundAgent": H("FundAgent"), "SentAgent": H("SentAgent"),
        "SocialAgent": H("SocialAgent"), "RiskAgent": H("RiskAgent"),
    }))

    # 5) COGUNLUK BUY (3 ajan BUY) -> majority True, confidence x1.2.
    out.append(("cogunluk_buy", DEFAULT_W, {
        "TechAgent": _vote("TechAgent", "BUY", 50),
        "FundAgent": _vote("FundAgent", "BUY", 40),
        "SentAgent": _vote("SentAgent", "BUY", 45),
        "SocialAgent": H("SocialAgent"), "RiskAgent": H("RiskAgent"),
    }))

    # 6) COGUNLUK SELL.
    out.append(("cogunluk_sell", DEFAULT_W, {
        "TechAgent": _vote("TechAgent", "SELL", 50),
        "FundAgent": _vote("FundAgent", "SELL", 40),
        "SentAgent": _vote("SentAgent", "SELL", 45),
        "SocialAgent": H("SocialAgent"), "RiskAgent": H("RiskAgent"),
    }))

    # 7) RISK VETO: on sinyal BUY, RiskAgent SELL -> HOLD ve confidence x0.5.
    out.append(("risk_veto", DEFAULT_W, {
        "TechAgent": _vote("TechAgent", "BUY", 90),
        "FundAgent": _vote("FundAgent", "BUY", 80),
        "SentAgent": H("SentAgent"), "SocialAgent": H("SocialAgent"),
        "RiskAgent": _vote("RiskAgent", "SELL", 30),
    }))

    # 8) VIX SHORT BOOST: nihai SELL + RiskAgent.short_boost.
    out.append(("vix_short_boost", DEFAULT_W, {
        "TechAgent": _vote("TechAgent", "SELL", 70),
        "FundAgent": _vote("FundAgent", "SELL", 60),
        "SentAgent": H("SentAgent"), "SocialAgent": H("SocialAgent"),
        "RiskAgent": _vote("RiskAgent", "SELL", 40, short_boost=15),
    }))

    # 9) CONFIDENCE DOYGUNLUGU: hepsi BUY conf 100 -> ws yuksek, conf 100'de kapanir.
    out.append(("confidence_doygunluk", DEFAULT_W, {
        "TechAgent": _vote("TechAgent", "BUY", 100),
        "FundAgent": _vote("FundAgent", "BUY", 100),
        "SentAgent": _vote("SentAgent", "BUY", 100),
        "SocialAgent": _vote("SocialAgent", "BUY", 100),
        "RiskAgent": _vote("RiskAgent", "BUY", 100),
    }))

    # 10) HEPSI HOLD.
    out.append(("hepsi_hold", DEFAULT_W, {
        n: H(n) for n in DEFAULT_W
    }))

    # 11) SocialAgent'in URETIMDEKI GERCEK HALI: HOLD conf 0.
    #     R15 sonrasi ajan KAPALIYKEN sonuc buna BIREBIR esit olmali.
    out.append(("social_uretim_hali_hold0", DEFAULT_W, {
        "TechAgent": _vote("TechAgent", "BUY", 55),
        "FundAgent": _vote("FundAgent", "SELL", 20),
        "SentAgent": _vote("SentAgent", "BUY", 30),
        "SocialAgent": _vote("SocialAgent", "HOLD", 0),
        "RiskAgent": H("RiskAgent"),
    }))

    # 12) SocialAgent ACIK ve OY VERIYOR ,  env ile acildiginda eski davranisin
    #     birebir dondugunu ispatlar.
    out.append(("social_acik_oy_veriyor", DEFAULT_W, {
        "TechAgent": _vote("TechAgent", "BUY", 55),
        "FundAgent": _vote("FundAgent", "SELL", 20),
        "SentAgent": _vote("SentAgent", "BUY", 30),
        "SocialAgent": _vote("SocialAgent", "BUY", 80),
        "RiskAgent": H("RiskAgent"),
    }))

    # 13) KARSIT SINYALLER ,  yon-farkinda guvenin dogru calistigini dondurur.
    out.append(("karsit_sinyaller", DEFAULT_W, {
        "TechAgent": _vote("TechAgent", "BUY", 70),
        "FundAgent": _vote("FundAgent", "SELL", 70),
        "SentAgent": _vote("SentAgent", "BUY", 40),
        "SocialAgent": H("SocialAgent"),
        "RiskAgent": _vote("RiskAgent", "SELL", 20),
    }))

    # 14-17) AYNI senaryolarin DINAMIK agirlik vektoruyle hali. Maskeleme sonrasi
    #        her ajanin payinin AYNEN korundugunu ispatlamak icin sart:
    #        sabit 0.85 invaryanti bu vektorde tutmaz (1 - 0.12 = 0.88).
    out.append(("dyn_ws_tam_sinir", DYNAMIC_W, {
        "TechAgent": _vote("TechAgent", "BUY", 50),
        "FundAgent": H("FundAgent"), "SentAgent": H("SentAgent"),
        "SocialAgent": H("SocialAgent"), "RiskAgent": H("RiskAgent"),
    }))
    out.append(("dyn_cogunluk_buy", DYNAMIC_W, {
        "TechAgent": _vote("TechAgent", "BUY", 50),
        "FundAgent": _vote("FundAgent", "BUY", 40),
        "SentAgent": _vote("SentAgent", "BUY", 45),
        "SocialAgent": H("SocialAgent"), "RiskAgent": H("RiskAgent"),
    }))
    out.append(("dyn_social_uretim_hali", DYNAMIC_W, {
        "TechAgent": _vote("TechAgent", "BUY", 55),
        "FundAgent": _vote("FundAgent", "SELL", 20),
        "SentAgent": _vote("SentAgent", "BUY", 30),
        "SocialAgent": _vote("SocialAgent", "HOLD", 0),
        "RiskAgent": H("RiskAgent"),
    }))
    out.append(("dyn_risk_veto", DYNAMIC_W, {
        "TechAgent": _vote("TechAgent", "BUY", 90),
        "FundAgent": _vote("FundAgent", "BUY", 80),
        "SentAgent": H("SentAgent"), "SocialAgent": H("SocialAgent"),
        "RiskAgent": _vote("RiskAgent", "SELL", 30),
    }))

    # 18-20) SocialAgent HOLD ama conf > 0 ,  HOLD katkisi 0 oldugu icin ws'yi
    #        degistirmemeli; maskeleme sonrasi da degismemeli.
    out.append(("social_hold_conf_var", DEFAULT_W, {
        "TechAgent": _vote("TechAgent", "BUY", 61),
        "FundAgent": H("FundAgent"), "SentAgent": H("SentAgent"),
        "SocialAgent": _vote("SocialAgent", "HOLD", 95),
        "RiskAgent": H("RiskAgent"),
    }))
    out.append(("tek_ajan_sell_sinir", DEFAULT_W, {
        "TechAgent": H("TechAgent"),
        "FundAgent": _vote("FundAgent", "SELL", 75),
        "SentAgent": H("SentAgent"), "SocialAgent": H("SocialAgent"),
        "RiskAgent": H("RiskAgent"),
    }))
    out.append(("veto_ama_on_sinyal_sell", DEFAULT_W, {
        "TechAgent": _vote("TechAgent", "SELL", 80),
        "FundAgent": _vote("FundAgent", "SELL", 70),
        "SentAgent": H("SentAgent"), "SocialAgent": H("SocialAgent"),
        "RiskAgent": _vote("RiskAgent", "SELL", 50),
    }))

    return out


def _agirlik_altin() -> dict:
    """MIN_TRADES_FOR_EVAL = 5 gecis sinirini dondurur.

    4 cozumlenmis ornekte DEFAULT_WEIGHTS daline, 5'te hesaplanan dala duser
    (agent_performance.py:177-179). Iki dal da donduruluyor.
    """
    from datetime import datetime, timedelta
    import tempfile
    import os

    out = {}
    for n in (4, 5):
        tmpdir = tempfile.mkdtemp(prefix=f"r15w{n}_")
        os.environ["_R15_TMP"] = tmpdir
        t = AgentPerformanceTracker()
        # Gecmisi dogrudan enjekte et; disk yoluna bagimli olma.
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
            for name in DEFAULT_W
        }
        w = t.get_dynamic_weights()
        out[f"cozumlenmis_{n}"] = {k: round(v, 10) for k, v in sorted(w.items())}
        out[f"cozumlenmis_{n}_toplam"] = round(sum(w.values()), 10)
    return out


def uret() -> dict:
    senaryolar = {}
    for ad, weights, votes in _senaryolar():
        senaryolar[ad] = {
            "weights": {k: round(v, 10) for k, v in sorted(weights.items())},
            "votes": {
                k: {
                    "signal": v.signal,
                    "confidence": v.confidence,
                    "short_boost": getattr(v, "short_boost", 0),
                }
                for k, v in sorted(votes.items())
            },
            "beklenen": _run(weights, votes),
        }
    return {
        "aciklama": (
            "R15 ONCESI uretim davranisi. R15 sonrasi testler bu degerlere karsi "
            "karsilastirilir. Bu dosya ELLE DUZENLENMEZ; degisirse davranis kaymistir."
        ),
        "sema_surumu": 1,
        "senaryolar": senaryolar,
        "agirlik_gecis_siniri": _agirlik_altin(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--goster", action="store_true", help="yazmadan ekrana bas")
    args = ap.parse_args()

    data = uret()
    metin = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)

    if args.goster:
        print(metin)
        return 0

    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(metin + "\n", encoding="utf-8")
    print(f"Yazildi: {GOLDEN_PATH}")
    print(f"Senaryo sayisi: {len(data['senaryolar'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
