"""
Kripto Bot Backtester — 1 Aylık Geriye Dönük Test
crypto_bot.py'deki aynı analiz mantığını kullanarak geçmiş verilerde simülasyon yapar.

Kullanım:
    python backtesting/crypto_backtester.py
    python backtesting/crypto_backtester.py --capital 500 --days 30
"""
import os
import sys
import json
import argparse
from datetime import datetime, timedelta
from typing import Dict, List

# Proje kök dizinini ekle
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import yfinance as yf
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import BollingerBands, AverageTrueRange

from utils.logger import logger


# crypto_bot.py'den aynı config
BACKTEST_CONFIG = {
    "stop_loss_pct": 0.025,             # %2.5 MINIMUM (ATR adaptif alt sinir)
    "stop_loss_max_pct": 0.04,           # %4 MAKSIMUM (ATR adaptif ust sinir)
    "atr_stop_multiplier": 1.5,          # ATR carpani: stop = 1.5 * ATR%
    "take_profit_pct": 0.050,           # %5.0 (2:1 R:R korunur)
    "trailing_stop_pct": 0.020,         # %2.0
    "partial_profit_pct": 0.030,        # %3.0
    "rsi_oversold": 30,
    "rsi_overbought": 72,
    "bb_proximity_pct": 0.012,
    "min_volume_ratio": 1.3,
    "commission_pct": 0.0025,
    "max_position_pct": 0.45,
    "max_open_positions": 2,
    "cash_reserve_pct": 0.10,
    "min_trade_interval_bars": 20,      # 20 bar (kalite > miktar)
    "min_confidence": 60,
    "micro_account_threshold": 600,     # $600 altinda 1 pozisyon + min %55 guven

    # === TREND FİLTRESİ ===
    "ema200_trend_gate": True,

    # === COIN FILTRELEME ===
    "coin_filter_enabled": True,
    "coin_max_consecutive_losses": 3,

    # === R:R GATE ===
    "rr_gate_enabled": True,
    "min_rr_ratio": 2.0,

    # === BREAK-EVEN STOP ===
    "breakeven_enabled": True,
    "breakeven_trigger_pct": 0.020,
    "breakeven_offset_pct": 0.002,

    # === VOLATILITE FILTRESI ===
    "volatility_filter_enabled": True,
    "max_atr_pct": 0.06,

    # === SUPPORT/RESISTANCE ===
    "sr_enabled": True,
    "sr_lookback_bars": 50,
    "sr_proximity_pct": 0.015,
}

# Test edilecek coinler (yfinance formatı) — ADA/DOT/AVAX/LTC çıkarıldı
CRYPTO_SYMBOLS = {
    "BTC-USD": "BTC/USD",
    "ETH-USD": "ETH/USD",
    "SOL-USD": "SOL/USD",
    "XRP-USD": "XRP/USD",
    "DOGE-USD": "DOGE/USD",
    "LINK-USD": "LINK/USD",
}


