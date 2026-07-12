"""
Earnings Calendar — Kazanç Takvimi Takibi (v4.8 yeniden yazım)

Earnings (kazanç raporu) çevresinde hisse fiyatları çok volatil olur; gate
earnings'e ≤2 gün kala yeni pozisyonu engeller.

v4.8 ÖNCESİ SORUNLAR (İhsan'a raporlanan "earnings gate güvenilmez" maddesi):
  - Hisse başına ayrı AV "EARNINGS" çağrısı → 25 sembol × günde 2 (12 sa cache)
    = free-tier 25 istek/gün kotasını aşıyordu; yarısı hep boş dönüyordu.
  - Tarih GERÇEK takvim değildi: son rapor tarihi + 90 gün TAHMİNİ (haftalarca
    şaşabilir → gate ya erken kapanır ya da asıl earnings gününü kaçırır).
  - Yahoo fallback endpoint'i ölü (crumb/consent duvarı) — hiç veri dönmüyordu.

v4.8 TASARIM:
  - AV EARNINGS_CALENDAR endpoint'i TEK çağrıda TÜM sembollerin önümüzdeki
    3 aylık GERÇEK beklenen rapor tarihlerini CSV döner → günde 1 çağrı, kota derdi yok.
  - Takvim state dizinine yazılır (restart'ta yeniden fetch yok), 24 saatte bir tazelenir.
  - Fetch başarısızsa: 7 güne kadar bayat cache ile devam (earnings tarihleri
    haftalar önceden bellidir); o da yoksa FAIL-OPEN (gate serbest bırakır, loglar).
"""
import csv
import io
import json
import os
import requests
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional

from utils.logger import logger


