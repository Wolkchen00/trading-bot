"""
Hisse Senedi Backtester — Geriye Dönük Strateji Testi

stock_bot.py'deki swing trading stratejisini geçmiş hisse verilerinde simüle eder.
yfinance üzerinden günlük bar verileri çekerek çalışır.

Kullanım:
    python backtesting/stock_backtester.py
    python backtesting/stock_backtester.py --capital 500 --days 90
    python backtesting/stock_backtester.py --symbols AAPL,MSFT,NVDA --days 60
"""
import os
import sys
import json
import argparse
from datetime import datetime, timedelta
from typing import Dict, List
from collections import defaultdict

# Proje kök dizinini ekle
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np

try:
    import yfinance as yf
except ImportError:
    print("HATA: yfinance yüklü değil → pip install yfinance")
    sys.exit(1)

from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import BollingerBands, AverageTrueRange

from utils.logger import logger
from config import STOCK_CONFIG, SHORT_CONFIG, SECTOR_MAP, STOCK_SEARCH_TERMS


# ============================================================
# BACKTEST KONFİGÜRASYONU (STOCK_CONFIG'den türetildi)
# ============================================================
BACKTEST_CONFIG = {
    # === STOP / TAKE PROFIT ===
    "stop_loss_pct": STOCK_CONFIG["stop_loss_pct"],
    "stop_loss_max_pct": STOCK_CONFIG["stop_loss_max_pct"],
    "atr_stop_multiplier": STOCK_CONFIG["atr_stop_multiplier"],
    "take_profit_pct": STOCK_CONFIG["take_profit_pct"],
    "trailing_stop_pct": STOCK_CONFIG["trailing_stop_pct"],
    "partial_profit_pct": STOCK_CONFIG["partial_profit_pct"],

    # === SINYAL EŞİKLERİ ===
    "rsi_oversold": STOCK_CONFIG["rsi_oversold"],
    "rsi_overbought": STOCK_CONFIG["rsi_overbought"],
    "bb_proximity_pct": STOCK_CONFIG.get("bb_proximity_pct", 0.01),
    "min_volume_ratio": STOCK_CONFIG.get("min_volume_ratio", 1.3),
    "min_confidence": 25,  # Backtester: dusuk esik (canli bot haber+agent ile yukseltir)

    # === POZİSYON ===
    "commission_pct": 0.0,  # Hisse: $0
    "max_position_pct": STOCK_CONFIG["max_position_pct"],
    "max_open_positions": STOCK_CONFIG["max_open_positions"],
    "cash_reserve_pct": STOCK_CONFIG["cash_reserve_pct"],
    "min_trade_interval_bars": 3,  # 3 gunluk bar

    # === FİLTRELER ===
    "ema200_trend_gate": True,
    "coin_filter_enabled": True,
    "coin_max_consecutive_losses": 3,
    "rr_gate_enabled": True,
    "min_rr_ratio": 2.0,
    "volatility_filter_enabled": True,
    "max_atr_pct": 0.05,

    # === BREAK-EVEN ===
    "breakeven_enabled": True,
    "breakeven_trigger_pct": STOCK_CONFIG.get("breakeven_trigger_pct", 0.025),
    "breakeven_offset_pct": STOCK_CONFIG.get("breakeven_offset_pct", 0.003),

    # === SEKTÖR ===
    "max_positions_per_sector": STOCK_CONFIG.get("max_positions_per_sector", 2),
}

# Test edilecek hisseler
DEFAULT_SYMBOLS = list(STOCK_SEARCH_TERMS.keys())[:20]


