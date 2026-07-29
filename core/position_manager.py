"""
Position Manager — Pozisyon Yönetimi

StockBot’tan ayrıştırılmış pozisyon modülü.
- manage_positions(): Trailing stop, break-even, kademeli kâr alma, stop-loss
- Sunucu taraflı SL güncellemesi: Break-even ve trailing stop değiştiğinde
  Alpaca’daki stop emri de güncellenir (bot çökse bile korunma devam eder)
"""
from datetime import datetime
import time
from typing import Dict

from alpaca.trading.requests import (
    MarketOrderRequest, StopLimitOrderRequest, GetOrdersRequest,
    ReplaceOrderRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus

from config import SHORT_CONFIG
from core.protection import (
    ACTIVE_STATUSES,
    ProtectionOutcome,
    ProtectionResult,
    ProtectionSummary,
    classify_covering_order,
    clear_expected_uncovered,
    clear_protection_alarm,
    exit_flag_cache_matches_entry,
    note_expected_uncovered,
    deterministic_client_order_id,
    enum_value,
    flatten_orders,
    is_active_order,
    is_stop_order,
    is_terminal_order,
    order_id,
    order_limit_price,
    order_stop_price,
    protection_alarm,
    should_exit_locally,
)
from utils.logger import logger


class PositionManager:
    """Açık pozisyonları yönetir. StockBot referansı üzerinden state'e erişir."""

    def __init__(self, bot):
        self.bot = bot
        self._small_pos_log_time = {}  # Log spam önleyici: sembol → son log zamanı
        self._verify_attempts = 6

    def _poll_pause(self):
        delay = float(getattr(self.bot, "_protection_poll_seconds", 0.25) or 0)
        if delay > 0:
            time.sleep(delay)

    def _position_book(self, side: str) -> Dict:
        return self.bot.positions if side == "LONG" else self.bot.short_positions

    def _apply_protection_result(
        self, symbol: str, side: str, result: ProtectionResult
    ) -> ProtectionResult:
        """Broker kanitini yerel strateji state'inden ayri sakla."""
        pos_data = self._position_book(side).get(symbol)
        if not isinstance(pos_data, dict):
            return result
        if result.verified:
            pos_data["server_stop_verified"] = True
            pos_data["server_stop_order_id"] = result.order_id
            if result.stop_price is not None and float(result.stop_price) > 0:
                # Broker'dan yeniden okunup doğrulanan mutlak fiyat, yerel stop
                # kararının da tek kanonik tetiğidir. Başarısız bir update'in
                # sonunda bulunan eski (daha gevşek) stop yerel korumayı
                # geriye götüremez.
                verified_trigger = float(result.stop_price)
                try:
                    existing_trigger = float(
                        pos_data.get("stop_loss_price", 0) or 0
                    )
                except (TypeError, ValueError):
                    existing_trigger = 0.0
                if (
                    existing_trigger <= 0
                    or (side == "LONG" and verified_trigger >= existing_trigger)
                    or (side == "SHORT" and verified_trigger <= existing_trigger)
                ):
                    pos_data["stop_loss_price"] = verified_trigger
            clear_protection_alarm(self.bot, f"{symbol}:{side}")
        elif result.outcome not in {
            ProtectionOutcome.ALREADY_FLAT,
            ProtectionOutcome.SKIPPED_PARKING,
        }:
            pos_data["server_stop_verified"] = False
            pos_data["server_stop_order_id"] = None
        if hasattr(self.bot, "_stash_exit_flags"):
            self.bot._stash_exit_flags(symbol, pos_data)
        return result

    def _alarm_result(
        self, symbol: str, side: str, result: ProtectionResult
    ) -> ProtectionResult:
        self._apply_protection_result(symbol, side, result)
        protection_alarm(self.bot, f"{symbol}:{side}", result.detail)
        return result

    def _current_position(self, symbol: str, side: str):
        positions = self.bot.client.get_all_positions()
        for position in positions:
            if str(getattr(position, "symbol", "")) != symbol:
                continue
            qty = float(getattr(position, "qty", 0) or 0)
            if (side == "LONG" and qty > 0) or (side == "SHORT" and qty < 0):
                return position
        return None

    def _open_orders(self, symbol: str) -> list:
        request = GetOrdersRequest(
            status=QueryOrderStatus.OPEN, symbols=[symbol], nested=True
        )
        return flatten_orders(self.bot.client.get_orders(request))

    def _reread_order(self, oid: str):
        return self.bot.client.get_order_by_id(oid)

    @staticmethod
    def _stop_candidates(orders: list, symbol: str, side: str) -> list:
        wanted = OrderSide.SELL if side == "LONG" else OrderSide.BUY
        return [
            order for order in orders
            if str(getattr(order, "symbol", "")) == symbol
            and enum_value(getattr(order, "side", None)) == enum_value(wanted)
            and is_stop_order(order)
        ]

    def _ensure_canonical_trigger(
        self, symbol: str, pos_data: Dict, entry_price: float,
        side: str, config: Dict,
    ) -> float | None:
        """Migrate a legacy position to its absolute local stop trigger."""
        existing = pos_data.get("stop_loss_price")
        try:
            trigger = float(existing)
        except (TypeError, ValueError):
            trigger = 0.0
        if trigger > 0:
            return trigger

        side = side.upper()
        if pos_data.get("breakeven_set", False):
            if side == "LONG":
                offset = float(config.get("breakeven_offset_pct", 0.001))
                trigger = entry_price * (1 + offset)
            else:
                offset = float(config.get("short_breakeven_offset_pct", 0.003))
                # SHORT'un mevcut BE sözleşmesi P&L uzayında -offset'tir:
                # fiyat entry'nin offset kadar ÜSTÜNE gelince cover eder.
                trigger = entry_price * (1 + offset)
        else:
            distance = pos_data.get("stop_loss_pct")
            if distance is None:
                key = "stop_loss_pct" if side == "LONG" else "short_stop_loss_pct"
                distance = config.get(key)
            try:
                distance = float(distance)
            except (TypeError, ValueError):
                return None
            if distance < 0:
                logger.error(
                    f"  {symbol}: stop_loss_pct imzasız olmalı, değer={distance}"
                )
                return None
            trigger = entry_price * (
                1 - distance if side == "LONG" else 1 + distance
            )

        if trigger <= 0:
            return None
        trigger = round(trigger, 2)
        pos_data["stop_loss_price"] = trigger
        if hasattr(self.bot, "_stash_exit_flags"):
            self.bot._stash_exit_flags(symbol, pos_data)
        return trigger

    def manage_positions(self, config: Dict):
        """Gelişmiş pozisyon yönetimi: trailing stop + kademeli kâr alma."""
        bot = self.bot
        try:
            positions = bot.client.get_all_positions()
        except Exception as e:
            logger.error(f"Pozisyon listesi alinamadi: {e}")
            bot.consecutive_errors += 1
            return

        for pos in positions:
            symbol = pos.symbol  # Hisse senedi: doğrudan sembol

            # Sadece us_equity yönet: opsiyonlar options_manager'ın, kripto/diğerleri
            # bu botun işi değil (aksi halde opsiyon premium'una %4 stop uygulanıyordu)
            asset_class = getattr(pos, "asset_class", "us_equity")
            if asset_class != "us_equity":
                continue

            # Parking sleeve (SPY) trade DEĞİL — stop/TP/partial uygulanmaz.
            # Yanlışlıkla bot.positions'a girmişse temizle (self-heal).
            if bot.index_parking.is_parking_symbol(symbol):
                bot.positions.pop(symbol, None)
                continue

            # Cooldown kontrolü
            cooldown_until = bot.sell_cooldown.get(symbol)
            if cooldown_until and datetime.now() < cooldown_until:
                continue

            # Minimum pozisyon değeri kontrolü ($5)
            pos_value = float(pos.qty) * float(pos.current_price)
            if pos_value < config.get("min_position_close_usd", 5.0):
                # Log spam önle: aynı sembol için 5 dk'da 1 kez logla
                now = datetime.now()
                last_log = self._small_pos_log_time.get(symbol)
                if not last_log or (now - last_log).total_seconds() > 300:
                    logger.debug(f"  Pozisyon cok kucuk, atla: {symbol} ${pos_value:.2f}")
                    self._small_pos_log_time[symbol] = now
                continue

            entry_price = float(pos.avg_entry_price)
            current_price = float(pos.current_price)
            pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0
            pnl_usd = float(pos.unrealized_pl)

            # Pozisyon senkronizasyonu — bot.positions'da yoksa ekle
            # A6: önbellekteki yönetim bayraklarını koru (partial_sold sıfırlanıp
            # cascade satış olmasın)
            if symbol not in bot.positions:
                cached = getattr(bot, "_exit_flag_cache", {}).get(symbol, {})
                if cached and not exit_flag_cache_matches_entry(cached, entry_price):
                    # Farkli girisin bayraklari — dusur (yanlis tetik enjeksiyonu)
                    getattr(bot, "_exit_flag_cache", {}).pop(symbol, None)
                    cached = {}
                bot.positions[symbol] = {
                    "entry_price": entry_price,
                    "qty": float(pos.qty),
                    "entry_time": cached.get("entry_time") or datetime.now().isoformat(),
                    "highest_price": max(current_price, cached.get("highest_price", 0) or 0),
                    "breakeven_set": cached.get("breakeven_set", False),
                    "partial_sold": cached.get("partial_sold", False),
                    "server_stop_verified": bool(cached.get("server_stop_verified", False)),
                    "server_stop_order_id": cached.get("server_stop_order_id") or None,
                    "close_in_progress": bool(cached.get("close_in_progress", False)),
                    "synced_from_alpaca": True,
                }
                if cached.get("stop_loss_pct") is not None:
                    bot.positions[symbol]["stop_loss_pct"] = cached["stop_loss_pct"]
                if cached.get("stop_loss_price") is not None:
                    bot.positions[symbol]["stop_loss_price"] = cached["stop_loss_price"]
                if cached.get("take_profit_pct") is not None:
                    bot.positions[symbol]["take_profit_pct"] = cached["take_profit_pct"]

            # Trailing stop güncelleme
            pos_data = bot.positions.get(symbol, {})
            stop_trigger = self._ensure_canonical_trigger(
                symbol, pos_data, entry_price, "LONG", config
            )
            highest = pos_data.get("highest_price", entry_price)
            if current_price > highest:
                highest = current_price
                bot.positions[symbol]["highest_price"] = highest

            trailing_drop = (highest - current_price) / highest if highest > 0 else 0

            # === BREAK-EVEN STOP ===
            if config.get("breakeven_enabled", True):
                be_trigger = config.get("breakeven_trigger_pct", 0.015)
                be_offset = config.get("breakeven_offset_pct", 0.001)
                if pnl_pct >= be_trigger and not pos_data.get("breakeven_set", False):
                    breakeven_price = entry_price * (1 + be_offset)
                    # Sunucu tarafli SL'yi guncelle (bot cokse bile korunsun)
                    result = self._update_server_stop_loss(
                        symbol, breakeven_price, float(pos.qty), side="LONG"
                    )
                    target_verified = (
                        result.verified
                        and result.stop_price is not None
                        and abs(result.stop_price - breakeven_price) <= 0.011
                    )
                    if target_verified:
                        stop_trigger = float(result.stop_price)
                        bot.positions[symbol]["stop_loss_price"] = stop_trigger
                        bot.positions[symbol]["breakeven_set"] = True
                        if hasattr(bot, "_stash_exit_flags"):
                            bot._stash_exit_flags(symbol, bot.positions[symbol])  # A6
                        logger.info(
                            f"  BREAK-EVEN {symbol}: +{pnl_pct:.1%} -> "
                            f"SL giris fiyatina cekildi (${breakeven_price:.2f})"
                        )

            # === SATIŞ KARARLARI (ÖNCELİK SIRASINA GÖRE) ===

            # 1. KESİN STOP-LOSS
            # v4.8: pozisyon-başına dinamik TP (girişte SL×min_rr planlandı);
            # yoksa config tabanı. None-güvenli okuma (null-metadata dersi).
            pos_tp_pct = pos_data.get("take_profit_pct")
            if pos_tp_pct is None:
                pos_tp_pct = config["take_profit_pct"]

            if should_exit_locally(current_price, stop_trigger, "LONG"):
                logger.info(
                    f"  🛑 STOP LOSS {symbol}: {pnl_pct:.1%} "
                    f"(trigger: ${stop_trigger:.2f}) (${pnl_usd:+.2f})"
                )
                bot.executor.execute_sell(
                    symbol,
                    f"STOP_LOSS ({pnl_pct:.1%} / trigger ${stop_trigger:.2f})",
                )

            # 2. TAKE PROFIT
            elif pnl_pct >= pos_tp_pct:
                logger.info(
                    f"  💰 TAKE PROFIT {symbol}: +{pnl_pct:.1%} "
                    f"(hedef {pos_tp_pct:.1%}) (${pnl_usd:+.2f})"
                )
                bot.executor.execute_sell(symbol, f"TAKE_PROFIT (+{pnl_pct:.1%})")

            # 3. TRAILING STOP
            elif pnl_pct > 0.01 and trailing_drop >= config["trailing_stop_pct"]:
                logger.info(
                    f"  TRAILING STOP {symbol}: Peak ${highest:,.2f} -> ${current_price:,.2f} "
                    f"(-{trailing_drop:.1%}) | P&L: {pnl_pct:.1%}"
                )
                bot.executor.execute_sell(symbol, f"TRAILING_STOP (peak -{trailing_drop:.1%})")

            # 3b. TRAILING SL sunucu guncelleme (her dongude en yuksek fiyata gore)
            elif pnl_pct > 0.02 and pos_data.get("breakeven_set", False):
                # Kar %2+ ve break-even aktifse, trailing SL'yi sunucuda da yukari cek
                trailing_sl_price = round(highest * (1 - config["trailing_stop_pct"]), 2)
                last_server_sl = pos_data.get("last_server_sl", 0)
                # Sadece fiyat yukseldiginde guncelle (gereksiz API cagrisi onle)
                if trailing_sl_price > last_server_sl + 0.10:
                    result = self._update_server_stop_loss(
                        symbol, trailing_sl_price, float(pos.qty), side="LONG"
                    )
                    if (result.verified and result.stop_price is not None
                            and abs(result.stop_price - trailing_sl_price) <= 0.011):
                        bot.positions[symbol]["last_server_sl"] = trailing_sl_price

            # 4. KADEMELİ KÂR ALMA (hisse senedi: tam hisse satılmalı)
            elif (pnl_pct >= config["partial_profit_pct"]
                  and not pos_data.get("partial_sold", False)):
                logger.info(
                    f"  📊 KADEMELI KÂR {symbol}: +{pnl_pct:.1%} -> Yarısı satılıyor"
                )
                try:
                    qty = float(pos.qty)
                    # For crypto allow fractional, for stocks int is fine. We can just use round(qty * 0.5, 4)
                    half_qty = round(qty * 0.5, 4)
                    # Minimum satış tutarı kontrolü — cascade selling önleyici
                    half_value = half_qty * current_price
                    if half_value < 10.0:
                        logger.debug(f"  {symbol} kademeli satış çok küçük: ${half_value:.2f} < $10, atla")
                    elif qty >= 2 or half_qty > 0:
                        # A5: Yarı satıştan ÖNCE tam-qty bracket çıkış bacaklarını (TP limit +
                        # SL stop) iptal et — aksi halde resting emir kalan adetten fazlasını
                        # satıp pozisyonu net-SHORT'a düşürebilir veya emir reddi üretir.
                        self._cancel_exit_orders(symbol, "LONG")
                        request = MarketOrderRequest(
                            symbol=symbol, qty=half_qty,
                            side=OrderSide.SELL, time_in_force=TimeInForce.DAY,
                        )
                        bot.client.submit_order(request)
                        bot.positions[symbol]["partial_sold"] = True
                        if hasattr(bot, "_stash_exit_flags"):
                            bot._stash_exit_flags(symbol, bot.positions[symbol])  # A6
                        from datetime import timedelta
                        bot.sell_cooldown[symbol] = datetime.now() + timedelta(seconds=config.get("sell_cooldown_seconds", 300))
                        bot._save_position_metadata()
                        logger.info(f"  ✅ Yarısı satıldı: {half_qty} {symbol} (${half_value:.2f}) (Cooldown eklendi)")
                        # A5: Kalan pozisyon için koruyucu stop'u yeniden kur (korumasız kalmasın)
                        remaining_qty = round(qty - half_qty, 4)
                        if remaining_qty > 0:
                            prot_price = self._ensure_canonical_trigger(
                                symbol, bot.positions[symbol], entry_price,
                                "LONG", config,
                            )
                            if prot_price is not None:
                                self._update_server_stop_loss(
                                    symbol, prot_price, remaining_qty, side="LONG"
                                )
                except Exception as e:
                    logger.error(f"Kademeli satış hatası {symbol}: {e}")

            # Durum logla (önemli pozisyonlar)
            if abs(pnl_pct) > 0.02:
                logger.info(
                    f"  📋 {symbol}: {pnl_pct:+.2%} (${pnl_usd:+.2f}) | "
                    f"Peak: ${highest:,.2f} | Trail: -{trailing_drop:.2%}"
                )

        # === DIŞ KAPANIŞ MUTABAKATI (LONG) ===
        # Bracket TP/SL bacağı sunucuda dolunca pozisyon Alpaca'dan düşer ama
        # execute_sell hiç çalışmaz → P&L, kayıp serisi, wash-sale ve PDT kaydı
        # atlanıyordu; slot da bir sonraki büyük sync'e dek (~2 saat) dolu kalıyordu.
        try:
            alpaca_longs = {
                p.symbol for p in positions
                if float(p.qty) > 0 and getattr(p, "asset_class", "us_equity") == "us_equity"
            }
            for sym in list(bot.positions.keys()):
                if sym in alpaca_longs or bot.index_parking.is_parking_symbol(sym):
                    continue
                if hasattr(bot, "_reconcile_external_exit"):
                    bot._reconcile_external_exit(sym, side="LONG")
        except Exception as e:
            logger.debug(f"  Dış kapanış mutabakat hatası (LONG): {e}")

    def manage_short_positions(self, config: Dict, short_config: Dict):
        """Short pozisyon yonetimi — ters mantik: fiyat duserse KAR."""
        bot = self.bot
        try:
            positions = bot.client.get_all_positions()
        except Exception as e:
            logger.error(f"Short pozisyon listesi alinamadi: {e}")
            return

        for pos in positions:
            symbol = pos.symbol
            qty = float(pos.qty)

            # Sadece us_equity (opsiyon/kripto bu yöneticinin işi değil)
            if getattr(pos, "asset_class", "us_equity") != "us_equity":
                continue

            # Sadece short pozisyonlar (Alpaca: negatif qty = short)
            if qty >= 0:
                continue

            abs_qty = abs(qty)

            # Cooldown kontrolu
            cooldown_until = bot.sell_cooldown.get(f"short_{symbol}")
            if cooldown_until and datetime.now() < cooldown_until:
                continue

            entry_price = float(pos.avg_entry_price)
            current_price = float(pos.current_price)

            # SHORT P&L: fiyat DUSTUYSE kar, YUKSELDI ise zarar
            pnl_pct = (entry_price - current_price) / entry_price if entry_price > 0 else 0
            pnl_usd = float(pos.unrealized_pl)

            # Short pozisyon senkronizasyonu
            if symbol not in bot.short_positions:
                cached = getattr(bot, "_exit_flag_cache", {}).get(symbol, {})
                if cached and not exit_flag_cache_matches_entry(cached, entry_price):
                    # Farkli girisin bayraklari — dusur (yanlis tetik enjeksiyonu)
                    getattr(bot, "_exit_flag_cache", {}).pop(symbol, None)
                    cached = {}
                bot.short_positions[symbol] = {
                    "entry_price": entry_price,
                    "qty": abs_qty,
                    "entry_time": cached.get("entry_time") or datetime.now().isoformat(),
                    "lowest_price": min(
                        current_price, cached.get("lowest_price", current_price) or current_price
                    ),
                    "synced_from_alpaca": True,
                    "partial_covered": cached.get("partial_covered", False),
                    "breakeven_set": cached.get("breakeven_set", False),
                    "server_stop_verified": bool(cached.get("server_stop_verified", False)),
                    "server_stop_order_id": cached.get("server_stop_order_id") or None,
                    "close_in_progress": bool(cached.get("close_in_progress", False)),
                }
                if cached.get("stop_loss_pct") is not None:
                    bot.short_positions[symbol]["stop_loss_pct"] = cached["stop_loss_pct"]
                if cached.get("stop_loss_price") is not None:
                    bot.short_positions[symbol]["stop_loss_price"] = cached["stop_loss_price"]

            pos_data = bot.short_positions.get(symbol, {})
            stop_trigger = self._ensure_canonical_trigger(
                symbol, pos_data, entry_price, "SHORT", short_config
            )

            # Trailing: en dusuk fiyat takibi (ters trailing)
            lowest = pos_data.get("lowest_price", entry_price)
            if current_price < lowest:
                lowest = current_price
                bot.short_positions[symbol]["lowest_price"] = lowest

            # Dipten yukari ziplama orani
            trailing_rise = (current_price - lowest) / lowest if lowest > 0 else 0

            # === BREAK-EVEN SHORT ===
            if short_config.get("short_breakeven_enabled", True):
                be_trigger = short_config.get("short_breakeven_trigger_pct", 0.025)
                be_offset = short_config.get("short_breakeven_offset_pct", 0.003)
                if pnl_pct >= be_trigger and not pos_data.get("breakeven_set", False):
                    # Mevcut SHORT BE semantiği P&L'de -be_offset'tir: stop
                    # entry'nin biraz üstündedir ve o küçük zararda cover eder.
                    be_price = round(entry_price * (1 + be_offset), 2)
                    result = self._update_server_stop_loss(
                        symbol, be_price, abs_qty, side="SHORT"
                    )
                    target_verified = (
                        result.verified
                        and result.stop_price is not None
                        and abs(result.stop_price - be_price) <= 0.011
                    )
                    if target_verified:
                        stop_trigger = float(result.stop_price)
                        bot.short_positions[symbol]["stop_loss_price"] = stop_trigger
                        bot.short_positions[symbol]["breakeven_set"] = True
                        if hasattr(bot, "_stash_exit_flags"):
                            bot._stash_exit_flags(
                                symbol, bot.short_positions[symbol]
                            )  # A6
                        logger.info(
                            f"  SHORT BREAK-EVEN {symbol}: +{pnl_pct:.1%} -> "
                            f"SL girisa cekildi (${be_price:.2f})"
                        )

            # === SATIS KARARLARI ===

            # 1. STOP-LOSS (fiyat YUKARI gitti = zarar)
            if should_exit_locally(current_price, stop_trigger, "SHORT"):
                logger.info(
                    f"  🛑 SHORT STOP {symbol}: {pnl_pct:.1%} "
                    f"(trigger: ${stop_trigger:.2f}) (${pnl_usd:+.2f})"
                )
                bot.short_executor.execute_cover(
                    symbol,
                    f"SHORT_STOP_LOSS ({pnl_pct:.1%} / trigger ${stop_trigger:.2f})",
                )

            # 2. TAKE PROFIT (fiyat ASAGI gitti = kar)
            elif pnl_pct >= short_config["short_take_profit_pct"]:
                logger.info(
                    f"  💰 SHORT TP {symbol}: +{pnl_pct:.1%} (${pnl_usd:+.2f})"
                )
                bot.short_executor.execute_cover(symbol, f"SHORT_TAKE_PROFIT (+{pnl_pct:.1%})")

            # 3. TRAILING STOP (dipten yukari ziplama)
            elif pnl_pct > 0.01 and trailing_rise >= short_config["short_trailing_stop_pct"]:
                logger.info(
                    f"  📉 SHORT TRAIL {symbol}: Low ${lowest:,.2f} -> ${current_price:,.2f} "
                    f"(+{trailing_rise:.1%}) | P&L: {pnl_pct:.1%}"
                )
                bot.short_executor.execute_cover(symbol, f"SHORT_TRAILING (+{trailing_rise:.1%})")

            # 4. KADEMELI COVER (yarisini kapat)
            elif (pnl_pct >= short_config.get("short_partial_profit_pct", 0.04)
                  and not pos_data.get("partial_covered", False)):
                logger.info(
                    f"  📊 SHORT PARTIAL {symbol}: +{pnl_pct:.1%} → Yarisini cover"
                )
                try:
                    from alpaca.trading.requests import MarketOrderRequest
                    from alpaca.trading.enums import OrderSide, TimeInForce
                    half_qty = round(abs_qty * 0.5, 4)
                    if half_qty > 0:
                        # A5: Yarı cover'dan ÖNCE tam-qty bracket çıkış (BUY) bacaklarını iptal et
                        self._cancel_exit_orders(symbol, "SHORT")
                        request = MarketOrderRequest(
                            symbol=symbol, qty=half_qty,
                            side=OrderSide.BUY,  # Cover = BUY
                            time_in_force=TimeInForce.DAY,
                        )
                        bot.client.submit_order(request)
                        bot.short_positions[symbol]["partial_covered"] = True
                        if hasattr(bot, "_stash_exit_flags"):
                            bot._stash_exit_flags(symbol, bot.short_positions[symbol])  # A6
                        from datetime import timedelta
                        bot.sell_cooldown[f"short_{symbol}"] = datetime.now() + timedelta(seconds=config.get("sell_cooldown_seconds", 300))
                        bot._save_position_metadata()
                        logger.info(f"  ✅ Short yarisini cover: {half_qty} {symbol} (Cooldown eklendi)")
                        # A5: Kalan short için koruyucu stop'u (BUY-stop) yeniden kur
                        remaining_qty = round(abs_qty - half_qty, 4)
                        if remaining_qty > 0:
                            prot_price = self._ensure_canonical_trigger(
                                symbol, bot.short_positions[symbol], entry_price,
                                "SHORT", short_config,
                            )
                            if prot_price is not None:
                                self._update_server_stop_loss(
                                    symbol, prot_price, remaining_qty, side="SHORT"
                                )
                except Exception as e:
                    logger.error(f"Short partial cover hatasi {symbol}: {e}")

            # Durum logla
            if abs(pnl_pct) > 0.02:
                logger.info(
                    f"  SHORT {symbol}: {pnl_pct:+.2%} (${pnl_usd:+.2f}) | "
                    f"Low: ${lowest:,.2f} | Rise: +{trailing_rise:.2%}"
                )

        # === DIŞ KAPANIŞ MUTABAKATI (SHORT) ===
        try:
            alpaca_shorts = {
                p.symbol for p in positions
                if float(p.qty) < 0 and getattr(p, "asset_class", "us_equity") == "us_equity"
            }
            for sym in list(bot.short_positions.keys()):
                if sym in alpaca_shorts:
                    continue
                if hasattr(bot, "_reconcile_external_exit"):
                    bot._reconcile_external_exit(sym, side="SHORT")
        except Exception as e:
            logger.debug(f"  Dış kapanış mutabakat hatası (SHORT): {e}")

    # ================================================================
    # KORUMA EMRİ GARANTİSİ (startup + günlük)
    # ================================================================

    def ensure_protective_stops(self, config: Dict) -> ProtectionSummary:
        """Tüm strategy equity pozisyonlarında gerçek stop kapsamasını uzlaştır."""
        bot = self.bot
        summary = ProtectionSummary()
        try:
            positions = bot.client.get_all_positions()
            orders = flatten_orders(bot.client.get_orders(
                GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True)
            ))
        except Exception as exc:
            result = ProtectionResult(
                ProtectionOutcome.FAILED_NAKED, None, None, 0.0,
                f"Koruma sorgusu başarısız: {exc}",
            )
            summary.results.append(result)
            summary.detail = result.detail
            protection_alarm(bot, "RECONCILIATION_QUERY", result.detail)
            return summary

        for pos in positions:
            symbol = str(getattr(pos, "symbol", "") or "")
            side = "LONG"
            if getattr(pos, "asset_class", "us_equity") != "us_equity":
                continue
            try:
                raw_qty = float(getattr(pos, "qty", 0) or 0)
                if raw_qty == 0:
                    continue
                side = "LONG" if raw_qty > 0 else "SHORT"
                qty = abs(raw_qty)

                parking = getattr(bot, "index_parking", None)
                is_parking = (
                    parking is not None and parking.is_parking_symbol(symbol)
                )

                book = self._position_book(side)
                pos_data = book.get(symbol, {})
                entry = float(getattr(pos, "avg_entry_price", 0) or 0)
                if entry <= 0:
                    result = ProtectionResult(
                        ProtectionOutcome.FAILED_NAKED, None, None, 0.0,
                        f"{symbol}: entry fiyatı geçersiz; koruma hesaplanamadı",
                    )
                    summary.results.append(self._alarm_result(symbol, side, result))
                    continue

                wanted_side = OrderSide.SELL if side == "LONG" else OrderSide.BUY
                symbol_orders = [
                    order for order in orders
                    if str(getattr(order, "symbol", "") or "") == symbol
                    and enum_value(getattr(order, "side", None)) == enum_value(wanted_side)
                ]
                stop_orders = [
                    order for order in symbol_orders if is_stop_order(order)
                ]

                elected = None
                covering = None
                for stop_order in stop_orders:
                    check = classify_covering_order(stop_order, pos, side)
                    if check.outcome == ProtectionOutcome.ELECTED_UNFILLED:
                        elected = check
                        break
                    if check.verified:
                        covering = check
                        break

                # Parking otomatik olarak değiştirilmez. Yine de kapsaması varsa
                # doğrulanır; yalnız uncovered parking SKIPPED + alarm döner.
                if is_parking:
                    if covering is not None:
                        summary.results.append(covering)
                        clear_expected_uncovered(bot, f"{symbol}:PARKING")
                    else:
                        reason = elected.detail if elected is not None else (
                            f"{symbol}: index parking otomatik koruma dışında "
                            "ve kapsayan stop yok"
                        )
                        result = ProtectionResult(
                            ProtectionOutcome.SKIPPED_PARKING,
                            elected.order_id if elected is not None else None,
                            elected.stop_price if elected is not None else None,
                            elected.qty_covered if elected is not None else 0.0,
                            reason,
                        )
                        summary.results.append(result)
                        note_expected_uncovered(
                            bot, f"{symbol}:PARKING", result.detail
                        )
                    continue

                if elected is not None:
                    summary.results.append(self._alarm_result(symbol, side, elected))
                    continue
                if covering is not None:
                    summary.results.append(
                        self._apply_protection_result(symbol, side, covering)
                    )
                    if pos_data.get("close_in_progress", False):
                        protection_alarm(
                            bot, f"{symbol}:CLOSE_IN_PROGRESS",
                            f"{symbol}: restart sonrası kapanış yarım kalmış; "
                            "pozisyon korumalı fakat hâlâ açık",
                        )
                    continue

                side_config = config if side == "LONG" else SHORT_CONFIG
                target = self._ensure_canonical_trigger(
                    symbol, pos_data, entry, side, side_config
                )
                if target is None:
                    raise ValueError(
                        f"{symbol}: kanonik stop_loss_price türetilemedi"
                    )

                logger.warning(
                    f"  Koruma kapsama eksiği: {symbol} -> "
                    f"stop ${target:.2f} doğrulanacak"
                )
                result = self._update_server_stop_loss(
                    symbol, round(target, 2), qty, side=side
                )
                summary.results.append(result)
            except Exception as exc:
                result = ProtectionResult(
                    ProtectionOutcome.FAILED_NAKED, None, None, 0.0,
                    f"{symbol}: koruma uzlaştırma hatası: {exc}",
                )
                summary.results.append(self._alarm_result(symbol, side, result))

        if summary.placed:
            logger.info(
                f"  {summary.placed} pozisyonun sunucu stop'u doğrulanarak kuruldu"
            )
        summary.detail = (
            f"verified={summary.verified}, placed={summary.placed}, "
            f"failed={summary.failed}"
        )
        return summary

    # ================================================================
    # SUNUCU TARAFLI STOP-LOSS GUNCELLEME
    # ================================================================

    def _cancel_exit_orders(self, symbol: str, side: str = "LONG"):
        """Sembol için açık çıkış emirlerini (bracket TP limit + SL stop) iptal eder.

        Yarı satış/cover öncesi çağrılır: tam-qty bracket bacaklarının kalan adetten
        fazlasını satıp pozisyonu net-SHORT'a düşürmesini veya emir reddini önler (A5).
        """
        bot = self.bot
        exit_side = OrderSide.SELL if side == "LONG" else OrderSide.BUY
        try:
            orders = bot.client.get_orders(
                GetOrdersRequest(status=QueryOrderStatus.OPEN)
            )
            for o in orders:
                if o.symbol == symbol and o.side == exit_side:
                    bot.client.cancel_order_by_id(o.id)
                    logger.debug(f"  Çıkış emri iptal ({side}): {symbol} #{o.id}")
        except Exception as e:
            logger.debug(f"  Çıkış emri iptal hatası {symbol}: {e}")

    def verify_protective_stop(
        self, symbol: str, side: str = "LONG",
        expected_stop: float | None = None,
    ) -> ProtectionResult:
        """Broker state'ini bounded poll ile yeniden okuyup kapsama kanıtı ver."""
        errors: list[str] = []
        for attempt in range(self._verify_attempts):
            try:
                position = self._current_position(symbol, side)
                if position is None:
                    result = ProtectionResult(
                        ProtectionOutcome.ALREADY_FLAT, None, expected_stop, 0.0,
                        f"{symbol}: {side} pozisyon henüz yok veya zaten düz",
                    )
                    if attempt == self._verify_attempts - 1:
                        return result
                    self._poll_pause()
                    continue

                orders = self._open_orders(symbol)
                elected = None
                for candidate in self._stop_candidates(orders, symbol, side):
                    result = classify_covering_order(
                        candidate, position, side, expected_stop
                    )
                    if result.verified:
                        return self._apply_protection_result(symbol, side, result)
                    if result.outcome == ProtectionOutcome.ELECTED_UNFILLED:
                        elected = result
                if elected is not None:
                    return self._alarm_result(symbol, side, elected)
                errors.append(f"deneme {attempt + 1}: kapsayan aktif stop yok")
            except Exception as exc:
                errors.append(f"deneme {attempt + 1}: {exc}")
            if attempt < self._verify_attempts - 1:
                self._poll_pause()

        result = ProtectionResult(
            ProtectionOutcome.FAILED_NAKED, None, expected_stop, 0.0,
            f"{symbol}: sunucu stop'u doğrulanamadı; {'; '.join(errors[-3:])}",
        )
        return self._alarm_result(symbol, side, result)

    def _wait_exit_cancellations(
        self, symbol: str, side: str, order_ids: list[str]
    ) -> tuple[bool, str]:
        """No-leg submit öncesi exit grubunun terminal olduğunu kanıtla."""
        pending = set(order_ids)
        details: list[str] = []
        for _ in range(self._verify_attempts):
            for oid in list(pending):
                try:
                    current = self._reread_order(oid)
                    status = enum_value(getattr(current, "status", None))
                    replaced_by = getattr(current, "replaced_by", None)
                    details.append(
                        f"{oid}:{status or 'durum-yok'}"
                        + (f"->{replaced_by}" if replaced_by else "")
                    )
                    if is_terminal_order(current):
                        pending.discard(oid)
                except Exception as exc:
                    # Açık emir listesinden kaybolduysa terminal kabul edilir.
                    try:
                        open_ids = {
                            order_id(order) for order in self._open_orders(symbol)
                        }
                        if oid not in open_ids:
                            pending.discard(oid)
                        else:
                            details.append(f"{oid}:sorgu-hatası:{exc}")
                    except Exception as query_exc:
                        details.append(f"{oid}:sorgu-hatası:{query_exc}")
            if not pending:
                return True, ", ".join(details[-6:])
            self._poll_pause()
        return False, (
            f"terminal iptal bekleme süresi doldu: {sorted(pending)}; "
            + ", ".join(details[-6:])
        )

    def _update_server_stop_loss(self, symbol: str, new_stop_price: float,
                                  qty: float, side: str = "LONG") -> ProtectionResult:
        """Mevcut stop'u PATCH et; stop yoksa iptal-bekle-submit et ve doğrula."""
        bot = self.bot
        side = side.upper()
        target = round(float(new_stop_price), 2)
        requested_qty = round(abs(float(qty)), 4)
        exit_side = OrderSide.SELL if side == "LONG" else OrderSide.BUY
        limit_price = round(
            target * (0.995 if side == "LONG" else 1.005), 2
        )
        tif = (
            TimeInForce.GTC
            if float(requested_qty) == int(requested_qty)
            else TimeInForce.DAY
        )
        client_id = deterministic_client_order_id(
            symbol, side, target, requested_qty
        )
        errors: list[str] = []
        replacement_ids: list[str] = []
        replace_attempted = False
        submitted_without_leg = False
        canceled_group = False

        for attempt in range(self._verify_attempts):
            try:
                # Retry kör değildir: pozisyon, eski stop, replacement chain ve
                # dönen replacement her turda yeniden okunur.
                position = self._current_position(symbol, side)
                if position is None:
                    result = ProtectionResult(
                        ProtectionOutcome.ALREADY_FLAT, None, target, 0.0,
                        f"{symbol}: stop değişirken pozisyon zaten düz",
                    )
                    return self._apply_protection_result(symbol, side, result)

                orders = self._open_orders(symbol)
                stop_orders = self._stop_candidates(orders, symbol, side)

                # Yerel kayıtlı eski ID ve broker replacement zincirini de oku.
                known_ids: list[str] = []
                local = self._position_book(side).get(symbol, {})
                local_id = local.get("server_stop_order_id")
                if local_id:
                    known_ids.append(str(local_id))
                known_ids.extend(replacement_ids)
                for candidate in list(stop_orders):
                    oid = order_id(candidate)
                    if oid:
                        known_ids.append(oid)
                for oid in dict.fromkeys(known_ids):
                    try:
                        fresh = self._reread_order(oid)
                        stop_orders.append(fresh)
                        replaced_by = getattr(fresh, "replaced_by", None)
                        if replaced_by:
                            replacement_id = str(replaced_by)
                            replacement_ids.append(replacement_id)
                            try:
                                stop_orders.append(
                                    self._reread_order(replacement_id)
                                )
                            except Exception as chain_exc:
                                errors.append(
                                    f"replacement {replacement_id}: {chain_exc}"
                                )
                    except Exception as read_exc:
                        errors.append(f"order {oid}: {read_exc}")

                deduped: list = []
                seen_ids: set[str] = set()
                for candidate in stop_orders:
                    marker = order_id(candidate) or f"object:{id(candidate)}"
                    if marker not in seen_ids:
                        seen_ids.add(marker)
                        deduped.append(candidate)
                stop_orders = deduped

                for candidate in stop_orders:
                    check = classify_covering_order(
                        candidate, position, side, expected_stop=target
                    )
                    if check.verified:
                        outcome = (
                            ProtectionOutcome.NO_LEG_RESUBMITTED
                            if submitted_without_leg
                            else ProtectionOutcome.REPLACED_VERIFIED
                            if replace_attempted
                            else ProtectionOutcome.VERIFIED
                        )
                        result = ProtectionResult(
                            outcome, check.order_id, check.stop_price,
                            check.qty_covered,
                            f"{symbol}: stop yeniden okunup doğrulandı "
                            f"({outcome.value})",
                        )
                        self._apply_protection_result(symbol, side, result)
                        logger.info(
                            f"  SL DOĞRULANDI {symbol}: ${target:.2f} ({side}) "
                            f"| #{result.order_id}"
                        )
                        return result

                # Kabul edilmiş/pending replacement varken aynı PATCH'i körlemesine
                # tekrarlama; sonraki poll zinciri tekrar okuyacak.
                pending_replacement = any(
                    order_id(candidate) in replacement_ids
                    and enum_value(getattr(candidate, "status", None))
                    in ACTIVE_STATUSES
                    for candidate in stop_orders
                )
                if pending_replacement:
                    errors.append(f"deneme {attempt + 1}: replacement pending")
                    self._poll_pause()
                    continue

                existing_stop = next(
                    (
                        candidate for candidate in stop_orders
                        if is_active_order(candidate)
                        or enum_value(getattr(candidate, "status", None)) == "stopped"
                    ),
                    None,
                )
                if existing_stop is not None:
                    oid = order_id(existing_stop)
                    if oid is None:
                        raise RuntimeError("Mevcut stop emrinin ID'si yok")
                    request_kwargs = {
                        "time_in_force": tif,
                        "stop_price": target,
                        "client_order_id": client_id,
                    }
                    # Fractional replace'te qty kesinlikle OMIT edilir.
                    if float(requested_qty) == int(requested_qty):
                        request_kwargs["qty"] = int(requested_qty)
                    current_type = enum_value(
                        getattr(existing_stop, "type", None)
                        or getattr(existing_stop, "order_type", None)
                    )
                    if current_type == "stop_limit":
                        request_kwargs["limit_price"] = limit_price
                    replacement = bot.client.replace_order_by_id(
                        oid, ReplaceOrderRequest(**request_kwargs)
                    )
                    replace_attempted = True
                    replacement_id = order_id(replacement)
                    if replacement_id:
                        replacement_ids.append(replacement_id)
                    errors.append(
                        f"deneme {attempt + 1}: PATCH {oid}"
                        + (f"->{replacement_id}" if replacement_id else "")
                    )
                    self._poll_pause()
                    continue

                # Stop bacağı YOK: ancak bu durumda conflicting exit grubunu
                # iptal et, terminal durumunu bekle ve yeni stop submit et.
                if not canceled_group:
                    conflicts = [
                        order for order in orders
                        if str(getattr(order, "symbol", "") or "") == symbol
                        and enum_value(getattr(order, "side", None))
                        == enum_value(exit_side)
                        and is_active_order(order)
                    ]
                    conflict_ids = [
                        oid for oid in (order_id(order) for order in conflicts) if oid
                    ]
                    for oid in conflict_ids:
                        bot.client.cancel_order_by_id(oid)
                    if conflict_ids:
                        canceled, cancel_detail = self._wait_exit_cancellations(
                            symbol, side, conflict_ids
                        )
                        if not canceled:
                            raise RuntimeError(cancel_detail)
                        errors.append(cancel_detail)
                    canceled_group = True

                # İptal beklerken pozisyon kapanmış olabilir; submit'ten önce tekrar oku.
                position = self._current_position(symbol, side)
                if position is None:
                    result = ProtectionResult(
                        ProtectionOutcome.ALREADY_FLAT, None, target, 0.0,
                        f"{symbol}: exit grubu iptal edilirken pozisyon düzleşti",
                    )
                    return self._apply_protection_result(symbol, side, result)

                sl_request = StopLimitOrderRequest(
                    symbol=symbol,
                    qty=requested_qty,
                    side=exit_side,
                    time_in_force=tif,
                    stop_price=target,
                    limit_price=limit_price,
                    client_order_id=client_id,
                )
                submitted = bot.client.submit_order(sl_request)
                submitted_without_leg = True
                submitted_id = order_id(submitted)
                if submitted_id:
                    replacement_ids.append(submitted_id)
                errors.append(
                    f"deneme {attempt + 1}: no-leg submit"
                    + (f"->{submitted_id}" if submitted_id else "")
                )
            except Exception as exc:
                errors.append(f"deneme {attempt + 1}: {exc}")
            if attempt < self._verify_attempts - 1:
                self._poll_pause()

        # Hedef update başarısız olsa bile eski stop hâlen gerçek kapsama
        # sağlıyorsa bunu yalanlamadan VERIFIED olarak raporla.
        try:
            position = self._current_position(symbol, side)
            if position is None:
                result = ProtectionResult(
                    ProtectionOutcome.ALREADY_FLAT, None, target, 0.0,
                    f"{symbol}: deadline sonunda pozisyon düz",
                )
                return self._apply_protection_result(symbol, side, result)
            for candidate in self._stop_candidates(
                self._open_orders(symbol), symbol, side
            ):
                old_check = classify_covering_order(candidate, position, side)
                if old_check.verified:
                    result = ProtectionResult(
                        ProtectionOutcome.VERIFIED, old_check.order_id,
                        old_check.stop_price, old_check.qty_covered,
                        f"{symbol}: hedef stop kurulamadı; eski kapsayan stop aktif. "
                        + "; ".join(errors[-3:]),
                    )
                    self._apply_protection_result(symbol, side, result)
                    protection_alarm(
                        bot, f"{symbol}:{side}:UPDATE", result.detail
                    )
                    return result
                if old_check.outcome == ProtectionOutcome.ELECTED_UNFILLED:
                    return self._alarm_result(symbol, side, old_check)
        except Exception as final_exc:
            errors.append(f"son doğrulama: {final_exc}")

        result = ProtectionResult(
            ProtectionOutcome.FAILED_NAKED, None, target, 0.0,
            f"{symbol}: bounded deadline içinde koruma kurulamadı; "
            + "; ".join(errors[-5:]),
        )
        return self._alarm_result(symbol, side, result)
