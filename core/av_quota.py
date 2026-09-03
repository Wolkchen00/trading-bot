"""R16 , Alpha Vantage kota rezervasyonu (UC tuketici icin TEK kapi).

NEDEN VAR
---------
Ayni `ALPHA_VANTAGE_KEY` UC yerde okunuyor:
  - core/fundamental_analyzer.py  (OVERVIEW)
  - core/news_analyzer.py:92      (NEWS_SENTIMENT)
  - core/earnings_calendar.py:40  (EARNINGS_CALENDAR)
Ucretsiz katman ANAHTAR BASINA 25 cagri/gun. Uc tuketici de kendi basina
cagirirsa kota sabahin ilk dakikalarinda tukenir ve FundAgent gun boyu kor kalir
(uretim telemetrisi 26 Agu: FundAgent veri_yok = %100).

"ANAHTAR GENELI 25" IDDIA EDILMEZ
--------------------------------
live ve paper AYRI konteynerlerde AYRI state hacimleriyle kosuyor; paylasilan
bir sayac fiziksel olarak imkansiz. Butce DETERMINISTIK bolunur (varsayilan
live 13 / paper 12) ve her konteyner yalniz kendi payini harcar. Kalici cozum
konteyner basina ayri anahtardir (RF-ISSUES-4.md::AV-ANAHTARI-IKI-KONTEYNER).

FAIL-CLOSED (uc ayri yerde)
---------------------------
1. Sayac okunamiyorsa ya da SEMANTIK olarak bozuksa gun tukenmis sayilir.
   Kurtarma kaydi DISKE YAZILIR, yoksa fail-closed kalici kilitlenmeye donusur.
2. Kilit ALINAMAZSA rezervasyon REDDEDILIR. Kilitsiz devam etmek, modulun tek
   garantisini (butce asilmaz) sessizce iptal ederdi.
3. Rezervasyon YAZILAMAZSA `try_reserve` False doner. True donup yazmamak,
   kaydedilmemis bir ag cagrisina ve restart'ta ayni slotun ikinci kez
   harcanmasina yol acardi.

TUKENME ISARETI ANAHTAR GENELINDE
---------------------------------
AV kotasi tukendiginde bunu OGRENEN ilk tuketici, isareti PAYLASILAN kayda
yazar. Aksi halde her sembol icin ayri ayri ogrenilir: kalan butce boşa
harcanir ve her biri icin 15 saniye uyunur.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from utils.logger import logger

SCHEMA_VERSION = 2   # 2: exhausted_day alani + katı dogrulama

CONSUMERS = ("fundamental", "news", "earnings")


class AVOutcome(str, Enum):
    """Tipli sonuclar , hepsini 'None' yapmak bilgi yok eder."""

    OK = "OK"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    RETRYABLE_ERROR = "RETRYABLE_ERROR"
    NO_DATA = "NO_DATA"


class LockUnavailable(Exception):
    """Kilit alinamadi , rezervasyon reddedilmeli."""


class ReserveReason(str, Enum):
    """Rezervasyonun NEDEN verilmedigi.

    Hepsini tek bir `False`'a cokertmek yanlisti: kilit ya da yazma hatasi
    KOTA TUKENMESI degildir, ama analizor onu oyle sayip sembolu gun boyu
    negatif cache'liyordu (Codex bulgusu).
    """

    OK = "OK"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"        # kendi gunluk butcemiz doldu
    PROVIDER_EXHAUSTED = "PROVIDER_EXHAUSTED"    # saglayici tukendi (isaretli)
    EARNINGS_RESERVED = "EARNINGS_RESERVED"      # slot takvime ayrilmis
    LOCK_UNAVAILABLE = "LOCK_UNAVAILABLE"        # gecici , negatif cache YOK
    WRITE_FAILED = "WRITE_FAILED"                # gecici , negatif cache YOK
    CORRUPT_COUNTER = "CORRUPT_COUNTER"          # bugun kapali, yarin iyilesir
    UNKNOWN_CONSUMER = "UNKNOWN_CONSUMER"


def utc_day(now: Optional[datetime] = None) -> str:
    """Kota gunu , UTC. Yerel saat DEGIL."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).strftime("%Y-%m-%d")


