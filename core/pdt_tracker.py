"""
PDT Tracker — Pattern Day Trader Kuralı Takibi

FINRA Kuralı 4210:
  - $25K altı hesaplarda 5 iş günü içinde MAX 3 day trade
  - Day trade = aynı gün içinde al-sat (pozisyon açıp aynı gün kapat)
  - 4. day trade → hesap kilitlenir (sadece satış yapılabilir)

Bot Stratejisi:
  - MAX 2 day trade/hafta (güvenlik marjı — limiti asla zorlama)
  - Day trade sınırı aşılırsa pozisyonu overnight tut
  - Alpaca 403 hatası handle et
"""
import json
import os
from datetime import datetime, date, timedelta
from typing import Dict, List, Tuple
from utils.logger import logger


class PDTTracker:
    """Pattern Day Trader kuralı takibi ve day trade sayacı."""

    MAX_DAY_TRADES_PER_WEEK = 2  # Güvenlik marjı (FINRA limiti 3)
    PDT_EQUITY_THRESHOLD = 25_000  # $25K üzerinde PDT kuralı geçersiz
    STATE_FILE = "pdt_state.json"

    def __init__(self, equity: float = 0, state_file: str = None):
        self.equity = equity
        if state_file is None:
            try:
                from config import state_path
                state_file = state_path("pdt_state.json")
            except Exception:
                state_file = self.STATE_FILE
        self.STATE_FILE = state_file  # instance, live/paper izole
        self.day_trades: List[Dict] = []  # [{date, symbol, buy_time, sell_time}]
        self._load_state()
        logger.info(
            f"PDT Tracker baslatildi — "
            f"Equity: ${equity:,.2f} | "
            f"PDT {'EXEMPT' if equity >= self.PDT_EQUITY_THRESHOLD else 'ACTIVE'} | "
            f"Haftalik DT: {self.get_week_day_trade_count()}/{self.MAX_DAY_TRADES_PER_WEEK}"
        )

    # ============================================================
    # DAY TRADE TAKİBİ
    # ============================================================

    def can_day_trade(self) -> Tuple[bool, str]:
        """
        Yeni bir day trade yapılabilir mi?
        
        Returns:
            (allowed: bool, reason: str)
        """
        # $25K üzeri → PDT kuralı uygulanmaz
        if self.equity >= self.PDT_EQUITY_THRESHOLD:
            return True, "PDT exempt (equity >= $25K)"

        week_count = self.get_week_day_trade_count()

        if week_count >= self.MAX_DAY_TRADES_PER_WEEK:
            return False, (
                f"PDT LIMIT: Bu hafta {week_count}/{self.MAX_DAY_TRADES_PER_WEEK} "
                f"day trade kullanildi. Pozisyon overnight tutulmali."
            )

        remaining = self.MAX_DAY_TRADES_PER_WEEK - week_count
        return True, f"Day trade mümkün ({remaining} kalan bu hafta)"

    def record_day_trade(self, symbol: str, buy_time: str, sell_time: str):
        """Yapılan day trade'i kaydet."""
        trade = {
            "date": date.today().isoformat(),
            "symbol": symbol,
            "buy_time": buy_time,
            "sell_time": sell_time,
        }
        self.day_trades.append(trade)
        self._save_state()

        week_count = self.get_week_day_trade_count()
        logger.warning(
            f"  DAY TRADE KAYDEDILDI: {symbol} | "
            f"Haftalik: {week_count}/{self.MAX_DAY_TRADES_PER_WEEK} | "
            f"{'⚠️ LIMIT YAKIN!' if week_count >= self.MAX_DAY_TRADES_PER_WEEK - 1 else 'OK'}"
        )

    def is_same_day_position(self, symbol: str, entry_time: str) -> bool:
        """
        Bir pozisyon bugün mü açıldı?
        Eğer evet → satarsan day trade sayılır.
        """
        try:
            entry_date = datetime.fromisoformat(entry_time).date()
            return entry_date == date.today()
        except (ValueError, TypeError):
            return False

    def should_hold_overnight(self, symbol: str, entry_time: str) -> Tuple[bool, str]:
        """
        Pozisyon aynı gün açıldıysa ve day trade limiti dolduysa,
        satış yerine overnight tutmayı öner.
        """
        if not self.is_same_day_position(symbol, entry_time):
            return False, "Pozisyon dün veya daha önce açıldı, satış güvenli"

        can_dt, reason = self.can_day_trade()
        if not can_dt:
            return True, (
                f"PDT KORUMA: {symbol} bugün alındı, satarsan day trade sayılır. "
                f"{reason} — Pozisyon overnight tutulacak."
            )
        return False, "Day trade limiti müsait, satış yapılabilir"

    # ============================================================
    # SAYAÇLAR
    # ============================================================

    def get_week_day_trade_count(self) -> int:
        """Son 5 iş günündeki day trade sayısı."""
        self._cleanup_old_trades()
        cutoff = date.today() - timedelta(days=7)  # yaklaşık 5 iş günü
        return sum(1 for t in self.day_trades
                   if date.fromisoformat(t["date"]) >= cutoff)

    def get_today_day_trade_count(self) -> int:
        """Bugünkü day trade sayısı."""
        today = date.today().isoformat()
        return sum(1 for t in self.day_trades if t["date"] == today)

    def update_equity(self, equity: float):
        """Equity güncelle (PDT exemption kontrolü için)."""
        old_exempt = self.equity >= self.PDT_EQUITY_THRESHOLD
        self.equity = equity
        new_exempt = equity >= self.PDT_EQUITY_THRESHOLD

        if old_exempt != new_exempt:
            if new_exempt:
                logger.info(f"  PDT EXEMPT: Equity ${equity:,.2f} >= $25K — day trade limiti kaldırıldı!")
            else:
                logger.warning(f"  PDT ACTIVE: Equity ${equity:,.2f} < $25K — max {self.MAX_DAY_TRADES_PER_WEEK} DT/hafta")

    # ============================================================
    # ALPACA 403 HANDLER
    # ============================================================

    def handle_pdt_rejection(self, symbol: str, error_msg: str) -> str:
        """
        Alpaca 403 (PDT violation) hatasını handle et.
        
        Returns:
            Kullanıcıya gösterilecek açıklama
        """
        logger.error(
            f"  ⚠️ PDT VIOLATION: Alpaca {symbol} satışını reddetti!\n"
            f"  Hata: {error_msg}\n"
            f"  Çözüm: Pozisyon overnight tutulacak, yarın satılacak."
        )
        return (
            f"PDT kuralı ihlali — {symbol} pozisyonu yarına taşınıyor. "
            f"Haftalık day trade: {self.get_week_day_trade_count()}/3 (FINRA limiti)"
        )

    # ============================================================
    # STATE YÖNETİMİ
    # ============================================================

    def _load_state(self):
        """Kaydedilmiş day trade geçmişini yükle."""
        try:
            if os.path.exists(self.STATE_FILE):
                with open(self.STATE_FILE, "r") as f:
                    data = json.load(f)
                self.day_trades = data.get("day_trades", [])
                self._cleanup_old_trades()
        except Exception as e:
            logger.debug(f"PDT state yüklenemedi: {e}")
            self.day_trades = []

    def _save_state(self):
        """Day trade geçmişini kaydet."""
        try:
            with open(self.STATE_FILE, "w") as f:
                json.dump({
                    "day_trades": self.day_trades,
                    "last_update": datetime.now().isoformat(),
                }, f, indent=2)
        except Exception as e:
            logger.debug(f"PDT state kaydedilemedi: {e}")

    def _cleanup_old_trades(self):
        """14 günden eski day trade kayıtlarını temizle."""
        cutoff = (date.today() - timedelta(days=14)).isoformat()
        self.day_trades = [t for t in self.day_trades if t["date"] >= cutoff]

    def get_status(self) -> Dict:
        """Mevcut PDT durumu özeti."""
        can_dt, reason = self.can_day_trade()
        return {
            "pdt_exempt": self.equity >= self.PDT_EQUITY_THRESHOLD,
            "equity": self.equity,
            "week_day_trades": self.get_week_day_trade_count(),
            "max_day_trades": self.MAX_DAY_TRADES_PER_WEEK,
            "can_day_trade": can_dt,
            "reason": reason,
            "today_day_trades": self.get_today_day_trade_count(),
        }
