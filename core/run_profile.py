"""R19 , CALISMA PROFILI: hangi karar profiliyle kosuyoruz ve kimligi nedir.

NEDEN VAR
---------
`tools/olcum_raporu.py::measured_profile()` profil adini `PAPER_AGGRESSIVE`
olarak SABITLIYORDU. Raporun kendisi bu profilin canli kanit olmadigini
soyluyor; yani mevcut paper kosusu canli kilidi acmak icin KULLANILAMAZ.

Uretici yoksa olcum kapisi KALICI olarak NOT_READY kalir, kilit hic acilmaz ve
Core Focus coker. Bu modul ureticiyi mumkun kilar: paper broker'inda ama CANLI
karar profiliyle kosan bir mod.

UC PROFIL
---------
  live             , gercek para, R5 kilidi kapali (yeni giris yok)
  paper_aggressive , mevcut paper botu; agresif override'larla (DOKUNULMADI)
  paper_live_config, YENI: paper broker'i + CANLI karar profili

`paper_live_config` GERCEK Alpaca paper dolumlari uretir: gercek kismi dolumlar,
gercek stop davranisi, gercek broker-defter mutabakati , SIFIR DOLAR RISKLE.
Golge defterin (R18) asla uretemeyecegi tek kanit turu budur: golge "ne
yapardim" der, bu "ne oldu" der.

KIMLIK TEK ALGORITMADAN
-----------------------
Profil hash'i R18 golge defteriyle AYNI fonksiyondan uretilir. Iki eksen
(golge-alfa ve calistirma) sonradan ancak kimlikleri ayni algoritmayla
uretildiyse eslestirilebilir.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Optional

LIVE = "live"
PAPER_AGGRESSIVE = "paper_aggressive"
PAPER_LIVE_CONFIG = "paper_live_config"

TUM_PROFILLER = (LIVE, PAPER_AGGRESSIVE, PAPER_LIVE_CONFIG)

# Canli kilidi acmak icin kanit olarak SAYILABILIR profiller.
# PAPER_AGGRESSIVE burada YOKTUR: agresif override'lar (esik 30, pozisyon $9000,
# tarama 15sn) canli davranisi temsil etmez.
KAPI_ICIN_GECERLI = (PAPER_LIVE_CONFIG,)


def _kanonik(veri) -> str:
    return json.dumps(veri, sort_keys=True, separators=(",", ":"), default=str)


def aktif_profil() -> str:
    """Su an YURURLUKTE olan profil.

    live modda her zaman `live`. Paper modda `PAPER_PROFILE` env'i belirler;
    varsayilan `aggressive` (mevcut davranis birebir korunur).
    """
    try:
        from config import TRADING_MODE
        if TRADING_MODE == "live":
            return LIVE
    except Exception:
        pass

    secim = os.getenv("PAPER_PROFILE", "aggressive").strip().lower()
    if secim in ("live_config", "live-config", "livecfg"):
        return PAPER_LIVE_CONFIG
    return PAPER_AGGRESSIVE


def agresif_override_uygulanir_mi() -> bool:
    """PAPER_AGGRESSIVE_CONFIG merge edilecek mi.

    `paper_live_config` modunda EDILMEZ: amac canli karar profilini paper
    broker'inda kosturmak.
    """
    return aktif_profil() == PAPER_AGGRESSIVE


def profil_hash(profil: Optional[str] = None) -> str:
    """ETKIN karar profilinin kanonik hash'i.

    R18 golge defteri BU fonksiyonu kullanir; iki eksenin kimligi ayni
    algoritmadan gelmezse sonradan ESLESTIRILEMEZ (Codex sarti).

    Config hash TEK BASINA yetmez ve commit TEK BASINA yetmez: v4.16'da paper
    config degismedi ama davranis degisti. Profil ADI da hash'e girer.
    """
    profil = profil or aktif_profil()
    try:
        from config import (
            AGENT_CONFIG,
            AV_QUOTA_CONFIG,
            SHORT_CONFIG,
            STOCK_CONFIG,
            TRADING_MODE,
        )
        yuk = {
            "profil": profil,
            "trading_mode": TRADING_MODE,
            "stock": {
                k: STOCK_CONFIG.get(k) for k in sorted(STOCK_CONFIG)
                if not callable(STOCK_CONFIG.get(k))
            },
            "short": {k: SHORT_CONFIG.get(k) for k in sorted(SHORT_CONFIG)},
            "agent": dict(sorted(AGENT_CONFIG.items())),
            "av_quota": {
                k: AV_QUOTA_CONFIG.get(k) for k in sorted(AV_QUOTA_CONFIG)
            },
        }
        return hashlib.sha256(_kanonik(yuk).encode("utf-8")).hexdigest()
    except Exception:
        return "UNKNOWN"


def kapi_icin_gecerli(profil: Optional[str] = None) -> bool:
    """Bu profilin gozlemleri canli kilit kapisinda KANIT sayilabilir mi.

    PAPER_AGGRESSIVE icin FALSE: agresif override'lar canli davranisi temsil
    etmez ve raporun kendisi bunu soyluyor. Kapi bu bayrağa uymak ZORUNDA.
    """
    return (profil or aktif_profil()) in KAPI_ICIN_GECERLI


def profil_ozeti() -> dict:
    p = aktif_profil()
    return {
        "profil": p,
        "profil_hash": profil_hash(p),
        "agresif_override": agresif_override_uygulanir_mi(),
        "kapi_icin_gecerli": kapi_icin_gecerli(p),
        "aciklama": {
            LIVE: "gercek para; R5 kilidi kapaliyken yeni giris yok",
            PAPER_AGGRESSIVE: (
                "paper broker + agresif override'lar , canli davranisi TEMSIL "
                "ETMEZ, kilit kapisinda kanit sayilmaz"
            ),
            PAPER_LIVE_CONFIG: (
                "paper broker + CANLI karar profili , gercek dolum/kismi dolum/"
                "stop davranisi uretir, SIFIR DOLAR RISKLE"
            ),
        }.get(p, "bilinmeyen"),
    }
