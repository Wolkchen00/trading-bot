"""R16 , temel veri disk cache'i: TTL, bayatlik sozlesmesi, yenileme imleci.

NEDEN DISK
----------
Bellek cache'i konteyner restart'inda oluyor ve kota onunla birlikte yeniden
yaniyor. Temel veriler CEYREKLIK degisir, tarama turu basina degil; 24 saatlik
disk cache ile gunluk butce tam olarak evrene yeter.

BAYATLIK SOZLESMESI (stale-while-revalidate)
--------------------------------------------
TTL tek basina "sonra ne olacak" sorusunu cevaplamiyor. Uc bolge var:
  yas <= TTL              -> TAZE, kullanilir
  TTL < yas <= MAX_STALE  -> BAYAT, kullanilir AMA yasi karara iliştirilir
  yas > MAX_STALE         -> SOURCE_UNAVAILABLE, KULLANILMAZ
Suresiz guven yok. Karar veren taraf verinin kac saatlik oldugunu bilmek zorunda.

YENILEME IMLECI
---------------
Sabit sembol sirasi + gunluk butce = her gun ayni ilk semboller tazelenir ve
listenin kuyrugu HIC tazelenmez. Imlec en-eski-once calisir ve DISKE yazilir,
yani restart'i atlatir.

BOZUK DOSYA
-----------
Yuk (payload) cache'i kota sayacindan BAGIMSIZ kurtarilir: cache bozuksa temiz
baslanir (veri kaybi, guvenlik sorunu degil), ama kota sayaci bozuksa gun
tukenmis sayilir (bkz. core/av_quota.py). Ikisi ayni dosyada tutulmaz.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from core.av_quota import utc_day
from utils.logger import logger

SCHEMA_VERSION = 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(str(ts))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


class FundamentalsCache:
    """Kalici temel veri cache'i + negatif cache + yenileme imleci."""

    def __init__(
        self,
        path: Optional[str] = None,
        ttl_hours: Optional[float] = None,
        max_stale_hours: Optional[float] = None,
        now_fn=None,
    ) -> None:
        self.path = path or self._default_path()
        cfg = self._config()
        self.ttl_hours = float(
            ttl_hours if ttl_hours is not None else cfg.get("ttl_hours", 24)
        )
        self.max_stale_hours = float(
            max_stale_hours if max_stale_hours is not None
            else cfg.get("max_stale_hours", 168)
        )
        self._now_fn = now_fn or _now
        self.entries: Dict[str, dict] = {}
        self.negative: Dict[str, str] = {}   # symbol -> tukendigi UTC gun
        self._load()

    @staticmethod
    def _default_path() -> str:
        try:
            from config import state_path
            return state_path("fundamentals_cache.json")
        except Exception:
            return "fundamentals_cache.json"

    @staticmethod
    def _config() -> dict:
        try:
            from config import AV_QUOTA_CONFIG
            return AV_QUOTA_CONFIG if isinstance(AV_QUOTA_CONFIG, dict) else {}
        except Exception:
            return {}

    # -------------------------------------------------- kalicilik

    def _load(self) -> None:
        try:
            if not os.path.exists(self.path):
                return
            with open(self.path, "r", encoding="utf-8") as f:
                ham = json.load(f)
            if not isinstance(ham, dict):
                raise ValueError("kayit sozluk degil")
            girdiler = ham.get("entries", {})
            if isinstance(girdiler, dict):
                self.entries = {
                    str(k): v for k, v in girdiler.items() if isinstance(v, dict)
                }
            negatif = ham.get("negative", {})
            if isinstance(negatif, dict):
                self.negative = {str(k): str(v) for k, v in negatif.items()}
        except Exception as exc:
            # Yuk cache'i bozuksa TEMIZ baslanir. Bu bir veri kaybidir, guvenlik
            # sorunu degil: kota sayaci ayri dosyada ve fail-closed kaliyor.
            logger.warning(
                f"Temel veri cache'i okunamadi ({exc}) , temiz baslaniyor. "
                f"Kota sayaci bundan BAGIMSIZ ve etkilenmedi."
            )
            self.entries = {}
            self.negative = {}

    def save(self) -> bool:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            dizin = os.path.dirname(self.path) or "."
            fd, gecici = tempfile.mkstemp(dir=dizin, prefix=".fc", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "schema_version": SCHEMA_VERSION,
                            "entries": self.entries,
                            "negative": self.negative,
                        },
                        f,
                    )
                os.replace(gecici, self.path)
                return True
            except Exception:
                try:
                    os.unlink(gecici)
                except Exception:
                    pass
                raise
        except Exception as exc:
            logger.debug(f"Temel veri cache'i yazilamadi: {exc}")
            return False

    # -------------------------------------------------- yas ve bolgeler

    def age_hours(self, symbol: str) -> Optional[float]:
        girdi = self.entries.get(symbol)
        if not girdi:
            return None
        alindi = _parse(girdi.get("fetched_at", ""))
        if alindi is None:
            return None
        return (self._now_fn() - alindi).total_seconds() / 3600.0

    def get(self, symbol: str) -> Tuple[Optional[dict], Optional[float], str]:
        """(yuk, yas_saat, bolge) doner.

        bolge: "TAZE" | "BAYAT" | "SOURCE_UNAVAILABLE" | "YOK"
        SOURCE_UNAVAILABLE'da yuk None doner , cok bayat veri KULLANILMAZ.
        """
        yas = self.age_hours(symbol)
        if yas is None:
            return None, None, "YOK"
        girdi = self.entries.get(symbol) or {}
        yuk = girdi.get("payload")
        if yas <= self.ttl_hours:
            return yuk, yas, "TAZE"
        if yas <= self.max_stale_hours:
            return yuk, yas, "BAYAT"
        return None, yas, "SOURCE_UNAVAILABLE"

    def put(self, symbol: str, payload: dict) -> None:
        self.entries[symbol] = {
            "payload": payload,
            "fetched_at": self._now_fn().isoformat(),
        }
        self.negative.pop(symbol, None)
        self.save()

    # -------------------------------------------------- negatif cache

    def mark_quota_exhausted(self, symbol: str) -> None:
        """Yalniz KOTA tukenmesi gun sonuna kadar cache'lenir.

        Gecici hatalar (timeout, HTTP 5xx) buraya GIRMEZ; girseydi gecici bir
        ariza kaynagi gun boyu susturuyor olurdu.
        """
        self.negative[symbol] = utc_day(self._now_fn())
        self.save()

    def is_negative_cached(self, symbol: str) -> bool:
        gun = self.negative.get(symbol)
        return bool(gun) and gun == utc_day(self._now_fn())

    # -------------------------------------------------- yenileme imleci

    def refresh_order(self, universe: Iterable[str]) -> List[str]:
        """En-eski-once sira. Hic cekilmemisler EN BASTA.

        Sabit alfabetik sira + gunluk butce, her gun ayni ilk sembolleri
        harcayip kuyrugu hic tazelemezdi.
        """
        semboller = [s for s in universe]
        def anahtar(s):
            yas = self.age_hours(s)
            # Hic cekilmemis (-1) en once; sonra en eski (buyuk yas) once.
            return (0, 0) if yas is None else (1, -yas)
        return sorted(semboller, key=anahtar)

    def next_refresh_candidates(
        self, universe: Iterable[str], limit: int
    ) -> List[str]:
        """Butce kadar, en-eski-once, negatif cache'lenmisleri atlayarak."""
        if limit <= 0:
            return []
        aday = []
        for s in self.refresh_order(universe):
            if self.is_negative_cached(s):
                continue
            _, yas, bolge = self.get(s)
            if bolge == "TAZE":
                continue          # zaten taze, butce harcama
            aday.append(s)
            if len(aday) >= limit:
                break
        return aday

    # -------------------------------------------------- kapsama telemetrisi

    def coverage(self, universe: Iterable[str]) -> dict:
        """BENZERSIZ sembol sayilari + yas dagilimi.

        Tarama sayisi DEGIL: ayni sembol bir turda defalarca degerlendirilebilir
        ve sayim sisirilirdi.
        """
        semboller = sorted(set(universe))
        taze = bayat = cok_bayat = yok = 0
        yaslar = []
        for s in semboller:
            _, yas, bolge = self.get(s)
            if bolge == "TAZE":
                taze += 1
            elif bolge == "BAYAT":
                bayat += 1
            elif bolge == "SOURCE_UNAVAILABLE":
                cok_bayat += 1
            else:
                yok += 1
            if yas is not None:
                yaslar.append(round(yas, 1))
        return {
            "benzersiz_sembol": len(semboller),
            "taze": taze,
            "bayat": bayat,
            "cok_bayat_kullanilmaz": cok_bayat,
            "verisiz": yok,
            "negatif_cache": sum(
                1 for s in semboller if self.is_negative_cached(s)
            ),
            "yas_saat_min": min(yaslar) if yaslar else None,
            "yas_saat_max": max(yaslar) if yaslar else None,
        }
