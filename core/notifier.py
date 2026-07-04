"""
Notifier — Telegram Bildirim Sistemi

Trade gerçekleştiğinde, KillSwitch tetiklendiğinde, günlük özet,
ve önemli olaylarda anlık Telegram bildirimi gönderir.

Kurulum:
  1. @BotFather'dan bot oluştur → TELEGRAM_BOT_TOKEN al
  2. Botu gruba/kanala ekle veya kendine mesaj at
  3. @userinfobot'tan TELEGRAM_CHAT_ID al
  4. .env dosyasına ekle:
     TELEGRAM_BOT_TOKEN=xxx
     TELEGRAM_CHAT_ID=xxx
"""
import os
import requests
from datetime import datetime
from typing import Dict, Optional
from utils.logger import logger


class TelegramNotifier:
    """Telegram bildirim gönderici."""

    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.bot_token and self.chat_id)
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

        if self.enabled:
            logger.info("📱 TelegramNotifier aktif")
        else:
            logger.info("📱 TelegramNotifier devre dışı (TELEGRAM_BOT_TOKEN/.CHAT_ID yok)")

    def _send(self, text: str, parse_mode: str = "HTML") -> bool:
        """Telegram mesajı gönder."""
        if not self.enabled:
            return False

        try:
            url = f"{self.base_url}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                return True
            else:
                logger.debug(f"Telegram hata: {response.status_code}")
                return False
        except Exception as e:
            logger.debug(f"Telegram gönderim hatası: {e}")
            return False

    # ============================================================
    # TİCARET BİLDİRİMLERİ
    # ============================================================

    def notify_buy(self, symbol: str, qty: float, price: float,
                   confidence: int, reasons: list):
        """Alım bildirimi."""
        text = (
            f"🟢 <b>ALIŞ: {symbol}</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📊 Adet: {qty:.4f} | Fiyat: ${price:,.2f}\n"
            f"💰 Toplam: ${qty * price:,.2f}\n"
            f"🎯 Güven: %{confidence}\n"
            f"📝 Nedenler: {', '.join(reasons[:3])}\n"
            f"🕒 {datetime.now().strftime('%H:%M:%S')}"
        )
        self._send(text)

    def notify_sell(self, symbol: str, reason: str,
                    pnl: float = 0, pnl_pct: float = 0):
        """Satış bildirimi."""
        emoji = "🔴" if pnl < 0 else "🟢"
        pnl_emoji = "📉" if pnl < 0 else "📈"

        text = (
            f"{emoji} <b>SATIŞ: {symbol}</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"{pnl_emoji} P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n"
            f"📝 Sebep: {reason}\n"
            f"🕒 {datetime.now().strftime('%H:%M:%S')}"
        )
        self._send(text)

    def notify_kill_switch(self, reason: str, equity: float):
        """KillSwitch tetiklenme bildirimi."""
        text = (
            f"🚨🚨🚨 <b>KILL SWITCH TETİKLENDİ</b> 🚨🚨🚨\n"
            f"━━━━━━━━━━━━━━━\n"
            f"⚠️ Sebep: {reason}\n"
            f"💰 Bakiye: ${equity:,.2f}\n"
            f"📋 Tüm pozisyonlar kapatılıyor!\n"
            f"🕒 {datetime.now().strftime('%H:%M:%S')}"
        )
        self._send(text)

    def notify_daily_summary(self, equity: float, pnl: float,
                              trades_count: int, positions: dict,
                              wins: int = 0, losses: int = 0):
        """Günlük özet bildirimi."""
        pnl_pct = (pnl / max(equity - pnl, 1)) * 100
        emoji = "📈" if pnl >= 0 else "📉"

        pos_text = ""
        if positions:
            pos_lines = []
            for sym, data in positions.items():
                entry = data.get("entry_price", 0)
                pos_lines.append(f"  • {sym} @ ${entry:,.2f}")
            pos_text = "\n".join(pos_lines)
        else:
            pos_text = "  Yok"

        text = (
            f"{emoji} <b>GÜNLÜK ÖZET</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 Bakiye: ${equity:,.2f}\n"
            f"📊 P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n"
            f"📋 İşlem: {trades_count} (✅{wins} / ❌{losses})\n"
            f"📌 Açık Pozisyonlar:\n{pos_text}\n"
            f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        self._send(text)

    def notify_error(self, error_msg: str):
        """Kritik hata bildirimi."""
        text = (
            f"⚠️ <b>HATA</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"{error_msg[:500]}\n"
            f"🕒 {datetime.now().strftime('%H:%M:%S')}"
        )
        self._send(text)

    def notify_pdt_warning(self, remaining: int):
        """PDT limiti uyarısı."""
        text = (
            f"⚠️ <b>PDT UYARISI</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"Kalan day trade hakkı: {remaining}/2\n"
            f"Dikkat: Hakkın dolduğunda gün içi satış engellenecek!"
        )
        self._send(text)

    def send_message(self, text: str) -> bool:
        """Genel amacli mesaj gonder (short executor, ozel bildirimler vb.)."""
        return self._send(text)

    def send_daily_report(self, bot):
        """
        Gunluk kapsamli performans raporu.

        Icerik:
        - P&L ozeti
        - Acik pozisyonlar
        - Ajan dogruluk oranlari
        - Piyasa rejimi
        - Kuyruk durumu
        """
        try:
            equity = bot.equity
            initial = bot.initial_equity
            pnl = equity - initial
            pnl_pct = (pnl / initial * 100) if initial > 0 else 0

            # Acik pozisyonlar
            pos_lines = []
            from core.gap_scanner import fetch_latest_price
            for sym, p in bot.positions.items():
                entry = p.get("entry_price", 0)
                curr = fetch_latest_price(bot.data_client, sym) or entry
                chg = ((curr - entry) / entry * 100) if entry > 0 else 0
                emoji = "+" if chg > 0 else ""
                pos_lines.append(f"  {sym}: {emoji}{chg:.1f}%")

            for sym, p in bot.short_positions.items():
                entry = p.get("entry_price", 0)
                curr = fetch_latest_price(bot.data_client, sym) or entry
                chg = ((entry - curr) / entry * 100) if entry > 0 else 0
                emoji = "+" if chg > 0 else ""
                pos_lines.append(f"  S:{sym}: {emoji}{chg:.1f}%")

            # Options pozisyonları
            if hasattr(bot, 'options_positions'):
                for sym, p in bot.options_positions.items():
                    opt_type = p.get("type", "?")
                    underlying = p.get("underlying", "?")
                    strike = p.get("strike", 0)
                    entry = p.get("entry_price", 0)
                    qty = p.get("qty", 0)
                    opt_emoji = "📞" if opt_type == "CALL" else "📉"
                    pos_lines.append(
                        f"  {opt_emoji}{underlying} {opt_type} ${strike} x{qty}"
                    )

            pos_text = "\n".join(pos_lines) if pos_lines else "  (yok)"

            # Ajan performansi
            agent_text = ""
            if hasattr(bot, 'agent_perf'):
                stats = bot.agent_perf.get_agent_stats()
                agent_lines = []
                for name, data in stats.items():
                    acc = data.get("accuracy", "N/A")
                    if isinstance(acc, (int, float)):
                        acc = f"{acc:.0f}%"
                    agent_lines.append(f"  {name}: {acc}")
                agent_text = "\n".join(agent_lines) if agent_lines else "  (veri yok)"

            # Rejim
            regime = getattr(bot, '_market_regime', 'N/A')
            enhanced = getattr(bot, '_enhanced_regime', {})
            regime_detail = enhanced.get("regime", "")
            trading_mode = enhanced.get("trading_mode", "")

            # Signal queue
            queue_count = 0
            if hasattr(bot, 'signal_queue'):
                q = bot.signal_queue.get_queue_status()
                queue_count = q.get("pending_count", 0)

            # Options özet
            opt_count = len(getattr(bot, 'options_positions', {}))
            opt_exposure = sum(
                p.get("cost_basis", 0)
                for p in getattr(bot, 'options_positions', {}).values()
            )

            text = (
                f"<b>GUNLUK RAPOR</b>\n"
                f"{'=' * 20}\n"
                f"Bakiye: ${equity:,.2f}\n"
                f"P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n"
                f"\nPozisyonlar:\n{pos_text}\n"
                f"\nOptions: {opt_count} adet | ${opt_exposure:,.0f}\n"
                f"\nAjan Accuracy:\n{agent_text}\n"
                f"\nRejim: {regime} | {regime_detail} ({trading_mode})\n"
                f"Kuyruk: {queue_count} sinyal\n"
                f"\n{datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )
            self._send(text)

        except Exception as e:
            logger.debug(f"  Gunluk rapor hatasi: {e}")
