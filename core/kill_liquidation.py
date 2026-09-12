"""R24b , DURUST KILL TASFIYESI: niyet once yazilir, flat olana kadar birakilmaz.

NEDEN VAR
---------
Eski `_emergency_close_all` on satirdi ve uc ayri sekilde yaniltiyordu:

  1. `close_all_positions(...)` KABULUNU dolum saydi ve yerel kaydi HEMEN sildi.
     Dolmayan bir kapanis, botun artik bilmedigi bir pozisyon birakiyordu.
  2. `cancel_orders=True` koruyucu stoplari da iptal etti. Yani kapanmayan
     pozisyon hem sahipsiz hem STOPSUZ kaliyordu.
  3. Niyet hicbir yere yazilmadi. Cagri sirasinda crash olursa geriye hicbir iz
     kalmiyordu: yeni surec ortada bir tasfiye oldugunu bilemezdi.

Ihsan bu riski acikca kabul etmedi.

TASARIM
-------
* NIYET CAGRIDAN ONCE kalici yazilir (broker anlik goruntusu dahil).
* ENVANTER BROKER'DAN gelir, yerel defterden DEGIL (canli SPY parki yerel
  defterde yok) ve HER TURDA yenilenir , gec dolan bir BUY emri envantere girer.
* YEREL KAYIT SILINMEZ; her pozisyon `kill_close_pending` ile isaretlenir ve
  yalniz broker o sembolde FLAT gosterdiginde dis-kapanis uzlastirma yolundan
  duser (PnL, seri, ledger, wash-sale).
* SEMBOL BASINA TEK CIKIS OTORITESI: ayni acik miktar icin hem yeniden kapatma
  emri hem koruyucu stop GONDERILMEZ , ikisi de dolarsa ters pozisyon acilir.
* MUHASEBE TAM BIR KEZ: `accounted` isareti episode bazinda tutulur.
* VAZGECME YOK: broker flat olana ya da SAHIP devralana kadar backoff'lu dongu
  surer. Sure asiminda kritik alarm + otomatik kilit, ama dongu DURMAZ.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional, Tuple

from core.order_journal import atomic_write_json
from core.safety_state import _oku, _yol
from utils.logger import logger

PENDING_DOSYA = "kill_close_pending.json"

# Sure asimi: bu sureden sonra kritik alarm + otomatik kilit. DONGU DURMAZ.
VARSAYILAN_ASIM_DK = 10
# Backoff: her tur arasi en az bu kadar beklenir (broker'i dovmeyelim).
MIN_TUR_ARALIGI_SN = 20


def _simdi() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: Optional[datetime] = None) -> str:
    return (d or _simdi()).isoformat()


# ======================================================================
# KALICI NIYET
# ======================================================================

def pending_oku(state_dir: str) -> Tuple[Optional[dict], Optional[str]]:
    """(kayit, hata). Fail-closed: hata doluysa cagiran BEKLEYEN VAR saymalidir."""
    return _oku(_yol(state_dir, PENDING_DOSYA))


def pending_yaz(state_dir: str, kayit: dict) -> bool:
    try:
        atomic_write_json(_yol(state_dir, PENDING_DOSYA), kayit)
        return True
    except Exception as exc:
        logger.error(f"  Kill tasfiye niyeti YAZILAMADI: {exc}")
        return False


def pending_temizle(state_dir: str) -> bool:
    try:
        yol = _yol(state_dir, PENDING_DOSYA)
        if os.path.exists(yol):
            os.remove(yol)
        return True
    except Exception as exc:
        logger.error(f"  Kill tasfiye kaydi temizlenemedi: {exc}")
        return False


def pending_var_mi(state_dir: str) -> Tuple[bool, str]:
    """(bekleyen_var, sebep). Okunamayan kayit BEKLEYEN VAR sayilir."""
    kayit, hata = pending_oku(state_dir)
    if hata:
        return True, f"KAYIT_OKUNAMADI ({hata})"
    if isinstance(kayit, dict) and kayit.get("envanter"):
        return True, str(kayit.get("sebep") or "KILL_TASFIYE")
    return False, ""


# ======================================================================
# BROKER ENVANTERI , yerel defter YETMEZ
# ======================================================================

def broker_envanteri(client) -> Tuple[dict, Optional[str]]:
    """Acik pozisyonlar + acik GIRIS emirleri -> envanter.

    Parking DAHIL: canli SPY parki yerel defterde yoktur ama tasfiyede
    kapatilmasi gereken gercek bir pozisyondur.
    Acik BUY emirleri de girer: anlik goruntuden SONRA dolarsa yeni pozisyon
    olusur ve envanterde olmayan bir pozisyon gozden kacardi.
    """
    envanter: dict = {}
    try:
        for poz in client.get_all_positions() or []:
            try:
                qty = float(poz.qty)
            except (TypeError, ValueError):
                continue
            if qty == 0:
                continue
            sym = str(poz.symbol).upper()
            envanter[sym] = {
                "qty": abs(qty),
                "side": "long" if qty > 0 else "short",
                "kaynak": "pozisyon",
                "isaretlendi": _iso(),
                "accounted": False,
            }
    except Exception as exc:
        return envanter, f"pozisyon sorgusu basarisiz: {exc}"

    try:
        for emir in client.get_orders() or []:
            durum = str(getattr(emir, "status", "")).lower()
            if "fill" in durum or "cancel" in durum or "expire" in durum:
                continue
            yon = str(getattr(emir, "side", "")).lower()
            if "buy" not in yon:
                continue
            sym = str(getattr(emir, "symbol", "")).upper()
            if not sym or sym in envanter:
                continue
            # Henuz dolmamis bir GIRIS emri: gec dolarsa pozisyon olusur.
            envanter[sym] = {
                "qty": 0.0,
                "side": "long",
                "kaynak": "acik_giris_emri",
                "isaretlendi": _iso(),
                "accounted": False,
            }
    except Exception as exc:
        logger.warning(f"  Kill envanteri: acik emir sorgusu basarisiz ({exc})")

    return envanter, None


# ======================================================================
# NIYET , CAGRIDAN ONCE
# ======================================================================

def niyet_baslat(bot, sebep: str) -> Tuple[Optional[dict], Optional[str]]:
    """Tasfiye niyetini `close_all_positions` CAGRISINDAN ONCE kalici yaz.

    Cagri sirasinda crash ya da belirsiz timeout olsa bile geriye iz kalir ve
    yeni surec tasfiyeyi SURDURUR.
    """
    state_dir = getattr(bot, "_state_dir", None)
    if not state_dir:
        return None, "state dizini yok"

    mevcut, hata = pending_oku(state_dir)
    envanter, env_hata = broker_envanteri(bot.client)
    if env_hata:
        logger.error(f"  Kill envanteri BROKER'DAN alinamadi: {env_hata}")

    if isinstance(mevcut, dict) and mevcut.get("envanter"):
        # Devam eden tasfiye: envanteri BIRLESTIR, sifirlama.
        birlesik = dict(mevcut.get("envanter") or {})
        for sym, veri in envanter.items():
            if sym in birlesik:
                birlesik[sym]["qty"] = max(
                    float(birlesik[sym].get("qty", 0) or 0), float(veri["qty"])
                )
            else:
                birlesik[sym] = veri
        mevcut["envanter"] = birlesik
        mevcut["son_guncelleme"] = _iso()
        pending_yaz(state_dir, mevcut)
        return mevcut, None

    kayit = {
        "sebep": str(sebep),
        "baslangic": _iso(),
        "son_guncelleme": _iso(),
        "envanter": envanter,
        "deneme_sayisi": 0,
        "asim_alarmi": False,
        "envanter_hatasi": env_hata or "",
    }
    if not pending_yaz(state_dir, kayit):
        return None, "niyet yazilamadi"
    logger.error(
        f"  KILL TASFIYE NIYETI yazildi , sebep={sebep} "
        f"envanter={sorted(envanter)}"
    )
    return kayit, None


# ======================================================================
# TEK CIKIS OTORITESI
# ======================================================================

def acik_cikis_miktari(client, symbol: str, uzun: bool) -> Tuple[float, float]:
    """(toplam_acik_cikis_qty, aktif_stop_qty) , sembol icin.

    Cikis = pozisyonun tersi yonde, dolmamis emir. Stop emirleri ayrica sayilir
    ki "aktif tam-miktarli close varken stop gonderme" kurali uygulanabilsin.
    """
    toplam = 0.0
    stop_qty = 0.0
    try:
        for emir in client.get_orders() or []:
            if str(getattr(emir, "symbol", "")).upper() != symbol.upper():
                continue
            durum = str(getattr(emir, "status", "")).lower()
            if "fill" in durum or "cancel" in durum or "expire" in durum:
                continue
            yon = str(getattr(emir, "side", "")).lower()
            cikis_yonu = "sell" if uzun else "buy"
            if cikis_yonu not in yon:
                continue
            try:
                q = abs(float(emir.qty))
            except (TypeError, ValueError):
                continue
            toplam += q
            tur = str(
                getattr(emir, "order_type", "") or getattr(emir, "type", "")
            ).lower()
            if "stop" in tur:
                stop_qty += q
    except Exception as exc:
        logger.warning(f"  {symbol} acik cikis sorgusu basarisiz: {exc}")
        # Belirsizlik: "cikis emri VAR" varsay ki ikinci emir GONDERILMESIN.
        return float("inf"), 0.0
    return toplam, stop_qty


# ======================================================================
# TASFIYE TURU , ana dongu kill dalindan HER TURDA cagrilir
# ======================================================================

def tasfiye_turu(bot, config: dict) -> Optional[dict]:
    """Bir uzlastirma turu. Yeni giris ACMAZ; yalniz kapatir ve korur.

    Adimlar:
      1. Envanteri broker'dan YENILE (gec dolan giris emri dahil).
      2. Broker'da FLAT olan sembolleri uzlastir ve envanterden dusur.
      3. Hala acik olanlar icin: aktif tam-miktarli cikis emri YOKSA yeniden
         kapat; kapatma gonderilmediyse koruyucu stop yerlestir.
      4. Sure asiminda kritik alarm + otomatik kilit (dongu DURMAZ).
    """
    state_dir = getattr(bot, "_state_dir", None)
    if not state_dir:
        return None
    kayit, hata = pending_oku(state_dir)
    if hata:
        logger.error(f"  Kill tasfiye kaydi OKUNAMADI ({hata}) , bekleyen sayiliyor")
        return None
    if not isinstance(kayit, dict) or not kayit.get("envanter"):
        return None

    # Backoff , broker'i dovmeyelim.
    son = kayit.get("son_tur")
    if son:
        try:
            gecen = (_simdi() - datetime.fromisoformat(son)).total_seconds()
            if gecen < MIN_TUR_ARALIGI_SN:
                return kayit
        except (TypeError, ValueError):
            pass

    envanter = dict(kayit.get("envanter") or {})

    # 1) ENVANTERI YENILE , her turda, tek seferlik anlik goruntu YETMEZ.
    taze, env_hata = broker_envanteri(bot.client)
    if env_hata:
        logger.error(f"  Kill turu: broker envanteri alinamadi ({env_hata})")
        kayit["son_tur"] = _iso()
        kayit["deneme_sayisi"] = int(kayit.get("deneme_sayisi", 0) or 0) + 1
        pending_yaz(state_dir, kayit)
        return kayit

    for sym, veri in taze.items():
        if sym not in envanter:
            logger.error(f"  Kill turu: GEC DOLAN pozisyon envantere alindi , {sym}")
            envanter[sym] = veri
        else:
            envanter[sym]["qty"] = max(
                float(envanter[sym].get("qty", 0) or 0), float(veri["qty"])
            )

    # 2) FLAT OLANLARI UZLASTIR ve dusur.
    for sym in list(envanter):
        if sym in taze:
            continue
        # Broker bu sembolde FLAT. Muhasebe TAM BIR KEZ.
        if not envanter[sym].get("accounted"):
            try:
                uzlastir = getattr(bot, "_reconcile_external_exit", None)
                if callable(uzlastir):
                    # Yon ONEMLI: short pozisyonu LONG defterinden uzlastirmak
                    # sessizce hicbir sey yapmaz ve muhasebe kaybolur.
                    yon = "SHORT" if envanter[sym].get("side") == "short" else "LONG"
                    try:
                        uzlastir(sym, side=yon)
                    except TypeError:
                        uzlastir(sym)
                envanter[sym]["accounted"] = True
            except Exception as exc:
                logger.error(f"  {sym} kill uzlastirmasi basarisiz: {exc}")
                continue
        logger.info(f"  Kill tasfiye: {sym} broker'da FLAT , kayittan dusuruldu")
        envanter.pop(sym, None)
        for defter in ("positions", "short_positions", "options_positions"):
            try:
                getattr(bot, defter, {}).pop(sym, None)
            except Exception:
                pass

    # 3) HALA ACIK OLANLAR.
    for sym, veri in envanter.items():
        qty = float(veri.get("qty", 0) or 0)
        if qty <= 0:
            continue          # yalniz acik giris emri , dolumu bekleniyor
        uzun = veri.get("side") != "short"
        acik_cikis, stop_qty = acik_cikis_miktari(bot.client, sym, uzun)

        # TEK CIKIS OTORITESI: toplam acik cikis pozisyonu ASLA asamaz.
        if acik_cikis >= qty - 1e-9:
            logger.info(
                f"  Kill tasfiye: {sym} icin aktif tam-miktarli cikis emri VAR "
                f"({acik_cikis:g}/{qty:g}) , ikinci emir gonderilmiyor"
            )
            continue

        try:
            bot.client.close_position(sym)
            logger.error(f"  Kill tasfiye: {sym} yeniden kapatma emri gonderildi")
            continue          # kapatma gonderildi -> stop GONDERILMEZ
        except Exception as exc:
            logger.error(f"  {sym} yeniden kapatma basarisiz: {exc}")

        # Kapatma gonderilemedi VE aktif cikis emri yok -> KORUYUCU STOP.
        if acik_cikis <= 1e-9:
            try:
                pm = getattr(bot, "position_manager", None)
                if pm is not None:
                    pm.ensure_protective_stops(config)
                    logger.error(
                        f"  Kill tasfiye: {sym} kapanmadi , koruyucu stop yeniden "
                        "yerlestirildi (sahipsiz+stopsuz birakilmaz)"
                    )
            except Exception as exc:
                logger.error(f"  {sym} koruyucu stop yerlestirilemedi: {exc}")

    kayit["envanter"] = envanter
    kayit["son_tur"] = _iso()
    kayit["son_guncelleme"] = _iso()
    kayit["deneme_sayisi"] = int(kayit.get("deneme_sayisi", 0) or 0) + 1

    # 4) SURE ASIMI , alarm + otomatik kilit, ama VAZGECME YOK.
    asim_dk = float(config.get("kill_liquidation_timeout_min", VARSAYILAN_ASIM_DK))
    try:
        gecen_dk = (
            _simdi() - datetime.fromisoformat(kayit["baslangic"])
        ).total_seconds() / 60.0
    except (TypeError, ValueError, KeyError):
        gecen_dk = 0.0
    if envanter and gecen_dk >= asim_dk and not kayit.get("asim_alarmi"):
        kayit["asim_alarmi"] = True
        logger.error(
            f"  KILL TASFIYE SURE ASIMI ({gecen_dk:.0f} dk >= {asim_dk:.0f} dk) , "
            f"kalan: {sorted(envanter)} , dongu DEVAM EDIYOR"
        )
        try:
            from core.safety_state import TETIK_KILL_TASFIYE_ASIMI, kilitle
            if not getattr(bot, "is_paper", False):
                kilitle(
                    bot, TETIK_KILL_TASFIYE_ASIMI, ",".join(sorted(envanter)),
                    f"{gecen_dk:.0f} dk",
                )
            bot.notifier.notify_critical(
                "KILL_TASFIYE_ASIMI",
                f"Kill tasfiyesi {gecen_dk:.0f} dakikadir bitmedi. Kalan: "
                f"{sorted(envanter)}. Bot denemeye DEVAM ediyor.",
            )
        except Exception as exc:
            logger.error(f"  Sure asimi alarmi/kilidi basarisiz: {exc}")

    if not envanter:
        logger.error("  KILL TASFIYE TAMAMLANDI , broker flat, kayit temizlendi")
        pending_temizle(state_dir)
        return None

    pending_yaz(state_dir, kayit)
    return kayit
