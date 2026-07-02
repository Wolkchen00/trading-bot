"""
Performance Tracker — Haftalık/Aylık P&L Takibi

İşlem geçmişini JSON olarak saklar ve performans metrikleri hesaplar:
  - Win rate, avg win, avg loss, profit factor
  - Sharpe ratio (basitleştirilmiş)
  - Drawdown takibi
  - Sektör bazlı performans
  - En iyi/kötü işlemler
"""
import json
import os
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional
from collections import defaultdict
from utils.logger import logger


class PerformanceTracker:
    """İşlem performansı takibi ve raporlama."""

    HISTORY_FILE = "trade_history.json"

    def __init__(self, history_file: str = None):
        if history_file is None:
            try:
                from config import state_path
                history_file = state_path("trade_history.json")
            except Exception:
                history_file = self.HISTORY_FILE
        self.HISTORY_FILE = history_file  # instance, live/paper izole
        self.trades: List[Dict] = self._load()
        self.peak_equity = 0.0
        self.max_drawdown = 0.0
        logger.info(f"PerformanceTracker başlatıldı — {len(self.trades)} geçmiş işlem")

    def _load(self) -> List[Dict]:
        if os.path.exists(self.HISTORY_FILE):
            try:
                with open(self.HISTORY_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def _save(self):
        try:
            with open(self.HISTORY_FILE, "w") as f:
                json.dump(self.trades, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Trade history kayıt hatası: {e}")

    def record_trade(self, symbol: str, action: str, qty: float,
                     price: float, pnl: float = 0, reason: str = "",
                     sector: str = ""):
        """İşlem kaydı ekle."""
        trade = {
            "symbol": symbol,
            "action": action,
            "qty": qty,
            "price": price,
            "pnl": round(pnl, 2),
            "reason": reason,
            "sector": sector,
            "timestamp": datetime.now().isoformat(),
            "date": date.today().isoformat(),
        }
        self.trades.append(trade)
        self._save()
        return trade

    def update_equity(self, equity: float):
        """Equity takibi — drawdown hesabı için."""
        if equity > self.peak_equity:
            self.peak_equity = equity
        if self.peak_equity > 0:
            dd = (self.peak_equity - equity) / self.peak_equity
            if dd > self.max_drawdown:
                self.max_drawdown = dd

    # ============================================================
    # PERFORMANS METRİKLERİ
    # ============================================================

    def get_stats(self, days: int = None) -> Dict:
        """
        Performans istatistikleri.
        days: Son N gün (None = tüm zamanlar)
        """
        trades = self.trades
        if days:
            cutoff = (date.today() - timedelta(days=days)).isoformat()
            trades = [t for t in trades if t.get("date", "") >= cutoff]

        sells = [t for t in trades if t.get("action") == "SELL" and "pnl" in t]

        if not sells:
            return {
                "total_trades": 0, "wins": 0, "losses": 0,
                "win_rate": 0, "total_pnl": 0, "avg_win": 0,
                "avg_loss": 0, "profit_factor": 0,
                "best_trade": 0, "worst_trade": 0,
                "max_drawdown_pct": round(self.max_drawdown * 100, 2),
            }

        wins = [t for t in sells if t["pnl"] > 0]
        losses = [t for t in sells if t["pnl"] < 0]

        total_pnl = sum(t["pnl"] for t in sells)
        total_wins = sum(t["pnl"] for t in wins)
        total_losses = abs(sum(t["pnl"] for t in losses))

        win_rate = (len(wins) / len(sells) * 100) if sells else 0
        avg_win = (total_wins / len(wins)) if wins else 0
        avg_loss = (total_losses / len(losses)) if losses else 0
        profit_factor = (total_wins / total_losses) if total_losses > 0 else float("inf")

        best = max(sells, key=lambda t: t["pnl"])
        worst = min(sells, key=lambda t: t["pnl"])

        return {
            "total_trades": len(sells),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 1),
            "total_pnl": round(total_pnl, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "best_trade": f"{best['symbol']} +${best['pnl']:.2f}",
            "worst_trade": f"{worst['symbol']} ${worst['pnl']:.2f}",
            "max_drawdown_pct": round(self.max_drawdown * 100, 2),
        }

    def get_sector_performance(self) -> Dict:
        """Sektör bazlı performans."""
        sector_pnl = defaultdict(lambda: {"pnl": 0, "trades": 0, "wins": 0})

        for t in self.trades:
            if t.get("action") == "SELL" and "pnl" in t:
                sector = t.get("sector", "Unknown")
                sector_pnl[sector]["pnl"] += t["pnl"]
                sector_pnl[sector]["trades"] += 1
                if t["pnl"] > 0:
                    sector_pnl[sector]["wins"] += 1

        return dict(sector_pnl)

    def format_stats(self, days: int = None) -> str:
        """İstatistiklerin okunabilir formatı."""
        stats = self.get_stats(days)
        period = f"Son {days} gün" if days else "Tüm zamanlar"

        return (
            f"📊 Performans ({period}):\n"
            f"  İşlem: {stats['total_trades']} "
            f"(✅{stats['wins']} / ❌{stats['losses']})\n"
            f"  Win Rate: %{stats['win_rate']}\n"
            f"  P&L: ${stats['total_pnl']:+.2f}\n"
            f"  Avg Win: ${stats['avg_win']:.2f} | Avg Loss: ${stats['avg_loss']:.2f}\n"
            f"  Profit Factor: {stats['profit_factor']:.2f}\n"
            f"  En İyi: {stats['best_trade']}\n"
            f"  En Kötü: {stats['worst_trade']}\n"
            f"  Max Drawdown: %{stats['max_drawdown_pct']}"
        )
