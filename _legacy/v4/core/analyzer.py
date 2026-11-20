"""
Technical Analyzer — Teknik + Hibrit Analiz Motoru

StockBot'tan ayrıştırılmış analiz modülü.
- analyze(): Saf teknik analiz (RSI, EMA, MACD, BB, Ichimoku, ADX, OBV, Fibonacci, S/R)
- analyze_with_news(): Teknik + haber + makro + ML + fundamental + ESG + korelasyon + agent
"""
from datetime import datetime, timedelta
from typing import Dict

import pandas as pd
import numpy as np

from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import BollingerBands, AverageTrueRange

from utils.logger import logger


class TechnicalAnalyzer:
    """Teknik ve hibrit analiz motoru. StockBot referansı üzerinden state'e erişir."""

    def __init__(self, bot):
        """
        Args:
            bot: StockBot instance (state erişimi için)
        """
        self.bot = bot

    def analyze(self, df: pd.DataFrame, config: Dict) -> Dict:
        """Gelişmiş teknik analiz: trend, volume, momentum + klasik göstergeler."""
        if len(df) < 30:
            return {"signal": "HOLD", "confidence": 0, "reason": "Yetersiz veri"}

        close = df["close"]
        volume = df["volume"] if "volume" in df.columns else None

        # === TEMEL GÖSTERGELER ===
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

        # === TREND TESPİTİ (GELİŞTİRİLMİŞ — EMA200 eklendi) ===
        ema_50 = EMAIndicator(close, window=min(50, len(close)-1)).ema_indicator().iloc[-1]
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

        # === EMA TREND SİNYALİ (TechAgent için) ===
        if ema_9 > ema_21 and current_price > ema_50:
            ema_trend = "BULLISH"
        elif ema_9 < ema_21 and current_price < ema_50:
            ema_trend = "BEARISH"
        else:
            ema_trend = "NEUTRAL"

        # === MACD SİNYALİ (TechAgent için) ===
        if macd_hist > 0:
            macd_signal_label = "BULLISH"
        elif macd_hist < 0:
            macd_signal_label = "BEARISH"
        else:
            macd_signal_label = "NEUTRAL"

        # === VOLUME ANALİZİ ===
        volume_ok = True
        volume_ratio = 1.0
        if volume is not None and len(volume) > 20:
            avg_volume = volume.rolling(20).mean().iloc[-1]
            current_volume = volume.iloc[-1]
            if avg_volume > 0:
                volume_ratio = current_volume / avg_volume
                volume_ok = volume_ratio >= config["min_volume_ratio"]

        # === VWAP (Volume Weighted Average Price) ===
        vwap = None
        vwap_signal = "NEUTRAL"
        if volume is not None and len(volume) > 20:
            try:
                typical_price = (df["high"] + df["low"] + df["close"]) / 3
                # Günlük VWAP — son 20 bar üzerinden
                tp_vol = (typical_price * volume).tail(20).sum()
                vol_sum = volume.tail(20).sum()
                if vol_sum > 0:
                    vwap = tp_vol / vol_sum
                    vwap_dist = (current_price - vwap) / vwap
                    if vwap_dist < -0.01:  # VWAP'ın altında: indirimli, alım fırsatı
                        vwap_signal = "BULLISH"
                    elif vwap_dist > 0.02:  # VWAP üzerinde: primli
                        vwap_signal = "BEARISH"
            except Exception:
                pass

        # === MOMENTUM ===
        price_change_5 = (close.iloc[-1] - close.iloc[-5]) / close.iloc[-5] * 100
        price_change_1 = (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100
        momentum_up = price_change_5 > 0 and price_change_1 > 0

        # === BB POZİSYON (TechAgent için) ===
        if current_price < bb_lower:
            bb_position = "BELOW"
        elif current_price > bb_upper:
            bb_position = "ABOVE"
        else:
            bb_position = "MIDDLE"

        # === BUY SKORLAMA ===
        buy_score = 0

        if rsi < config["rsi_oversold"]:
            buy_score += 25
            reasons.append(f"RSI={rsi:.0f}")

        if ema_9 > ema_21:
            buy_score += 15
            reasons.append("EMA+")

        if macd_hist > 0 and prev_macd_hist <= 0:
            buy_score += 20
            reasons.append("MACD+")

        if current_price < bb_lower * (1 + config["bb_proximity_pct"]):
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

        # === VWAP SKORLAMA ===
        if vwap_signal == "BULLISH":
            buy_score += 10
            reasons.append("VWAP↓")  # Fiyat VWAP altında = alım fırsatı
        elif vwap_signal == "BEARISH":
            buy_score -= 5
            reasons.append("VWAP↑")  # Fiyat VWAP çok üstünde = primli

        # === GELİŞMİŞ GÖSTERGELER ===
        ichimoku_signal = "NEUTRAL"
        try:
            from ta.trend import IchimokuIndicator
            ichimoku = IchimokuIndicator(df["high"], df["low"], window1=9, window2=26, window3=52)
            ich_a = ichimoku.ichimoku_a().iloc[-1]
            ich_b = ichimoku.ichimoku_b().iloc[-1]
            cloud_top = max(ich_a, ich_b) if pd.notna(ich_a) and pd.notna(ich_b) else 0
            cloud_bottom = min(ich_a, ich_b) if pd.notna(ich_a) and pd.notna(ich_b) else 0

            if cloud_top > 0:
                if current_price > cloud_top:
                    buy_score += 10
                    ichimoku_signal = "BULLISH"
                    reasons.append("Ichi+")
                elif current_price < cloud_bottom:
                    buy_score -= 10
                    ichimoku_signal = "BEARISH"
                    reasons.append("Ichi-")
        except Exception:
            pass

        adx_val = 0
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
            adx_val = float(adx_val) if pd.notna(adx_val) else 0
        except Exception:
            pass

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

        try:
            lookback = min(50, len(df))
            fib_high = df["high"].tail(lookback).max()
            fib_low = df["low"].tail(lookback).min()
            fib_range = fib_high - fib_low
            if fib_range > 0:
                fib_618 = fib_high - fib_range * 0.618
                fib_382 = fib_high - fib_range * 0.382
                proximity_618 = abs(current_price - fib_618) / current_price
                proximity_382 = abs(current_price - fib_382) / current_price
                if proximity_618 < 0.015 and current_price <= fib_618:
                    buy_score += 12
                    reasons.append("Fib61.8")
                elif proximity_382 < 0.015 and current_price <= fib_382:
                    buy_score += 8
                    reasons.append("Fib38.2")
        except Exception:
            pass

        try:
            if len(df) >= 25 and "close" in df.columns:
                rsi_series = RSIIndicator(df["close"], window=14).rsi()
                price_vals = df["close"].tail(20).values
                rsi_vals = rsi_series.tail(20).values
                valid = ~(np.isnan(price_vals) | np.isnan(rsi_vals))
                if valid.sum() >= 10:
                    pv = price_vals[valid]
                    rv = rsi_vals[valid]
                    mid = len(pv) // 2
                    if (pv[mid:].min() < pv[:mid].min() and
                        rv[mid:].min() > rv[:mid].min()):
                        buy_score += 15
                        reasons.append("RSI_div+")
        except Exception:
            pass

        # === SUPPORT / RESISTANCE ===
        sell_score = 0
        try:
            if config.get("sr_enabled", True):
                sr_lookback = config.get("sr_lookback_bars", 50)
                sr_prox = config.get("sr_proximity_pct", 0.015)
                lb = min(sr_lookback, len(df))
                recent = df.tail(lb)
                swing_low = recent["low"].min()
                swing_high = recent["high"].max()

                if swing_low > 0:
                    dist_to_support = (current_price - swing_low) / current_price
                    if dist_to_support < sr_prox:
                        buy_score += 15
                        reasons.append("SR_support")

                if swing_high > 0:
                    dist_to_resist = (swing_high - current_price) / current_price
                    if dist_to_resist < sr_prox:
                        buy_score -= 20  # Dirençte alım YAPMA
                        sell_score += 15
                        reasons.append("SR_resist")
        except Exception:
            pass

        # === SELL SKORLAMA ===
        if rsi > config["rsi_overbought"]:
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

        # === YENİ: EMA200 ALTI = güçlü düşüş trendi ===
        if not above_ema200:
            sell_score += 15
            reasons.append("EMA200-")

        # === YENİ: Negatif momentum (5 günde %3+ düşüş) ===
        if price_change_5 < -3:
            sell_score += 10
            reasons.append(f"Mom-:{price_change_5:.1f}%")

        # === YENİ: Yüksek hacimle düşüş (panik satışı) ===
        if volume_ratio > 1.5 and price_change_1 < -1:
            sell_score += 10
            reasons.append("Vol_Sell")

        # === MOMENTUM / BREAKOUT ===
        if trend == "UPTREND" and 40 <= rsi <= 65:
            if momentum_up and volume_ok:
                buy_score += 15
                reasons.append("Momentum_BUY")
            elif price_change_5 > 2.0:
                buy_score += 10
                reasons.append(f"Breakout:{price_change_5:.1f}%")

        # === YENİ: DOWNTREND BREAKDOWN ===
        if trend == "DOWNTREND" and rsi < 45:
            if not momentum_up and volume_ratio > 1.3:
                sell_score += 10
                reasons.append("Breakdown")

        # === TECH SCORE (TechAgent için birleşik skor) ===
        tech_score = buy_score - sell_score

        # === KARAR ===
        if buy_score >= 45:
            signal = "BUY"
            confidence = min(buy_score, 100)
        elif sell_score >= 45:
            signal = "SHORT"  # Guclu dusus sinyali (45+)
            confidence = min(sell_score, 100)
        else:
            signal = "HOLD"
            confidence = 0

        return {
            "signal": signal,
            "confidence": confidence,
            "sell_score": sell_score,
            "buy_score": buy_score,
            "tech_score": tech_score,
            "reasons": reasons,
            "price": current_price,
            "rsi": rsi,
            "ema_9": ema_9,
            "ema_21": ema_21,
            "ema_200": ema_200,
            "above_ema200": above_ema200,
            "ema_trend": ema_trend,
            "macd_hist": macd_hist,
            "macd_signal": macd_signal_label,
            "ichimoku_signal": ichimoku_signal,
            "adx": adx_val,
            "bb_position": bb_position,
            "atr": atr,
            "bb_lower": bb_lower,
            "bb_upper": bb_upper,
            "trend": trend,
            "volume_ratio": volume_ratio,
            "momentum_5bar": price_change_5,
            "vwap": vwap,
            "vwap_signal": vwap_signal,
        }

    # analyze_with_news() KALDIRILDI (v2.0)
    # Sebep: Bu metot haberleri, temel analizi, makro veriyi ve coordinator'ı
    # hep birlikte çağırıyordu. Ancak stock_bot.py._get_agent_decision() zaten
    # bunları ayrı ayrı yapıyor. Bu metot kullanılsaydı coordinator 2x çalışır,
    # çift sinyal üretilirdi. Tüm orkestrasyon stock_bot.py'da yönetiliyor.

