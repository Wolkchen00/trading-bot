"""
Position Manager — Pozisyon Yönetimi

StockBot’tan ayrıştırılmış pozisyon modülü.
- manage_positions(): Trailing stop, break-even, kademeli kâr alma, stop-loss
- Sunucu taraflı SL güncellemesi: Break-even ve trailing stop değiştiğinde
  Alpaca’daki stop emri de güncellenir (bot çökse bile korunma devam eder)
"""
from datetime import datetime, timedelta
import time
from typing import Dict
from uuid import uuid4

from alpaca.trading.requests import (
    MarketOrderRequest, StopLimitOrderRequest, GetOrdersRequest,
    ReplaceOrderRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus

from config import SHORT_CONFIG, STOCK_CONFIG
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
from core.telemetry import append_telemetry
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
                if result.at_target:
                    pos_data["stop_loss_price"] = verified_trigger
                elif (
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
                    "partial_intent": cached.get("partial_intent"),
                    "partial_retry_budget": cached.get("partial_retry_budget"),
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
                        and (
                            result.at_target
                            or abs(result.stop_price - breakeven_price) <= 0.011
                        )
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
            exit_action_attempted = False

            if should_exit_locally(current_price, stop_trigger, "LONG"):
                exit_action_attempted = True
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
                exit_action_attempted = True
                logger.info(
                    f"  💰 TAKE PROFIT {symbol}: +{pnl_pct:.1%} "
                    f"(hedef {pos_tp_pct:.1%}) (${pnl_usd:+.2f})"
                )
                bot.executor.execute_sell(symbol, f"TAKE_PROFIT (+{pnl_pct:.1%})")

            # 3. TRAILING STOP
            elif pnl_pct > 0.01 and trailing_drop >= config["trailing_stop_pct"]:
                exit_action_attempted = True
                logger.info(
                    f"  TRAILING STOP {symbol}: Peak ${highest:,.2f} -> ${current_price:,.2f} "
                    f"(-{trailing_drop:.1%}) | P&L: {pnl_pct:.1%}"
                )
                bot.executor.execute_sell(symbol, f"TRAILING_STOP (peak -{trailing_drop:.1%})")

            # 4. KADEMELİ KÂR ALMA
            elif (pnl_pct >= config["partial_profit_pct"]
                  and not pos_data.get("partial_sold", False)):
                exit_action_attempted = self._handle_long_partial(
                    symbol=symbol,
                    snapshot_position=pos,
                    pos_data=pos_data,
                    entry_price=entry_price,
                    current_price=current_price,
                    pnl_pct=pnl_pct,
                    config=config,
                )

            # 3b. Trailing sunucu-SL bakimi karar zincirinin DISINDADIR.
            # Bu dongude herhangi bir exit denendiyse bayat qty ile stop yazilmaz.
            if (
                not exit_action_attempted
                and pnl_pct > 0.02
                and pos_data.get("breakeven_set", False)
            ):
                trailing_sl_price = round(
                    highest * (1 - config["trailing_stop_pct"]), 2
                )
                try:
                    canonical_stop = float(
                        pos_data.get("stop_loss_price", 0) or 0
                    )
                except (TypeError, ValueError):
                    canonical_stop = 0.0
                if trailing_sl_price > canonical_stop + 0.10:
                    self._update_server_stop_loss(
                        symbol, trailing_sl_price, float(pos.qty), side="LONG"
                    )

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

    def _partial_day(self) -> str:
        get_day = getattr(self.bot, "_et_today", None)
        if callable(get_day):
            try:
                return get_day().isoformat()
            except Exception:
                pass
        return datetime.now().date().isoformat()

    def _persist_partial_position(self, symbol: str, pos_data: Dict) -> bool:
        """Partial intent state'ini order submit'ten once kalici hale getir."""
        if hasattr(self.bot, "_stash_exit_flags"):
            self.bot._stash_exit_flags(symbol, pos_data)
        save = getattr(self.bot, "_save_position_metadata", None)
        if not callable(save):
            logger.error(f"  {symbol}: partial intent icin kalici state yazici yok")
            return False
        try:
            return save() is True
        except Exception as exc:
            logger.error(f"  {symbol}: partial intent state yazilamadi: {exc}")
            return False

    def _partial_budget(self, pos_data: Dict) -> Dict:
        today = self._partial_day()
        budget = pos_data.get("partial_retry_budget")
        if not isinstance(budget, dict) or budget.get("date") != today:
            budget = {"date": today, "terminal_nofill": 0, "warned": False}
            pos_data["partial_retry_budget"] = budget
        return budget

    @staticmethod
    def _partial_tolerance(target_qty: float) -> float:
        return max(1e-4, abs(float(target_qty)) * 1e-4)

    @staticmethod
    def _partial_client_id(symbol: str) -> str:
        return f"r1p-{symbol.upper()[:10]}-{uuid4().hex[:24]}"

    def _partial_event(
        self, kind: str, symbol: str, pos_data: Dict, **fields
    ) -> bool:
        intent = pos_data.get("partial_intent")
        common = {
            "symbol": symbol,
            "side": "LONG",
            "entry_price": pos_data.get("entry_price"),
        }
        if isinstance(intent, dict):
            common.update({
                "intent_status": intent.get("status"),
                "client_order_id": intent.get("client_order_id"),
                "order_id": intent.get("order_id"),
                "target_qty": intent.get("target_qty"),
                "filled_qty": intent.get("filled_qty", 0),
                "remaining_target_qty": max(
                    float(intent.get("target_qty", 0) or 0)
                    - float(intent.get("filled_qty", 0) or 0),
                    0.0,
                ),
            })
        return append_telemetry(kind, **common, **fields)

    @staticmethod
    def _order_not_found(exc: Exception) -> bool:
        message = str(exc).lower()
        status_code = getattr(exc, "status_code", None)
        return (
            isinstance(exc, KeyError)
            or status_code == 404
            or "404" in message
            or "not found" in message
            or "order does not exist" in message
        )

    def _reconcile_partial_order(
        self, symbol: str, intent: Dict
    ) -> tuple[object | None, bool, str]:
        """Intent cid'ini brokerla uzlastir; ikinci satis ancak kesin yoklukta acilir."""
        oid = str(intent.get("order_id") or "")
        if oid:
            try:
                return self._reread_order(oid), True, f"order_id={oid}"
            except Exception as exc:
                if not self._order_not_found(exc):
                    return None, False, f"order_id sorgu hatasi: {exc}"

        cid = str(intent.get("client_order_id") or "")
        getter = getattr(self.bot.client, "get_order_by_client_id", None)
        if cid and callable(getter):
            try:
                return getter(cid), True, f"client_order_id={cid}"
            except Exception as exc:
                if not self._order_not_found(exc):
                    return None, False, f"client_order_id sorgu hatasi: {exc}"

        try:
            status_all = getattr(QueryOrderStatus, "ALL", QueryOrderStatus.OPEN)
            orders = flatten_orders(self.bot.client.get_orders(
                GetOrdersRequest(status=status_all, symbols=[symbol], nested=True)
            ))
            for candidate in orders:
                if str(getattr(candidate, "client_order_id", "") or "") == cid:
                    return candidate, True, f"order listesinde cid={cid}"
            return None, True, f"brokerda cid bulunamadi: {cid}"
        except Exception as exc:
            return None, False, f"broker intent uzlastirmasi basarisiz: {exc}"

    def _cancel_partial_conflicts(self, symbol: str) -> tuple[bool, str]:
        """Partial submit oncesi tum LONG exit bacaklarini terminal-dogrula."""
        try:
            conflicts = [
                order for order in self._open_orders(symbol)
                if str(getattr(order, "symbol", "") or "") == symbol
                and enum_value(getattr(order, "side", None))
                == enum_value(OrderSide.SELL)
                and is_active_order(order)
            ]
            ids = list(dict.fromkeys(
                oid for oid in (order_id(order) for order in conflicts) if oid
            ))
            for oid in ids:
                self.bot.client.cancel_order_by_id(oid)
            if not ids:
                return True, "acik exit bacagi yok"
            return self._wait_exit_cancellations(symbol, "LONG", ids)
        except Exception as exc:
            return False, f"exit iptal hazirligi basarisiz: {exc}"

    def _wait_partial_fill(
        self, symbol: str, intent: Dict, submitted: object | None
    ) -> tuple[object | None, bool, str]:
        """Partial emrini bounded bekle; timeout'ta iptal edip terminal-dogrula."""
        current = submitted
        details: list[str] = []
        for attempt in range(self._verify_attempts):
            if current is None:
                current, reconciled, detail = self._reconcile_partial_order(
                    symbol, intent
                )
                details.append(detail)
                if not reconciled:
                    if attempt < self._verify_attempts - 1:
                        self._poll_pause()
                    continue
            else:
                oid = order_id(current)
                if oid:
                    try:
                        current = self._reread_order(oid)
                    except Exception as exc:
                        details.append(f"{oid}: yeniden okuma hatasi: {exc}")

            if current is not None:
                status = enum_value(getattr(current, "status", None))
                try:
                    filled = abs(float(getattr(current, "filled_qty", 0) or 0))
                except (TypeError, ValueError):
                    filled = 0.0
                details.append(f"{order_id(current)}:{status}:fill={filled:.4f}")
                attempt_qty = float(intent.get("attempt_qty", 0) or 0)
                if filled + self._partial_tolerance(attempt_qty) >= attempt_qty:
                    return current, is_terminal_order(current) or status == "filled", ", ".join(details[-6:])
                if is_terminal_order(current):
                    return current, True, ", ".join(details[-6:])
            if attempt < self._verify_attempts - 1:
                self._poll_pause()

        oid = order_id(current) if current is not None else intent.get("order_id")
        if oid:
            try:
                self.bot.client.cancel_order_by_id(oid)
            except Exception as exc:
                details.append(f"timeout iptal hatasi {oid}: {exc}")
            terminal, detail = self._wait_exit_cancellations(
                symbol, "LONG", [str(oid)]
            )
            details.append(detail)
            try:
                current = self._reread_order(str(oid))
            except Exception as exc:
                details.append(f"terminal order okunamadi {oid}: {exc}")
            return current, terminal, ", ".join(details[-8:])
        return current, False, ", ".join(details[-8:]) or "partial order id yok"

    def _warn_partial_budget(self, symbol: str, pos_data: Dict) -> None:
        budget = self._partial_budget(pos_data)
        if budget.get("warned", False):
            return
        budget["warned"] = True
        self._persist_partial_position(symbol, pos_data)
        logger.warning(
            f"  PARTIAL RETRY BUTCESI DOLDU {symbol}: "
            f"{budget.get('terminal_nofill', 0)}/3; bugun yeni yari-satis yok"
        )
        self._partial_event(
            "PARTIAL_RETRY_EXHAUSTED", symbol, pos_data,
            severity="WARNING", retry_count=budget.get("terminal_nofill", 0),
        )

    def _finish_partial_attempt(
        self, symbol: str, pos_data: Dict, order: object | None,
        terminal_confirmed: bool, detail: str,
    ) -> None:
        intent = pos_data["partial_intent"]
        base = float(intent.get("attempt_base_filled_qty", 0) or 0)
        try:
            attempt_filled = abs(float(getattr(order, "filled_qty", 0) or 0))
        except (TypeError, ValueError):
            attempt_filled = 0.0
        target = float(intent.get("target_qty", 0) or 0)
        total_filled = min(round(base + attempt_filled, 4), target)
        intent["filled_qty"] = total_filled
        intent["attempt_terminal"] = bool(terminal_confirmed)
        intent["accounted_order_id"] = order_id(order) if order is not None else None
        intent["updated_at"] = datetime.now().isoformat()
        tolerance = self._partial_tolerance(target)

        if not terminal_confirmed:
            # Terminal kanit yoksa intent kapanmaz. Sonraki dongu AYNI cid/order'i
            # yeniden uzlastirir; yeni yari-satis emri acamaz.
            intent["status"] = "PARTIAL" if total_filled > tolerance else "SUBMITTED"
            pos_data["partial_sold"] = False
        elif total_filled + tolerance >= target:
            intent["status"] = "FILLED"
            pos_data["partial_sold"] = True
            self.bot.sell_cooldown[symbol] = datetime.now() + timedelta(
                seconds=float(intent.get("cooldown_seconds", 300) or 300)
            )
            logger.info(
                f"  PARTIAL DOLUM DOGRULANDI {symbol}: "
                f"{total_filled:.4f}/{target:.4f}"
            )
        elif total_filled > tolerance:
            intent["status"] = "PARTIAL"
            pos_data["partial_sold"] = False
            logger.warning(
                f"  PARTIAL KISMI DOLUM {symbol}: "
                f"{total_filled:.4f}/{target:.4f}; kalan hedef yeniden denenecek"
            )
        else:
            intent["status"] = "TERMINAL_NOFILL"
            pos_data["partial_sold"] = False

        nofill_attempt = attempt_filled <= self._partial_tolerance(
            float(intent.get("attempt_qty", 0) or 0)
        )
        if nofill_attempt and terminal_confirmed:
            budget = self._partial_budget(pos_data)
            budget["terminal_nofill"] = int(
                budget.get("terminal_nofill", 0) or 0
            ) + 1

        self._persist_partial_position(symbol, pos_data)
        self._partial_event(
            "PARTIAL_STATE", symbol, pos_data,
            terminal_confirmed=terminal_confirmed,
            broker_status=enum_value(getattr(order, "status", None)),
            detail=detail,
        )
        if int(self._partial_budget(pos_data).get("terminal_nofill", 0)) >= 3:
            self._warn_partial_budget(symbol, pos_data)

    def _restore_partial_protection(
        self, symbol: str, pos_data: Dict, entry_price: float, config: Dict,
        terminal_safe: bool = True,
    ) -> None:
        """Her partial sonucunda gercek kalan qty icin tek stopu geri kur."""
        if not terminal_safe:
            protection_alarm(
                self.bot, f"{symbol}:LONG:PARTIAL",
                f"{symbol}: partial emir terminal-dogrulanamadi; "
                "guvenli stop restore yapilamadi",
            )
            return
        try:
            position = self._current_position(symbol, "LONG")
            if position is None:
                return
            live_qty = abs(float(getattr(position, "qty", 0) or 0))
            if live_qty <= 0:
                return
            pos_data["qty"] = live_qty
            target = self._ensure_canonical_trigger(
                symbol, pos_data, entry_price, "LONG", config
            )
            if target is None:
                raise RuntimeError("kanonik stop_loss_price yok")
            result = self._update_server_stop_loss(
                symbol, target, live_qty, side="LONG"
            )
            if not result.verified:
                raise RuntimeError(result.detail)

            active_stops = [
                order for order in self._stop_candidates(
                    self._open_orders(symbol), symbol, "LONG"
                )
                if is_active_order(order)
            ]
            keep_id = str(result.order_id or "")
            extras = [
                str(order_id(order)) for order in active_stops
                if order_id(order) and str(order_id(order)) != keep_id
            ]
            for oid in extras:
                self.bot.client.cancel_order_by_id(oid)
            if extras:
                canceled, detail = self._wait_exit_cancellations(
                    symbol, "LONG", extras
                )
                if not canceled:
                    raise RuntimeError(detail)
            final = self.verify_protective_stop(
                symbol, "LONG", expected_stop=float(result.stop_price)
            )
            qty_tolerance = self._partial_tolerance(live_qty)
            if (
                not final.verified
                or abs(final.qty_covered - live_qty) > qty_tolerance
            ):
                raise RuntimeError(final.detail)
        except Exception as exc:
            protection_alarm(
                self.bot, f"{symbol}:LONG:PARTIAL",
                f"{symbol}: partial sonrasi kalan pozisyon icin stop "
                f"kurulamadi: {exc}",
            )

    def _handle_long_partial(
        self, symbol: str, snapshot_position, pos_data: Dict,
        entry_price: float, current_price: float, pnl_pct: float, config: Dict,
    ) -> bool:
        """Persisted intent + fill dogrulamali LONG yari-satis state machine'i."""
        threshold = float(config["partial_profit_pct"])
        if not self._partial_event(
            "PARTIAL_THRESHOLD", symbol, pos_data,
            pnl_pct=pnl_pct, threshold_pct=threshold,
            observed_qty=abs(float(snapshot_position.qty)),
        ):
            return False

        budget = self._partial_budget(pos_data)
        intent = pos_data.get("partial_intent")
        if not isinstance(intent, dict) or intent.get("status") in {
            "FILLED", "TERMINAL_NOFILL"
        }:
            if int(budget.get("terminal_nofill", 0) or 0) >= 3:
                self._warn_partial_budget(symbol, pos_data)
                return False
            snapshot_qty = abs(float(snapshot_position.qty))
            target_qty = round(snapshot_qty * 0.5, 4)
            if target_qty <= 0 or target_qty * current_price < 10.0:
                logger.debug(
                    f"  {symbol} kademeli satis cok kucuk: "
                    f"${target_qty * current_price:.2f} < $10, atla"
                )
                return False
            intent = {
                "status": "INTENT",
                "client_order_id": self._partial_client_id(symbol),
                "order_id": None,
                "target_qty": target_qty,
                "filled_qty": 0.0,
                "attempt_qty": target_qty,
                "attempt_base_filled_qty": 0.0,
                "attempt_terminal": False,
                "starting_qty": snapshot_qty,
                "sell_cooldown_seconds": config.get("sell_cooldown_seconds", 300),
                "cooldown_seconds": config.get("sell_cooldown_seconds", 300),
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
            pos_data["partial_intent"] = intent
            pos_data["partial_sold"] = False
            if not self._persist_partial_position(symbol, pos_data):
                return False
            if not self._partial_event(
                "PARTIAL_INTENT", symbol, pos_data,
                pnl_pct=pnl_pct, threshold_pct=threshold,
            ):
                return False
            created_now = True
        else:
            created_now = False

        terminal_safe = True
        attempted = False
        try:
            if intent.get("status") == "PARTIAL" and intent.get(
                "attempt_terminal", False
            ):
                existing_order, reconciled, detail = (
                    None, True, "onceki kismi dolum terminal-dogrulanmis"
                )
            else:
                existing_order, reconciled, detail = self._reconcile_partial_order(
                    symbol, intent
                )
            if (
                intent.get("status") == "PARTIAL"
                and existing_order is not None
                and str(intent.get("accounted_order_id") or "")
                == str(order_id(existing_order) or "")
                and is_terminal_order(existing_order)
            ):
                existing_order = None
            if not created_now and not reconciled:
                logger.warning(
                    f"  {symbol}: partial intent brokerla uzlastirilamadi; "
                    f"ikinci satis yok ({detail})"
                )
                terminal_safe = False
                return True

            if existing_order is not None:
                attempted = True
                intent["order_id"] = order_id(existing_order)
                intent["status"] = "SUBMITTED"
                self._persist_partial_position(symbol, pos_data)
                final_order, terminal_safe, wait_detail = self._wait_partial_fill(
                    symbol, intent, existing_order
                )
                self._finish_partial_attempt(
                    symbol, pos_data, final_order, terminal_safe, wait_detail
                )
                return True

            # Onceki terminal kismi dolumda yeni yari hedef hesaplanmaz; yalniz
            # orijinal hedefin eksik kalan adedi icin yeni attempt acilir.
            target = float(intent.get("target_qty", 0) or 0)
            filled = float(intent.get("filled_qty", 0) or 0)
            remaining_target = round(max(target - filled, 0.0), 4)
            if remaining_target <= self._partial_tolerance(target):
                intent["status"] = "FILLED"
                pos_data["partial_sold"] = True
                self._persist_partial_position(symbol, pos_data)
                return False
            if not created_now:
                if int(budget.get("terminal_nofill", 0) or 0) >= 3:
                    self._warn_partial_budget(symbol, pos_data)
                    return False
                intent["client_order_id"] = self._partial_client_id(symbol)
                intent["order_id"] = None
                intent["attempt_qty"] = remaining_target
                intent["attempt_base_filled_qty"] = filled
                intent["attempt_terminal"] = False
                intent["updated_at"] = datetime.now().isoformat()
                if not self._persist_partial_position(symbol, pos_data):
                    return False
                if not self._partial_event("PARTIAL_INTENT", symbol, pos_data):
                    return False

            canceled, cancel_detail = self._cancel_partial_conflicts(symbol)
            attempted = True
            if not canceled:
                terminal_safe = False
                raise RuntimeError(cancel_detail)

            # Iptal beklerken bracket bacagi dolmus olabilir. Bayat qty ile SELL
            # gonderilmez; taraf/adet submit'ten hemen once brokerdan yeniden okunur.
            live = self._current_position(symbol, "LONG")
            if live is None:
                intent["status"] = "TERMINAL_NOFILL"
                intent["updated_at"] = datetime.now().isoformat()
                self._persist_partial_position(symbol, pos_data)
                self._partial_event(
                    "PARTIAL_ABORTED_FLAT", symbol, pos_data,
                    detail="exit iptali beklenirken pozisyon kapandi",
                )
                return True
            live_qty = abs(float(getattr(live, "qty", 0) or 0))
            expected_live_qty = float(intent.get("starting_qty", live_qty)) - filled
            tolerance = self._partial_tolerance(target)
            if live_qty + tolerance < expected_live_qty:
                intent["status"] = "TERMINAL_NOFILL" if filled <= tolerance else "PARTIAL"
                intent["updated_at"] = datetime.now().isoformat()
                self._persist_partial_position(symbol, pos_data)
                self._partial_event(
                    "PARTIAL_ABORTED_POSITION_CHANGED", symbol, pos_data,
                    expected_position_qty=expected_live_qty,
                    observed_position_qty=live_qty,
                )
                return True
            submit_qty = round(min(remaining_target, live_qty), 4)
            if submit_qty <= 0:
                return True

            request = MarketOrderRequest(
                symbol=symbol,
                qty=submit_qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
                client_order_id=intent["client_order_id"],
            )
            try:
                submitted = self.bot.client.submit_order(request)
            except Exception as exc:
                detail = f"submit reddedildi: {exc}"
                logger.error(f"Kademeli satis hatasi {symbol}: {detail}")
                self._finish_partial_attempt(
                    symbol, pos_data, None, True, detail
                )
                return True

            intent["status"] = "SUBMITTED"
            intent["order_id"] = order_id(submitted)
            intent["attempt_qty"] = submit_qty
            intent["attempt_terminal"] = False
            intent["updated_at"] = datetime.now().isoformat()
            self._persist_partial_position(symbol, pos_data)
            self._partial_event("PARTIAL_STATE", symbol, pos_data)
            final_order, terminal_safe, wait_detail = self._wait_partial_fill(
                symbol, intent, submitted
            )
            self._finish_partial_attempt(
                symbol, pos_data, final_order, terminal_safe, wait_detail
            )
            return True
        except Exception as exc:
            logger.error(f"Kademeli satis hatasi {symbol}: {exc}")
            intent["updated_at"] = datetime.now().isoformat()
            self._persist_partial_position(symbol, pos_data)
            self._partial_event(
                "PARTIAL_ERROR", symbol, pos_data, detail=str(exc)
            )
            return attempted
        finally:
            self._restore_partial_protection(
                symbol, pos_data, entry_price, config,
                terminal_safe=terminal_safe,
            )

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

    def _monotonic_stop_target(
        self, symbol: str, side: str, requested_target: float,
        requested_qty: float,
    ) -> tuple[float, ProtectionResult | None]:
        """Sinirda stop regresyonunu engelle; tum cagiricilar ayni klampi kullanir."""
        side = side.upper()
        local = self._position_book(side).get(symbol, {})
        candidates = [float(requested_target)]

        try:
            canonical = float(local.get("stop_loss_price", 0) or 0)
        except (TypeError, ValueError):
            canonical = 0.0
        if canonical > 0:
            candidates.append(canonical)

        # BE ancak dogrulanmis bayrakla klampa katilir. Salt esik gozlemi,
        # stopu break-even'a cekme yetkisi vermez.
        if local.get("breakeven_set", False):
            try:
                entry = float(local.get("entry_price", 0) or 0)
                if side == "LONG":
                    offset = float(STOCK_CONFIG.get("breakeven_offset_pct", 0.001))
                else:
                    offset = float(
                        SHORT_CONFIG.get("short_breakeven_offset_pct", 0.003)
                    )
                breakeven = round(entry * (1 + offset), 2)
                if breakeven > 0:
                    candidates.append(breakeven)
            except (TypeError, ValueError):
                pass

        covering: list[ProtectionResult] = []
        try:
            position = self._current_position(symbol, side)
            if position is not None:
                for order in self._stop_candidates(
                    self._open_orders(symbol), symbol, side
                ):
                    check = classify_covering_order(order, position, side)
                    qty_tolerance = self._partial_tolerance(requested_qty)
                    exact_qty = abs(check.qty_covered - requested_qty) <= qty_tolerance
                    if check.verified and check.stop_price is not None and exact_qty:
                        covering.append(check)
                        candidates.append(float(check.stop_price))
        except Exception as exc:
            # Broker sorgusu gecici olarak dusse bile kanonik klamp uygulanir;
            # asil update dongusu bounded retry ve alarm yolunu korur.
            logger.debug(f"  {symbol} monoton stop klampi broker sorgusu: {exc}")

        effective = max(candidates) if side == "LONG" else min(candidates)
        effective = round(float(effective), 2)

        if covering:
            best = (
                max(covering, key=lambda item: float(item.stop_price))
                if side == "LONG"
                else min(covering, key=lambda item: float(item.stop_price))
            )
            best_price = float(best.stop_price)
            reaches_effective = (
                best_price + 0.011 >= effective
                if side == "LONG"
                else best_price - 0.011 <= effective
            )
            is_better_than_requested = (
                best_price > requested_target + 0.011
                if side == "LONG"
                else best_price < requested_target - 0.011
            )
            if reaches_effective and is_better_than_requested:
                return effective, best
        return effective, None

    def _update_server_stop_loss(self, symbol: str, new_stop_price: float,
                                  qty: float, side: str = "LONG") -> ProtectionResult:
        """Mevcut stop'u PATCH et; stop yoksa iptal-bekle-submit et ve doğrula."""
        bot = self.bot
        side = side.upper()
        requested_target = round(float(new_stop_price), 2)
        requested_qty = round(abs(float(qty)), 4)
        target, better_cover = self._monotonic_stop_target(
            symbol, side, requested_target, requested_qty
        )
        if better_cover is not None:
            result = ProtectionResult(
                ProtectionOutcome.NOOP_BETTER_PROTECTED,
                better_cover.order_id,
                better_cover.stop_price,
                better_cover.qty_covered,
                f"{symbol}: mevcut stop istenen hedeften daha iyi; "
                f"regresif yenileme atlandi (${requested_target:.2f} -> "
                f"${float(better_cover.stop_price):.2f})",
                at_target=True,
            )
            self._apply_protection_result(symbol, side, result)
            logger.info(
                f"  SL KORUNDU {symbol}: mevcut ${float(result.stop_price):.2f} "
                f"regresif ${requested_target:.2f} hedefinden daha iyi ({side})"
            )
            return result
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
                    coverage = classify_covering_order(
                        candidate, position, side
                    )
                    qty_tolerance = self._partial_tolerance(requested_qty)
                    exact_qty = abs(
                        coverage.qty_covered - requested_qty
                    ) <= qty_tolerance
                    if (
                        coverage.verified
                        and coverage.stop_price is not None
                        and exact_qty
                    ):
                        active_price = float(coverage.stop_price)
                        better = (
                            active_price > target + 0.011
                            if side == "LONG"
                            else active_price < target - 0.011
                        )
                        if better:
                            result = ProtectionResult(
                                ProtectionOutcome.NOOP_BETTER_PROTECTED,
                                coverage.order_id,
                                coverage.stop_price,
                                coverage.qty_covered,
                                f"{symbol}: update sirasinda daha iyi aktif stop "
                                "dogrulandi; regresif PATCH atlandi",
                                at_target=True,
                            )
                            self._apply_protection_result(symbol, side, result)
                            return result
                    check = classify_covering_order(
                        candidate, position, side, expected_stop=target
                    )
                    qty_tolerance = self._partial_tolerance(requested_qty)
                    if (
                        check.verified
                        and abs(check.qty_covered - requested_qty) <= qty_tolerance
                    ):
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
                            at_target=True,
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
