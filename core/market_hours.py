"""
Market Hours — NYSE/NASDAQ Piyasa Saatleri Kontrolü

US Eastern Time bazlı piyasa durumu:
  - Pre-market:  04:00 - 09:30 ET
  - Regular:     09:30 - 16:00 ET
  - After-hours: 16:00 - 20:00 ET
  - Kapalı:      20:00 - 04:00 ET + Hafta sonu + Tatiller
"""
from datetime import datetime, time, date, timedelta
from typing import Dict, Tuple
import pytz

from utils.logger import logger

# NYSE tatil günleri (2026)
NYSE_HOLIDAYS_2026 = [
    date(2026, 1, 1),   # New Year's Day
    date(2026, 1, 19),  # MLK Day
    date(2026, 2, 16),  # Presidents' Day
    date(2026, 4, 3),   # Good Friday
    date(2026, 5, 25),  # Memorial Day
    date(2026, 7, 3),   # Independence Day (observed)
    date(2026, 9, 7),   # Labor Day
    date(2026, 11, 26), # Thanksgiving
    date(2026, 12, 25), # Christmas
]

# 2027 tatilleri (ileriye dönük)
NYSE_HOLIDAYS_2027 = [
    date(2027, 1, 1),   # New Year's Day
    date(2027, 1, 18),  # MLK Day
    date(2027, 2, 15),  # Presidents' Day
    date(2027, 3, 26),  # Good Friday
    date(2027, 5, 31),  # Memorial Day
    date(2027, 7, 5),   # Independence Day (observed)
    date(2027, 9, 6),   # Labor Day
    date(2027, 11, 25), # Thanksgiving
    date(2027, 12, 24), # Christmas (observed)
]

ALL_HOLIDAYS = set(NYSE_HOLIDAYS_2026 + NYSE_HOLIDAYS_2027)

# v4.8: NYSE YARIM GÜNLERİ (erken kapanış 13:00 ET) — statik fallback.
# Kural: Şükran Günü ertesi Cuma + Noel arifesi (hafta içiyse).
# (2026'da 3 Temmuz tam tatil — gözetlenen Bağımsızlık Günü; yarım gün değil.
#  2027'de Noel Cts→24 Ara gözetlenen tatil, dolayısıyla arife yarım günü yok.)
NYSE_EARLY_CLOSE = {
    date(2026, 11, 27): time(13, 0),  # Thanksgiving ertesi
    date(2026, 12, 24): time(13, 0),  # Noel arifesi (Perşembe)
    date(2027, 11, 26): time(13, 0),  # Thanksgiving ertesi
}

ET = pytz.timezone("US/Eastern")


