"""
Position Manager — Pozisyon Yönetimi

StockBot’tan ayrıştırılmış pozisyon modülü.
- manage_positions(): Trailing stop, break-even, kademeli kâr alma, stop-loss
- Sunucu taraflı SL güncellemesi: Break-even ve trailing stop değiştiğinde
  Alpaca’daki stop emri de güncellenir (bot çökse bile korunma devam eder)
"""
from datetime import datetime
from typing import Dict

from alpaca.trading.requests import (
    MarketOrderRequest, StopLimitOrderRequest, GetOrdersRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus

from utils.logger import logger


class PositionManager:
    """Açık pozisyonları yönetir. StockBot referansı üzerinden state'e erişir."""

    def __init__(self, bot):
        self.bot = bot
        self._small_pos_log_time = {}  # Log spam önleyici: sembol → son log zamanı

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
                bot.positions[symbol] = {
                    "entry_price": entry_price,
                    "qty": float(pos.qty),
                    "entry_time": cached.get("entry_time") or datetime.now().isoformat(),
                    "highest_price": max(current_price, cached.get("highest_price", 0) or 0),
                    "breakeven_set": cached.get("breakeven_set", False),
                    "partial_sold": cached.get("partial_sold", False),
                    "synced_from_alpaca": True,
                }
                if cached.get("stop_loss_pct") is not None:
                    bot.positions[symbol]["stop_loss_pct"] = cached["stop_loss_pct"]

            # Trailing stop güncelleme
            pos_data = bot.positions.get(symbol, {})
            highest = pos_data.get("highest_price", entry_price)
            if current_price > highest:
                highest = current_price
                bot.positions[symbol]["highest_price"] = highest

            trailing_drop = (highest - current_price) / highest if highest > 0 else 0

            # === BREAK-EVEN STOP ===
            pos_sl_pct_override = None
            if config.get("breakeven_enabled", True):
                be_trigger = config.get("breakeven_trigger_pct", 0.015)
                be_offset = config.get("breakeven_offset_pct", 0.001)
                if pnl_pct >= be_trigger and not pos_data.get("breakeven_set", False):
                    breakeven_price = entry_price * (1 + be_offset)
                    bot.positions[symbol]["stop_loss_pct"] = be_offset
                    bot.positions[symbol]["breakeven_set"] = True
                    if hasattr(bot, "_stash_exit_flags"):
                        bot._stash_exit_flags(symbol, bot.positions[symbol])  # A6
                    logger.info(
                        f"  BREAK-EVEN {symbol}: +{pnl_pct:.1%} -> SL giris fiyatina cekildi (${breakeven_price:.2f})"
                    )
                    pos_sl_pct_override = be_offset
                    # Sunucu tarafli SL'yi guncelle (bot cokse bile korunsun)
                    self._update_server_stop_loss(
                        symbol, breakeven_price, float(pos.qty), side="LONG"
                    )

            # === SATIŞ KARARLARI (ÖNCELİK SIRASINA GÖRE) ===

            # 1. KESİN STOP-LOSS
            # None-güvenli okuma: eski metadata stop_loss_pct=null taşıyabiliyor;
            # .get(key, default) key VARSA None döner → "-None" TypeError ile tüm
            # pozisyon yönetimi çöküyordu (canlıda 2 Tem 2026'da yaşandı).
            if pos_sl_pct_override is not None:
                pos_sl_pct = pos_sl_pct_override
            else:
                pos_sl_pct = pos_data.get("stop_loss_pct")
                if pos_sl_pct is None:
                    pos_sl_pct = config["stop_loss_pct"]
            if pnl_pct <= -pos_sl_pct:
                logger.info(
                    f"  🛑 STOP LOSS {symbol}: {pnl_pct:.1%} (limit: -{pos_sl_pct:.1%}) (${pnl_usd:+.2f})"
                )
                bot.executor.execute_sell(symbol, f"STOP_LOSS ({pnl_pct:.1%} / limit -{pos_sl_pct:.1%})")

            # 2. TAKE PROFIT
            elif pnl_pct >= config["take_profit_pct"]:
                logger.info(
                    f"  💰 TAKE PROFIT {symbol}: +{pnl_pct:.1%} (${pnl_usd:+.2f})"
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
                    self._update_server_stop_loss(
                        symbol, trailing_sl_price, float(pos.qty), side="LONG"
                    )
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
                            sl_pct = bot.positions[symbol].get("stop_loss_pct", config["stop_loss_pct"])
                            if pos_data.get("breakeven_set", False):
                                prot_price = entry_price * (1 + config.get("breakeven_offset_pct", 0.001))
                            else:
                                prot_price = entry_price * (1 - sl_pct)
                            self._update_server_stop_loss(symbol, round(prot_price, 2), remaining_qty, side="LONG")
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
                bot.short_positions[symbol] = {
                    "entry_price": entry_price,
                    "qty": abs_qty,
                    "entry_time": datetime.now().isoformat(),
                    "lowest_price": current_price,
                    "synced_from_alpaca": True,
                    "partial_covered": False,
                }

            pos_data = bot.short_positions.get(symbol, {})

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
                    bot.short_positions[symbol]["stop_loss_pct"] = be_offset
                    bot.short_positions[symbol]["breakeven_set"] = True
                    if hasattr(bot, "_stash_exit_flags"):
                        bot._stash_exit_flags(symbol, bot.short_positions[symbol])  # A6
                    # Short break-even: fiyat giris fiyatinin biraz USTUNE SL koy
                    be_price = round(entry_price * (1 + be_offset), 2)
                    logger.info(
                        f"  SHORT BREAK-EVEN {symbol}: +{pnl_pct:.1%} -> SL girisa cekildi (${be_price:.2f})"
                    )
                    self._update_server_stop_loss(
                        symbol, be_price, abs_qty, side="SHORT"
                    )

            # === SATIS KARARLARI ===

            # 1. STOP-LOSS (fiyat YUKARI gitti = zarar) — None-güvenli okuma
            pos_sl = pos_data.get("stop_loss_pct")
            if pos_sl is None:
                pos_sl = short_config["short_stop_loss_pct"]
            if pnl_pct <= -pos_sl:
                logger.info(
                    f"  🛑 SHORT STOP {symbol}: {pnl_pct:.1%} (limit: -{pos_sl:.1%}) (${pnl_usd:+.2f})"
                )
                bot.short_executor.execute_cover(symbol, f"SHORT_STOP_LOSS ({pnl_pct:.1%})")

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
                            sl_pct = bot.short_positions[symbol].get("stop_loss_pct", short_config["short_stop_loss_pct"])
                            if pos_data.get("breakeven_set", False):
                                prot_price = entry_price * (1 + short_config.get("short_breakeven_offset_pct", 0.003))
                            else:
                                prot_price = entry_price * (1 + sl_pct)
                            self._update_server_stop_loss(symbol, round(prot_price, 2), remaining_qty, side="SHORT")
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

    def ensure_protective_stops(self, config: Dict):
        """Açık LONG pozisyonlarda sunucu-taraflı koruma emri yoksa stop yerleştirir.

        Bracket emirler DAY olduğundan TP/SL bacakları gün sonunda düşer; ertesi
        günden itibaren pozisyon yalnız bot-loop stop'una kalıyordu (bot çöker ya
        da takılırsa tamamen korumasız — 2 Tem 2026'da canlıda AMZN+META böyle
        kaldı). Startup ve günlük reset'te çağrılır.

        Güvenlik: sembolde AÇIK herhangi bir SELL emri varsa (bracket TP bacağı
        dahil) DOKUNMAZ — aynı adet için ikinci satış emri over-commit yaratırdı.
        """
        bot = self.bot
        try:
            positions = bot.client.get_all_positions()
            orders = bot.client.get_orders(
                GetOrdersRequest(status=QueryOrderStatus.OPEN)
            )
        except Exception as e:
            logger.debug(f"  Koruma emri kontrolü yapılamadı: {e}")
            return

        open_sell_syms = {o.symbol for o in orders if o.side == OrderSide.SELL}
        placed = 0
        for pos in positions:
            symbol = pos.symbol
            try:
                if getattr(pos, "asset_class", "us_equity") != "us_equity":
                    continue
                qty = float(pos.qty)
                if qty <= 0:
                    continue  # short'ların GTC stop'u girişte konuyor
                if bot.index_parking.is_parking_symbol(symbol):
                    continue
                if symbol in open_sell_syms:
                    continue  # zaten bir çıkış emri var (bracket/stop) — karışma
                entry = float(pos.avg_entry_price)
                if entry <= 0:
                    continue
                pos_data = bot.positions.get(symbol, {})
                sl_pct = pos_data.get("stop_loss_pct")
                if sl_pct is None:
                    sl_pct = config["stop_loss_pct"]
                if pos_data.get("breakeven_set"):
                    prot_price = entry * (1 + config.get("breakeven_offset_pct", 0.001))
                else:
                    prot_price = entry * (1 - sl_pct)
                logger.info(
                    f"  🛡️ Koruma emri eksikti: {symbol} → stop ${prot_price:.2f} yerleştiriliyor"
                )
                self._update_server_stop_loss(symbol, round(prot_price, 2), qty, side="LONG")
                placed += 1
            except Exception as e:
                logger.debug(f"  Koruma emri hatası {symbol}: {e}")
        if placed:
            logger.info(f"  🛡️ {placed} pozisyona koruyucu stop yerleştirildi")

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

    def _update_server_stop_loss(self, symbol: str, new_stop_price: float,
                                  qty: float, side: str = "LONG"):
        """
        Alpaca'daki mevcut stop emrini iptal edip yeni fiyattan yenisini koyar.

        Bu metot sayesinde:
        - Break-even'a gectiginde sunucu SL de giris fiyatina cekilir
        - Trailing stop yukseldikce sunucu SL de yukarı cekilir
        - Bot cokse/restart olsa bile Alpaca stop emri aktif kalir

        Args:
            symbol: Hisse sembolu
            new_stop_price: Yeni stop fiyati
            qty: Hisse adedi
            side: "LONG" (SELL stop) veya "SHORT" (BUY stop)
        """
        bot = self.bot
        try:
            # 1. Mevcut stop emirlerini bul ve iptal et
            orders = bot.client.get_orders(
                GetOrdersRequest(status=QueryOrderStatus.OPEN)
            )

            cancel_side = OrderSide.SELL if side == "LONG" else OrderSide.BUY

            for order in orders:
                if (order.symbol == symbol
                    and order.side == cancel_side
                    and order.type in ("stop", "stop_limit")):
                    bot.client.cancel_order_by_id(order.id)
                    logger.debug(f"  Eski stop emri iptal: {symbol} #{order.id}")

            # 2. Yeni stop emri koy
            if side == "LONG":
                limit_price = round(new_stop_price * 0.995, 2)  # %0.5 slippage payi
            else:
                limit_price = round(new_stop_price * 1.005, 2)  # SHORT: limit > stop

            # Fractional qty'de Alpaca GTC kabul etmez → DAY (bot-loop stop'u yedek)
            sl_qty = round(qty, 4)
            sl_tif = TimeInForce.GTC if float(sl_qty) == int(sl_qty) else TimeInForce.DAY
            sl_request = StopLimitOrderRequest(
                symbol=symbol,
                qty=sl_qty,
                side=cancel_side,
                time_in_force=sl_tif,
                stop_price=round(new_stop_price, 2),
                limit_price=limit_price,
            )
            bot.client.submit_order(sl_request)

            logger.info(
                f"  SL GUNCELLENDI {symbol}: ${new_stop_price:.2f} ({side}) "
                f"| Limit: ${limit_price:.2f}"
            )

        except Exception as e:
            logger.warning(f"  Sunucu SL guncelleme hatasi {symbol}: {e}")
