"""
Compliance Module - ABD düzenleyici uyumluluk.
Wash Sale tespiti, vergi raporu dışa aktarma.

NOT: PDTTracker → core/pdt_tracker.py'de tanımlı (canonical versiyon).
     Geriye uyumluluk için import redirect aşağıda.
"""
import csv
import json
import os
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
from utils.logger import logger

# PDTTracker geriye uyumluluk redirect'i (canonical: core/pdt_tracker.py)
from core.pdt_tracker import PDTTracker  # noqa: F401

class WashSaleTracker:
    """
    Wash Sale Rule takibi (IRS kuralı).

    Kural: Bir hisseyi zararına satıp, 30 gün içinde aynı hisseyi
    tekrar alırsan, o zararı vergiden düşemezsin.

    NOT: Kripto şu an Wash Sale'den muaf (ancak değişebilir).
    """

    def __init__(self, wash_file: str = None):
        if wash_file is None:
            try:
                from config import state_path
                wash_file = state_path("wash_sale_tracker.json")
            except Exception:
                wash_file = "wash_sale_tracker.json"
        self.wash_file = wash_file
        self.loss_sales: List[Dict] = self._load()
        self.WASH_SALE_WINDOW_DAYS = 30
        logger.info("WashSaleTracker başlatıldı")

    def _load(self) -> List[Dict]:
        if os.path.exists(self.wash_file):
            try:
                with open(self.wash_file, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def _save(self):
        try:
            with open(self.wash_file, "w") as f:
                json.dump(self.loss_sales, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Wash sale kayıt hatası: {e}")

    def record_loss_sale(self, symbol: str, loss_amount: float, sell_date: str):
        """Zararına satış kaydeder."""
        if loss_amount < 0:  # Zarar
            # Dedup: aynı symbol + sell_date için tekrar kaydetme
            # (restart/retry'da aynı zararın mükerrer eklenmesini önler)
            if any(s.get("symbol") == symbol and s.get("sell_date") == sell_date
                   for s in self.loss_sales):
                return
            self.loss_sales.append({
                "symbol": symbol,
                "loss": loss_amount,
                "sell_date": sell_date,
                "wash_window_end": (
                    datetime.fromisoformat(sell_date) + timedelta(days=30)
                ).isoformat()[:10],
            })
            self._save()

    def check_wash_sale(self, symbol: str, asset_type: str = "stock") -> Tuple[bool, str]:
        """
        Wash sale riski kontrol eder.
        True = WASH SALE RİSKİ VAR (alım yapılMAMALI veya dikkat edilmeli)
        """
        # Kripto muaf (şimdilik)
        if asset_type == "crypto":
            return False, "✅ Kripto: Wash Sale muaf"

        today = date.today().isoformat()
        active_windows = [
            s for s in self.loss_sales
            if s["symbol"] == symbol
            and s.get("wash_window_end", "") >= today
        ]

        if active_windows:
            total_loss = sum(s["loss"] for s in active_windows)
            end_date = max(s["wash_window_end"] for s in active_windows)
            return True, (
                f"⚠️ WASH SALE RİSKİ: {symbol} son 30 günde zararına satıldı "
                f"(toplam: ${total_loss:,.2f}). {end_date} tarihine kadar "
                f"bu hisseyi alırsan zarar vergiden düşülemez!"
            )

        return False, "✅ Wash Sale riski yok"


class TaxExporter:
    """
    Vergi raporu dışa aktarma.
    TurboTax ve benzeri vergi yazılımlarına uygun CSV formatı.
    """

    @staticmethod
    def export_to_csv(
        trades: List[Dict],
        filename: str = "tax_report.csv",
        year: Optional[int] = None,
    ) -> str:
        """
        İşlem geçmişini vergi raporu formatında CSV'ye aktarır.
        TurboTax / H&R Block / CoinTracker uyumlu format.
        """
        if year is None:
            year = date.today().year

        # Yıla göre filtrele
        year_trades = [
            t for t in trades
            if t.get("timestamp", t.get("date", "")).startswith(str(year))
            or t.get("date", "").startswith(str(year))
        ]

        filepath = f"tax_report_{year}.csv"
        if filename:
            filepath = filename

        headers = [
            "Date",
            "Type",
            "Symbol",
            "Quantity",
            "Price",
            "Total Value",
            "Fee/Commission",
            "P&L",
            "Short/Long Term",
            "Holding Period",
            "Notes",
        ]

        try:
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(headers)

                for t in year_trades:
                    trade_date = t.get("timestamp", t.get("date", ""))[:10]
                    trade_type = t.get("action", t.get("type", ""))
                    symbol = t.get("symbol", "")
                    qty = t.get("qty", t.get("shares", 0))
                    price = t.get("price", 0)
                    total = float(qty) * float(price) if qty and price else 0
                    fee = t.get("fee", 0)
                    pnl = t.get("pnl", "")
                    term = "Short-Term"  # Day trading = always short-term
                    notes = t.get("reason", t.get("strategy", ""))

                    writer.writerow([
                        trade_date,
                        trade_type,
                        symbol,
                        qty,
                        f"{float(price):.2f}" if price else "",
                        f"{total:.2f}" if total else "",
                        f"{float(fee):.4f}" if fee else "0",
                        f"{float(pnl):.2f}" if pnl != "" else "",
                        term,
                        "< 1 year",
                        notes,
                    ])

            logger.info(f"📄 Vergi raporu oluşturuldu: {filepath} ({len(year_trades)} işlem)")
            return filepath

        except Exception as e:
            logger.error(f"CSV export hatası: {e}")
            return ""

    @staticmethod
    def export_wash_sales(
        wash_sales: List[Dict],
        filename: str = "wash_sales_report.csv",
    ) -> str:
        """Wash sale kayıtlarını CSV'ye aktarır."""
        try:
            with open(filename, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Symbol", "Loss Amount", "Sell Date", "Wash Window End"])
                for ws in wash_sales:
                    writer.writerow([
                        ws.get("symbol", ""),
                        ws.get("loss", 0),
                        ws.get("sell_date", ""),
                        ws.get("wash_window_end", ""),
                    ])
            logger.info(f"📄 Wash sale raporu: {filename}")
            return filename
        except Exception as e:
            logger.error(f"Wash sale export hatası: {e}")
            return ""


# ============================================================
# API GÜVENLİK KONTROL LİSTESİ
# ============================================================
API_SECURITY_CHECKLIST = """
🔒 API GÜVENLİK KONTROL LİSTESİ
================================

✅ YAPILMASI GEREKENLER:
  1. API anahtarını SADECE "Read" + "Trade" yetkisiyle oluştur
  2. "Withdrawal" (Para Çekme) yetkisini KESİNLİKLE AÇMA
  3. IP whitelist kullan (sadece kendi sunucu IP'n)
  4. API anahtarını .env dosyasında tut, asla koda yapıştırma
  5. .env dosyasını .gitignore'a ekle
  6. API anahtarını düzenli olarak yenile (her 90 gün)

❌ YAPILMAMASI GEREKENLER:
  1. API anahtarını kimseyle paylaşma
  2. Withdrawal yetkisi açma
  3. API anahtarını GitHub'a yükleme
  4. Güvenilmeyen 3. parti servislere verme

🔑 BROKER BAZLI GÜVENLİK:
  Alpaca:  API Settings → Sadece Trading izni
  Binance.US: API Management → "Enable Spot Trading" 
              → "Enable Withdrawals" = KAPALI
  Coinbase: API Settings → "Trade" izni → "Transfer" = KAPALI
  Kraken: API Settings → "Query" + "Trade" → "Withdraw" = KAPALI

📍 SUNUCU LOKASYONU ÖNERİSİ:
  - AWS us-east-1 (Virginia) → NYSE/NASDAQ'a en yakın
  - Google Cloud us-east4 (Virginia)
  - Azure East US (Virginia)
  Bot'u yerel bilgisayarda çalıştırmak da olur ama
  canlı scalping için gecikme (latency) fark yaratır.
"""


def print_security_checklist():
    """Güvenlik kontrol listesini yazdırır."""
    print(API_SECURITY_CHECKLIST)
    logger.info("🔒 API güvenlik kontrol listesi gösterildi")
