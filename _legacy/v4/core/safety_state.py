"""R24a , CANLI EMNIYET DURUMU: kalici taban, otomatik kilit, fail-closed okuma.

NEDEN VAR
---------
1) TABAN RESTART'TA DUSUYORDU. `equity_floor = mevcut_equity * pct` her
   baslangicta yeniden hesaplaniyordu. $491'de $417 olan taban, hesap $450'ye
   dustukten sonraki bir restart'ta $382'ye INIYORDU , yani koruma, korumasi
   gereken dususun pesinden gidiyordu. Artik taban KALICI ZIRVEDEN hesaplanir
   ve bir restart onu ASLA asagi cekemez.

2) BOT KENDINI KILITLEYEMIYORDU. Girisi dolan ama kapsayan stop'u
   dogrulanamayan bir pozisyon, korumasi basarisiz olan bir tur, ya da emir
   GONDERILDIKTEN sonra alinan beklenmedik bir istisna: bunlarin hepsi
   "belirsiz sonuc" birakir. Belirsizlikte dogru davranis DURMAKTIR ve bu karar
   restart'i ATLATMALIDIR. `live_auto_lock.json` tam olarak bunu yapar.

3) GUVENLIK DOSYALARI FAIL-OPEN'DI. Var ama okunamayan bir kayit "yok gibi"
   ele aliniyordu. Bu, guvenligi bozuk bir dosyayla devre disi birakmak
   demektir. Artik canlida VAR-AMA-OKUNAMAZ = KILITLI.

TASARIM KURALI
--------------
Bu modulun HER okuma fonksiyonu (deger, hata) ikilisi dondurur. `hata` doluysa
canli tarafta karar DAIMA fail-closed'dir. "Dosya yok" ile "dosya bozuk" ayri
seylerdir ve asla ayni kovaya konmaz.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional, Tuple

from core.order_journal import atomic_write_json
from utils.logger import logger

PEAK_DOSYA = "peak_equity.json"
AUTO_LOCK_DOSYA = "live_auto_lock.json"

# Otomatik kilit tetikleyicileri (R24a sozlesmesi).
TETIK_STOP_DOGRULANMADI = "STOP_UNVERIFIED"
TETIK_KORUMA_BASARISIZ = "PROTECTION_FAILED"
TETIK_PEAK_BOZUK = "PEAK_RECORD_CORRUPT"
TETIK_GONDERIM_SONRASI_ISTISNA = "POST_SUBMIT_EXCEPTION"
TETIK_KILL_TASFIYE_ASIMI = "KILL_LIQUIDATION_TIMEOUT"


def _yol(state_dir: str, ad: str) -> str:
    return os.path.join(state_dir, ad)


def _simdi() -> str:
    return datetime.now(timezone.utc).isoformat()


def _oku(yol: str) -> Tuple[Optional[dict], Optional[str]]:
    """(veri, hata). Dosya YOKSA (None, None) , bu bir hata DEGILDIR."""
    if not os.path.exists(yol):
        return None, None
    try:
        with open(yol, "r", encoding="utf-8") as handle:
            veri = json.load(handle)
        if not isinstance(veri, dict):
            return None, "sema bozuk (sozluk degil)"
        return veri, None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


# ======================================================================
# GUVENLIK DIZINI , yazilamiyorsa canli giris BASTAN reddedilir
# ======================================================================

def guvenlik_dizini_yazilabilir(state_dir: str) -> Tuple[bool, Optional[str]]:
    """Kilit yazilamayacaksa canli giris yapilmamalidir.

    Aksi halde bir arizada kilit YAZILAMAZ ve restart'ta kilit buharlasir:
    bot, durmasi gereken durumda islem acmaya devam eder.
    """
    try:
        os.makedirs(state_dir, exist_ok=True)
        deneme = _yol(state_dir, f".yazma_denemesi_{os.getpid()}")
        with open(deneme, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(deneme)
        return True, None
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


# ======================================================================
# KALICI ZIRVE (peak equity) , taban buradan hesaplanir
# ======================================================================

def peak_oku(state_dir: str) -> Tuple[Optional[float], Optional[str]]:
    veri, hata = _oku(_yol(state_dir, PEAK_DOSYA))
    if hata:
        return None, hata
    if veri is None:
        return None, None
    try:
        deger = float(veri.get("peak_equity"))
    except (TypeError, ValueError):
        return None, "peak_equity sayi degil"
    if not (deger > 0) or deger != deger or deger in (float("inf"), float("-inf")):
        return None, f"peak_equity gecersiz: {veri.get('peak_equity')!r}"
    return deger, None


def peak_yaz(state_dir: str, deger: float, *, sebep: str = "") -> bool:
    try:
        atomic_write_json(_yol(state_dir, PEAK_DOSYA), {
            "peak_equity": float(deger),
            "guncellendi": _simdi(),
            "sebep": str(sebep),
        })
        return True
    except Exception as exc:
        logger.error(f"  Zirve kaydi YAZILAMADI: {exc}")
        return False


def taban_hesapla(peak: float, pct: float) -> float:
    try:
        return float(peak) * float(pct)
    except (TypeError, ValueError):
        return 0.0


def zirve_guncelle(
    state_dir: str,
    mevcut_equity: float,
    *,
    is_live: bool,
    sebep: str = "gunluk",
) -> Tuple[Optional[float], Optional[str]]:
    """Zirveyi `max(kayitli, mevcut)` yap ve dondur.

    GUNLUK yuksek-su: yalniz acilista ve gunluk resette cagrilir. Gun ici zirve
    KASITLI olarak sayilmaz , yoksa gun ici bir tepe tabani yukari cakar ve
    normal bir geri cekilme yeni girisleri durdurur.

    Donen `hata` doluysa: CANLIDA taban yeniden kurulmaz ve cagiran otomatik
    kilit yazar. Paper'da cagiran mevcut equity ile yeniden kurar.
    """
    kayitli, hata = peak_oku(state_dir)
    if hata:
        if is_live:
            logger.error(
                f"  CANLI zirve kaydi BOZUK ({hata}) , taban yeniden KURULMAZ, "
                "otomatik kilit yazilacak"
            )
            return None, hata
        logger.warning(
            f"  Zirve kaydi bozuk ({hata}) , PAPER: mevcut equity ile yeniden kuruluyor"
        )
        kayitli = None

    try:
        mevcut = float(mevcut_equity)
    except (TypeError, ValueError):
        return None, f"mevcut equity sayi degil: {mevcut_equity!r}"
    if not (mevcut > 0):
        return None, f"mevcut equity gecersiz: {mevcut_equity!r}"

    if kayitli is None:
        peak_yaz(state_dir, mevcut, sebep=f"ilk kurulum ({sebep})")
        logger.info(f"  Zirve kaydi YOK , ilk deger mevcut equity ${mevcut:,.2f}")
        return mevcut, None

    yeni = max(kayitli, mevcut)
    if yeni > kayitli:
        peak_yaz(state_dir, yeni, sebep=sebep)
        logger.info(f"  Zirve guncellendi: ${kayitli:,.2f} -> ${yeni:,.2f}")
    return yeni, None


# ======================================================================
# OTOMATIK KILIT , belirsizlikte DUR, ve bunu restart'tan sonra da hatirla
# ======================================================================

def auto_lock_oku(state_dir: str) -> Tuple[Optional[dict], Optional[str]]:
    """(kayit, hata). Kayit None ve hata None ise kilit YOK."""
    return _oku(_yol(state_dir, AUTO_LOCK_DOSYA))


def auto_lock_yaz(
    state_dir: str, sebep: str, symbol: str = "", ayrinti: str = "",
) -> bool:
    """Kilidi kalici yaz. Yazamazsak cagiran bellek-ici halt'a duser."""
    try:
        atomic_write_json(_yol(state_dir, AUTO_LOCK_DOSYA), {
            "sebep": str(sebep),
            "symbol": str(symbol or ""),
            "ayrinti": str(ayrinti or ""),
            "zaman": _simdi(),
        })
        logger.error(
            f"  OTOMATIK KILIT YAZILDI , sebep={sebep} symbol={symbol or '-'} "
            f"({ayrinti or 'ayrinti yok'})"
        )
        return True
    except Exception as exc:
        logger.error(f"  OTOMATIK KILIT YAZILAMADI ({exc}) , bellek-ici halt")
        return False