def classify_response(status_code: int, body_text: str, payload: object) -> AVOutcome:
    """AV yanitini tipli sonuca cevirir.

    KRITIK: AV kota tukendiginde HTTP 200 + icinde uyari mesaji olan bir govde
    doner. Sadece status_code'a bakan kod bunu 'basarili ama veri yok' sanar ve
    her turda yeniden cagirir.
    """
    if status_code != 200:
        return AVOutcome.RETRYABLE_ERROR

    dusuk = (body_text or "").lower()
    kota_isaretleri = (
        "rate limit",
        "api call frequency",
        "premium",
        "thank you for using alpha vantage",
        "higher api call volume",
        "25 requests per day",
    )
    if any(isaret in dusuk for isaret in kota_isaretleri):
        return AVOutcome.QUOTA_EXHAUSTED

    if isinstance(payload, dict):
        for anahtar in ("Note", "Information"):
            if anahtar in payload:
                return AVOutcome.QUOTA_EXHAUSTED
        if payload.get("Error Message"):
            return AVOutcome.NO_DATA

    return AVOutcome.OK


@contextmanager
def file_lock(lock_path: str):
    """Surecler arasi kilit. Windows msvcrt, POSIX fcntl.

    Kilit ALINAMAZSA `LockUnavailable` firlatir , kilitsiz devam ETMEZ.
    Atomik yazma tek basina yetmez: eszamanli iki surec ayni degeri okuyup ayri
    ayri artirabilir ve butce iki katina cikar.

    Istisna kapsami DARDIR: yalniz kilit ALMA sirasindaki hatalar yakalanir.
    Govde (yield sonrasi) firlatan istisna yakalanmaz , yakalanirsa generator
    ikinci kez yield etmeye calisir ve RuntimeError uretir.
    """
    handle = None
    try:
        os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
        handle = open(lock_path, "a+")
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except Exception as exc:
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
        raise LockUnavailable(str(exc)) from exc

    # Buradan sonrasi GOVDE. Istisnalari yakalamiyoruz; yalniz kilidi biraktigimiz
    # finally var.
    try:
        yield handle
    finally:
        try:
            if sys.platform == "win32":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            handle.close()
        except Exception:
            pass


