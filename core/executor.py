"""
Order Executor ,  Hisse Senedi Alım/Satım Emir Yönetimi

- execute_buy(): Alım emri + adaptif stop-loss + PDT koruması
- execute_sell(): Satım emri + cooldown + PDT kontrolü
- Alpaca hisse senedi: komisyon $0, fractional shares destekli
"""
from datetime import datetime, timedelta, timezone
import time
from typing import Dict
from uuid import uuid4

from alpaca.trading.requests import (
    MarketOrderRequest, StopLimitOrderRequest, GetOrdersRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus

from core.streak import update_loss_streak
from core.fill_ledger import episode_realized_pnl, record_fill
from core.order_journal import bind as journal_bind
from core.order_journal import prepare as journal_prepare
from core.trade_gates import plan_exit_pcts
from core.protection import (
    ProtectionOutcome,
    protection_alarm,
)
from core.risk_guard import can_open_new_risk
from utils.logger import logger


class OrderExecutor:
    """Hisse senedi alım/satım emirlerini yönetir."""

    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    def _funnel_bump_safe(
        bot, stage: str, reason: str | None = None, symbol: str | None = None
    ) -> None:
        """Telemetri arizasi emir akisini veya sonucunu degistiremez."""
        try:
            bump = getattr(bot, "_funnel_bump", None)
            if bump is None:
                return
            if symbol is None:
                bump(stage, reason=reason)
            else:
                bump(stage, reason=reason, symbol=symbol)
        except Exception:
            pass

    @staticmethod
    def _fill_ids(order: object | None) -> tuple[str | None, str | None, str | None]:
        if order is None:
            return None, None, None
        execution_id = None
        for attr in ("execution_id", "activity_id", "fill_id"):
            value = str(getattr(order, attr, "") or "").strip()
            if value:
                execution_id = value
                break
        order_id = str(getattr(order, "id", "") or "").strip() or None
        client_order_id = (
            str(getattr(order, "client_order_id", "") or "").strip() or None
        )
        return execution_id, order_id, client_order_id

    def _ledger_alarm(self, symbol: str, operation: str, exc: Exception) -> None:
        detail = f"{symbol}: fill ledger {operation} hatasi: {exc}"
        try:
            logger.error(f"  DEFTER HATASI {detail}")
        except Exception:
            pass
        try:
            protection_alarm(self.bot, f"{symbol}:FILL_LEDGER", detail)
        except Exception:
            pass

    def _journal_prepare_safe(
        self, client_order_id: str, symbol: str, side: str, provenance: str,
        *, qty: float | None = None, notional: float | None = None,
    ) -> None:
        try:
            journal_prepare(
                client_order_id, symbol, side, provenance,
                qty=qty, notional=notional,
            )
        except Exception as exc:
            self._ledger_alarm(symbol, "journal prepare", exc)

    def _journal_bind_safe(
        self, client_order_id: str, order: object | None, symbol: str
    ) -> None:
        order_id = str(getattr(order, "id", "") or "").strip()
        if not order_id:
            return
        try:
            journal_bind(client_order_id, order_id)
        except Exception as exc:
            self._ledger_alarm(symbol, "journal bind", exc)

    def _record_fill_safe(self, symbol: str, **fields) -> bool:
        try:
            return record_fill(symbol=symbol, **fields)
        except Exception as exc:
            self._ledger_alarm(symbol, "yazim", exc)
            return False

    def _prior_episode_pnl_safe(self, symbol: str, entry_ts: str) -> float:
        try:
            return episode_realized_pnl(symbol, entry_ts) if entry_ts else 0.0
        except Exception as exc:
            self._ledger_alarm(symbol, "episode okuma", exc)
            return 0.0

    def _real_entry_fill(
        self, symbol: str, order: object | None, expected_qty: float,
        expected_price: float,
    ) -> tuple[float, float, object | None, bool]:
        """Entry order/pozisyonundan broker ortalama dolumunu en iyi kanitla oku."""
        candidates = [order] if order is not None else []
        order_id = str(getattr(order, "id", "") or "")
        if order_id:
            try:
                candidates.insert(0, self.bot.client.get_order_by_id(order_id))
            except Exception:
                pass
        for candidate in candidates:
            try:
                fill_price = float(
                    getattr(candidate, "filled_avg_price", 0) or 0
                )
                filled_qty = abs(float(getattr(candidate, "filled_qty", 0) or 0))
            except (TypeError, ValueError):
                continue
            if fill_price > 0 and filled_qty > 0:
                return fill_price, filled_qty, candidate, False
        try:
            position = self.bot.client.get_open_position(symbol)
            fill_price = float(getattr(position, "avg_entry_price", 0) or 0)
            filled_qty = abs(float(getattr(position, "qty", 0) or 0))
            if fill_price > 0 and filled_qty > 0:
                return fill_price, filled_qty, order, True
        except Exception:
            pass
        # Mevcut akisin basarili saydigi entry kaybolmasin; broker ortalamasi
        # okunamadigi acikca degraded olarak isaretlenir.
        return float(expected_price), abs(float(expected_qty)), order, True

    def _position_is_flat(self, symbol: str) -> tuple[bool, object | None, str]:
        """Başarılı pozisyon sorgusuyla flat'i ayır; sorgu hatasını flat sayma."""
        errors = []
        attempts = int(getattr(self.bot.position_manager, "_verify_attempts", 6))
        for attempt in range(attempts):
            try:
                positions = self.bot.client.get_all_positions()
                position = next(
                    (
                        item for item in positions
                        if str(getattr(item, "symbol", "") or "") == symbol
                        and float(getattr(item, "qty", 0) or 0) != 0
                    ),
                    None,
                )
                if position is None:
                    return True, None, ""
                if attempt == attempts - 1:
                    return False, position, ""
            except Exception as exc:
                errors.append(str(exc))
            if attempt < attempts - 1:
                delay = float(
                    getattr(self.bot, "_protection_poll_seconds", 0.25) or 0
                )
                if delay > 0:
                    time.sleep(delay)
        return False, None, "; ".join(errors[-3:])

    def _real_exit_fill(
        self, symbol: str, close_order: object, expected_qty: float
    ) -> tuple[float, float, str]:
        """Flat doğrulandıktan sonra close emrinin gerçek broker fill'ini oku."""
        order_id = str(getattr(close_order, "id", "") or "")
        candidates = [close_order]
        if order_id:
            try:
                candidates.insert(0, self.bot.client.get_order_by_id(order_id))
            except Exception as exc:
                logger.debug(f"  {symbol} close fill yeniden okunamadı #{order_id}: {exc}")

        for order in candidates:
            try:
                fill_price = float(getattr(order, "filled_avg_price", 0) or 0)
                filled_qty = abs(float(getattr(order, "filled_qty", 0) or 0))
            except (TypeError, ValueError):
                continue
            if fill_price > 0 and filled_qty > 0:
                # close_position tam kapama niyetidir. Broker'ın gerçek fill qty'si
                # daha hassas olsa da yerel qty'yi aşan eski/stale bir emri kabul etme.
                tolerance = max(1e-4, abs(float(expected_qty)) * 1e-4)
                if filled_qty <= abs(float(expected_qty)) + tolerance:
                    return fill_price, filled_qty, order_id
        return 0.0, 0.0, order_id

    def _restore_after_failed_close(
        self, symbol: str, pos: Dict, close_detail: str
    ) -> None:
        """Kapanmayan LONG pozisyonda yerel marker'ı koru ve stop'u geri kur."""
        bot = self.bot
        pos["close_in_progress"] = True
        try:
            from config import STOCK_CONFIG
            entry = float(pos.get("entry_price", 0) or 0)
            sl_pct = pos.get("stop_loss_pct")
            if sl_pct is None:
                sl_pct = STOCK_CONFIG["stop_loss_pct"]
            target = float(pos.get("stop_loss_price", 0) or 0)
            if target <= 0 and pos.get("breakeven_set"):
                target = entry * (
                    1 + STOCK_CONFIG.get("breakeven_offset_pct", 0.001)
                )
            if target <= 0:
                target = entry * (1 - float(sl_pct))
            qty = float(pos.get("qty", 0) or 0)
            result = bot.position_manager._update_server_stop_loss(
                symbol, round(target, 2), qty, side="LONG"
            )
            pos["server_stop_verified"] = result.verified
            pos["server_stop_order_id"] = result.order_id if result.verified else None
            detail = (
                f"{symbol}: close_position sonrası pozisyon açık kaldı; "
                f"koruma={result.outcome.value}. {close_detail}"
            )
            protection_alarm(bot, f"{symbol}:CLOSE_FAILED", detail)
        except Exception as exc:
            pos["server_stop_verified"] = False
            pos["server_stop_order_id"] = None
            protection_alarm(
                bot, f"{symbol}:CLOSE_FAILED",
                f"{symbol}: kapanış başarısız ve koruma geri kurulamadı: "
                f"{close_detail}; {exc}",
            )
        if hasattr(bot, "_stash_exit_flags"):
            bot._stash_exit_flags(symbol, pos)
        if hasattr(bot, "_save_position_metadata"):
            saved = bot._save_position_metadata()
            if saved is not True:
                logger.error(
                    f"  {symbol} kapanış-failure marker kaydı doğrulanamadı"
                )

    def execute_buy(self, symbol: str, analysis: Dict, config: Dict) -> bool:
        """Hisse alım emri ,  PDT-aware, fractional shares destekli."""
        bot = self.bot
        try:
            fill_provenance = str(config.get("ledger_provenance", "strategy"))
            allowed, block_reason = can_open_new_risk(
                bot, config, kind="stock_long", symbol=symbol
            )
            if not allowed:
                if block_reason == "LIVE_LOCK_R5":
                    logger.info(
                        f"  {symbol} canlı alım R5 kilidiyle atlandı "
                        f"(live_entries_enabled=False)"
                    )
                else:
                    logger.info(
                        f"  {symbol} yeni risk girişi engellendi: {block_reason}"
                    )
                return False

            self._funnel_bump_safe(
                bot, "reached_executor", symbol=symbol
            )

            account = bot.client.get_account()
            cash = float(account.cash)
            equity = float(account.equity)

            # Equity floor kontrolü (A3: live+paper ikisinde de uygulanır)
            if bot.equity_floor > 0 and equity < bot.equity_floor:
                logger.warning(
                    f"EQUITY FLOOR! Hesap ${equity:,.2f} < floor ${bot.equity_floor:,.2f} ,  "
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

            # ADAPTIF STOP-LOSS + DİNAMİK TP (v4.8) ,  R:R gate ile AYNI plan
            # (plan_exit_pcts tek doğruluk kaynağı; TP = SL × min_rr, tavanlı)
            atr_value = analysis.get("atr", 0)
            adaptive_sl, adaptive_tp = plan_exit_pcts(atr_value, price, config)

            stop_price = round(price * (1 - adaptive_sl), 2)
            tp_price = round(price * (1 + adaptive_tp), 2)

            # BRACKET ORDER ,  BUY + TP + SL tek atomik emirle.
            # LIVE'da bracket reddi pozisyon açmama sebebidir; iki-adımlı fallback yok.
            bracket_success = False
            paper_fallback = False
            order = None
            entry_client_order_id = None
            try:
                # Tam payda GTC: TP/SL bacakları gece de aktif kalır (DAY'de gün
                # sonunda düşüp pozisyonu emirsiz bırakıyordu). Fractional'da
                # Alpaca GTC kabul etmez → DAY zorunlu.
                bracket_tif = (
                    TimeInForce.GTC if float(qty) == int(qty) else TimeInForce.DAY
                )
                entry_client_order_id = (
                    f"r9-{symbol.upper()[:10]}-b-{uuid4().hex[:24]}"
                )
                request = MarketOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=OrderSide.BUY,
                    time_in_force=bracket_tif,
                    client_order_id=entry_client_order_id,
                    order_class="bracket",
                    take_profit={"limit_price": tp_price},
                    stop_loss={
                        "stop_price": stop_price,
                        "limit_price": round(stop_price * 0.995, 2),
                    },
                )
                self._journal_prepare_safe(
                    entry_client_order_id, symbol, "BUY", fill_provenance, qty=qty
                )
                order = bot.client.submit_order(request)
                self._journal_bind_safe(entry_client_order_id, order, symbol)
                bracket_success = True
            except Exception as bracket_err:
                try:
                    from config import TRADING_MODE
                    default_paper = TRADING_MODE != "live"
                except Exception:
                    default_paper = True
                is_live = not bool(getattr(bot, "is_paper", default_paper))
                if is_live:
                    self._funnel_bump_safe(
                        bot,
                        "gate_block",
                        reason="FRACTIONAL_NO_BRACKET",
                        symbol=symbol,
                    )
                    logger.error(
                        f"  LIVE bracket reddedildi; {symbol} pozisyonu AÇILMADI: "
                        f"{bracket_err}"
                    )
                    return False
                logger.warning(
                    f"  PAPER bracket desteklenmiyor, iki-adımlı fallback: {bracket_err}"
                )

            # PAPER-only fallback: canlıda bu kola yukarıda kesinlikle girilmez.
            if not bracket_success:
                paper_fallback = True
                entry_client_order_id = (
                    f"r9-{symbol.upper()[:10]}-b-{uuid4().hex[:24]}"
                )
                request = MarketOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                    client_order_id=entry_client_order_id,
                )
                self._journal_prepare_safe(
                    entry_client_order_id, symbol, "BUY", fill_provenance, qty=qty
                )
                order = bot.client.submit_order(request)
                self._journal_bind_safe(entry_client_order_id, order, symbol)

            # Pozisyon kaydet ,  take_profit_pct de pozisyon-başına saklanır ki
            # position_manager dinamik hedefi bilsin (sabit config TP'si değil)
            entry_now = datetime.now()
            if symbol in bot.positions:
                old_pos = bot.positions[symbol]
                logger.warning(
                    f"  {symbol} mevcut pozisyon kaydi uzerine yaziliyor: "
                    f"episode_id={old_pos.get('episode_id', '')} | "
                    f"entry_time_utc={old_pos.get('entry_time_utc', '')}"
                )
            bot.positions[symbol] = {
                "entry_price": price,
                "qty": qty,
                "entry_time": entry_now.isoformat(),
                "entry_time_utc": entry_now.astimezone(timezone.utc).isoformat(),
                "episode_id": entry_client_order_id,
                "provenance": fill_provenance,
                "order_id": (
                    str(getattr(order, "id", ""))
                    if getattr(order, "id", None) is not None else None
                ),
                "stop_loss_price": stop_price,
                "stop_loss_pct": adaptive_sl,
                "take_profit_pct": adaptive_tp,
                "server_stop_verified": False,
                "server_stop_order_id": None,
                "close_in_progress": False,
            }

            # Submission/HTTP 200 kanıt değildir. Pozisyonu ve stop bacağını broker'dan
            # yeniden oku; ancak kapsama doğrulanırsa BUY başarısı raporla.
            if paper_fallback:
                protection = bot.position_manager._update_server_stop_loss(
                    symbol, stop_price, qty, side="LONG"
                )
            else:
                protection = bot.position_manager.verify_protective_stop(
                    symbol, side="LONG", expected_stop=stop_price
                )

            target_verified = (
                protection.verified
                and protection.stop_price is not None
                and abs(protection.stop_price - stop_price) <= 0.011
            )
            if protection.outcome == ProtectionOutcome.ALREADY_FLAT:
                bot.positions.pop(symbol, None)
                logger.warning(
                    f"  {symbol} bracket kabul edildi fakat bounded sürede entry "
                    "pozisyonu görülmedi; BUY başarısı raporlanmadı"
                )
                return False

            entry_fill_price, entry_filled_qty, fill_order, entry_degraded = (
                self._real_entry_fill(symbol, order, qty, price)
            )
            execution_id, entry_order_id, broker_client_id = self._fill_ids(
                fill_order
            )
            self._record_fill_safe(
                symbol,
                side="BUY",
                qty=entry_filled_qty,
                price=entry_fill_price,
                pnl_usd=None,
                provenance=fill_provenance,
                execution_id=execution_id,
                order_id=entry_order_id,
                client_order_id=broker_client_id or entry_client_order_id,
                episode_id=entry_client_order_id,
                degraded=entry_degraded,
            )
            if not target_verified:
                bot.positions[symbol]["server_stop_verified"] = False
                bot.positions[symbol]["server_stop_order_id"] = None
                if hasattr(bot, "_stash_exit_flags"):
                    bot._stash_exit_flags(symbol, bot.positions[symbol])
                if hasattr(bot, "_save_position_metadata"):
                    bot._save_position_metadata()
                protection_alarm(
                    bot, f"{symbol}:LONG:ENTRY",
                    f"{symbol}: entry doldu/kaydedildi fakat kapsayan stop "
                    f"doğrulanamadı ({protection.outcome.value}): "
                    f"{protection.detail}",
                )
                return False

            bot.positions[symbol]["server_stop_verified"] = True
            bot.positions[symbol]["server_stop_order_id"] = protection.order_id
            if hasattr(bot, "_stash_exit_flags"):
                bot._stash_exit_flags(symbol, bot.positions[symbol])
            if hasattr(bot, "_save_position_metadata"):
                saved = bot._save_position_metadata()
                if saved is not True:
                    protection_alarm(
                        bot, f"{symbol}:LONG:ENTRY_PERSIST",
                        f"{symbol}: doğrulanmış stop state'i diske yazılamadı",
                    )
            logger.info(
                f"  BUY {symbol}: {qty:.4f} @ ${price:,.2f} "
                f"(${qty * price:,.2f}) | "
                f"{'PAPER FALLBACK' if paper_fallback else 'BRACKET'} "
                f"TP=${tp_price} SL=${stop_price} "
                f"| {', '.join(analysis.get('reasons', []))}"
            )
            logger.info(
                f"  STOP-LOSS DOĞRULANDI: {symbol} @ ${protection.stop_price:,.2f} "
                f"({adaptive_sl:.1%}) #{protection.order_id} | "
                f"TP: ${tp_price:,.2f} ({adaptive_tp:.1%}, "
                f"R:R {adaptive_tp/adaptive_sl:.1f}:1) | ATR={atr_value:.4f}"
            )
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
                logger.error(f"PDT VIOLATION: {symbol} alım reddedildi ,  {error_msg}")
            else:
                logger.error(f"BUY hatasi {symbol}: {e}")
            bot.consecutive_errors += 1
            return False

    def execute_sell(self, symbol: str, reason: str) -> bool:
        """Satış emri ,  PDT kontrolü ile."""
        bot = self.bot
        try:
            # Cooldown kontrolü
            cooldown_until = bot.sell_cooldown.get(symbol)
            if cooldown_until and datetime.now() < cooldown_until:
                logger.debug(f"  SELL cooldown: {symbol}")
                return False

            # PDT kontrolü ,  aynı gün alınmış pozisyon mu?
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
                        logger.warning(f"  PDT: STOP_LOSS override ,  sermaye koruması öncelikli")

            # Pozisyon verileri ,  close_position ÖNCE al (kapandıktan sonra erişilemez)
            entry = pos.get("entry_price", 0)
            qty = pos.get("qty", 0)

            # Crash-safe kapanış niyeti: exit emirlerine dokunmadan ÖNCE diske yaz.
            pos["close_in_progress"] = True
            if hasattr(bot, "_stash_exit_flags"):
                bot._stash_exit_flags(symbol, pos)
            if not hasattr(bot, "_save_position_metadata"):
                protection_alarm(
                    bot, f"{symbol}:CLOSE_MARKER",
                    f"{symbol}: close-in-progress marker kalıcı yazılamıyor; "
                    "çıkış başlatılmadı",
                )
                return False
            marker_saved = bot._save_position_metadata()
            if marker_saved is not True:
                protection_alarm(
                    bot, f"{symbol}:CLOSE_MARKER",
                    f"{symbol}: close-in-progress marker diske yazılamadı; "
                    "çıkış başlatılmadı",
                )
                return False

            # Güncel fiyatı al ve PnL hesapla (close_position öncesi)
            pnl_usd = 0.0
            current_price = entry  # fallback
            try:
                alpaca_pos = bot.client.get_open_position(symbol)
                current_price = float(alpaca_pos.current_price)
                pnl_usd = float(alpaca_pos.unrealized_pl)
            except Exception:
                # v4.12.2: eski "manuel hesap" ölü koddu ,  current_price=entry
                # ile PnL matematiksel olarak HEP 0 çıkıyor, kayıp serisi
                # (streak) sessizce atlanıyordu. Önce snapshot fiyatı dene;
                # o da yoksa PnL bilinmiyor: 0 bırak ama GÖRÜNÜR uyarı bas.
                snap_price = None
                try:
                    from core.gap_scanner import fetch_latest_price
                    snap_price = fetch_latest_price(bot.data_client, symbol)
                except Exception:
                    snap_price = None
                if snap_price and entry > 0 and qty > 0:
                    current_price = float(snap_price)
                    pnl_usd = (current_price - entry) * qty
                else:
                    logger.warning(
                        f"  {symbol} kapanış PnL'i hesaplanamadı (pozisyon+snapshot "
                        f"erişilemedi) ,  kayıp serisi bu çıkışta güncellenmeyecek"
                    )

            # Bekleyen stop-loss emirlerini iptal et
            try:
                orders = bot.client.get_orders(
                    GetOrdersRequest(status=QueryOrderStatus.OPEN)
                )
                for o in orders:
                    if o.symbol == symbol and o.side == OrderSide.SELL:
                        bot.client.cancel_order_by_id(o.id)
                        logger.debug(f"  Eski stop-loss iptal: {o.id}")
            except Exception as cancel_exc:
                protection_alarm(
                    bot, f"{symbol}:CLOSE_CANCEL",
                    f"{symbol}: kapanış exit emirleri sorgu/iptal hatası: {cancel_exc}",
                )

            # close_position kabulü execution değildir; broker pozisyonunu poll et.
            close_order = None
            try:
                close_order = bot.client.close_position(symbol)
            except Exception as close_exc:
                self._restore_after_failed_close(
                    symbol, pos, f"close_position reddi/hatası: {close_exc}"
                )
                logger.error(f"SELL hatasi {symbol}: {close_exc}")
                bot.consecutive_errors += 1
                return False

            is_flat, remaining_position, query_detail = self._position_is_flat(symbol)
            if not is_flat:
                if remaining_position is not None:
                    pos["qty"] = abs(float(
                        getattr(remaining_position, "qty", qty) or qty
                    ))
                self._restore_after_failed_close(
                    symbol, pos,
                    query_detail or "close_position kabul edildi fakat pozisyon açık",
                )
                logger.error(
                    f"SELL doğrulanamadı {symbol}: pozisyon hâlâ açık; koruma geri kuruldu"
                )
                bot.consecutive_errors += 1
                return False

            # Flat olmak tek başına fiyat/PnL kanıtı değildir. Snapshot/unrealized
            # değerlerini muhasebeleştirmek phantom kayda yol açar; gerçek close fill
            # broker'dan yeniden okunamazsa kayıt reconciler'a bırakılır.
            fill_price, filled_qty, exit_order_id = self._real_exit_fill(
                symbol, close_order, qty
            )
            if fill_price <= 0 or filled_qty <= 0:
                pos["close_in_progress"] = True
                if hasattr(bot, "_stash_exit_flags"):
                    bot._stash_exit_flags(symbol, pos)
                bot._save_position_metadata()
                protection_alarm(
                    bot, f"{symbol}:CLOSE_FILL",
                    f"{symbol}: broker flat doğrulandı fakat gerçek close fill "
                    "okunamadı; yerel çıkış kaydedilmedi, reconciler bekleniyor",
                )
                bot.consecutive_errors += 1
                return False

            qty = filled_qty
            current_price = fill_price
            pnl_usd = (fill_price - float(entry)) * filled_qty

            # Final bacak deftere girmeden once yalniz onceki strategy partial'lari
            # topla; final PnL ayrica eklenir ve episode sonucu tek kez uretilir.
            episode_entry_ts = str(
                pos.get("entry_time_utc") or entry_time or ""
            )
            previous_partial_pnl = self._prior_episode_pnl_safe(
                symbol, episode_entry_ts
            )
            episode_pnl = pnl_usd + previous_partial_pnl
            fill_order = close_order
            if exit_order_id:
                try:
                    fill_order = bot.client.get_order_by_id(exit_order_id)
                except Exception:
                    pass
            execution_id, fill_order_id, fill_client_id = self._fill_ids(fill_order)
            fill_provenance = str(
                pos.get("provenance")
                or ("bear_etf" if pos.get("bear_brain") else "strategy")
            )
            self._record_fill_safe(
                symbol,
                side="SELL",
                qty=filled_qty,
                price=fill_price,
                pnl_usd=pnl_usd,
                provenance=fill_provenance,
                execution_id=execution_id,
                order_id=exit_order_id or fill_order_id,
                client_order_id=fill_client_id,
                episode_id=pos.get("episode_id") or f"{symbol}|{entry_time}",
            )

            # Marker yalnız broker'dan flat + gerçek fill kanıtı alındıktan sonra temizlenir.
            pos["close_in_progress"] = False

            # PDT kaydı (aynı gün alınıp satıldıysa)
            if hasattr(bot, 'pdt_tracker') and entry_time:
                if bot.pdt_tracker.is_same_day_position(symbol, entry_time):
                    bot.pdt_tracker.record_day_trade(
                        symbol, entry_time, datetime.now().isoformat()
                    )

            # Cooldown ,  swing trade için daha uzun (varsayılan 5dk)
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
                "qty": qty, "pnl": pnl_usd, "reason": reason,
                "entry_time": entry_time, "exit_order_id": exit_order_id or None,
                "time": datetime.now().isoformat(),
            })
            # Pozisyon kaldırma + gerçekleşmiş exit satırı aynı metadata yazımında
            # kalıcılaşsın; restart aralığında reconciler phantom üretmesin.
            bot._save_position_metadata()

            # Kayıp/kazanç serisi ,  tek kaynak: gerçekleşen PnL işareti
            # (v4.12.1, core/streak.py; kârlı stop-out artık zarar SAYILMAZ).
            # Bear/ters-ETF hedge kapanışları seriyi etkilemez ,  hedge zararı
            # long giriş hunisini kilitlemesin (BEAR_* eski davranışla uyumlu).
            if not pos.get("bear_brain"):
                update_loss_streak(bot, symbol, episode_pnl)
            # WashSale kaydı ,  gerçekleşen zarar, çıkış etiketinden bağımsız
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
                    price=float(current_price), pnl=episode_pnl, reason=reason,
                    sector=sector, episode_id=str(pos.get("episode_id") or ""),
                    pnl_scope="episode", provenance=fill_provenance,
                )

            # Ajan öz-değerlendirme feedback loop
            if hasattr(bot, 'agent_perf'):
                outcome = "WIN" if episode_pnl > 0 else "LOSS" if episode_pnl < 0 else "NEUTRAL"
                try:
                    bot.agent_perf.record_outcome(symbol, outcome, episode_pnl)
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
                logger.error(f"PDT: {symbol} satış reddedildi ,  pozisyon overnight tutulacak")
            else:
                logger.error(f"SELL hatasi {symbol}: {e}")
            bot.consecutive_errors += 1
            return False
