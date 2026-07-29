"""
Gap Scanner — Pre-Market Gap Analizi ve Overnight Risk Yonetimi

Sabah piyasa acilmadan tum acik pozisyonlari kontrol eder:
  1. Gap-Down tespiti -> ACIL SELL (zarar buyumeden kapat)
  2. Gap-Up tespiti -> trailing stop sikilastir (kari kilitle)
  3. Telegram ile acil bildirim
  4. Hem LONG hem SHORT pozisyonlar icin calisir

Kullanim: Piyasa acilmadan ~30dk once (09:00 ET) cagrilir.
"""
from datetime import datetime
from typing import Dict, List, Optional
from config import STOCK_CONFIG, SHORT_CONFIG
from core.protection import ProtectionResult
from utils.logger import logger


def fetch_latest_price(data_client, symbol: str) -> Optional[float]:
    """Snapshot ile son işlem fiyatı (pre-market dahil).

    alpaca-py get_stock_snapshot() sembol string'i DEĞİL StockSnapshotRequest
    ister ve {symbol: Snapshot} dict döner — eski çağrı her seferinde sessizce
    patlıyordu (gap koruması fiilen hiç çalışmamış). Ortak yardımcı: gap_scanner,
    signal_queue ve notifier bunu kullanır.
    """
    try:
        from alpaca.data.requests import StockSnapshotRequest
        snaps = data_client.get_stock_snapshot(
            StockSnapshotRequest(symbol_or_symbols=symbol)
        )
        snap = snaps.get(symbol) if isinstance(snaps, dict) else snaps
        if snap is None or snap.latest_trade is None:
            return None
        price = float(snap.latest_trade.price)
        return price if price > 0 else None
    except Exception as e:
        logger.debug(f"  {symbol} snapshot fiyatı alınamadı: {e}")
        return None