class StockBacktester:
    """Hisse senedi swing trading backtest motoru."""

    def __init__(self, initial_capital: float = 500.0, symbols: List[str] = None):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.symbols = symbols or DEFAULT_SYMBOLS
        self.positions = {}
        self.short_positions = {}  # SHORT pozisyonlar
        self.trades = []
        self.equity_curve = []
        self.daily_equity = {}
        self.total_fees = 0.0
        self.last_trade_bar = {}
        self._symbol_consecutive_losses = {}
        logger.info(f"StockBacktester baslatildi | Sermaye: ${initial_capital:,.2f} | {len(self.symbols)} hisse")

    def _fetch_data(self, symbol: str, days: int = 90) -> pd.DataFrame:
        """yfinance'dan günlük veri çeker."""
        try:
            end = datetime.now()
            start = end - timedelta(days=days + 250)  # Ekstra 250 gun (EMA200 icin)
            df = yf.download(symbol, start=start, end=end, interval="1d", progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.columns = [c.lower() for c in df.columns]
            return df
        except Exception as e:
            logger.error(f"{symbol} veri çekilemedi: {e}")
            return pd.DataFrame()

    def _analyze(self, df: pd.DataFrame) -> Dict:
        """
        Tam teknik analiz — analyzer.py ile ayni mantik.
        Ichimoku, ADX, OBV, Fibonacci, S/R, VWAP, Momentum dahil.
        """
        if len(df) < 30:
            return {"signal": "HOLD", "confidence": 0, "reasons": []}

        close = df["close"]
        volume = df["volume"] if "volume" in df.columns else None

        # === TEMEL GOSTERGELER ===
        rsi = RSIIndicator(close, window=14).rsi().iloc[-1]
        ema_9 = EMAIndicator(close, window=9).ema_indicator().iloc[-1]
        ema_21 = EMAIndicator(close, window=21).ema_indicator().iloc[-1]

        macd_ind = MACD(close)
        macd_hist = macd_ind.macd_diff().iloc[-1]
        prev_macd_hist = macd_ind.macd_diff().iloc[-2]
        macd_cross = "BULLISH" if macd_hist > 0 and prev_macd_hist <= 0 else (
            "BEARISH" if macd_hist < 0 and prev_macd_hist >= 0 else "NEUTRAL"
        )

        bb = BollingerBands(close, window=20, window_dev=2)
        bb_lower = bb.bollinger_lband().iloc[-1]
        bb_upper = bb.bollinger_hband().iloc[-1]

        atr = AverageTrueRange(
            df["high"], df["low"], df["close"], window=14
        ).average_true_range().iloc[-1]

        price = float(close.iloc[-1])
        reasons = []
        buy_score = 0
        sell_score = 0

        # === TREND (EMA50 + EMA200) ===
        ema_50 = EMAIndicator(close, window=min(50, len(close)-1)).ema_indicator().iloc[-1]
        above_ema200 = True
        if len(close) >= 200:
            ema_200 = EMAIndicator(close, window=200).ema_indicator().iloc[-1]
            above_ema200 = price > ema_200
        elif len(close) >= 100:
            ema_200 = EMAIndicator(close, window=len(close)-1).ema_indicator().iloc[-1]
            above_ema200 = price > ema_200

        if price > ema_50 and ema_9 > ema_21:
            trend = "UPTREND"
        elif price < ema_50 and ema_9 < ema_21:
            trend = "DOWNTREND"
        else:
            trend = "SIDEWAYS"

        # === VOLUME ===
        volume_ok = True
        volume_ratio = 1.0
        if volume is not None and len(volume) > 20:
            avg_volume = volume.rolling(20).mean().iloc[-1]
            current_volume = volume.iloc[-1]
            if avg_volume > 0:
                volume_ratio = float(current_volume / avg_volume)
                volume_ok = volume_ratio >= BACKTEST_CONFIG.get("min_volume_ratio", 1.3)

        # === VWAP ===
        vwap_signal = "NEUTRAL"
        if volume is not None and len(volume) > 20:
            try:
                typical_price = (df["high"] + df["low"] + df["close"]) / 3
                tp_vol = (typical_price * volume).tail(20).sum()
                vol_sum = volume.tail(20).sum()
                if vol_sum > 0:
                    vwap = tp_vol / vol_sum
                    vwap_dist = (price - vwap) / vwap
                    if vwap_dist < -0.01:
                        vwap_signal = "BULLISH"
                    elif vwap_dist > 0.02:
                        vwap_signal = "BEARISH"
            except Exception:
                pass

        # === MOMENTUM ===
        price_change_5 = (close.iloc[-1] - close.iloc[-5]) / close.iloc[-5] * 100
        price_change_1 = (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100
        momentum_up = price_change_5 > 0 and price_change_1 > 0

        # === BUY SKORLAMA ===
        if rsi < BACKTEST_CONFIG["rsi_oversold"]:
            buy_score += 25
            reasons.append(f"RSI={rsi:.0f}")
        if ema_9 > ema_21:
            buy_score += 15
            reasons.append("EMA+")
        if macd_cross == "BULLISH":
            buy_score += 20
            reasons.append("MACD+")
        if price <= bb_lower * (1 + BACKTEST_CONFIG["bb_proximity_pct"]):
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
        if vwap_signal == "BULLISH":
            buy_score += 10
            reasons.append("VWAP-")
        elif vwap_signal == "BEARISH":
            buy_score -= 5

        # === GELISMIS GOSTERGELER ===
        # Ichimoku
        try:
            from ta.trend import IchimokuIndicator
            ichimoku = IchimokuIndicator(df["high"], df["low"], window1=9, window2=26, window3=52)
            ich_a = ichimoku.ichimoku_a().iloc[-1]
            ich_b = ichimoku.ichimoku_b().iloc[-1]
            if pd.notna(ich_a) and pd.notna(ich_b):
                cloud_top = max(ich_a, ich_b)
                cloud_bottom = min(ich_a, ich_b)
                if price > cloud_top:
                    buy_score += 10
                    reasons.append("Ichi+")
                elif price < cloud_bottom:
                    buy_score -= 10
                    reasons.append("Ichi-")
        except Exception:
            pass

        # ADX
        try:
            from ta.trend import ADXIndicator
            adx_ind = ADXIndicator(df["high"], df["low"], df["close"], window=14)
            adx_val = adx_ind.adx().iloc[-1]
            adx_pos = adx_ind.adx_pos().iloc[-1]
            adx_neg = adx_ind.adx_neg().iloc[-1]
            if pd.notna(adx_val) and adx_val > 25:
                if adx_pos > adx_neg and trend == "UPTREND":
                    buy_score += 10
                    reasons.append(f"ADX:{adx_val:.0f}+")
                elif adx_neg > adx_pos:
                    buy_score -= 5
        except Exception:
            pass

        # OBV Divergence
        try:
            from ta.volume import OnBalanceVolumeIndicator
            obv = OnBalanceVolumeIndicator(df["close"], df["volume"]).on_balance_volume()
            obv_sma = obv.rolling(10).mean()
            obv_rising = obv.iloc[-1] > obv_sma.iloc[-1] if pd.notna(obv_sma.iloc[-1]) else False
            if obv_rising and price_change_5 < 0:
                buy_score += 15
                reasons.append("OBV_div+")
            elif not obv_rising and price_change_5 > 0:
                buy_score -= 5
        except Exception:
            pass

        # Fibonacci Retracement
        try:
            lookback = min(50, len(df))
            fib_high = df["high"].tail(lookback).max()
            fib_low = df["low"].tail(lookback).min()
            fib_range = fib_high - fib_low
            if fib_range > 0:
                fib_382 = fib_high - fib_range * 0.382
                fib_618 = fib_high - fib_range * 0.618
                if abs(price - fib_618) / price < 0.01:
                    buy_score += 10
                    reasons.append("Fib_618")
                elif abs(price - fib_382) / price < 0.01:
                    buy_score += 5
                    reasons.append("Fib_382")
        except Exception:
            pass

        # Support/Resistance
        try:
            sr_lb = min(50, len(df))
            sr_prox = 0.015
            recent = df.tail(sr_lb)
            sw_low = recent["low"].min()
            sw_high = recent["high"].max()
            if sw_low > 0:
                dist_sup = (price - sw_low) / price
                if dist_sup < sr_prox:
                    buy_score += 15
                    reasons.append("SR_support")
            if sw_high > 0:
                dist_res = (sw_high - price) / price
                if dist_res < sr_prox:
                    buy_score -= 20  # Direncte alim YAPMA (backtest: cok fazla direnste alim)
                    sell_score += 15
                    reasons.append("SR_resist")
        except Exception:
            pass

        # === SELL SKORLAMA ===
        if rsi > BACKTEST_CONFIG["rsi_overbought"]:
            sell_score += 25
            reasons.append(f"RSI={rsi:.0f}")
        if ema_9 < ema_21:
            sell_score += 15
        if macd_cross == "BEARISH":
            sell_score += 20
            reasons.append("MACD-")
        if price > bb_upper:
            sell_score += 20
            reasons.append("BB_top")
        if trend == "DOWNTREND":
            sell_score += 10

        # Momentum/Breakout
        if trend == "UPTREND" and 40 <= rsi <= 65:
            if momentum_up and volume_ok:
                buy_score += 15
                reasons.append("Momentum_BUY")
            elif price_change_5 > 2.0:
                buy_score += 10
                reasons.append(f"Breakout:{price_change_5:.1f}%")

        # === KARAR ===
        if buy_score >= 25:
            signal = "BUY"
            confidence = min(buy_score, 100)
        elif sell_score >= 45:
            signal = "SHORT"  # Short pozisyon sinyali (45+ = guclu dusus beklentisi)
            confidence = min(sell_score, 100)
        else:
            signal = "HOLD"
            confidence = 0

        return {
            "signal": signal,
            "confidence": confidence,
            "tech_score": buy_score,
            "reasons": reasons,
            "price": price,
            "rsi": float(rsi),
            "atr": float(atr),
            "above_ema200": above_ema200,
            "volume_ratio": volume_ratio,
        }

    def _get_portfolio_value(self, current_prices: Dict[str, float]) -> float:
        value = self.capital
        for sym, pos in self.positions.items():
            price = current_prices.get(sym, pos["entry_price"])
            value += pos["qty"] * price
        return value

    def _sector_limit_reached(self, symbol: str, current_prices: Dict) -> bool:
        """Sektör korelasyon limiti kontrolü."""
        sector = SECTOR_MAP.get(symbol, "Unknown")
        if sector == "Unknown":
            return False
        max_per_sector = BACKTEST_CONFIG.get("max_positions_per_sector", 2)
        count = sum(1 for s in self.positions if SECTOR_MAP.get(s, "") == sector)
        return count >= max_per_sector

    # ============================================================
    # ANA BACKTEST DÖNGÜSÜ
    # ============================================================

    def run(self, days: int = 90) -> Dict:
        """
        Tüm hisseler üzerinde geriye dönük test çalıştırır.
        Günlük bar bazında simülasyon.
        """
        logger.info(f"\n{'='*60}")
        logger.info(f"  📊 HİSSE SENEDİ BACKTEST BAŞLIYOR")
        logger.info(f"  Sermaye: ${self.initial_capital:,.2f}")
        logger.info(f"  Periyot: {days} gün")
        logger.info(f"  Hisseler: {len(self.symbols)} adet")
        logger.info(f"  Max Pozisyon: {BACKTEST_CONFIG['max_open_positions']}")
        logger.info(f"  Stop-Loss: {BACKTEST_CONFIG['stop_loss_pct']:.1%}")
        logger.info(f"  Take-Profit: {BACKTEST_CONFIG['take_profit_pct']:.1%}")
        logger.info(f"  Komisyon: $0 (hisse)")
        logger.info(f"{'='*60}\n")

        # Veri çek
        all_data = {}
        for symbol in self.symbols:
            df = self._fetch_data(symbol, days=days)
            if not df.empty and len(df) >= 50:
                all_data[symbol] = df
                logger.info(f"  {symbol}: {len(df)} gün veri")
            else:
                logger.warning(f"  {symbol}: Yetersiz veri, atlanıyor")

        if not all_data:
            logger.error("Hiç veri alınamadı!")
            return {"error": "Veri alınamadı"}

        # Ortak zaman çizelgesi
        all_times = set()
        for df in all_data.values():
            all_times.update(df.index.tolist())
        all_times = sorted(all_times)

        logger.info(f"\n  Toplam {len(all_times)} bar simüle edilecek...")
        logger.info(f"  Başlangıç: {all_times[0]}")
        logger.info(f"  Bitiş:     {all_times[-1]}\n")

        # Bar-bar simülasyon
        lookback = 50
        total_signals = 0
        total_buys = 0
        total_sells = 0

        for bar_idx in range(lookback, len(all_times)):
            current_time = all_times[bar_idx]
            current_prices = {}

            for sym, df in all_data.items():
                if current_time in df.index:
                    current_prices[sym] = float(df.loc[current_time, "close"])

            # Portföy değeri
            portfolio_value = self._get_portfolio_value(current_prices)
            self.equity_curve.append({
                "time": str(current_time),
                "equity": round(portfolio_value, 2),
            })
            day_str = str(current_time)[:10]
            self.daily_equity[day_str] = round(portfolio_value, 2)

            # === POZİSYON YÖNETİMİ ===
            symbols_to_close = []
            for sym, pos in self.positions.items():
                if sym not in current_prices:
                    continue

                price = current_prices[sym]
                entry = pos["entry_price"]
                pnl_pct = (price - entry) / entry

                if price > pos.get("highest_price", entry):
                    pos["highest_price"] = price

                highest = pos.get("highest_price", entry)
                trailing_drop = (highest - price) / highest if highest > 0 else 0

                # Break-Even Stop
                if BACKTEST_CONFIG.get("breakeven_enabled", True):
                    be_trigger = BACKTEST_CONFIG["breakeven_trigger_pct"]
                    be_offset = BACKTEST_CONFIG["breakeven_offset_pct"]
                    if pnl_pct >= be_trigger and not pos.get("breakeven_set", False):
                        pos["stop_loss_pct"] = be_offset
                        pos["breakeven_set"] = True

                # Stop-loss
                pos_sl = pos.get("stop_loss_pct", BACKTEST_CONFIG["stop_loss_pct"])
                if pnl_pct <= -pos_sl:
                    symbols_to_close.append((sym, "STOP_LOSS", price, pnl_pct))
                # Take-profit
                elif pnl_pct >= BACKTEST_CONFIG["take_profit_pct"]:
                    symbols_to_close.append((sym, "TAKE_PROFIT", price, pnl_pct))
                # Trailing stop
                elif pnl_pct > 0.01 and trailing_drop >= BACKTEST_CONFIG["trailing_stop_pct"]:
                    symbols_to_close.append((sym, "TRAILING_STOP", price, pnl_pct))
                # Kademeli kar
                elif (pnl_pct >= BACKTEST_CONFIG["partial_profit_pct"]
                      and not pos.get("partial_sold", False)):
                    half_qty = max(int(pos["qty"] * 0.5), 1) if pos["qty"] >= 2 else pos["qty"]
                    half_value = half_qty * price
                    partial_pnl = (price - pos["entry_price"]) * half_qty
                    self.capital += half_value
                    pos["qty"] -= half_qty
                    pos["partial_sold"] = True
                    pos["partial_pnl_usd"] = partial_pnl  # Partial kar takibi
                    self.trades.append({
                        "action": "PARTIAL_SELL", "symbol": sym,
                        "price": price, "qty": half_qty,
                        "pnl_usd": round(partial_pnl, 2),
                        "pnl_pct": round(pnl_pct * 100, 2),
                        "reason": "PARTIAL_PROFIT",
                        "time": str(current_time),
                    })

            # Satislari uygula
            for sym, reason, price, pnl_pct in symbols_to_close:
                pos = self.positions[sym]
                sell_value = pos["qty"] * price
                remaining_pnl = (price - pos["entry_price"]) * pos["qty"]
                # Toplam P&L = partial kar + kalan hisseler kari
                total_pnl = remaining_pnl + pos.get("partial_pnl_usd", 0)
                self.capital += sell_value
                total_sells += 1

                # Ardisik zarar takibi
                if total_pnl < 0:
                    self._symbol_consecutive_losses[sym] = self._symbol_consecutive_losses.get(sym, 0) + 1
                else:
                    self._symbol_consecutive_losses[sym] = 0

                sector = SECTOR_MAP.get(sym, "Unknown")
                self.trades.append({
                    "action": "SELL", "symbol": sym, "sector": sector,
                    "price": price, "qty": pos["qty"],
                    "entry_price": pos["entry_price"],
                    "pnl_usd": round(total_pnl, 2),
                    "pnl_pct": round(pnl_pct * 100, 2),
                    "reason": reason,
                    "time": str(current_time),
                })
                del self.positions[sym]

            # === YENİ ALIM SİNYALLERİ ===
            open_count = len(self.positions)
            current_equity = self._get_portfolio_value(current_prices)

            for sym, df in all_data.items():
                if sym in self.positions:
                    continue
                if open_count >= BACKTEST_CONFIG["max_open_positions"]:
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
                analysis = self._analyze(available.tail(250))
                total_signals += 1

                if analysis["signal"] == "BUY" and analysis["confidence"] >= BACKTEST_CONFIG["min_confidence"]:
                    # EMA200 Gate
                    if BACKTEST_CONFIG.get("ema200_trend_gate", True) and not analysis.get("above_ema200", True):
                        continue

                    # Hisse ardışık zarar filtresi
                    if BACKTEST_CONFIG.get("coin_filter_enabled", True):
                        losses = self._symbol_consecutive_losses.get(sym, 0)
                        if losses >= BACKTEST_CONFIG.get("coin_max_consecutive_losses", 3):
                            continue

                    # Sektör limiti
                    if self._sector_limit_reached(sym, current_prices):
                        continue

                    # R:R Gate
                    if BACKTEST_CONFIG.get("rr_gate_enabled", True):
                        atr_val = analysis.get("atr", 0)
                        a_price = analysis.get("price", 0)
                        if atr_val > 0 and a_price > 0:
                            atr_pct = atr_val / a_price
                            actual_sl = max(min(
                                atr_pct * BACKTEST_CONFIG["atr_stop_multiplier"],
                                BACKTEST_CONFIG["stop_loss_max_pct"]
                            ), BACKTEST_CONFIG["stop_loss_pct"])
                            rr = BACKTEST_CONFIG["take_profit_pct"] / actual_sl if actual_sl > 0 else 0
                            if rr < BACKTEST_CONFIG["min_rr_ratio"]:
                                continue

                    # Volatilite filtresi
                    if BACKTEST_CONFIG.get("volatility_filter_enabled", True):
                        atr_val = analysis.get("atr", 0)
                        if atr_val > 0 and analysis["price"] > 0:
                            if atr_val / analysis["price"] > BACKTEST_CONFIG["max_atr_pct"]:
                                continue

                    price = analysis["price"]

                    # Pozisyon boyutu
                    cash_reserve = current_equity * BACKTEST_CONFIG["cash_reserve_pct"]
                    available_cash = max(self.capital - cash_reserve, 0)
                    max_invest = min(
                        available_cash * 0.35,
                        current_equity * BACKTEST_CONFIG["max_position_pct"],
                    )

                    if max_invest < 10:
                        continue

                    qty = int(max_invest / price)  # Tam hisse
                    if qty < 1:
                        # Fractional share
                        qty = round(max_invest / price, 4)
                    if qty * price < 1:
                        continue

                    # ATR adaptif stop-loss
                    atr_value = analysis.get("atr", 0)
                    if atr_value > 0 and price > 0:
                        atr_pct = atr_value / price
                        adaptive_sl = max(min(
                            atr_pct * BACKTEST_CONFIG["atr_stop_multiplier"],
                            BACKTEST_CONFIG["stop_loss_max_pct"]
                        ), BACKTEST_CONFIG["stop_loss_pct"])
                    else:
                        adaptive_sl = BACKTEST_CONFIG["stop_loss_pct"]

                    self.capital -= qty * price
                    total_buys += 1

                    sector = SECTOR_MAP.get(sym, "Unknown")
                    self.positions[sym] = {
                        "entry_price": price, "qty": qty,
                        "highest_price": price, "partial_sold": False,
                        "entry_time": str(current_time),
                        "stop_loss_pct": adaptive_sl, "sector": sector,
                    }
                    self.last_trade_bar[sym] = bar_idx

                    self.trades.append({
                        "action": "BUY", "symbol": sym, "sector": sector,
                        "price": price, "qty": qty,
                        "invest": round(qty * price, 2),
                        "confidence": analysis["confidence"],
                        "reasons": ", ".join(analysis["reasons"]),
                        "time": str(current_time),
                    })
                    open_count += 1

            # === SHORT POZISYON YONETIMI ===
            short_sc = SHORT_CONFIG
            shorts_to_close = []
            for sym, pos in self.short_positions.items():
                if sym not in current_prices:
                    continue
                price = current_prices[sym]
                entry = pos["entry_price"]
                # SHORT P&L: fiyat DUSTUYSE kar
                pnl_pct = (entry - price) / entry

                # Lowest price tracking (ters trailing)
                if price < pos.get("lowest_price", entry):
                    pos["lowest_price"] = price
                lowest = pos.get("lowest_price", entry)
                trailing_rise = (price - lowest) / lowest if lowest > 0 else 0

                # Short break-even
                if pnl_pct >= short_sc.get("short_breakeven_trigger_pct", 0.025) and not pos.get("breakeven_set", False):
                    pos["stop_loss_pct"] = short_sc.get("short_breakeven_offset_pct", 0.003)
                    pos["breakeven_set"] = True

                pos_sl = pos.get("stop_loss_pct", short_sc["short_stop_loss_pct"])

                # Stop-loss (fiyat YUKARI)
                if pnl_pct <= -pos_sl:
                    shorts_to_close.append((sym, "SHORT_STOP_LOSS", price, pnl_pct))
                # Take-profit (fiyat ASAGI)
                elif pnl_pct >= short_sc["short_take_profit_pct"]:
                    shorts_to_close.append((sym, "SHORT_TAKE_PROFIT", price, pnl_pct))
                # Trailing stop
                elif pnl_pct > 0.01 and trailing_rise >= short_sc["short_trailing_stop_pct"]:
                    shorts_to_close.append((sym, "SHORT_TRAILING", price, pnl_pct))

            # Short kapanislari uygula
            for sym, reason, price, pnl_pct in shorts_to_close:
                pos = self.short_positions[sym]
                # Short kar: (entry - exit) * qty
                remaining_pnl = (pos["entry_price"] - price) * pos["qty"]
                total_pnl = remaining_pnl + pos.get("partial_pnl_usd", 0)
                self.capital += pos["qty"] * pos["entry_price"] + total_pnl  # Margin geri + kar/zarar
                total_sells += 1
                sector = SECTOR_MAP.get(sym, "Unknown")
                self.trades.append({
                    "action": "COVER", "symbol": sym, "sector": sector,
                    "price": price, "qty": pos["qty"],
                    "entry_price": pos["entry_price"],
                    "pnl_usd": round(total_pnl, 2),
                    "pnl_pct": round(pnl_pct * 100, 2),
                    "reason": reason,
                    "time": str(current_time),
                })
                del self.short_positions[sym]

            # === YENi SHORT SINYALLERI ===
            short_count = len(self.short_positions)
            for sym, df in all_data.items():
                if sym in self.short_positions or sym in self.positions:
                    continue
                if short_count >= short_sc.get("short_max_positions", 2):
                    break
                if sym in short_sc.get("short_blacklist", []):
                    continue

                last_bar = self.last_trade_bar.get(f"short_{sym}", 0)
                if bar_idx - last_bar < BACKTEST_CONFIG["min_trade_interval_bars"]:
                    continue

                mask = df.index <= current_time
                available = df[mask]
                if len(available) < 50:
                    continue

                analysis = self._analyze(available.tail(250))
                total_signals += 1

                if analysis["signal"] == "SHORT" and analysis["confidence"] >= short_sc.get("short_min_confidence", 35):
                    price = analysis["price"]
                    max_invest = min(
                        current_equity * short_sc.get("short_max_position_pct", 0.20),
                        short_sc.get("short_max_position_usd", 150),
                    )
                    if max_invest < 10:
                        continue

                    qty = int(max_invest / price) if price > 0 else 0
                    if qty < 1:
                        qty = round(max_invest / price, 4)
                    if qty * price < 1:
                        continue

                    # Margin: short icin sermaye ayir
                    if self.capital < qty * price:
                        continue

                    self.capital -= qty * price  # Margin olarak tut
                    total_buys += 1
                    sector = SECTOR_MAP.get(sym, "Unknown")
                    self.short_positions[sym] = {
                        "entry_price": price, "qty": qty,
                        "lowest_price": price, "partial_covered": False,
                        "entry_time": str(current_time),
                        "stop_loss_pct": short_sc["short_stop_loss_pct"],
                    }
                    self.last_trade_bar[f"short_{sym}"] = bar_idx
                    self.trades.append({
                        "action": "SHORT", "symbol": sym, "sector": sector,
                        "price": price, "qty": qty,
                        "invest": round(qty * price, 2),
                        "confidence": analysis["confidence"],
                        "reasons": ", ".join(analysis["reasons"]),
                        "time": str(current_time),
                    })
                    short_count += 1

        # Kalan LONG pozisyonlari kapat
        for sym, pos in list(self.positions.items()):
            if sym in current_prices:
                price = current_prices[sym]
                pnl_usd = (price - pos["entry_price"]) * pos["qty"]
                self.capital += pos["qty"] * price
                total_sells += 1
                self.trades.append({
                    "action": "SELL", "symbol": sym,
                    "price": price, "qty": pos["qty"],
                    "entry_price": pos["entry_price"],
                    "pnl_usd": round(pnl_usd, 2),
                    "pnl_pct": round((price - pos["entry_price"]) / pos["entry_price"] * 100, 2),
                    "reason": "BACKTEST_END",
                    "time": str(all_times[-1]),
                })
        self.positions.clear()

        # Kalan SHORT pozisyonlari kapat
        for sym, pos in list(self.short_positions.items()):
            if sym in current_prices:
                price = current_prices[sym]
                pnl_usd = (pos["entry_price"] - price) * pos["qty"]
                self.capital += pos["qty"] * pos["entry_price"] + pnl_usd
                total_sells += 1
                self.trades.append({
                    "action": "COVER", "symbol": sym,
                    "price": price, "qty": pos["qty"],
                    "entry_price": pos["entry_price"],
                    "pnl_usd": round(pnl_usd, 2),
                    "pnl_pct": round((pos["entry_price"] - price) / pos["entry_price"] * 100, 2),
                    "reason": "BACKTEST_END",
                    "time": str(all_times[-1]),
                })
        self.short_positions.clear()

        return self._calculate_results(total_signals, total_buys, total_sells)

    # ============================================================
    # PORTFOLIO VALUE
    # ============================================================

    def _get_portfolio_value(self, current_prices: Dict) -> float:
        """Toplam portfoy degeri: nakit + long pozisyonlar + short P&L."""
        value = self.capital
        # Long pozisyonlar
        for sym, pos in self.positions.items():
            if sym in current_prices:
                value += pos["qty"] * current_prices[sym]
        # Short pozisyonlar (margin + unrealized P&L)
        for sym, pos in self.short_positions.items():
            if sym in current_prices:
                # Margin zaten capital'dan dusuldu, kar/zarar ekle
                unrealized = (pos["entry_price"] - current_prices[sym]) * pos["qty"]
                value += pos["qty"] * pos["entry_price"] + unrealized
        return value

    # ============================================================
    # SONUCLAR
    # ============================================================

    def _calculate_results(self, total_signals, total_buys, total_sells) -> Dict:
        final_equity = self.capital
        total_return = final_equity - self.initial_capital
        total_return_pct = (total_return / self.initial_capital) * 100

        # Hem SELL (long cikis) hem COVER (short cikis) islemi
        sell_trades = [t for t in self.trades if t["action"] in ("SELL", "COVER") and "pnl_usd" in t]
        long_trades = [t for t in self.trades if t["action"] == "SELL" and "pnl_usd" in t]
        short_trades = [t for t in self.trades if t["action"] == "COVER" and "pnl_usd" in t]
        wins = [t for t in sell_trades if t["pnl_usd"] > 0]
        losses = [t for t in sell_trades if t["pnl_usd"] <= 0]

        win_rate = (len(wins) / len(sell_trades) * 100) if sell_trades else 0
        avg_win = np.mean([t["pnl_usd"] for t in wins]) if wins else 0
        avg_loss = np.mean([t["pnl_usd"] for t in losses]) if losses else 0
        avg_win_pct = np.mean([t["pnl_pct"] for t in wins]) if wins else 0
        avg_loss_pct = np.mean([t["pnl_pct"] for t in losses]) if losses else 0

        total_wins_amt = sum(t["pnl_usd"] for t in wins)
        total_losses_amt = abs(sum(t["pnl_usd"] for t in losses))
        profit_factor = total_wins_amt / total_losses_amt if total_losses_amt > 0 else float("inf")

        # Max Drawdown
        if self.equity_curve:
            eq = pd.Series([e["equity"] for e in self.equity_curve])
            peak = eq.cummax()
            drawdown = (eq - peak) / peak * 100
            max_drawdown = float(drawdown.min())
        else:
            max_drawdown = 0

        # Sharpe Ratio (günlük)
        if len(self.equity_curve) > 1:
            returns = pd.Series([e["equity"] for e in self.equity_curve]).pct_change().dropna()
            sharpe = (returns.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else 0
        else:
            sharpe = 0

        # Satış sebep dağılımı
        sell_reasons = {}
        for t in sell_trades:
            r = t.get("reason", "UNKNOWN")
            sell_reasons[r] = sell_reasons.get(r, 0) + 1

        # Hisse bazlı P&L
        symbol_pnl = {}
        for t in sell_trades:
            sym = t["symbol"]
            symbol_pnl[sym] = symbol_pnl.get(sym, 0) + t["pnl_usd"]

        # Sektör bazlı P&L
        sector_pnl = defaultdict(lambda: {"pnl": 0, "trades": 0, "wins": 0})
        for t in sell_trades:
            sector = t.get("sector", SECTOR_MAP.get(t["symbol"], "Unknown"))
            sector_pnl[sector]["pnl"] += t["pnl_usd"]
            sector_pnl[sector]["trades"] += 1
            if t["pnl_usd"] > 0:
                sector_pnl[sector]["wins"] += 1

        best_sym = max(symbol_pnl, key=symbol_pnl.get) if symbol_pnl else "N/A"
        worst_sym = min(symbol_pnl, key=symbol_pnl.get) if symbol_pnl else "N/A"

        results = {
            "initial_capital": self.initial_capital,
            "final_equity": round(final_equity, 2),
            "total_return_usd": round(total_return, 2),
            "total_return_pct": round(total_return_pct, 2),
            "total_signals_checked": total_signals,
            "total_buys": total_buys,
            "total_sells": total_sells,
            "total_trades": len(sell_trades),
            "long_trades": len(long_trades),
            "short_trades": len(short_trades),
            "long_wins": len([t for t in long_trades if t["pnl_usd"] > 0]),
            "short_wins": len([t for t in short_trades if t["pnl_usd"] > 0]),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 1),
            "avg_win_usd": round(float(avg_win), 2),
            "avg_loss_usd": round(float(avg_loss), 2),
            "avg_win_pct": round(float(avg_win_pct), 2),
            "avg_loss_pct": round(float(avg_loss_pct), 2),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown_pct": round(max_drawdown, 2),
            "sharpe_ratio": round(float(sharpe), 2),
            "sell_reasons": sell_reasons,
            "best_symbol": best_sym,
            "worst_symbol": worst_sym,
            "symbol_pnl": {k: round(v, 2) for k, v in sorted(symbol_pnl.items(), key=lambda x: x[1], reverse=True)},
            "sector_pnl": {k: {"pnl": round(v["pnl"], 2), "trades": v["trades"], "wins": v["wins"]} for k, v in sector_pnl.items()},
        }

        self._print_results(results)
        self._save_results(results)
        return results

    def _print_results(self, r: Dict):
        marker = "+" if r["total_return_usd"] >= 0 else ""

        logger.info("\n" + "=" * 60)
        logger.info("  📊 HİSSE SENEDİ BACKTEST SONUÇLARI")
        logger.info("=" * 60)
        logger.info(f"  Başlangıç:       ${r['initial_capital']:,.2f}")
        logger.info(f"  Final:           ${r['final_equity']:,.2f}")
        logger.info(f"  Getiri:          {marker}${r['total_return_usd']:,.2f} ({marker}{r['total_return_pct']:.1f}%)")
        logger.info(f"  {'-'*40}")
        logger.info(f"  Taranan Sinyal:  {r['total_signals_checked']}")
        logger.info(f"  Toplam Giris:    {r['total_buys']}")
        logger.info(f"  Toplam Cikis:    {r['total_sells']}")
        logger.info(f"  Kapanan Islem:   {r['total_trades']}")
        logger.info(f"  {'-'*40}")
        logger.info(f"  LONG:  {r.get('long_trades', 0)} islem ({r.get('long_wins', 0)}W)")
        logger.info(f"  SHORT: {r.get('short_trades', 0)} islem ({r.get('short_wins', 0)}W)")
        logger.info(f"  {'-'*40}")
        logger.info(f"  Kazanc / Kayip:  {r['wins']}W / {r['losses']}L")
        logger.info(f"  Win Rate:        {r['win_rate']:.1f}%")
        logger.info(f"  {'-'*40}")
        logger.info(f"  Ort. Kazanç:     ${r['avg_win_usd']:,.2f} ({r['avg_win_pct']:+.2f}%)")
        logger.info(f"  Ort. Kayıp:      ${r['avg_loss_usd']:,.2f} ({r['avg_loss_pct']:+.2f}%)")
        logger.info(f"  Profit Factor:   {r['profit_factor']:.2f}")
        logger.info(f"  Max Drawdown:    {r['max_drawdown_pct']:.2f}%")
        logger.info(f"  Sharpe Ratio:    {r['sharpe_ratio']:.2f}")
        logger.info(f"  {'-'*40}")
        logger.info(f"  SATIŞ SEBEPLERİ:")
        for reason, count in r.get("sell_reasons", {}).items():
            logger.info(f"    {reason}: {count}")
        logger.info(f"  {'-'*40}")
        logger.info(f"  HİSSE BAZLI P&L:")
        for sym, pnl in r.get("symbol_pnl", {}).items():
            m = "+" if pnl >= 0 else ""
            logger.info(f"    {sym}: {m}${pnl:.2f}")
        logger.info(f"  En İyi:  {r['best_symbol']}")
        logger.info(f"  En Kötü: {r['worst_symbol']}")

        # Sektör performansı
        if r.get("sector_pnl"):
            logger.info(f"  {'-'*40}")
            logger.info(f"  SEKTÖR PERFORMANSI:")
            for sector, data in r["sector_pnl"].items():
                wr = (data["wins"] / data["trades"] * 100) if data["trades"] > 0 else 0
                m = "+" if data["pnl"] >= 0 else ""
                logger.info(f"    {sector}: {m}${data['pnl']:.2f} ({data['trades']} işlem, WR:{wr:.0f}%)")

        logger.info("=" * 60)

        # Bot sağlığı
        logger.info("\n  📋 BOT SAĞLIĞI:")
        issues = []
        good = []

        if r["win_rate"] >= 50:
            good.append(f"  ✅ Win Rate iyi: {r['win_rate']:.1f}%")
        else:
            issues.append(f"  ⚠️ Win Rate düşük: {r['win_rate']:.1f}%")

        if r["profit_factor"] >= 1.5:
            good.append(f"  ✅ Profit Factor iyi: {r['profit_factor']:.2f}")
        elif r["profit_factor"] >= 1.0:
            good.append(f"  ⚡ Profit Factor kabul edilebilir: {r['profit_factor']:.2f}")
        else:
            issues.append(f"  ⚠️ Profit Factor < 1: {r['profit_factor']:.2f}")

        if r["max_drawdown_pct"] > -5:
            good.append(f"  ✅ Max DD kontrol altında: {r['max_drawdown_pct']:.1f}%")
        else:
            issues.append(f"  ⚠️ Max DD yüksek: {r['max_drawdown_pct']:.1f}%")

        if r["total_trades"] >= 5:
            good.append(f"  ✅ Yeterli işlem: {r['total_trades']}")
        else:
            issues.append(f"  ⚠️ Az işlem: {r['total_trades']}")

        if r["total_return_pct"] > 0:
            good.append(f"  ✅ Kârlı: {r['total_return_pct']:+.1f}%")
        else:
            issues.append(f"  ⚠️ Zararda: {r['total_return_pct']:+.1f}%")

        for g in good:
            logger.info(g)
        for i in issues:
            logger.info(i)

        if not issues:
            logger.info("\n  🟢 BOT SAĞLIĞI: MÜKEMMEL")
        elif len(issues) <= 2:
            logger.info("\n  🟡 BOT SAĞLIĞI: KABUL EDİLEBİLİR")
        else:
            logger.info("\n  🔴 BOT SAĞLIĞI: SORUNLU — Strateji gözden geçirilmeli")
        logger.info("")

    def _save_results(self, results: Dict):
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
        os.makedirs(output_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        results_file = os.path.join(output_dir, f"stock_backtest_{timestamp}.json")

        save_data = {k: v for k, v in results.items()}
        save_data["trades"] = self.trades
        save_data["timestamp"] = timestamp
        save_data["config"] = {k: v for k, v in BACKTEST_CONFIG.items()}

        with open(results_file, "w") as f:
            json.dump(save_data, f, indent=2, default=str)
        logger.info(f"  📁 Sonuçlar kaydedildi: {results_file}")


# ============================================================
# CLI
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hisse Senedi Backtester")
    parser.add_argument("--capital", type=float, default=500,
                        help="Başlangıç sermayesi (default: $500)")
    parser.add_argument("--days", type=int, default=90,
                        help="Test periyodu gün (default: 90)")
    parser.add_argument("--symbols", type=str, default="",
                        help="Hisse listesi, virgülle ayır (default: config'den)")

    args = parser.parse_args()

    symbols = None
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]

    bt = StockBacktester(initial_capital=args.capital, symbols=symbols)
    results = bt.run(days=args.days)
