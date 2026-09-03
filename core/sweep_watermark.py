"""R17 , defter supurgesi icin TAAHHUT EDILMIS, ortusme-guvenli yuksek-su isareti.

NEDEN SABIT PENCERE YETMEZ
--------------------------
PLAN.md v4.18 supurge penceresini "ACIK KALAN RISK" olarak kaydetmisti: bot 24
saatten uzun kapali kalirsa o sure icindeki dolumlar acilis supurgesinin
menzili disinda kalir ve defterde KALICI DELIK olusur.

Pencereyi 72 saate cikarmak COZUM DEGIL: 73 saatlik bir kesinti ayni deligi
acar, ve "72/24 degerleri config'de dogru mu" diye bakan bir test bunu
GORMEZ , kanit gecerken ozellik bozuk kalir.

Cozum: nereye kadar BASARIYLA supuruldugunu KALICI olarak tut ve bir dahaki
sefer ORADAN basla. Pencere degil, isaret.

TAAHHUT KURALI (kritik)
-----------------------
Isaret YALNIZ su iki sart birden saglandiginda ilerler:
  1. Broker sayfalarinin TAMAMI eksiksiz alindi (sayfa ortasi hata yok)
  2. Gereken BUTUN defter yazmalari basarili oldu
Biri bile tutmazsa isaret ILERLEMEZ ve bir sonraki kosu ayni araligi yeniden
tarar. Ortusme zararsizdir (dedupe var); BOSLUK kalicidir.

ILK ACILIS ve RETANSIYON
------------------------
Isaret yoksa tanimli bir sinirdan baslanir (olcum epoch'u ya da dogrulanmis
backfill siniri). Kesinti broker retansiyonundan eskiyse eksiksiz kurtarma
IMKANSIZDIR; bu durum DEGRADED olarak raporlanir, sessizce "tamam" denmez.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from utils.logger import logger

SCHEMA_VERSION = 1

# Alpaca hesap aktivitelerinin pratik retansiyonu. Bundan eski bir kesinti
# eksiksiz kurtarilamaz , dolayisiyla sessizce basarili sayilamaz.
DEFAULT_RETENTION_DAYS = 90


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class SweepWatermark:
    """Kalici, taahhut edilmis supurge yuksek-su isareti."""

    def __init__(
        self,
        path: Optional[str] = None,
        *,
        retention_days: float = DEFAULT_RETENTION_DAYS,
        now_fn=None,
    ) -> None:
        self.path = path or self._default_path()
        self.retention_days = float(retention_days)
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _default_path() -> str:
        try:
            from config import state_path
            return state_path("sweep_watermark.json")
        except Exception:
            return "sweep_watermark.json"

    # ------------------------------------------------------------------ okuma

    def read(self) -> Optional[datetime]:
        """Son TAAHHUT EDILMIS supurge sinirini doner; yoksa None."""
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                ham = json.load(f)
            if not isinstance(ham, dict):
                return None
            if int(ham.get("schema_version", 0)) != SCHEMA_VERSION:
                return None
            ts = ham.get("committed_until")
            if not isinstance(ts, str):
                return None
            dt = _as_utc(datetime.fromisoformat(ts))
            if dt is None:
                return None
            # GELECEK tarihli isaret guvenilmez: kabul edilirse arasindaki
            # butun dolumlar sonsuza dek atlanir.
            if dt > self._now_fn() + timedelta(minutes=5):
                logger.warning(
                    "Supurge isareti GELECEK tarihli , yok sayiliyor "
                    f"({ts}); tanimli sinirdan baslanacak"
                )
                return None
            return dt
        except FileNotFoundError:
            return None
        except Exception as exc:
            logger.warning(f"Supurge isareti okunamadi ({exc}) , yok sayiliyor")
            return None

    # ------------------------------------------------------------------ yazma

    def commit(
        self,
        until: datetime,
        *,
        pages_complete: bool,
        writes_ok: bool,
    ) -> bool:
        """Isareti ILERLET , yalniz iki sart birden saglaniyorsa.

        Donen deger: isaret gercekten ilerledi mi. Cagiran buna bakmali;
        "commit cagirdim" demek ilerledigini KANITLAMAZ.
        """
        if not pages_complete:
            logger.warning(
                "  SUPURGE ISARETI ILERLEMEDI: broker sayfalari eksik , "
                "bir sonraki kosu ayni araligi yeniden tarayacak"
            )
            return False
        if not writes_ok:
            logger.warning(
                "  SUPURGE ISARETI ILERLEMEDI: defter yazmalari basarisiz , "
                "bir sonraki kosu ayni araligi yeniden tarayacak"
            )
            return False

        until = _as_utc(until)
        if until is None:
            return False

        onceki = self.read()
        if onceki is not None and until <= onceki:
            return True          # geriye gitme yok; zaten ilerideyiz

        kayit = {
            "schema_version": SCHEMA_VERSION,
            "committed_until": until.isoformat(),
            "committed_at": self._now_fn().isoformat(),
        }
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            dizin = os.path.dirname(self.path) or "."
            fd, gecici = tempfile.mkstemp(dir=dizin, prefix=".swm", suffix=".tmp")
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
            logger.warning(f"  SUPURGE ISARETI YAZILAMADI: {exc}")
            return False

    # ------------------------------------------------------------------ plan

    def plan_window(
        self,
        until: datetime,
        *,
        bootstrap_from: Optional[datetime] = None,
        min_window_hours: float = 24.0,
    ) -> Tuple[datetime, str, bool]:
        """Bu kosunun tarayacagi araligi belirler.

        Doner: (cutoff, durum, eksiksiz_kurtarilabilir)
          durum: "ISARET" | "ILK_ACILIS" | "RETANSIYON_ASILDI"

        `eksiksiz_kurtarilabilir` False ise kesinti broker retansiyonundan
        eskidir ve bu kosu KESIN OLARAK bir sey kacirir. Cagiran bunu DEGRADED
        raporlamali, sessizce basarili saymamali.
        """
        until = _as_utc(until) or self._now_fn()
        retansiyon_siniri = until - timedelta(days=self.retention_days)

        isaret = self.read()
        if isaret is None:
            taban = _as_utc(bootstrap_from) or (
                until - timedelta(hours=min_window_hours)
            )
            # Ilk acilista bile retansiyonun disina cikma
            cutoff = max(taban, retansiyon_siniri)
            eksiksiz = taban >= retansiyon_siniri
            return cutoff, "ILK_ACILIS", eksiksiz

        if isaret < retansiyon_siniri:
            # Kesinti retansiyondan eski: arada KALICI delik var, kapatilamaz.
            logger.warning(
                f"  SUPURGE: son taahhut {isaret.isoformat()} broker "
                f"retansiyonunun ({self.retention_days:.0f} gun) DISINDA , "
                f"eksiksiz kurtarma IMKANSIZ, DEGRADED"
            )
            return retansiyon_siniri, "RETANSIYON_ASILDI", False

        # Normal yol: isaretten basla. Ortusme icin biraz geriye sark;
        # dedupe zaten var, boşluk ise kalicidir.
        cutoff = min(isaret, until - timedelta(hours=min_window_hours))
        return max(cutoff, retansiyon_siniri), "ISARET", True
