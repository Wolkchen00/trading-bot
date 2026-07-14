"""
Order Executor — Hisse Senedi Alım/Satım Emir Yönetimi

- execute_buy(): Alım emri + adaptif stop-loss + PDT koruması
- execute_sell(): Satım emri + cooldown + PDT kontrolü
- Alpaca hisse senedi: komisyon $0, fractional shares destekli
"""
from datetime import datetime, timedelta
from typing import Dict

from alpaca.trading.requests import (
    MarketOrderRequest, StopLimitOrderRequest, GetOrdersRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus

from core.streak import update_loss_streak
from core.trade_gates import plan_exit_pcts
from utils.logger import logger


class OrderExecutor:
    """Hisse senedi alım/satım emirlerini yönetir."""

    def __init__(self, bot):
        self.bot = bot

    def execute_buy(self, symbol: str, analysis: Dict, config: Dict) -> bool:
        """Hisse alım emri — PDT-aware, fractional shares destekli."""
        bot = self.bot
        try:
            account = bot.client.get_account()
            cash = float(account.cash)
            equity = float(account.equity)

            # Equity floor kontrolü (A3: live+paper ikisinde de uygulanır)
            if bot.equity_floor > 0 and equity < bot.equity_floor:
                logger.warning(
                    f"EQUITY FLOOR! Hesap ${equity:,.2f} < floor ${bot.equity_floor:,.2f} — "
                    f"Yeni alim yapilmiyor."
                )
                return False

            # Market saati kontrolü
            if hasattr(bot, 'market_hours'):
                status = bot.market_hours.get_market_status()
                if not status["is_trading_allowed"]:
                    # Extended hours: sadece çok yüksek güvenle
                    confidence = analysis.get("confidence", 0)
                    if not bot.market_hours.should_allow_extended_hours(confidence):
                        logger.info(f"  Piyasa kapalı ({status['status']}), alım engellendi")
                        return False

            # Nakit rezerv kontrolü
            cash_reserve = equity * config.get("cash_reserve_pct", 0.15)
            available_cash = max(cash - cash_reserve, 0)

            if available_cash < 10:
                logger.warning(f"Nakit rezerv korumasi: Cash ${cash:.2f}, Rezerv ${cash_reserve:.2f}")
                return False

            # === KELLY-ATR ADAPTİF POZİSYON BOYUTLANDIRMA ===
            price = analysis["price"]

            if hasattr(bot, 'position_sizer'):
                sizing = bot.position_sizer.calculate_position_size(
                    equity=equity,
                    price=price,
                    atr=analysis.get("atr", 0),
                    config=config,
                    side="LONG",
                    consecutive_losses=getattr(bot, '_consecutive_losses', 0),
                    market_regime=getattr(bot, '_market_regime', 'NORMAL'),
                    sector_weight=analysis.get("sector_weight", 1.0),
                    confidence=analysis.get("confidence", 0),
                )
                max_invest = sizing["position_usd"]
                tier_weight = sizing.get("weight", 0.20)  # FIX: NameError önlemi
                if max_invest <= 0:
                    logger.debug(f"  {symbol} PositionSizer: {sizing['reasoning']}")
                    return False
            else:
                # Fallback: eski tier-based hesaplama
                tier_weight = config.get("tier_weights", {}).get(
                    symbol, config.get("default_tier_weight", 0.20)
                )
                max_invest = min(
                    available_cash * tier_weight,
                    equity * config["max_position_pct"],
                    bot.max_pos_usd,
                )

            # Available cash limiti
            max_invest = min(max_invest, available_cash)

            if max_invest < config.get("min_trade_value", 10):
                logger.warning(f"Yetersiz bakiye: ${max_invest:.2f} < min ${config.get('min_trade_value', 10)}")
                return False

            qty = round(max_invest / price, 4)  # Fractional shares
            # TAM PAY tercihi: Alpaca fractional emirleri DAY-only → GTC server-side
            # stop konulamıyor (gece koruması bot-loop'a kalıyor). Tam pay, hedef
            # tutarın >=%75'ini karşılıyorsa tam paya yuvarla ki GTC stop çalışsın.
            whole_qty = int(max_invest / price)
            if whole_qty >= 1 and whole_qty * price >= 0.75 * max_invest:
                qty = float(whole_qty)

            if qty * price < 1:
                logger.warning(f"Çok küçük işlem: ${qty * price:.2f}")
                return False

            logger.info(f"  Pozisyon: ${max_invest:.2f} | {qty:.4f} adet @ ${price:,.2f} (tier: {tier_weight:.0%})")

            # ADAPTIF STOP-LOSS + DİNAMİK TP (v4.8) — R:R gate ile AYNI plan
            # (plan_exit_pcts tek doğruluk kaynağı; TP = SL × min_rr, tavanlı)
            atr_value = analysis.get("atr", 0)
            adaptive_sl, adaptive_tp = plan_exit_pcts(atr_value, price, config)

            stop_price = round(price * (1 - adaptive_sl), 2)
            tp_price = round(price * (1 + adaptive_tp), 2)

            # BRACKET ORDER — BUY + TP + SL tek atomik emirle
            # Boylece SL basarisiz olursa korumasiz pozisyon kalmaz
            bracket_success = False
            try:
                # Tam payda GTC: TP/SL bacakları gece de aktif kalır (DAY'de gün
                # sonunda düşüp pozisyonu emirsiz bırakıyordu). Fractional'da
                # Alpaca GTC kabul etmez → DAY zorunlu.
                bracket_tif = (
                    TimeInForce.GTC if float(qty) == int(qty) else TimeInForce.DAY
                )
                request = MarketOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=OrderSide.BUY,
                    time_in_force=bracket_tif,
                    order_class="bracket",
                    take_profit={"limit_price": tp_price},
                    stop_loss={
                        "stop_price": stop_price,
                        "limit_price": round(stop_price * 0.995, 2),
                    },
                )
                order = bot.client.submit_order(request)
                bracket_success = True
                logger.info(
                    f"  BUY {symbol}: {qty:.4f} @ ${price:,.2f} "
                    f"(${qty * price:,.2f}) | BRACKET TP=${tp_price} SL=${stop_price} "
                    f"| {', '.join(analysis.get('reasons', []))}"
                )
            except Exception as bracket_err:
                logger.debug(f"  Bracket order desteklenmiyor, fallback: {bracket_err}")

            # FALLBACK: Bracket basarisizsa eski 2-adimli yontem
            if not bracket_success:
                request = MarketOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                )
                order = bot.client.submit_order(request)
                logger.info(
                    f"  BUY {symbol}: {qty:.4f} @ ${price:,.2f} "
                    f"(${qty * price:,.2f}) | Komisyon: $0 "
                    f"| {', '.join(analysis.get('reasons', []))}"
                )
                # Ayri SL emri — fractional qty'de Alpaca GTC kabul etmez, DAY kullan
                # (bot-loop stop'u her durumda devrede; DAY en azından bugünü korur)
                try:
                    limit_price = round(stop_price * 0.995, 2)
                    sl_tif = TimeInForce.GTC if float(qty) == int(qty) else TimeInForce.DAY
                    sl_request = StopLimitOrderRequest(
                        symbol=symbol, qty=qty,
                        side=OrderSide.SELL, time_in_force=sl_tif,
                        stop_price=stop_price, limit_price=limit_price,
                    )
                    bot.client.submit_order(sl_request)
                except Exception as sl_err:
                    logger.warning(f"  Stop-loss emri gonderilemedi: {sl_err}")

            logger.info(
                f"  STOP-LOSS: {symbol} @ ${stop_price:,.2f} ({adaptive_sl:.1%}) | "
                f"TP: ${tp_price:,.2f} ({adaptive_tp:.1%}, R:R {adaptive_tp/adaptive_sl:.1f}:1) "
                f"| ATR={atr_value:.4f}"
            )

            # Pozisyon kaydet — take_profit_pct de pozisyon-başına saklanır ki
            # position_manager dinamik hedefi bilsin (sabit config TP'si değil)
            bot.positions[symbol] = {
                "entry_price": price,
                "qty": qty,
                "entry_time": datetime.now().isoformat(),
                "order_id": str(order.id),
                "stop_loss_price": stop_price,
                "stop_loss_pct": adaptive_sl,
                "take_profit_pct": adaptive_tp,
            }
            bot.last_trade_time[symbol] = datetime.now()
            bot.trades_today.append({
                "action": "BUY", "symbol": symbol, "price": price,
                "qty": qty, "time": datetime.now().isoformat(),
            })
            bot.consecutive_errors = 0
            bot._daily_buys_count = getattr(bot, '_daily_buys_count', 0) + 1

            # Telegram bildirim
            if hasattr(bot, 'notifier'):
                bot.notifier.notify_buy(
                    symbol, qty, price,
                    confidence=int(analysis.get('confidence', 0)),
                    reasons=analysis.get('reasons', []),
                )

            return True

        except Exception as e:
            error_msg = str(e)
            # PDT rejection handler
            if "403" in error_msg or "pattern day trader" in error_msg.lower():
                if hasattr(bot, 'pdt_tracker'):
                    bot.pdt_tracker.handle_pdt_rejection(symbol, error_msg)
                logger.error(f"PDT VIOLATION: {symbol} alım reddedildi — {error_msg}")
            else:
                logger.error(f"BUY hatasi {symbol}: {e}")
            bot.consecutive_errors += 1
            return False

    def execute_sell(self, symbol: str, reason: str) -> bool:
        """Satış emri — PDT kontrolü ile."""
        bot = self.bot
        try:
            # Cooldown kontrolü
            cooldown_until = bot.sell_cooldown.get(symbol)
            if cooldown_until and datetime.now() < cooldown_until:
                logger.debug(f"  SELL cooldown: {symbol}")
                return False

            # PDT kontrolü — aynı gün alınmış pozisyon mu?
            pos = bot.positions.get(symbol, {})
            entry_time = pos.get("entry_time", "")
            if hasattr(bot, 'pdt_tracker') and entry_time:
                should_hold, hold_reason = bot.pdt_tracker.should_hold_overnight(symbol, entry_time)
                if should_hold:
                    # STOP_LOSS durumunda PDT'yi görmezden gel (sermaye koruması > PDT)
                    if "STOP_LOSS" not in reason:
                        logger.warning(f"  {hold_reason}")
                        return False
                    else:
                        logger.warning(f"  PDT: STOP_LOSS override — sermaye koruması öncelikli")

            # Pozisyon verileri — close_position ÖNCE al (kapandıktan sonra erişilemez)
            entry = pos.get("entry_price", 0)
            qty = pos.get("qty", 0)

            # Güncel fiyatı al ve PnL hesapla (close_position öncesi)
            pnl_usd = 0.0
            current_price = entry  # fallback
            try:
                alpaca_pos = bot.client.get_open_position(symbol)
                current_price = float(alpaca_pos.current_price)
                pnl_usd = float(alpaca_pos.unrealized_pl)
            except Exception:
                # Alpaca'dan alınamazsa manuel hesapla
                if entry > 0 and qty > 0:
                    pnl_usd = (current_price - entry) * qty

            # Bekleyen stop-loss emirlerini iptal et
            try:
                orders = bot.client.get_orders(
                    GetOrdersRequest(status=QueryOrderStatus.OPEN)
                )
                for o in orders:
                    if o.symbol == symbol and o.side == OrderSide.SELL:
                        bot.client.cancel_order_by_id(o.id)
                        logger.debug(f"  Eski stop-loss iptal: {o.id}")
            except Exception:
                pass

            # Pozisyonu kapat
            bot.client.close_position(symbol)

            # PDT kaydı (aynı gün alınıp satıldıysa)
            if hasattr(bot, 'pdt_tracker') and entry_time:
                if bot.pdt_tracker.is_same_day_position(symbol, entry_time):
                    bot.pdt_tracker.record_day_trade(
                        symbol, entry_time, datetime.now().isoformat()
                    )

            # Cooldown — swing trade için daha uzun (varsayılan 5dk)
            cooldown_secs = 300  # default 5 dakika
            try:
                from config import STOCK_CONFIG
                cooldown_secs = STOCK_CONFIG.get("sell_cooldown_seconds", 300)
            except Exception:
                pass
            bot.sell_cooldown[symbol] = datetime.now() + timedelta(seconds=cooldown_secs)

            pnl_pct = (pnl_usd / max(float(entry) * float(qty), 0.01)) * 100 if entry > 0 and qty > 0 else 0
            logger.info(
                f"  ✅ SELL {symbol}: {qty:.4f} @ ${current_price:,.2f} | "
                f"P&L: ${pnl_usd:+.2f} ({pnl_pct:+.1f}%) | Sebep: {reason}"
            )

            bot.positions.pop(symbol, None)
            # A6: tam kapanışta yönetim bayrak önbelleğini temizle (yeniden alımda
            # eski partial_sold/breakeven taşınmasın)
            if hasattr(bot, "_exit_flag_cache"):
                bot._exit_flag_cache.pop(symbol, None)
            bot.last_trade_time[symbol] = datetime.now()
            bot.trades_today.append({
                "action": "SELL", "symbol": symbol, "price": current_price,
                "pnl": pnl_usd, "reason": reason, "time": datetime.now().isoformat(),
            })

            # Kayıp/kazanç serisi — tek kaynak: gerçekleşen PnL işareti
            # (v4.12.1, core/streak.py; kârlı stop-out artık zarar SAYILMAZ).
            # Bear/ters-ETF hedge kapanışları seriyi etkilemez — hedge zararı
            # long giriş hunisini kilitlemesin (BEAR_* eski davranışla uyumlu).
            if not pos.get("bear_brain"):
                update_loss_streak(bot, symbol, pnl_usd)
            # WashSale kaydı — gerçekleşen zarar, çıkış etiketinden bağımsız
            if hasattr(bot, 'wash_sale_tracker') and pnl_usd < 0:
                bot.wash_sale_tracker.record_loss_sale(
                    symbol, pnl_usd, datetime.now().isoformat()[:10]
                )

            # Performans takibi
            if hasattr(bot, 'performance'):
                from config import SECTOR_MAP
                sector = SECTOR_MAP.get(symbol, "Unknown")
                bot.performance.record_trade(
                    symbol=symbol, action="SELL", qty=float(qty),
                    price=float(current_price), pnl=pnl_usd, reason=reason,
                    sector=sector,
                )

            # Ajan öz-değerlendirme feedback loop
            if hasattr(bot, 'agent_perf'):
                outcome = "WIN" if pnl_usd > 0 else "LOSS" if pnl_usd < 0 else "NEUTRAL"
                try:
                    bot.agent_perf.record_outcome(symbol, outcome, pnl_usd)
                except Exception:
                    pass

            # Telegram bildirim
            if hasattr(bot, 'notifier'):
                bot.notifier.notify_sell(symbol, reason, pnl_usd, pnl_pct)

            bot.consecutive_errors = 0
            return True

        except Exception as e:
            error_msg = str(e)
            if "403" in error_msg or "pattern day trader" in error_msg.lower():
                if hasattr(bot, 'pdt_tracker'):
                    bot.pdt_tracker.handle_pdt_rejection(symbol, error_msg)
                logger.error(f"PDT: {symbol} satış reddedildi — pozisyon overnight tutulacak")
            else:
                logger.error(f"SELL hatasi {symbol}: {e}")
            bot.consecutive_errors += 1
            return False
