"""R15 , ajan etkinlik cozucusu (TEK KAYNAK).

Hem `AgentCoordinator` hem `AgentPerformanceTracker` bu moduldan gecer. Iki
ayri yerde ayri mantik olursa biri yesil yanarken digeri uretimde baska is yapar
(Codex Same Page Meeting Round 1 bulgusu: `stock_bot.py:1298` coordinator'in
sinif sabitini her sembolde eziyor).

MASKELEME, RENORMALIZASYON DEGIL
--------------------------------
`weighted_score` HAM bir toplamdir ve hicbir yerde toplam agirliga bolunmez
(`agent_coordinator.py:419-423`). Esikler MUTLAKTIR: `ws > 15` ve
`confidence = abs(ws) * 2.0`. Bu yuzden kapali bir ajanin payini kalanlara
DAGITMAK, ayni kanittan daha yuksek skor uretir ve her giris kapisini gizlice
gevsetir. Olculmus ornek: tek basina TechAgent BUY conf 60 -> ws = 0.25*60 = 15
-> `> 15` degil -> HOLD. Renormalize edilirse ws = 0.294*60 = 17.65 -> BUY.

Bu yuzden maskeleme = SUZME. Kalan ajanlarin paylari AYNEN korunur, toplam
1.0'in altina duser. Iki invaryant birden tutmalidir:
  1. active_sum == 1.0 - (kapali ajanlarin maskeleme oncesi agirlik toplami)
  2. her etkin ajanin agirligi maskeleme oncesiyle BIREBIR ayni
Birinci invaryant tek basina yetmez: toplam korunurken paylar kendi aralarinda
yeniden dagitilabilir (Tech 0.25 -> 0.30, Fund 0.20 -> 0.15).
"""
from __future__ import annotations

from typing import Dict, Iterable, Tuple

# Sistemdeki tum ajanlar, coordinator'daki oy sirasiyla.
ALL_AGENTS: Tuple[str, ...] = (
    "TechAgent",
    "FundAgent",
    "SentAgent",
    "SocialAgent",
    "RiskAgent",
)

# Etkinligi config ile kontrol edilen ajanlar. Burada olmayan ajan her zaman acik.
_CONFIGURABLE = {
    "SocialAgent": "social_agent_enabled",
}


def _agent_config() -> dict:
    """AGENT_CONFIG'i guvenle oku; config yuklenemezse bos sozluk don."""
    try:
        from config import AGENT_CONFIG
        return AGENT_CONFIG if isinstance(AGENT_CONFIG, dict) else {}
    except Exception:
        return {}


def is_agent_enabled(name: str) -> bool:
    """Ajan etkin mi. Config okunamazsa YAPILANDIRILABILIR ajan KAPALI sayilir.

    Fail-closed: `SocialAgent`'in veri kaynagi olculmus bicimde olu (Reddit
    HTTP 403, Nitter yok). Config kaybolursa onu sessizce geri acmak, kor bir
    ajani oy kumesine geri sokar. Yapilandirilamayan ajanlar her zaman aciktir.
    """
    key = _CONFIGURABLE.get(name)
    if key is None:
        return True
    return bool(_agent_config().get(key, False))


def enabled_agents() -> Tuple[str, ...]:
    """Etkin ajanlar, ALL_AGENTS sirasini koruyarak."""
    return tuple(n for n in ALL_AGENTS if is_agent_enabled(n))


def disabled_agents() -> Tuple[str, ...]:
    """Politika geregi kapatilmis ajanlar."""
    return tuple(n for n in ALL_AGENTS if not is_agent_enabled(n))


def mask_weights(normalized: Dict[str, float]) -> Dict[str, float]:
    """Normalize edilmis vektorden kapali ajanlari CIKARIR.

    Kalan paylar AYNEN korunur; yeniden dagitim YAPILMAZ. Cagiran, vektoru
    ONCE bes ajanlik haliyle normalize etmis olmalidir , maskeleme normalizasyondan
    SONRA gelir, aksi halde kalanlar 1.0'a sisirilir ve esikler gevser.
    """
    if not isinstance(normalized, dict):
        return {}
    return {
        name: weight
        for name, weight in normalized.items()
        if is_agent_enabled(name)
    }


def masked_weight_total(normalized: Dict[str, float]) -> float:
    """Maskelemeyle dusen toplam agirlik (invaryant 1 icin)."""
    if not isinstance(normalized, dict):
        return 0.0
    return sum(
        float(w or 0)
        for name, w in normalized.items()
        if not is_agent_enabled(name)
    )


def filter_votes(votes: Iterable) -> list:
    """Kapali ajanlarin oylarini oy kumesinden cikarir."""
    return [
        v for v in votes
        if is_agent_enabled(getattr(v, "agent_name", ""))
    ]