class EarningsCalendar:
    """Earnings takvimi takibi ve earnings-aware trading."""

    CACHE_TTL_HOURS = 24      # takvim bu sıklıkta tazelenir (1 AV çağrısı/gün)
    STALE_OK_DAYS = 7         # fetch başarısızken bayat cache'e tolerans
    RETRY_MINUTES = 30        # başarısız fetch'i bu aralıkla yeniden dene

    def __init__(self):
        self.alpha_vantage_key = os.getenv("ALPHA_VANTAGE_KEY", "")
        self._calendar: Dict[str, List[str]] = {}   # {SEMBOL: ["2026-07-28", ...]}
        self._fetched_at: Optional[datetime] = None
        self._last_attempt = datetime.min
        self._warned_no_data = False

        # Kalıcı cache (state dizini) — restart/redeploy fetch tetiklemez
        try:
            from config import state_path
            self._cache_file = state_path("earnings_calendar.json")
        except Exception:
            self._cache_file = "earnings_calendar.json"
        self._load_disk_cache()

        if self.alpha_vantage_key:
            logger.info(
                "EarningsCalendar baslatildi — AV toplu takvim modu"
                + (f" (cache: {len(self._calendar)} sembol)" if self._calendar else "")
            )
        else:
            logger.info("EarningsCalendar baslatildi — API key yok, gate fail-open")

    # ------------------------------------------------------------
    # Takvim yenileme
    # ------------------------------------------------------------

    def _load_disk_cache(self):
        try:
            if os.path.exists(self._cache_file):
                with open(self._cache_file, "r") as f:
                    data = json.load(f)
                self._calendar = data.get("calendar", {})
                ts = data.get("fetched_at")
                if ts:
                    self._fetched_at = datetime.fromisoformat(ts)
        except Exception as e:
            logger.debug(f"Earnings cache okunamadı: {e}")

    def _save_disk_cache(self):
        try:
            with open(self._cache_file, "w") as f:
                json.dump({
                    "fetched_at": self._fetched_at.isoformat() if self._fetched_at else None,
                    "calendar": self._calendar,
                }, f)
        except Exception as e:
            logger.debug(f"Earnings cache yazılamadı: {e}")

    def _cache_age_hours(self) -> float:
        if not self._fetched_at:
            return float("inf")
        return (datetime.now() - self._fetched_at).total_seconds() / 3600

    def _refresh_if_needed(self):
        """Takvim bayatsa tek toplu AV çağrısıyla yenile (günde ~1)."""
        if not self.alpha_vantage_key:
            return
        if self._cache_age_hours() < self.CACHE_TTL_HOURS:
            return
        if (datetime.now() - self._last_attempt).total_seconds() < self.RETRY_MINUTES * 60:
            return
        self._last_attempt = datetime.now()

        try:
            resp = requests.get(
                "https://www.alphavantage.co/query",
                params={
                    "function": "EARNINGS_CALENDAR",
                    "horizon": "3month",
                    "apikey": self.alpha_vantage_key,
                },
                timeout=20,
            )
            if resp.status_code != 200 or not resp.text or resp.text.lstrip().startswith("{"):
                # JSON dönmesi = hata/limit mesajı (normal yanıt CSV'dir)
                logger.warning(
                    f"Earnings takvimi alınamadı (HTTP {resp.status_code}) — "
                    f"{'bayat cache ile devam' if self._calendar else 'gate fail-open'}"
                )
                return

            # Evrenimizdeki sembollerle sınırla (dosya ~binlerce satır gelir)
            try:
                from config import STOCK_IDS
                universe = set(STOCK_IDS.keys())
            except Exception:
                universe = set()

            new_cal: Dict[str, List[str]] = {}
            reader = csv.DictReader(io.StringIO(resp.text))
            for row in reader:
                sym = (row.get("symbol") or "").strip().upper()
                report_date = (row.get("reportDate") or "").strip()
                if not sym or not report_date:
                    continue
                if universe and sym not in universe:
                    continue
                new_cal.setdefault(sym, []).append(report_date)

            for sym in new_cal:
                new_cal[sym].sort()

            # v4.10 CACHE ZEHİRLENMESİ FIX: AV, dakika/gün kotası dolunca 200 +
            # header-only (boş) CSV dönebiliyor. Eski kod bunu "başarılı yenileme"
            # sayıp DOLU cache'in üstüne {} yazıyordu — 09-10 Tem'de canlı+paper
            # "0 sembol" ile kör kaldı (temmuz kazanç sezonu kapıdayken). Boş
            # sonuç = başarısız fetch: eski takvim korunur, fetched_at ilerlemez
            # (bayat-cache toleransı + 30dk retry devrede kalır).
            if not new_cal:
                logger.warning(
                    "Earnings takvimi BOŞ döndü (muhtemel AV kota/limit) — "
                    f"{'eski takvim korunuyor' if self._calendar else 'gate fail-open'}"
                )
                return

            self._calendar = new_cal
            self._fetched_at = datetime.now()
            self._warned_no_data = False
            self._save_disk_cache()
            logger.info(
                f"  📅 Earnings takvimi yenilendi: {len(new_cal)} sembol, "
                f"{sum(len(v) for v in new_cal.values())} rapor tarihi (3 ay)"
            )
        except Exception as e:
            logger.warning(f"Earnings takvim fetch hatası: {e} — mevcut cache ile devam")

    def ensure_fresh(self):
        """Takvimi gerekiyorsa yenile — sabah taraması çağırır (v4.10).

        Amaç: yenilemeyi AV kotası TAZEyken (pre-market, henüz haber/overview
        çağrıları kotayı yakmadan) yapmak. Eski akışta ilk yenileme tarama
        ortasında (ilk gate kontrolünde) tetikleniyordu; o saatte kota çoktan
        bitmiş oluyor ve boş CSV dönüyordu.
        """
        self._refresh_if_needed()

    # ------------------------------------------------------------
    # Sorgular
    # ------------------------------------------------------------

    def get_upcoming_earnings(self, symbol: str) -> Optional[Dict]:
        """
        Hissenin bir SONRAKİ beklenen earnings raporunu döndür.

        Returns:
            {'date': str, 'days_until': int, 'is_near': bool, 'source': str}
            veya None (takvimde yok / veri erişilemez → çağıran fail-open uygular)
        """
        self._refresh_if_needed()

        # Cache kullanılamayacak kadar bayatsa güvenilir veri yok → None (fail-open)
        if self._cache_age_hours() > self.STALE_OK_DAYS * 24:
            if self._calendar and not self._warned_no_data:
                logger.warning(
                    f"  Earnings takvimi {self._cache_age_hours()/24:.0f} gündür "
                    f"yenilenemedi — gate fail-open çalışıyor"
                )
                self._warned_no_data = True
            return None

        dates = self._calendar.get(symbol.upper())
        if not dates:
            return None

        today = date.today()
        for d_str in dates:
            try:
                d = datetime.strptime(d_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            if d >= today:
                days_until = (d - today).days
                return {
                    "date": d.isoformat(),
                    "days_until": days_until,
                    "is_near": days_until <= 2,
                    "source": "av_calendar",
                }
        return None  # takvim penceresinde (3 ay) rapor yok

    def should_avoid_trading(self, symbol: str) -> tuple:
        """
        Earnings yakınsa trading'den kaçınılmalı mı?

        Returns:
            (should_avoid: bool, reason: str)
        """
        earnings = self.get_upcoming_earnings(symbol)

        if earnings is None:
            return False, "Earnings verisi yok (3 ay içinde rapor yok/veri yok), trading serbest"

        days = earnings.get("days_until", 999)

        if days <= 0:
            return True, f"EARNINGS BUGÜN ({earnings['date']})! {symbol} — çok volatil, alım yok"
        elif days <= 1:
            return True, f"Earnings YARIN ({earnings['date']})! {symbol} — yeni pozisyon açma"
        elif days <= 2:
            return True, f"Earnings {days} gün içinde ({earnings['date']})! {symbol} — bekle"

        return False, f"Earnings {days} gün sonra ({earnings['date']}), trading serbest"