def auto_lock_temizle(state_dir: str) -> bool:
    try:
        yol = _yol(state_dir, AUTO_LOCK_DOSYA)
        if os.path.exists(yol):
            os.remove(yol)
        return True
    except Exception as exc:
        logger.error(f"  Otomatik kilit temizlenemedi: {exc}")
        return False


def kilitle(bot, sebep: str, symbol: str = "", ayrinti: str = "") -> None:
    """Botu otomatik kilide al , kalici yazim + bellek-ici yedek.

    Yazim basarisiz olsa bile `_auto_lock_memory` set edilir: surec ayakta
    kaldigi surece yeni risk acilmaz. Yazim basarisizligi KRITIK alarmdir.
    """
    state_dir = getattr(bot, "_state_dir", None)
    yazildi = False
    if state_dir:
        yazildi = auto_lock_yaz(state_dir, sebep, symbol, ayrinti)
    bot._auto_lock_memory = {
        "sebep": sebep, "symbol": symbol, "ayrinti": ayrinti,
        "zaman": _simdi(), "kalici": yazildi,
    }
    if not yazildi:
        try:
            bot.notifier.notify_critical(
                "AUTO_LOCK_YAZILAMADI",
                f"Otomatik kilit diske YAZILAMADI (sebep={sebep}, symbol={symbol}). "
                "Bellek-ici halt aktif; surec yeniden baslarsa kilit KAYBOLUR.",
            )
        except Exception:
            pass


def auto_lock_durumu(bot, state_dir: str) -> Tuple[bool, str]:
    """(kilitli_mi, sebep). Fail-closed: okunamayan kayit KILITLI sayilir."""
    bellek = getattr(bot, "_auto_lock_memory", None)
    if isinstance(bellek, dict) and bellek.get("sebep"):
        return True, str(bellek["sebep"])
    kayit, hata = auto_lock_oku(state_dir)
    if hata:
        return True, f"KAYIT_OKUNAMADI ({hata})"
    if isinstance(kayit, dict):
        return True, str(kayit.get("sebep") or "BILINMIYOR")
    return False, ""