class GapScanner:
    """Pre-market gap analizi — acik pozisyonlar icin erken uyari sistemi."""

    # Gap esikleri
    GAP_DOWN_ALERT_PCT = -2.0     # %2+ gap-down = ALARM
    GAP_DOWN_SELL_PCT = -3.5      # %3.5+ gap-down = ACIL SELL
    GAP_UP_TIGHTEN_PCT = 3.0     # %3+ gap-up = trailing sikilastir
    GAP_UP_PARTIAL_PCT = 5.0     # %5+ gap-up = kismi kar al

    # Short icin (ters yonlu)
    SHORT_GAP_UP_ALERT_PCT = 2.0  # Short icin gap-up = ALARM
    SHORT_GAP_UP_COVER_PCT = 3.5  # Short icin gap-up = ACIL COVER

    def __init__(self):
        self.last_scan = None
        self.alerts = []
        logger.info("GapScanner baslatildi — pre-market gap korumasi aktif")

    def scan_overnight_gaps(self, bot) -> List[Dict]:
        """
        Tum acik pozisyonlar icin overnight gap analizi yap.

        Args:
            bot: StockBot instance (positions, short_positions, data_client)

        Returns:
            List of gap alerts with recommended actions
        """
        self.alerts = []

        # LONG pozisyonlar
        for symbol, pos_data in dict(bot.positions).items():
            try:
                alert = self._check_gap(
                    bot, symbol, pos_data, side="LONG"
                )
                if alert:
                    self.alerts.append(alert)
            except Exception as e:
                logger.debug(f"  Gap scan hatasi {symbol}: {e}")

        # SHORT pozisyonlar
        for symbol, pos_data in dict(bot.short_positions).items():
            try:
                alert = self._check_gap(
                    bot, symbol, pos_data, side="SHORT"
                )
                if alert:
                    self.alerts.append(alert)
            except Exception as e:
                logger.debug(f"  Gap scan hatasi SHORT {symbol}: {e}")

        self.last_scan = datetime.now()

        if self.alerts:
            logger.info(
                f"  GAP SCANNER: {len(self.alerts)} uyari tespit edildi!"
            )
            for alert in self.alerts:
                logger.info(
                    f"    {alert['symbol']}: {alert['type']} "
                    f"({alert['gap_pct']:+.1f}%) -> {alert['action']}"
                )

        return self.alerts

    def _check_gap(self, bot, symbol: str, pos_data: Dict,
                    side: str = "LONG") -> Optional[Dict]:
        """Tek bir pozisyon icin gap kontrolu."""
        entry_price = pos_data.get("entry_price", 0)
        if entry_price <= 0:
            return None

        # Snapshot ile en son fiyati al (pre-market dahil)
        current_price = fetch_latest_price(bot.data_client, symbol)
        if current_price is None:
            return None

        # Gap yuzdesini hesapla
        gap_pct = ((current_price - entry_price) / entry_price) * 100

        # === LONG pozisyon gap analizi ===
        if side == "LONG":
            # Gap-Down: fiyat dusmus = zarar
            if gap_pct <= self.GAP_DOWN_SELL_PCT:
                return {
                    "symbol": symbol,
                    "side": "LONG",
                    "type": "GAP_DOWN_CRITICAL",
                    "gap_pct": round(gap_pct, 2),
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "action": "SELL_AT_OPEN",
                    "reason": f"Kritik gap-down {gap_pct:.1f}% — acil sat",
                }
            elif gap_pct <= self.GAP_DOWN_ALERT_PCT:
                return {
                    "symbol": symbol,
                    "side": "LONG",
                    "type": "GAP_DOWN_ALERT",
                    "gap_pct": round(gap_pct, 2),
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "action": "TIGHTEN_STOP",
                    "reason": f"Gap-down {gap_pct:.1f}% — SL sikilastir",
                }
            # Gap-Up: fiyat artmis = kar
            elif gap_pct >= self.GAP_UP_PARTIAL_PCT:
                return {
                    "symbol": symbol,
                    "side": "LONG",
                    "type": "GAP_UP_BIG",
                    "gap_pct": round(gap_pct, 2),
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "action": "PARTIAL_SELL",
                    "reason": f"Buyuk gap-up +{gap_pct:.1f}% — kismi kar al",
                }
            elif gap_pct >= self.GAP_UP_TIGHTEN_PCT:
                return {
                    "symbol": symbol,
                    "side": "LONG",
                    "type": "GAP_UP",
                    "gap_pct": round(gap_pct, 2),
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "action": "TIGHTEN_TRAILING",
                    "reason": f"Gap-up +{gap_pct:.1f}% — trailing sikilastir",
                }

        # === SHORT pozisyon gap analizi (ters mantik) ===
        elif side == "SHORT":
            # Short icin gap-up = zarar (fiyat yukseldi)
            if gap_pct >= self.SHORT_GAP_UP_COVER_PCT:
                return {
                    "symbol": symbol,
                    "side": "SHORT",
                    "type": "GAP_UP_CRITICAL",
                    "gap_pct": round(gap_pct, 2),
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "action": "COVER_AT_OPEN",
                    "reason": f"Short gap-up +{gap_pct:.1f}% — acil cover",
                }
            elif gap_pct >= self.SHORT_GAP_UP_ALERT_PCT:
                return {
                    "symbol": symbol,
                    "side": "SHORT",
                    "type": "GAP_UP_ALERT",
                    "gap_pct": round(gap_pct, 2),
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "action": "TIGHTEN_STOP",
                    "reason": f"Short gap-up +{gap_pct:.1f}% — SL sikilastir",
                }
            # Short icin gap-down = kar (fiyat dustu)
            elif gap_pct <= -self.GAP_UP_TIGHTEN_PCT:
                return {
                    "symbol": symbol,
                    "side": "SHORT",
                    "type": "GAP_DOWN_PROFIT",
                    "gap_pct": round(gap_pct, 2),
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "action": "TIGHTEN_TRAILING",
                    "reason": f"Short gap-down {gap_pct:.1f}% — trailing sikilastir",
                }

        return None

    def execute_gap_actions(self, bot, alerts: List[Dict]):
        """
        Gap uyarilarini islem olarak calistir.

        1. SELL_AT_OPEN -> aninda market sell
        2. COVER_AT_OPEN -> aninda market cover
        3. TIGHTEN_STOP -> stop-loss'u sikilastir
        4. TIGHTEN_TRAILING -> trailing stop'u sikilastir
        """
        for alert in alerts:
            symbol = alert["symbol"]
            action = alert["action"]

            try:
                if action == "SELL_AT_OPEN":
                    logger.warning(
                        f"  GAP ACIL SATIS: {symbol} {alert['gap_pct']:.1f}%"
                    )
                    bot.executor.execute_sell(
                        symbol, f"GAP_DOWN ({alert['gap_pct']:.1f}%)"
                    )

                elif action == "COVER_AT_OPEN":
                    logger.warning(
                        f"  GAP ACIL COVER: {symbol} {alert['gap_pct']:+.1f}%"
                    )
                    bot.short_executor.execute_cover(
                        symbol, f"GAP_UP_SHORT ({alert['gap_pct']:+.1f}%)",
                        analysis={"price": alert["current_price"]}
                    )

                elif action == "TIGHTEN_STOP":
                    side = alert["side"].upper()
                    book = bot.positions if side == "LONG" else bot.short_positions
                    if symbol not in book:
                        continue
                    pos_data = book[symbol]
                    current_price = float(alert["current_price"])
                    candidate = round(
                        current_price * (0.99 if side == "LONG" else 1.01), 2
                    )

                    existing = pos_data.get("stop_loss_price")
                    try:
                        existing = float(existing)
                    except (TypeError, ValueError):
                        existing = 0.0
                    if existing <= 0:
                        entry = float(pos_data.get("entry_price", 0) or 0)
                        if pos_data.get("breakeven_set", False):
                            offset = (
                                STOCK_CONFIG.get("breakeven_offset_pct", 0.001)
                                if side == "LONG"
                                else SHORT_CONFIG.get(
                                    "short_breakeven_offset_pct", 0.003
                                )
                            )
                            existing = round(entry * (1 + float(offset)), 2)
                        else:
                            distance = pos_data.get("stop_loss_pct")
                            if distance is None:
                                distance = (
                                    STOCK_CONFIG.get("stop_loss_pct")
                                    if side == "LONG"
                                    else SHORT_CONFIG.get("short_stop_loss_pct")
                                )
                            if entry > 0 and distance is not None and float(distance) >= 0:
                                existing = round(
                                    entry * (
                                        1 - float(distance)
                                        if side == "LONG"
                                        else 1 + float(distance)
                                    ),
                                    2,
                                )

                    more_protective = (
                        existing <= 0
                        or (side == "LONG" and candidate > existing)
                        or (side == "SHORT" and candidate < existing)
                    )
                    if not more_protective:
                        logger.info(
                            f"  GAP SL DEĞİŞMEDİ: {symbol} ${candidate:.2f}, "
                            f"mevcut ${existing:.2f} daha koruyucu"
                        )
                        continue

                    qty = abs(float(pos_data.get("qty", 0) or 0))
                    result: ProtectionResult = (
                        bot.position_manager._update_server_stop_loss(
                            symbol, candidate, qty, side=side
                        )
                    )
                    target_verified = (
                        result.verified
                        and result.stop_price is not None
                        and abs(float(result.stop_price) - candidate) <= 0.011
                    )
                    if not target_verified:
                        logger.warning(
                            f"  GAP SL SIKILAŞTIRMA BAŞARISIZ: {symbol} "
                            f"hedef ${candidate:.2f}, sonuç={result.outcome.value}"
                        )
                        continue

                    pos_data["stop_loss_price"] = float(result.stop_price)
                    if hasattr(bot, "_stash_exit_flags"):
                        bot._stash_exit_flags(symbol, pos_data)
                    if hasattr(bot, "_save_position_metadata"):
                        saved = bot._save_position_metadata()
                        if saved is not True:
                            logger.error(
                                f"  {symbol} gap stop metadata kaydı doğrulanamadı"
                            )
                    logger.info(
                        f"  GAP SL SIKILAŞTIRMA: {symbol} "
                        f"trigger=${float(result.stop_price):.2f} ({side})"
                    )

                elif action == "TIGHTEN_TRAILING":
                    # Trailing stop'u %2'ye sikilastir
                    if alert["side"] == "LONG" and symbol in bot.positions:
                        bot.positions[symbol]["trailing_override"] = 0.02
                        logger.info(f"  GAP TRAILING SIKILASTIRMA: {symbol} TS=%2")

                elif action == "PARTIAL_SELL":
                    logger.info(
                        f"  GAP KISMI KAR: {symbol} +{alert['gap_pct']:.1f}%"
                    )
                    # Kismi kar alma position_manager'a birakiyoruz

                # Telegram bildirim
                if hasattr(bot, 'notifier') and action in ("SELL_AT_OPEN", "COVER_AT_OPEN"):
                    bot.notifier.send_message(
                        f"GAP ALARM {symbol}: {alert['gap_pct']:+.1f}% "
                        f"| {alert['reason']}"
                    )

            except Exception as e:
                logger.error(f"  Gap action hatasi {symbol}: {e}")