class AVQuotaStore:
    """Profil-belirli, kalici, fail-closed AV kota sayaci."""

    def __init__(
        self,
        path: Optional[str] = None,
        budget: Optional[int] = None,
        profile: Optional[str] = None,
        now_fn=None,
        earnings_reserve: Optional[int] = None,
    ) -> None:
        self.path = path or self._default_path()
        self.lock_path = self.path + ".lock"
        self.budget = int(budget if budget is not None else self._default_budget())
        self.profile = profile or self._default_profile()
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._reserve_override = earnings_reserve

    # -------------------------------------------------- varsayilanlar

    @staticmethod
    def _default_path() -> str:
        try:
            from config import state_path
            return state_path("av_quota.json")
        except Exception:
            return "av_quota.json"

    @staticmethod
    def _default_profile() -> str:
        try:
            from config import TRADING_MODE
            return "live" if TRADING_MODE == "live" else "paper"
        except Exception:
            return "paper"

    @classmethod
    def _default_budget(cls) -> int:
        try:
            from config import AV_QUOTA_CONFIG
            return int(AV_QUOTA_CONFIG["profile_budget"][cls._default_profile()])
        except Exception:
            return 12          # config okunamazsa DAR taraf

    def _earnings_reserve(self) -> int:
        """Kazanc takvimine ayrilan slot sayisi (yalniz yenileme BEKLERKEN)."""
        if self._reserve_override is not None:
            return int(self._reserve_override)
        try:
            from config import AV_QUOTA_CONFIG
            return max(0, int(AV_QUOTA_CONFIG.get("earnings_reserve", 0)))
        except Exception:
            return 0

    # -------------------------------------------------- kayit

    def _bos_kayit(self, gun: str, tumu_harcanmis: bool = False) -> dict:
        harcanan = self.budget if tumu_harcanmis else 0
        return {
            "schema_version": SCHEMA_VERSION,
            "utc_day": gun,
            "profile": self.profile,
            "budget": self.budget,
            "used": {c: (harcanan if c == CONSUMERS[0] else 0) for c in CONSUMERS},
            "total_used": harcanan,
            "corrupt_recovered": tumu_harcanmis,
            "exhausted_day": None,
            "earnings_refreshed_day": None,
        }

    def _dogrula(self, ham: object, gun: str) -> Optional[dict]:
        """Kaydi KATI dogrular. Gecersizse None doner (cagiran fail-closed davranir).

        Ayristirilabilir ama SEMANTIK olarak bozuk kayitlar (tarih yok, profil
        yanlis, sayac negatif, toplam tutmuyor) 'taze butce' olarak okunmamali;
        eski kod bunlari gun donusu sanip sayaci sifirliyordu.
        """
        if not isinstance(ham, dict):
            return None
        try:
            surum = int(ham.get("schema_version"))
        except (TypeError, ValueError):
            return None                # surum YOK ya da sayi degil , guvenilmez
        if surum != SCHEMA_VERSION:
            # Eski surum de gelecek surum de KABUL EDILMEZ. Eski surumu sessizce
            # okumak, alan anlamlari degistiyse yanlis butce verir; gelecek surumu
            # okumak zaten imkansiz. Fail-closed: bugun kapali, yarin temiz baslar.
            return None

        try:
            kayitli_butce = int(ham.get("budget"))
        except (TypeError, ValueError):
            return None
        if kayitli_butce != self.budget:
            # Butce config'de degistiyse eski sayac anlamsizdir.
            return None

        ham_gun = ham.get("utc_day")
        if not isinstance(ham_gun, str) or len(ham_gun) != 10:
            return None
        try:
            datetime.strptime(ham_gun, "%Y-%m-%d")
        except ValueError:
            return None
        if ham_gun > gun:
            return None                # GELECEK tarihli kayit , guvenilmez

        if ham.get("profile") != self.profile:
            return None                # baska profilin dosyasi

        ham_used = ham.get("used")
        if not isinstance(ham_used, dict):
            return None
        used = {}
        for c in CONSUMERS:
            try:
                v = int(ham_used[c])
            except (KeyError, TypeError, ValueError):
                return None            # EKSIK/BOZUK sayac , 0 varsaymak yasak
            if v < 0:
                return None
            used[c] = v

        try:
            toplam = int(ham.get("total_used"))
        except (TypeError, ValueError):
            return None
        if toplam != sum(used.values()):
            return None                # tutarsiz , guvenilmez

        tuk = ham.get("exhausted_day")
        if tuk is not None and not isinstance(tuk, str):
            return None

        return {
            "schema_version": SCHEMA_VERSION,
            "utc_day": ham_gun,
            "profile": self.profile,
            "budget": self.budget,
            "used": used,
            "total_used": toplam,
            "corrupt_recovered": False,
            "exhausted_day": tuk,
            "earnings_refreshed_day": ham.get("earnings_refreshed_day"),
        }

    def _oku(self) -> dict:
        gun = utc_day(self._now_fn())
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                ham = json.load(f)
        except FileNotFoundError:
            return self._bos_kayit(gun)
        except Exception as exc:
            logger.warning(
                f"AV kota sayaci okunamadi ({exc}) , BUGUN TUKENMIS sayiliyor. "
                f"Yuk cache'i bundan bagimsizdir ve etkilenmez."
            )
            return self._bos_kayit(gun, tumu_harcanmis=True)

        kayit = self._dogrula(ham, gun)
        if kayit is None:
            logger.warning(
                "AV kota sayaci SEMANTIK olarak gecersiz (tarih/profil/sayac "
                "tutarsiz) , BUGUN TUKENMIS sayiliyor."
            )
            return self._bos_kayit(gun, tumu_harcanmis=True)

        if kayit["utc_day"] != gun:
            # GECERLI bir onceki gun kaydi , sadece bu durumda sifirlanir.
            yeni = self._bos_kayit(gun)
            # Tukenme isareti gun bazli; eski gunun isareti tasinmaz.
            return yeni
        return kayit

    def _yaz(self, kayit: dict) -> bool:
        """Atomik + fsync'li yazma. BASARIYI DONDURUR , cagiran buna bakmali."""
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            dizin = os.path.dirname(self.path) or "."
            fd, gecici = tempfile.mkstemp(dir=dizin, prefix=".avq", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(kayit, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(gecici, self.path)
                return True
            except Exception:
                try:
                    os.unlink(gecici)
                except Exception:
                    pass
                raise
        except Exception as exc:
            logger.warning(f"AV kota sayaci YAZILAMADI: {exc} , rezervasyon reddedildi")
            return False

    # -------------------------------------------------- genel API

    def try_reserve(self, consumer: str, count: int = 1) -> bool:
        """Geriye uyumlu bool sarmalayici. Sebep gerekiyorsa reserve() kullan."""
        return self.reserve(consumer, count)[0]

    def reserve(self, consumer: str, count: int = 1):
        """(verildi_mi, ReserveReason) doner. False ise AG CAGRISI YAPILMAMALI."""
        if consumer not in CONSUMERS:
            logger.warning(
                f"AV kota: tanimsiz tuketici '{consumer}' , butce disinda kalmasin "
                f"diye CONSUMERS'a eklenmeli. Simdilik reddediliyor."
            )
            return False, ReserveReason.UNKNOWN_CONSUMER

        gun = utc_day(self._now_fn())
        try:
            with file_lock(self.lock_path):
                kayit = self._oku()

                # SIRA: bozuk dosya onarimi HER SEYDEN ONCE. Baska bir kontrol
                # once erken return yaparsa onarim kaydi yazilmaz ve fail-closed
                # KALICI KILITLENMEYE donusur.
                if kayit.get("corrupt_recovered"):
                    self._yaz(kayit)
                    return False, ReserveReason.CORRUPT_COUNTER

                # ANAHTAR GENELI TUKENME: bir tuketici kotanin bittigini ogrendiyse
                # digerleri bosa cagri yapip 15 sn uyumamali.
                if kayit.get("exhausted_day") == gun:
                    return False, ReserveReason.PROVIDER_EXHAUSTED

                if consumer != "earnings":
                    # KAZANC TAKVIMI REZERVI: takvim bir ISLEM KAPISINI besliyor
                    # (earnings_gate). Temel analiz butcenin tamamini yerse takvim
                    # bayatlar ve kapi fail-open'a duser , kazanc gunlerinde islem
                    # acilir. Bu yuzden takvim GUNLUK CAGRISINI YAPANA KADAR bir
                    # slot ayrilir.
                    #
                    # Takvim slotunu kullandiginda rezerv TAMAMEN serbest kalir.
                    # Onceki surum `reserve - used` yaziyordu ve reserve=2 iken
                    # normal tek cagridan sonra 1 slot kalici olarak bosa
                    # yatiyordu (Codex bulgusu).
                    # Rezerv, takvim BASARIYLA tazeleyene kadar korunur.
                    # Onceki surum "herhangi bir cagri denendi" ile serbest
                    # birakiyordu; basarisiz bir deneme, reklam edilen yeniden
                    # deneme kapasitesini temel analize kaptiriyordu.
                    ayrilan = (
                        0 if kayit.get("earnings_refreshed_day") == gun
                        else self._earnings_reserve()
                    )
                    if kayit["total_used"] + count > kayit["budget"] - ayrilan:
                        return (
                            False,
                            ReserveReason.EARNINGS_RESERVED if ayrilan
                            else ReserveReason.BUDGET_EXHAUSTED,
                        )

                if kayit["total_used"] + count > kayit["budget"]:
                    return False, ReserveReason.BUDGET_EXHAUSTED

                kayit["used"][consumer] += count
                kayit["total_used"] += count
                # Yazma BASARISIZSA rezervasyon verilmez: aksi halde kaydedilmemis
                # bir ag cagrisi olur ve restart ayni slotu yeniden harcar.
                if self._yaz(kayit):
                    return True, ReserveReason.OK
                return False, ReserveReason.WRITE_FAILED
        except LockUnavailable as exc:
            logger.warning(
                f"AV kota kilidi alinamadi ({exc}) , rezervasyon REDDEDILDI. "
                f"Kilitsiz devam etmek butce garantisini iptal ederdi."
            )
            return False, ReserveReason.LOCK_UNAVAILABLE

    def mark_earnings_refreshed(self) -> None:
        """Takvim BASARIYLA tazelendi , ayrilan slot artik serbest."""
        gun = utc_day(self._now_fn())
        try:
            with file_lock(self.lock_path):
                kayit = self._oku()
                if kayit.get("corrupt_recovered"):
                    return
                kayit["earnings_refreshed_day"] = gun
                self._yaz(kayit)
        except LockUnavailable:
            pass

    def mark_exhausted(self) -> None:
        """Kotanin bugun tukendigini PAYLASILAN kayda isle (anahtar geneli)."""
        gun = utc_day(self._now_fn())
        try:
            with file_lock(self.lock_path):
                kayit = self._oku()
                kayit["exhausted_day"] = gun
                kayit["corrupt_recovered"] = False
                self._yaz(kayit)
        except LockUnavailable as exc:
            logger.debug(f"AV tukenme isareti yazilamadi (kilit): {exc}")

    def is_exhausted(self) -> bool:
        """Bugun tukenmis olarak isaretlendi mi (anahtar geneli).

        BOZUK kaydi burada da ONARIR: uretimde bu metot try_reserve'den ONCE
        cagriliyor, dolayisiyla onarim yalniz try_reserve'de olsaydi hic
        tetiklenmez ve fail-closed KALICI kilitlenmeye donusurdu (Codex bulgusu).
        """
        gun = utc_day(self._now_fn())
        try:
            with file_lock(self.lock_path):
                kayit = self._oku()
                if kayit.get("corrupt_recovered"):
                    self._yaz(kayit)     # yarin UTC gun donusuyle iyilessin
                    return True
                return kayit.get("exhausted_day") == gun
        except LockUnavailable:
            return True          # fail-closed

    def remaining(self) -> int:
        try:
            with file_lock(self.lock_path):
                kayit = self._oku()
                if kayit.get("exhausted_day") == utc_day(self._now_fn()):
                    return 0
                return max(0, kayit["budget"] - kayit["total_used"])
        except LockUnavailable:
            return 0             # fail-closed

    def snapshot(self) -> dict:
        try:
            with file_lock(self.lock_path):
                return self._oku()
        except LockUnavailable:
            return self._bos_kayit(utc_day(self._now_fn()), tumu_harcanmis=True)


_paylasilan: Optional[AVQuotaStore] = None


def shared_store() -> AVQuotaStore:
    """Surec icindeki tek ortak depo. Uc tuketici de bunu kullanir."""
    global _paylasilan
    if _paylasilan is None:
        _paylasilan = AVQuotaStore()
    return _paylasilan


def reset_shared_store() -> None:
    """Yalniz testler icin."""
    global _paylasilan
    _paylasilan = None
