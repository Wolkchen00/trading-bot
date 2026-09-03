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
bir sayac fiziksel olarak imkansiz. Iki yerel sayac her biri 25'e izin verirdi.
Bu yuzden butce DETERMINISTIK olarak bolunur (varsayilan live 13 / paper 12) ve
her konteyner yalniz kendi payini harcar. Kalici cozum konteyner basina ayri
anahtardir (RF-ISSUES-4.md::AV-ANAHTARI-IKI-KONTEYNER).

FAIL-CLOSED
-----------
Sayac okunamiyorsa gun TUKENMIS sayilir. Atomik yer degistirme tek basina
YETMEZ: restart ya da deploy ortusmesinde ayni profilden iki surec eszamanli
kosabilir ve oku-degistir-yaz yarisir. Bu yuzden rezervasyon SURECLER ARASI
KILIT altinda ve cagridan ONCE yazilir.

SAAT DILIMI
-----------
Kota muhasebesi UTC gun sinirindadir (AV ucretsiz katman sinirinin sifirlandigi
sinir). Islem-gunu telemetrisi ayri bir alandir ve America/New_York kullanir;
ikisi birbirine karismaz.
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

SCHEMA_VERSION = 1

# Butce paylasan tuketiciler. Yeni bir AV cagirani eklenirse BURAYA da eklenmeli,
# yoksa butce disinda kalir ve sinir sessizce asilir.
CONSUMERS = ("fundamental", "news", "earnings")


class AVOutcome(str, Enum):
    """Tipli sonuclar , hepsini 'None' yapmak bilgi yok eder.

    Yalniz QUOTA_EXHAUSTED gun sonuna kadar negatif cache'lenir. RETRYABLE_ERROR
    aynı gun tekrar denenir; ikisini karistirmak gecici bir kaynagi gun boyu
    susturur.
    """

    OK = "OK"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    RETRYABLE_ERROR = "RETRYABLE_ERROR"
    NO_DATA = "NO_DATA"


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
def _file_lock(lock_path: str):
    """Surecler arasi kilit. Windows msvcrt, POSIX fcntl.

    Atomik yazma tek basina yetmez: eszamanli iki surec ayni degeri okuyup ayri
    ayri artirabilir ve butce iki katina cikar.
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
        yield handle
    except Exception as exc:
        # Kilit mekanizmasi yoksa is durmamali, ama SESSIZ de kalmamali:
        # kilitsiz kosmak butce asimi riskidir.
        logger.warning(f"AV kota kilidi alinamadi ({exc}) , kilitsiz devam ediliyor")
        yield handle
    finally:
        if handle is not None:
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
    ) -> None:
        self.path = path or self._default_path()
        self.lock_path = self.path + ".lock"
        self.budget = int(budget if budget is not None else self._default_budget())
        self.profile = profile or self._default_profile()
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

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
            # Config okunamazsa DAR taraf: en kucuk makul pay.
            return 12

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
        }

    def _oku(self, handle) -> dict:
        """Kaydi okur. Bozuksa gun TUKENMIS sayilir (fail-closed)."""
        gun = utc_day(self._now_fn())
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                ham = json.load(f)
            if not isinstance(ham, dict):
                raise ValueError("kayit sozluk degil")
        except FileNotFoundError:
            return self._bos_kayit(gun)
        except Exception as exc:
            # FAIL-CLOSED: ne kadar harcandigini bilemiyoruz. Bugunu tukenmis
            # say ve YENI bir kayit yaz ki yarin UTC gun donusuyle kendiliginden
            # iyilessin (aksi halde kalici kilitlenme olurdu).
            logger.warning(
                f"AV kota sayaci okunamadi ({exc}) , BUGUN TUKENMIS sayiliyor. "
                f"Yuk cache'i bundan bagimsizdir ve etkilenmez."
            )
            return self._bos_kayit(gun, tumu_harcanmis=True)

        if str(ham.get("utc_day")) != gun:
            return self._bos_kayit(gun)          # UTC gun dondu, sayac sifirlanir
        if str(ham.get("profile")) != self.profile:
            return self._bos_kayit(gun)          # baska profilin dosyasi

        kayit = self._bos_kayit(gun)
        kayit["budget"] = self.budget
        ham_used = ham.get("used", {})
        if isinstance(ham_used, dict):
            for c in CONSUMERS:
                try:
                    kayit["used"][c] = max(0, int(ham_used.get(c, 0) or 0))
                except (TypeError, ValueError):
                    kayit["used"][c] = 0
        kayit["total_used"] = sum(kayit["used"].values())
        kayit["corrupt_recovered"] = bool(ham.get("corrupt_recovered", False))
        return kayit

    def _yaz(self, kayit: dict) -> None:
        """Atomik yazma , yarim dosya birakma."""
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            dizin = os.path.dirname(self.path) or "."
            fd, gecici = tempfile.mkstemp(dir=dizin, prefix=".avq", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(kayit, f, indent=2)
                os.replace(gecici, self.path)
            except Exception:
                try:
                    os.unlink(gecici)
                except Exception:
                    pass
                raise
        except Exception as exc:
            logger.warning(f"AV kota sayaci yazilamadi: {exc}")

    # -------------------------------------------------- genel API

    def try_reserve(self, consumer: str, count: int = 1) -> bool:
        """Cagridan ONCE kota rezerve eder. False ise AG CAGRISI YAPILMAMALI.

        Rezervasyon cagri oncesi yazilir: sonra yazilsaydi, cagri sirasinda
        surec olurse kota harcandigi halde sayilmamis olurdu.
        """
        if consumer not in CONSUMERS:
            logger.warning(
                f"AV kota: tanimsiz tuketici '{consumer}' , butce disinda kalmasin "
                f"diye CONSUMERS'a eklenmeli. Simdilik reddediliyor."
            )
            return False
        with _file_lock(self.lock_path) as handle:
            kayit = self._oku(handle)

            # Bozuk dosyadan kurtarilan kayit DISKE YAZILMALI. Yazilmazsa dosya
            # bozuk kalir, her gun yeniden "bugun tukenmis" okunur ve fail-closed
            # KALICI KILITLENMEYE donusur. Yazinca yarin UTC gun donusuyle
            # kendiliginden iyilesir.
            if kayit.get("corrupt_recovered"):
                self._yaz(kayit)
                return False

            if kayit["total_used"] + count > kayit["budget"]:
                return False
            kayit["used"][consumer] += count
            kayit["total_used"] += count
            self._yaz(kayit)
            return True

    def remaining(self) -> int:
        with _file_lock(self.lock_path) as handle:
            kayit = self._oku(handle)
            return max(0, kayit["budget"] - kayit["total_used"])

    def snapshot(self) -> dict:
        with _file_lock(self.lock_path) as handle:
            return self._oku(handle)


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