class MarketHours:
    """NYSE/NASDAQ piyasa saatleri kontrolü.

    v4.8: Alpaca takvimi (varsa) günün GERÇEK açılış/kapanış saatini verir —
    yarım günlerde (13:00 kapanış) bot eskiden 16:00'ya kadar "açık" sanıyordu:
    ölü seansta emir/stop yönetimi yapmaya çalışıyordu. Takvim erişilemezse
    statik tatil + yarım-gün listeleriyle çalışır (fail-safe).
    """

    # Saat aralıkları (Eastern Time)
    PRE_MARKET_OPEN = time(4, 0)
    MARKET_OPEN = time(9, 30)
    MARKET_CLOSE = time(16, 0)
    AFTER_HOURS_CLOSE = time(20, 0)

    # Trading güvenli bölge (ilk 30dk volatil, son 15dk riskli)
    SAFE_TRADING_START = time(10, 0)
    SAFE_TRADING_END = time(15, 45)

    # Kapanışa bu kadar kala yeni işlem durur (güvenli bölge sonu)
    SAFE_END_BUFFER_MIN = 15

    def __init__(self, trading_client=None):
        # Alpaca TradingClient (opsiyonel) — günün gerçek seans saatleri için
        self._trading_client = trading_client
        self._session_cache = None      # (et_date, open_t, close_t, is_trading_day)
        self._last_calendar_attempt = datetime.min
        logger.info(
            "MarketHours baslatildi — NYSE/NASDAQ saatleri aktif"
            + (" + Alpaca takvim" if trading_client else " (statik takvim)")
        )

    def now_et(self) -> datetime:
        """Şu anki zamanı ET olarak döndür."""
        return datetime.now(ET)

    # ------------------------------------------------------------
    # v4.8: Günün gerçek seansı (Alpaca takvimi → statik fallback)
    # ------------------------------------------------------------

    def _today_session(self) -> Tuple[bool, time, time]:
        """Bugünün (ET) seansı: (işlem günü mü, açılış, kapanış).

        Önce Alpaca takviminden (günde 1 fetch; hata olursa 30 dk'da bir yeniden
        dener), yoksa statik tatil/yarım-gün listelerinden.
        """
        today = self.now_et().date()

        # Cache güncel mi?
        if self._session_cache and self._session_cache[0] == today:
            _, open_t, close_t, is_day = self._session_cache
            return is_day, open_t, close_t

        # Alpaca takviminden dene (başarısızsa 30 dk'da bir yeniden dener,
        # aradaki çağrılar statik fallback'e düşer — API'yi dövmeyiz)
        if self._trading_client is not None:
            since_last = (datetime.now() - self._last_calendar_attempt).total_seconds()
            if since_last >= 1800:
                self._last_calendar_attempt = datetime.now()
                try:
                    from alpaca.trading.requests import GetCalendarRequest
                    sessions = self._trading_client.get_calendar(
                        GetCalendarRequest(start=today, end=today)
                    )
                    if sessions:
                        s = sessions[0]
                        open_t = s.open.time() if hasattr(s.open, "time") else self.MARKET_OPEN
                        close_t = s.close.time() if hasattr(s.close, "time") else self.MARKET_CLOSE
                        self._session_cache = (today, open_t, close_t, True)
                        if close_t != self.MARKET_CLOSE:
                            logger.info(
                                f"  📅 NYSE ERKEN KAPANIŞ (Alpaca takvim): bugün "
                                f"{close_t.strftime('%H:%M')} ET'de kapanıyor"
                            )
                        return True, open_t, close_t
                    else:
                        # Takvimde bugün yok = işlem günü değil (tatil/hafta sonu)
                        self._session_cache = (today, self.MARKET_OPEN, self.MARKET_CLOSE, False)
                        return False, self.MARKET_OPEN, self.MARKET_CLOSE
                except Exception as e:
                    logger.debug(f"  Alpaca takvim hatası (statik fallback): {e}")

        # Statik fallback
        is_day = today.weekday() < 5 and today not in ALL_HOLIDAYS
        close_t = NYSE_EARLY_CLOSE.get(today, self.MARKET_CLOSE)
        # Statik sonucu da cache'le ki her döngüde hesaplanmasın (takvim yoksa)
        if self._trading_client is None:
            self._session_cache = (today, self.MARKET_OPEN, close_t, is_day)
        return is_day, self.MARKET_OPEN, close_t

    def get_market_status(self) -> Dict:
        """
        Piyasa durumu:
        Returns:
            {
                'status': 'PRE_MARKET' | 'OPEN' | 'AFTER_HOURS' | 'CLOSED',
                'is_trading_allowed': bool,
                'is_safe_zone': bool,  (volatil açılış/kapanış hariç)
                'next_event': str,
                'time_et': str,
            }
        """
        now = self.now_et()
        current_time = now.time()
        current_date = now.date()
        weekday = now.weekday()  # 0=Mon, 6=Sun

        # Hafta sonu
        if weekday >= 5:
            return {
                "status": "CLOSED",
                "is_trading_allowed": False,
                "is_safe_zone": False,
                "reason": "Hafta sonu",
                "next_event": "Pazartesi 09:30 ET açılış",
                "time_et": now.strftime("%H:%M ET"),
            }

        # v4.8: günün gerçek seansı (Alpaca takvim → statik; yarım günleri bilir)
        is_trading_day, session_open, session_close = self._today_session()

        # Tatil kontrolü (takvim "bugün seans yok" dediyse veya statik listede)
        if not is_trading_day or current_date in ALL_HOLIDAYS:
            return {
                "status": "CLOSED",
                "is_trading_allowed": False,
                "is_safe_zone": False,
                "reason": "NYSE tatili",
                "next_event": "Sonraki iş günü 09:30 ET açılış",
                "time_et": now.strftime("%H:%M ET"),
            }

        # Güvenli bölge sonu: kapanıştan SAFE_END_BUFFER_MIN dk önce
        # (yarım günde 12:45, normal günde 15:45)
        safe_end = (
            datetime.combine(current_date, session_close)
            - timedelta(minutes=self.SAFE_END_BUFFER_MIN)
        ).time()

        # Pre-market
        if self.PRE_MARKET_OPEN <= current_time < session_open:
            return {
                "status": "PRE_MARKET",
                "is_trading_allowed": False,  # Normal modda pre-market'te işlem yok
                "is_safe_zone": False,
                "reason": "Pre-market (sadece olağanüstü fırsatlarda)",
                "next_event": f"Açılış {session_open.strftime('%H:%M')} ET",
                "time_et": now.strftime("%H:%M ET"),
            }

        # Regular market
        if session_open <= current_time < session_close:
            is_safe = self.SAFE_TRADING_START <= current_time < safe_end
            early_note = "" if session_close == self.MARKET_CLOSE else " — YARIM GÜN"
            return {
                "status": "OPEN",
                "is_trading_allowed": True,
                "is_safe_zone": is_safe,
                "reason": "Piyasa açık" + (" (güvenli bölge)" if is_safe else " (volatil bölge)") + early_note,
                "next_event": f"Kapanış {session_close.strftime('%H:%M')} ET",
                "time_et": now.strftime("%H:%M ET"),
            }

        # After-hours (yarım günde erken kapanıştan itibaren)
        if session_close <= current_time < self.AFTER_HOURS_CLOSE:
            return {
                "status": "AFTER_HOURS",
                "is_trading_allowed": False,  # Normal modda after-hours'da işlem yok
                "is_safe_zone": False,
                "reason": "After-hours (sadece olağanüstü fırsatlarda)",
                "next_event": f"Kapanış {self.AFTER_HOURS_CLOSE.strftime('%H:%M')} ET",
                "time_et": now.strftime("%H:%M ET"),
            }

        # Gece — piyasa tamamen kapalı
        return {
            "status": "CLOSED",
            "is_trading_allowed": False,
            "is_safe_zone": False,
            "reason": "Piyasa kapalı",
            "next_event": "Pre-market 04:00 ET",
            "time_et": now.strftime("%H:%M ET"),
        }

    def is_market_open(self) -> bool:
        """Piyasa açık mı?"""
        return self.get_market_status()["status"] == "OPEN"

    def is_safe_to_trade(self) -> bool:
        """Güvenli bölgede miyiz? (10:00-15:45 ET)"""
        status = self.get_market_status()
        return status["is_trading_allowed"] and status["is_safe_zone"]

    def should_allow_extended_hours(self, signal_confidence: float) -> bool:
        """
        Pre/After market'te işlem yapılmalı mı?
        Sadece çok güçlü sinyallerde (confidence >= 80%) izin ver.
        """
        status = self.get_market_status()
        if status["status"] in ("PRE_MARKET", "AFTER_HOURS"):
            if signal_confidence >= 80:
                logger.warning(
                    f"  EXTENDED HOURS: {status['status']} — "
                    f"Güven %{signal_confidence:.0f} ≥ 80%, işleme izin veriliyor"
                )
                return True
        return False

    def seconds_until_open(self) -> int:
        """Piyasa açılışına kaç saniye var?"""
        now = self.now_et()
        if self.is_market_open():
            return 0
        
        # Bugün açılacaksa
        today_open = now.replace(
            hour=self.MARKET_OPEN.hour,
            minute=self.MARKET_OPEN.minute,
            second=0, microsecond=0
        )
        
        if now < today_open and now.weekday() < 5:
            return int((today_open - now).total_seconds())
        
        # Yarın veya sonraki iş günü
        days_ahead = 1
        while True:
            next_day = now + timedelta(days=days_ahead)
            if next_day.weekday() < 5 and next_day.date() not in ALL_HOLIDAYS:
                next_open = next_day.replace(
                    hour=self.MARKET_OPEN.hour,
                    minute=self.MARKET_OPEN.minute,
                    second=0, microsecond=0
                )
                return int((next_open - now).total_seconds())
            days_ahead += 1
            if days_ahead > 7:
                return 86400  # fallback: 1 gün
