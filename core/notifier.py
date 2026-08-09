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
from core.ntfy_notifier import CriticalAlarmPublisher, PublishResult
from utils.logger import logger


CRITICAL_EVENT_INVENTORY = (
    "notify_critical",
    "notify_kill_switch",
    "stock_bot.main_loop_consecutive_error",
)


class TelegramNotifier:
    """Telegram bildirim gönderici."""

    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.bot_token and self.chat_id)
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.critical_publisher = CriticalAlarmPublisher(telegram_send=self._send)

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
                logger.warning(f"Telegram gonderilemedi: HTTP {response.status_code}")
                return False
        except Exception as e:
            logger.warning(f"Telegram gonderim hatasi: {e}")
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
        return self._send(text)

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
        return self._send(text)

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
        message = (
            f"KILL SWITCH TETIKLENDI | Sebep: {reason} | "
            f"Bakiye: ${equity:,.2f} | Tum pozisyonlar kapatiliyor"
        )
        result = self.publish_critical(
            "KILL_SWITCH",
            message,
            telegram_text=text,
            symbol="BOT",
            state_code="triggered",
        )
        return result.direct_delivered

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
        return self._send(text)

    def notify_funnel_summary(self, date_str: str, funnel_dict: dict) -> bool:
        """Kapali ET gununun giris hunisini kritik olmayan kanaldan gonder."""
        try:
            data = funnel_dict or {}
            reasons = data.get("gate_block_reasons", {})
            reason_text = ", ".join(
                f"{reason}={count}"
                for reason, count in sorted(
                    reasons.items(), key=lambda item: (-item[1], item[0])
                )
            ) or "yok"
            text = (
                f"<b>GIRIS HUNISI {date_str}</b>\n"
                f"---------------\n"
                f"Taranan: {data.get('scanned', 0)} | "
                f"BUY: {data.get('signal_buy', 0)} | "
                f"SELL: {data.get('signal_sell', 0)} | "
                f"HOLD: {data.get('signal_hold', 0)}\n"
                f"Dusuk guven: {data.get('conf_below_min', 0)} | "
                f"Sektor blok: {data.get('sector_block', 0)} | "
                f"Gate blok: {data.get('gate_block', 0)}\n"
                f"Pullback: {data.get('queued_pullback', 0)} | "
                f"Kuyruk tekrari: {data.get('queue_dup', 0)} | "
                f"Giris: {data.get('entries', 0)} | "
                f"Cikis: {data.get('exits', 0)}\n"
                f"Gate nedenleri: {reason_text}"
            )
            return self._send(text)
        except Exception as exc:
            logger.debug(f"  Funnel bildirim hatasi: {exc}")
            return False

    def notify_error(self, error_msg: str):
        """Kritik hata bildirimi."""
        text = (
            f"⚠️ <b>HATA</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"{error_msg[:500]}\n"
            f"🕒 {datetime.now().strftime('%H:%M:%S')}"
        )
        return self._send(text)

    def notify_pdt_warning(self, remaining: int):
        """PDT limiti uyarısı."""
        text = (
            f"⚠️ <b>PDT UYARISI</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"Kalan day trade hakkı: {remaining}/2\n"
            f"Dikkat: Hakkın dolduğunda gün içi satış engellenecek!"
        )
        return self._send(text)

    # ============================================================
    # KRITIK ALARM (R0-E/Rock 4) — tek dayanikli publisher
    # ============================================================
    # NEDEN: Telegram kimlik bilgisi yoksa notifier sessizce devre disi kalir
    # ve her gonderim False doner. Publisher alarmi once alarms.jsonl'e yazar,
    # sonra Telegram/ntfy dener. VPS bridge teslim edilmemis kayitlarin backstop'idir.

    def publish_critical(
        self,
        kind: str,
        message: str,
        *,
        telegram_text: Optional[str] = None,
        symbol: Optional[str] = None,
        state_code: Optional[str] = None,
    ) -> PublishResult:
        """Return the honest persisted/direct-delivered result model."""
        return self.critical_publisher.publish(
            kind,
            message,
            telegram_text=telegram_text,
            symbol=symbol,
            state_code=state_code,
        )

    def notify_critical(
        self,
        kind: str,
        message: str,
        *,
        symbol: Optional[str] = None,
        state_code: Optional[str] = None,
    ) -> bool:
        """Persist and deliver a critical alarm; return direct delivery only."""
        text = (
            f"🚨 <b>{kind}</b>\n"
            f"---------------\n"
            f"{message[:1500]}\n"
            f"🕐 {datetime.now().strftime('%H:%M:%S')}"
        )
        result = self.publish_critical(
            kind,
            message,
            telegram_text=text,
            symbol=symbol,
            state_code=state_code,
        )
        return result.direct_delivered

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
