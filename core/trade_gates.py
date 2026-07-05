"""
Trade Gates — Hisse Senedi Alım Filtre Sistemi

Gates:
1. Market Hours Gate (piyasa açık mı?)
2. EMA200 Trend Gate
3. Earnings Gate (earnings yakınsa alım yapma)
4. Kayıp Serisi Koruyucu
5. Coin/Hisse Filtresi (ardışık zarar)
6. R:R Gate (Risk/Ödül oranı)
7. Multi-Timeframe Onay
8. Volatilite Filtresi
9. PDT Gate (day trade limiti)
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, Tuple

from ta.trend import EMAIndicator

from utils.logger import logger


def plan_exit_pcts(atr: float, price: float, config: Dict) -> Tuple[float, float]:
    """Bir alımın planlanan (stop_loss_pct, take_profit_pct) çiftini hesaplar.

    TEK DOĞRULUK KAYNAĞI — executor.execute_buy ve R:R gate aynı planı buradan
    okur; ikisi ayrışırsa gate gerçekte verilmeyecek bir emri değerlendirmiş olur.

    SL: ATR × atr_stop_multiplier, [stop_loss_pct, stop_loss_max_pct] aralığına
        kırpılır (ATR yoksa taban).
    TP (v4.8 dinamik): max(take_profit_pct, SL × min_rr_ratio), take_profit_max_pct
        tavanlı — SL genişledikçe hedef orantılı uzar, R:R her işlemde korunur.
    """
    if atr and atr > 0 and price and price > 0:
        atr_pct = atr / price
        sl = atr_pct * config.get("atr_stop_multiplier", 1.8)
        sl = max(sl, config.get("stop_loss_pct", 0.04))
        sl = min(sl, config.get("stop_loss_max_pct", 0.06))
    else:
        sl = config.get("stop_loss_pct", 0.04)

    tp_floor = config.get("take_profit_pct", 0.08)
    tp_cap = config.get("take_profit_max_pct", 0.12)
    min_rr = config.get("min_rr_ratio", 2.0)
    tp = min(max(tp_floor, sl * min_rr), max(tp_cap, tp_floor))
    return sl, tp


class TradeGates:
    """Alım öncesi tüm güvenlik filtrelerini kontrol eder."""

    def __init__(self, bot):
        self.bot = bot

    def check_all_gates(self, symbol: str, analysis: Dict, config: Dict) -> Tuple[bool, str]:
        """
        Tüm gate'leri kontrol eder.
        Returns: (passed: bool, block_reason: str)
        """
        # NOT: Erken "signal != BUY → gates geç" çıkışı KALDIRILDI.
        # Bu fonksiyon zaten yalnız koordinatör BUY kararında çağrılır; teknik
        # sinyal HOLD/SHORT iken erken çıkış TÜM kapıları (earnings, EMA200,
        # volatilite, kayıp serisi...) atlatıyordu — sentiment kaynaklı BUY'lar
        # hiçbir filtreden geçmeden emir olabiliyordu.

        # 1. Market Hours Gate (YENİ)
        if hasattr(self.bot, 'market_hours'):
            status = self.bot.market_hours.get_market_status()
            if not status["is_trading_allowed"]:
                confidence = analysis.get("confidence", 0)
                if not self.bot.market_hours.should_allow_extended_hours(confidence):
                    logger.debug(f"  {symbol} MARKET GATE: {status['reason']}")
                    return False, "MARKET_CLOSED"

        # 2. EMA200 Trend Gate
        if config.get("ema200_trend_gate", True):
            if not analysis.get("above_ema200", True):
                logger.debug(f"  {symbol} EMA200 GATE: Fiyat EMA200 altinda, BUY engellendi")
                return False, "EMA200"

        # 3. Earnings Gate (YENİ)
        if config.get("earnings_gate_enabled", True):
            if hasattr(self.bot, 'earnings_calendar'):
                should_avoid, reason = self.bot.earnings_calendar.should_avoid_trading(symbol)
                if should_avoid:
                    logger.info(f"  {symbol} EARNINGS GATE: {reason}")
                    return False, "EARNINGS"

        # 4. Kayıp Serisi Koruyucu
        blocked, reason = self._check_loss_streak(symbol, analysis, config)
        if blocked:
            return False, reason

        # 5. Hisse Filtresi (ardışık zarar)
        if config.get("coin_filter_enabled", True):
            losses = getattr(self.bot, '_symbol_consecutive_losses', {}).get(symbol, 0)
            max_losses = config.get("coin_max_consecutive_losses", 3)
            if losses >= max_losses:
                logger.info(f"  {symbol} HİSSE FİLTRE: {losses} ardisik zarar, devre disi")
                return False, "STOCK_FILTER"

        # 6. R:R Gate
        if config.get("rr_gate_enabled", True):
            blocked, reason = self._check_rr_gate(symbol, analysis, config)
            if blocked:
                return False, reason

        # 7. Multi-Timeframe Onay
        if config.get("multi_tf_enabled", True):
            blocked, reason = self._check_mtf(symbol, config)
            if blocked:
                return False, reason

        # 8. Volatilite Filtresi
        if config.get("volatility_filter_enabled", True):
            atr_val = analysis.get("atr", 0)
            cur_price = analysis.get("price", 1)
            if atr_val > 0 and cur_price > 0:
                atr_pct = atr_val / cur_price
                max_atr = config.get("max_atr_pct", 0.05)
                if atr_pct > max_atr:
                    logger.debug(f"  {symbol} VOL GATE: ATR={atr_pct:.1%} > {max_atr:.0%}")
                    return False, "VOLATILITY"

        # 9. PDT Gate (YENİ)
        if hasattr(self.bot, 'pdt_tracker'):
            can_dt, reason = self.bot.pdt_tracker.can_day_trade()
            if not can_dt:
                # Day trade yapılamaz ama swing trade olabilir (overnight tutulacak)
                logger.info(f"  {symbol} PDT GATE: {reason} — swing trade olarak devam")
                # PDT gate blok ETMİYOR, sadece uyarı veriyor
                # Çünkü alım yapabilir, satışı ertesi gün yapar

        return True, ""

    def _check_loss_streak(self, symbol: str, analysis: Dict, config: Dict) -> Tuple[bool, str]:
        """Kayıp serisi kontrolü."""
        bot = self.bot
        if not config.get("loss_streak_enabled", True):
            return False, ""

        loss_count = getattr(bot, '_consecutive_losses', 0)

        if loss_count >= config.get("loss_streak_halt", 4):
            halt_until = getattr(bot, '_loss_halt_until', None)
            if halt_until is None or datetime.now() < halt_until:
                if halt_until is None:
                    halt_hours = config.get("loss_streak_halt_hours", 24)
                    bot._loss_halt_until = datetime.now() + timedelta(hours=halt_hours)
                    logger.warning(f"  ⚠️ {loss_count} ardisik zarar! {halt_hours} saat alim yasagi")
                return True, "LOSS_STREAK_HALT"
            else:
                bot._consecutive_losses = 0
                bot._loss_halt_until = None

        elif loss_count >= config.get("loss_streak_warn", 2):
            elevated_conf = config.get("loss_streak_elevated_conf", 70)
            if analysis["confidence"] < elevated_conf:
                logger.info(
                    f"  {symbol} KAYIP KORUYUCU: {loss_count} ardisik zarar, "
                    f"guven {analysis['confidence']}% < {elevated_conf}%"
                )
                return True, "LOSS_STREAK_WARN"

        return False, ""

    def _check_rr_gate(self, symbol: str, analysis: Dict, config: Dict) -> Tuple[bool, str]:
        """Risk/Ödül oranı kontrolü — executor'ın GERÇEK planladığı SL/TP ile.

        v4.8 öncesi bug: TP sabit %8, SL tabanı %4 iken oran yapısal olarak en çok
        2.0'dı → gate fiilen "ATR ≤ %2.22" filtresiydi; paper'da (TP %6 / SL taban
        %5, maks oran 1.2) HER alımı blokluyordu. Artık executor dinamik TP kullanır
        (SL × min_rr, take_profit_max_pct tavanlı) ve bu gate aynı planı hesaplayıp
        yalnız tavanın orana izin vermediği uç durumda bloklar. Volatilite koruması
        bu gate'in işi değil — VOL GATE (max_atr_pct) yapar.
        """
        planned_sl, planned_tp = plan_exit_pcts(
            analysis.get("atr", 0), analysis.get("price", 0), config
        )
        min_rr = config.get("min_rr_ratio", 2.0)
        if planned_sl > 0:
            rr_ratio = planned_tp / planned_sl
            if rr_ratio + 1e-9 < min_rr:
                logger.debug(f"  {symbol} R:R GATE: {rr_ratio:.2f}:1 < {min_rr}:1")
                return True, "RR_GATE"

        return False, ""

    def _check_mtf(self, symbol: str, config: Dict) -> Tuple[bool, str]:
        """Multi-Timeframe onay — 4h trend kontrolü."""
        try:
            df_1h = self.bot.get_stock_bars(symbol, days=14)
            if not df_1h.empty and len(df_1h) >= 50:
                df_4h = df_1h.resample('4h').agg({
                    'open': 'first', 'high': 'max',
                    'low': 'min', 'close': 'last',
                    'volume': 'sum'
                }).dropna()
                if len(df_4h) >= 20:
                    ema9_4h = EMAIndicator(df_4h['close'], window=9).ema_indicator().iloc[-1]
                    ema21_4h = EMAIndicator(df_4h['close'], window=21).ema_indicator().iloc[-1]
                    if ema9_4h < ema21_4h:
                        logger.debug(f"  {symbol} MTF GATE: 4h trend düşüşte")
                        return True, "MTF"
        except Exception:
            pass
        return False, ""