class CryptoBacktester:
    """Kripto bot için backtest motoru."""

    def __init__(self, initial_capital: float = 1000.0):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.positions = {}  # {symbol: {entry_price, qty, highest_price, partial_sold}}
        self.trades = []
        self.equity_curve = []
        self.daily_equity = {}
        self.total_fees = 0.0
        self.last_trade_bar = {}
        self.signal_log = []
        logger.info(f"CryptoBacktester baslatildi - Sermaye: ${initial_capital:,.2f}")

    def _fetch_data(self, yf_symbol: str, days: int = 30) -> pd.DataFrame:
        """yfinance'dan saatlik veri çeker."""
        try:
            df = yf.download(yf_symbol, period=f"{days}d", interval="1h", progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.columns = [c.lower() for c in df.columns]
            return df
        except Exception as e:
            logger.error(f"{yf_symbol} veri cekilemedi: {e}")
            return pd.DataFrame()

    def _analyze(self, df: pd.DataFrame) -> Dict:
        """crypto_bot.py'deki analyze() fonksiyonunun kopyası."""
        if len(df) < 30:
            return {"signal": "HOLD", "confidence": 0, "reason": "Yetersiz veri"}

        close = df["close"]
        volume = df["volume"] if "volume" in df.columns else None

        # Göstergeler
        rsi = RSIIndicator(close, window=14).rsi().iloc[-1]
        ema_9 = EMAIndicator(close, window=9).ema_indicator().iloc[-1]
        ema_21 = EMAIndicator(close, window=21).ema_indicator().iloc[-1]

        macd = MACD(close)
        macd_hist = macd.macd_diff().iloc[-1]
        prev_macd_hist = macd.macd_diff().iloc[-2]

        bb = BollingerBands(close, window=20, window_dev=2)
        bb_lower = bb.bollinger_lband().iloc[-1]
        bb_upper = bb.bollinger_hband().iloc[-1]

        atr = AverageTrueRange(
            df["high"], df["low"], df["close"], window=14
        ).average_true_range().iloc[-1]

        current_price = close.iloc[-1]
        reasons = []

        # Trend
        ema_50 = EMAIndicator(close, window=min(50, len(close) - 1)).ema_indicator().iloc[-1]
        # EMA200: yeterli veri varsa hesapla
        ema_200 = None
        if len(close) >= 200:
            ema_200 = EMAIndicator(close, window=200).ema_indicator().iloc[-1]
        elif len(close) >= 100:
            ema_200 = EMAIndicator(close, window=len(close)-1).ema_indicator().iloc[-1]

        if current_price > ema_50 and ema_9 > ema_21:
            trend = "UPTREND"
        elif current_price < ema_50 and ema_9 < ema_21:
            trend = "DOWNTREND"
        else:
            trend = "SIDEWAYS"

        above_ema200 = True
        if ema_200 is not None:
            above_ema200 = current_price > ema_200

        # Volume
        volume_ok = True
        volume_ratio = 1.0
        if volume is not None and len(volume) > 20:
            avg_volume = volume.rolling(20).mean().iloc[-1]
            current_volume = volume.iloc[-1]
            if avg_volume > 0:
                volume_ratio = current_volume / avg_volume
                volume_ok = volume_ratio >= BACKTEST_CONFIG["min_volume_ratio"]

        # Momentum
        price_change_5 = (close.iloc[-1] - close.iloc[-5]) / close.iloc[-5] * 100
        price_change_1 = (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100
        momentum_up = price_change_5 > 0 and price_change_1 > 0

        # BUY skoru
        buy_score = 0
        if rsi < BACKTEST_CONFIG["rsi_oversold"]:
            buy_score += 25
            reasons.append(f"RSI={rsi:.0f}")
        if ema_9 > ema_21:
            buy_score += 15
            reasons.append("EMA+")
        if macd_hist > 0 and prev_macd_hist <= 0:
            buy_score += 20
            reasons.append("MACD+")
        if current_price < bb_lower * (1 + BACKTEST_CONFIG["bb_proximity_pct"]):
            buy_score += 20
            reasons.append("BB_dip")
        if trend == "UPTREND":
            buy_score += 10
            reasons.append("Trend+")
        elif trend == "DOWNTREND":
            buy_score -= 15
            reasons.append("Trend-")
        if volume_ok and volume_ratio > 1.5:
            buy_score += 10
            reasons.append(f"Vol:{volume_ratio:.1f}x")
        elif not volume_ok:
            buy_score -= 10
        if momentum_up:
            buy_score += 5
            reasons.append("Mom+")

        # Support/Resistance
        try:
            if BACKTEST_CONFIG.get("sr_enabled", True):
                sr_lb = min(BACKTEST_CONFIG.get("sr_lookback_bars", 50), len(df))
                sr_prox = BACKTEST_CONFIG.get("sr_proximity_pct", 0.015)
                recent_sr = df.tail(sr_lb)
                sw_low = recent_sr["low"].min()
                sw_high = recent_sr["high"].max()
                if sw_low > 0:
                    dist_sup = (current_price - sw_low) / current_price
                    if dist_sup < sr_prox:
                        buy_score += 15
                        reasons.append("SR_support")
                if sw_high > 0:
                    dist_res = (sw_high - current_price) / current_price
                    if dist_res < sr_prox:
                        buy_score -= 10
                        sell_score += 10
                        reasons.append("SR_resist")
        except Exception:
            pass

        # SELL skoru
        sell_score = 0
        if rsi > BACKTEST_CONFIG["rsi_overbought"]:
            sell_score += 25
            reasons.append(f"RSI={rsi:.0f}")
        if ema_9 < ema_21:
            sell_score += 15
        if macd_hist < 0 and prev_macd_hist >= 0:
            sell_score += 20
            reasons.append("MACD-")
        if current_price > bb_upper:
            sell_score += 20
            reasons.append("BB_top")
        if trend == "DOWNTREND":
            sell_score += 10

        # Karar
        if buy_score >= 55:
            signal = "BUY"
            confidence = min(buy_score, 100)
        elif sell_score >= 50:
            signal = "SELL"
            confidence = min(sell_score, 100)
        else:
            signal = "HOLD"
            confidence = 0

        return {
            "signal": signal,
            "confidence": confidence,
            "reasons": reasons,
            "price": float(current_price),
            "rsi": float(rsi),
            "atr": float(atr),
            "trend": trend,
            "volume_ratio": float(volume_ratio),
            "above_ema200": above_ema200,
        }

    def _get_portfolio_value(self, current_prices: Dict[str, float]) -> float:
        """Toplam portföy değerini hesaplar."""
        value = self.capital
        for sym, pos in self.positions.items():
            price = current_prices.get(sym, pos["entry_price"])
            value += pos["qty"] * price
        return value

    def run(self, days: int = 30) -> Dict:
        """
        Tüm coinler üzerinde 1 aylık backtest çalıştırır.
        Saatlik bar bazında simülasyon.
        """
        logger.info(f"\n{'='*60}")
        logger.info(f"  KRIPTO BACKTEST BASLIYOR")
        logger.info(f"  Sermaye: ${self.initial_capital:,.2f}")
        logger.info(f"  Periyot: {days} gun")
        logger.info(f"  Coinler: {len(CRYPTO_SYMBOLS)} adet")
        logger.info(f"  Max Pozisyon: {BACKTEST_CONFIG['max_open_positions']}")
        logger.info(f"  Stop-Loss: {BACKTEST_CONFIG['stop_loss_pct']:.1%}")
        logger.info(f"  Take-Profit: {BACKTEST_CONFIG['take_profit_pct']:.1%}")
        logger.info(f"{'='*60}\n")

        # Tüm coinlerin verilerini çek
        all_data = {}
        for yf_sym, display_sym in CRYPTO_SYMBOLS.items():
            df = self._fetch_data(yf_sym, days=days + 5)  # ekstra 5 gün (göstergeler için)
            if not df.empty and len(df) >= 50:
                all_data[display_sym] = df
                logger.info(f"  {display_sym}: {len(df)} bar veri alindi")
            else:
                logger.warning(f"  {display_sym}: Yetersiz veri, atlaniyor")

        if not all_data:
            logger.error("Hic veri alinamadi!")
            return {"error": "Veri alinamadi"}

        # Tüm bar zamanlarını birleştir (ortak zaman çizelgesi)
        all_times = set()
        for df in all_data.values():
            all_times.update(df.index.tolist())
        all_times = sorted(all_times)

        logger.info(f"\n  Toplam {len(all_times)} bar simule edilecek...")
        logger.info(f"  Baslangic: {all_times[0]}")
        logger.info(f"  Bitis:     {all_times[-1]}\n")

        # Bar-bar simülasyon
        lookback = 50  # Analiz için minimum bar
        total_signals = 0
        total_buys = 0
        total_sells = 0

        for bar_idx in range(lookback, len(all_times)):
            current_time = all_times[bar_idx]
            current_prices = {}

            # Her coin için mevcut fiyatı güncelle
            for sym, df in all_data.items():
                if current_time in df.index:
                    current_prices[sym] = float(df.loc[current_time, "close"])

            # Portföy değerini kaydet
            portfolio_value = self._get_portfolio_value(current_prices)
            self.equity_curve.append({
                "time": str(current_time),
                "equity": round(portfolio_value, 2),
            })

            # Günlük kayıt
            day_str = str(current_time)[:10]
            self.daily_equity[day_str] = round(portfolio_value, 2)

            # === POZISYON YÖNETIMI (önce satış kontrolleri) ===
            symbols_to_close = []
            for sym, pos in self.positions.items():
                if sym not in current_prices:
                    continue

                price = current_prices[sym]
                entry = pos["entry_price"]
                pnl_pct = (price - entry) / entry

                # Highest price güncelle
                if price > pos.get("highest_price", entry):
                    pos["highest_price"] = price

                highest = pos.get("highest_price", entry)
                trailing_drop = (highest - price) / highest if highest > 0 else 0

                # Break-Even Stop
                if BACKTEST_CONFIG.get("breakeven_enabled", True):
                    be_trigger = BACKTEST_CONFIG.get("breakeven_trigger_pct", 0.015)
                    be_offset = BACKTEST_CONFIG.get("breakeven_offset_pct", 0.001)
                    if pnl_pct >= be_trigger and not pos.get("breakeven_set", False):
                        pos["stop_loss_pct"] = be_offset
                        pos["breakeven_set"] = True

                # Stop-loss (ATR adaptif)
                pos_sl = pos.get("stop_loss_pct", BACKTEST_CONFIG["stop_loss_pct"])
                if pnl_pct <= -pos_sl:
                    symbols_to_close.append((sym, "STOP_LOSS", price, pnl_pct))
                # Take-profit
                elif pnl_pct >= BACKTEST_CONFIG["take_profit_pct"]:
                    symbols_to_close.append((sym, "TAKE_PROFIT", price, pnl_pct))
                # Trailing stop
                elif pnl_pct > 0.01 and trailing_drop >= BACKTEST_CONFIG["trailing_stop_pct"]:
                    symbols_to_close.append((sym, "TRAILING_STOP", price, pnl_pct))
                # Kademeli kâr alma (%1.8'de yarısını sat)
                elif (pnl_pct >= BACKTEST_CONFIG["partial_profit_pct"]
                      and not pos.get("partial_sold", False)):
                    # Yarısını sat
                    half_qty = pos["qty"] / 2
                    half_value = half_qty * price
                    fee = half_value * BACKTEST_CONFIG["commission_pct"]
                    self.capital += half_value - fee
                    self.total_fees += fee
                    pos["qty"] -= half_qty
                    pos["partial_sold"] = True
                    self.trades.append({
                        "action": "PARTIAL_SELL",
                        "symbol": sym,
                        "price": price,
                        "qty": half_qty,
                        "pnl_pct": round(pnl_pct * 100, 2),
                        "reason": "PARTIAL_PROFIT",
                        "time": str(current_time),
                        "fee": round(fee, 4),
                    })

            # Satışları uygula
            for sym, reason, price, pnl_pct in symbols_to_close:
                pos = self.positions[sym]
                sell_value = pos["qty"] * price
                fee = sell_value * BACKTEST_CONFIG["commission_pct"]
                pnl_usd = (price - pos["entry_price"]) * pos["qty"] - fee
                self.capital += sell_value - fee
                self.total_fees += fee
                total_sells += 1

                self.trades.append({
                    "action": "SELL",
                    "symbol": sym,
                    "price": price,
                    "qty": pos["qty"],
                    "entry_price": pos["entry_price"],
                    "pnl_usd": round(pnl_usd, 2),
                    "pnl_pct": round(pnl_pct * 100, 2),
                    "reason": reason,
                    "time": str(current_time),
                    "fee": round(fee, 4),
                })
                del self.positions[sym]

            # === YENİ ALIM SINYALLERI ===
            open_count = len(self.positions)

            # Micro hesap korumasi
            current_equity = self._get_portfolio_value(current_prices)
            max_positions = BACKTEST_CONFIG["max_open_positions"]
            min_conf = BACKTEST_CONFIG.get("min_confidence", 50)
            if current_equity < BACKTEST_CONFIG.get("micro_account_threshold", 600):
                max_positions = 1
                min_conf = 55

            for sym, df in all_data.items():
                if sym in self.positions:
                    continue
                if open_count >= max_positions:
                    break

                # Min trade aralığı
                last_bar = self.last_trade_bar.get(sym, 0)
                if bar_idx - last_bar < BACKTEST_CONFIG["min_trade_interval_bars"]:
                    continue

                # Yeterli veri var mı?
                mask = df.index <= current_time
                available = df[mask]
                if len(available) < 50:
                    continue

                # Analiz
                analysis = self._analyze(available.tail(100))
                total_signals += 1

                if analysis["signal"] == "BUY" and analysis["confidence"] >= min_conf:
                    # EMA200 Trend Gate
                    if BACKTEST_CONFIG.get("ema200_trend_gate", True) and not analysis.get("above_ema200", True):
                        continue

                    # Coin filtreleme
                    if BACKTEST_CONFIG.get("coin_filter_enabled", True):
                        coin_losses = getattr(self, '_coin_consecutive_losses', {}).get(sym, 0)
                        if coin_losses >= BACKTEST_CONFIG.get("coin_max_consecutive_losses", 3):
                            continue

                    # R:R Gate
                    if BACKTEST_CONFIG.get("rr_gate_enabled", True):
                        atr_val = analysis.get("atr", 0)
                        a_price = analysis.get("price", 0)
                        if atr_val > 0 and a_price > 0:
                            atr_pct = atr_val / a_price
                            actual_sl = atr_pct * BACKTEST_CONFIG.get('atr_stop_multiplier', 1.5)
                            actual_sl = max(actual_sl, BACKTEST_CONFIG.get('stop_loss_pct', 0.015))
                            actual_sl = min(actual_sl, BACKTEST_CONFIG.get('stop_loss_max_pct', 0.04))
                            rr_ratio = BACKTEST_CONFIG.get('take_profit_pct', 0.04) / actual_sl if actual_sl > 0 else 0
                            if rr_ratio < BACKTEST_CONFIG.get('min_rr_ratio', 2.0):
                                continue

                    # Multi-Timeframe: backtesterde 4h resample
                    if available is not None and len(available) >= 80:
                        try:
                            df_4h = available.resample('4h').agg({
                                'open': 'first', 'high': 'max',
                                'low': 'min', 'close': 'last',
                                'volume': 'sum'
                            }).dropna()
                            if len(df_4h) >= 20:
                                ema9_4h = EMAIndicator(df_4h['close'], window=9).ema_indicator().iloc[-1]
                                ema21_4h = EMAIndicator(df_4h['close'], window=21).ema_indicator().iloc[-1]
                                if ema9_4h < ema21_4h:
                                    continue  # 4h downtrend
                        except Exception:
                            pass

                    # Volatilite filtresi
                    if BACKTEST_CONFIG.get("volatility_filter_enabled", True):
                        atr_val = analysis.get("atr", 0)
                        a_price = analysis.get("price", 1)
                        if atr_val > 0 and a_price > 0:
                            atr_pct = atr_val / a_price
                            if atr_pct > BACKTEST_CONFIG.get("max_atr_pct", 0.06):
                                continue

                    price = analysis["price"]

                    # Pozisyon boyutu
                    cash_reserve = current_equity * BACKTEST_CONFIG["cash_reserve_pct"]
                    available_cash = max(self.capital - cash_reserve, 0)
                    max_invest = min(
                        available_cash * 0.45,  # %45 kullan
                        current_equity * BACKTEST_CONFIG["max_position_pct"],
                    )

                    if max_invest < 5:
                        continue

                    fee = max_invest * BACKTEST_CONFIG["commission_pct"]
                    invest_after_fee = max_invest - fee
                    qty = invest_after_fee / price

                    if qty * price < 1:
                        continue

                    self.capital -= (qty * price + fee)
                    self.total_fees += fee
                    total_buys += 1

                    # ATR adaptif stop-loss hesapla
                    atr_value = analysis.get("atr", 0)
                    if atr_value > 0 and price > 0:
                        atr_pct = atr_value / price
                        adaptive_sl = atr_pct * BACKTEST_CONFIG['atr_stop_multiplier']
                        adaptive_sl = max(adaptive_sl, BACKTEST_CONFIG['stop_loss_pct'])
                        adaptive_sl = min(adaptive_sl, BACKTEST_CONFIG['stop_loss_max_pct'])
                    else:
                        adaptive_sl = BACKTEST_CONFIG['stop_loss_pct']

                    self.positions[sym] = {
                        "entry_price": price,
                        "qty": qty,
                        "highest_price": price,
                        "partial_sold": False,
                        "entry_time": str(current_time),
                        "stop_loss_pct": adaptive_sl,
                    }
                    self.last_trade_bar[sym] = bar_idx

                    self.trades.append({
                        "action": "BUY",
                        "symbol": sym,
                        "price": price,
                        "qty": round(qty, 8),
                        "invest": round(qty * price, 2),
                        "confidence": analysis["confidence"],
                        "reasons": ", ".join(analysis["reasons"]),
                        "time": str(current_time),
                        "fee": round(fee, 4),
                    })
                    open_count += 1

                    self.signal_log.append({
                        "time": str(current_time),
                        "symbol": sym,
                        "signal": "BUY",
                        "confidence": analysis["confidence"],
                        "rsi": round(analysis["rsi"], 1),
                        "trend": analysis["trend"],
                        "reasons": ", ".join(analysis["reasons"]),
                    })

        # === SON: Kalan pozisyonları kapat ===
        for sym, pos in list(self.positions.items()):
            if sym in current_prices:
                price = current_prices[sym]
                sell_value = pos["qty"] * price
                pnl_pct = (price - pos["entry_price"]) / pos["entry_price"]
                pnl_usd = (price - pos["entry_price"]) * pos["qty"]
                self.capital += sell_value
                total_sells += 1

                self.trades.append({
                    "action": "SELL",
                    "symbol": sym,
                    "price": price,
                    "qty": pos["qty"],
                    "pnl_usd": round(pnl_usd, 2),
                    "pnl_pct": round(pnl_pct * 100, 2),
                    "reason": "BACKTEST_END",
                    "time": str(all_times[-1]),
                })

        self.positions.clear()

        # === SONUÇLARI HESAPLA ===
        return self._calculate_results(total_signals, total_buys, total_sells)

    def _calculate_results(self, total_signals, total_buys, total_sells) -> Dict:
        """Backtest sonuçlarını hesaplar ve yazdırır."""
        final_equity = self.capital
        total_return = final_equity - self.initial_capital
        total_return_pct = (total_return / self.initial_capital) * 100

        # İşlem istatistikleri
        sell_trades = [t for t in self.trades if t["action"] == "SELL" and "pnl_usd" in t]
        wins = [t for t in sell_trades if t["pnl_usd"] > 0]
        losses = [t for t in sell_trades if t["pnl_usd"] <= 0]

        win_rate = (len(wins) / len(sell_trades) * 100) if sell_trades else 0
        avg_win = np.mean([t["pnl_usd"] for t in wins]) if wins else 0
        avg_loss = np.mean([t["pnl_usd"] for t in losses]) if losses else 0
        avg_win_pct = np.mean([t["pnl_pct"] for t in wins]) if wins else 0
        avg_loss_pct = np.mean([t["pnl_pct"] for t in losses]) if losses else 0

        # Profit Factor
        total_wins_amt = sum(t["pnl_usd"] for t in wins)
        total_losses_amt = abs(sum(t["pnl_usd"] for t in losses))
        profit_factor = total_wins_amt / total_losses_amt if total_losses_amt > 0 else float("inf")

        # Max Drawdown
        if self.equity_curve:
            eq = pd.Series([e["equity"] for e in self.equity_curve])
            peak = eq.cummax()
            drawdown = (eq - peak) / peak * 100
            max_drawdown = float(drawdown.min())
            max_dd_idx = drawdown.idxmin()
        else:
            max_drawdown = 0
            max_dd_idx = 0

        # Sharpe Ratio
        if len(self.equity_curve) > 1:
            returns = pd.Series([e["equity"] for e in self.equity_curve]).pct_change().dropna()
            sharpe = (returns.mean() / returns.std() * np.sqrt(252 * 24)) if returns.std() > 0 else 0
        else:
            sharpe = 0

        # Satış sebep dağılımı
        sell_reasons = {}
        for t in sell_trades:
            reason = t.get("reason", "UNKNOWN")
            sell_reasons[reason] = sell_reasons.get(reason, 0) + 1

        # En karlı ve zararlı coinler
        coin_pnl = {}
        for t in sell_trades:
            sym = t["symbol"]
            coin_pnl[sym] = coin_pnl.get(sym, 0) + t["pnl_usd"]
        best_coin = max(coin_pnl, key=coin_pnl.get) if coin_pnl else "N/A"
        worst_coin = min(coin_pnl, key=coin_pnl.get) if coin_pnl else "N/A"

        # Günlük ortalama kâr
        if self.daily_equity:
            days_list = sorted(self.daily_equity.keys())
            if len(days_list) > 1:
                daily_returns = []
                for i in range(1, len(days_list)):
                    prev = self.daily_equity[days_list[i-1]]
                    curr = self.daily_equity[days_list[i]]
                    daily_returns.append((curr - prev) / prev * 100)
                avg_daily_return = np.mean(daily_returns)
            else:
                avg_daily_return = 0
        else:
            avg_daily_return = 0

        results = {
            "initial_capital": self.initial_capital,
            "final_equity": round(final_equity, 2),
            "total_return_usd": round(total_return, 2),
            "total_return_pct": round(total_return_pct, 2),
            "total_signals_checked": total_signals,
            "total_buys": total_buys,
            "total_sells": total_sells,
            "total_trades": len(sell_trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 1),
            "avg_win_usd": round(avg_win, 2),
            "avg_loss_usd": round(avg_loss, 2),
            "avg_win_pct": round(avg_win_pct, 2),
            "avg_loss_pct": round(avg_loss_pct, 2),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown_pct": round(max_drawdown, 2),
            "sharpe_ratio": round(float(sharpe), 2),
            "total_fees": round(self.total_fees, 2),
            "avg_daily_return_pct": round(avg_daily_return, 3),
            "sell_reasons": sell_reasons,
            "best_coin": best_coin,
            "worst_coin": worst_coin,
            "coin_pnl": {k: round(v, 2) for k, v in sorted(coin_pnl.items(), key=lambda x: x[1], reverse=True)},
        }

        self._print_results(results)
        self._save_results(results)
        return results

    def _print_results(self, r: Dict):
        """Sonuçları konsola yazdırır."""
        logger.info("\n" + "=" * 60)
        logger.info("  KRIPTO BACKTEST SONUCLARI")
        logger.info("=" * 60)
        logger.info(f"  Baslangic Sermaye:  ${r['initial_capital']:,.2f}")
        logger.info(f"  Final Sermaye:      ${r['final_equity']:,.2f}")

        marker = "+" if r["total_return_usd"] >= 0 else ""
        logger.info(f"  Toplam Getiri:      {marker}${r['total_return_usd']:,.2f} ({marker}{r['total_return_pct']:.1f}%)")

        logger.info(f"  " + "-" * 40)
        logger.info(f"  Taranan Sinyal:     {r['total_signals_checked']}")
        logger.info(f"  Toplam Alis:        {r['total_buys']}")
        logger.info(f"  Toplam Satis:       {r['total_sells']}")
        logger.info(f"  Kapanan Islem:      {r['total_trades']}")
        logger.info(f"  Kazanc / Kayip:     {r['wins']}W / {r['losses']}L")
        logger.info(f"  Win Rate:           {r['win_rate']:.1f}%")

        logger.info(f"  " + "-" * 40)
        logger.info(f"  Ort. Kazanc:        ${r['avg_win_usd']:,.2f} ({r['avg_win_pct']:+.2f}%)")
        logger.info(f"  Ort. Kayip:         ${r['avg_loss_usd']:,.2f} ({r['avg_loss_pct']:+.2f}%)")
        logger.info(f"  Profit Factor:      {r['profit_factor']:.2f}")
        logger.info(f"  Max Drawdown:       {r['max_drawdown_pct']:.2f}%")
        logger.info(f"  Sharpe Ratio:       {r['sharpe_ratio']:.2f}")
        logger.info(f"  Toplam Komisyon:    ${r['total_fees']:,.2f}")
        logger.info(f"  Ort. Gunluk Getiri: {r['avg_daily_return_pct']:+.3f}%")

        logger.info(f"  " + "-" * 40)
        logger.info(f"  SATIS SEBEPLERI:")
        for reason, count in r.get("sell_reasons", {}).items():
            logger.info(f"    {reason}: {count}")

        logger.info(f"  " + "-" * 40)
        logger.info(f"  COIN BAZLI P&L:")
        for coin, pnl in r.get("coin_pnl", {}).items():
            m = "+" if pnl >= 0 else ""
            logger.info(f"    {coin}: {m}${pnl:.2f}")
        logger.info(f"  En Iyi Coin:  {r['best_coin']}")
        logger.info(f"  En Kotu Coin: {r['worst_coin']}")

        logger.info("=" * 60)

        # BOT SAĞLIĞI DEĞERLENDİRMESİ
        logger.info("\n  BOT SAGLIGI DEGERLENDIRMESI:")
        issues = []
        good = []

        if r["win_rate"] >= 50:
            good.append(f"  ✅ Win Rate iyi: {r['win_rate']:.1f}%")
        else:
            issues.append(f"  ⚠️ Win Rate dusuk: {r['win_rate']:.1f}% (hedef: >50%)")

        if r["profit_factor"] >= 1.5:
            good.append(f"  ✅ Profit Factor iyi: {r['profit_factor']:.2f}")
        elif r["profit_factor"] >= 1.0:
            good.append(f"  ⚡ Profit Factor kabul edilebilir: {r['profit_factor']:.2f} (hedef: >1.5)")
        else:
            issues.append(f"  ⚠️ Profit Factor < 1 (zararda): {r['profit_factor']:.2f}")

        if r["max_drawdown_pct"] > -5:
            good.append(f"  ✅ Max Drawdown kontrol altinda: {r['max_drawdown_pct']:.1f}%")
        else:
            issues.append(f"  ⚠️ Max Drawdown yuksek: {r['max_drawdown_pct']:.1f}%")

        if r["total_trades"] >= 10:
            good.append(f"  ✅ Yeterli islem: {r['total_trades']}")
        else:
            issues.append(f"  ⚠️ Az islem: {r['total_trades']} (istatistik guvenilir olmayabilir)")

        if r["total_return_pct"] > 0:
            good.append(f"  ✅ Karli: {r['total_return_pct']:+.1f}%")
        else:
            issues.append(f"  ⚠️ Zararda: {r['total_return_pct']:+.1f}%")

        for g in good:
            logger.info(g)
        for i in issues:
            logger.info(i)

        if not issues:
            logger.info("\n  🟢 BOT SAGLIGI: MÜKEMMEL — Canliya geçmeye hazir!")
        elif len(issues) <= 2:
            logger.info("\n  🟡 BOT SAGLIGI: KABUL EDILEBILIR — Parametreler ayarlanabilir")
        else:
            logger.info("\n  🔴 BOT SAGLIGI: SORUNLU — Strateji gözden gecirilmeli!")

        logger.info("")

    def _save_results(self, results: Dict):
        """Sonuçları dosyaya kaydet."""
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
        os.makedirs(output_dir, exist_ok=True)

        # Genel sonuçlar
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        results_file = os.path.join(output_dir, f"backtest_{timestamp}.json")

        # equity_curve ve trades dahil değil (büyük olabilir)
        save_data = {k: v for k, v in results.items() if k not in ["equity_curve"]}
        save_data["trades"] = self.trades
        save_data["timestamp"] = timestamp

        with open(results_file, "w") as f:
            json.dump(save_data, f, indent=2, default=str)
        logger.info(f"  Sonuclar kaydedildi: {results_file}")


# ============================================================
# CLI
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kripto Bot Backtester")
    parser.add_argument("--capital", type=float, default=1000,
                        help="Baslangic sermayesi (default: $1000)")
    parser.add_argument("--days", type=int, default=30,
                        help="Test periyodu gun (default: 30)")

    args = parser.parse_args()

    bt = CryptoBacktester(initial_capital=args.capital)
    results = bt.run(days=args.days)
