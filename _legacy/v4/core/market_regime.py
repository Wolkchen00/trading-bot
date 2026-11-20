"""
Market Regime Detector — 4 Rejim Algilama

Sadece VIX degil, SPY trend ve volatilite yapisiyla 4 rejim tespit eder:
  1. BULL_TREND   — Guclu yukselis trendi (agresif long)
  2. BEAR_TREND   — Guclu dusus trendi (agresif short)
  3. RANGE_BOUND  — Yatay piyasa (sadece cok guclu sinyallerde islem)
  4. CHOPPY       — Karisik (patience mode, az islem)

ADX (trend gucu) + Bollinger Band genisligi (volatilite) + EMA cross kullanir.
"""
import pandas as pd
import numpy as np
from typing import Dict
from utils.logger import logger

try:
    from ta.trend import ADXIndicator, EMAIndicator
    from ta.volatility import BollingerBands
    TA_AVAILABLE = True
except ImportError:
    TA_AVAILABLE = False


class MarketRegimeDetector:
    """4 rejimli piyasa durumu algilayici."""

    # ADX esikleri
    ADX_TREND_THRESHOLD = 25    # ADX > 25 = trend var
    ADX_STRONG_THRESHOLD = 35   # ADX > 35 = guclu trend

    # BB genislik esikleri
    BB_NARROW = 0.04            # %4'ten dar = sikisma (breakout yakin)
    BB_WIDE = 0.08              # %8'den genis = yuksek volatilite

    def __init__(self):
        self.current_regime = "UNKNOWN"
        self.regime_confidence = 0
        self.regime_history = []
        logger.info("MarketRegimeDetector baslatildi — 4 rejim algilama aktif")

    def detect_regime(self, spy_df: pd.DataFrame, vix: float = 0) -> Dict:
        """
        SPY verisiyle piyasa rejimini tespit et.

        Args:
            spy_df: SPY gunluk OHLCV DataFrame (min 30 gun)
            vix: VIX degeri (opsiyonel, ek bilgi)

        Returns:
            {
                "regime": str,          # BULL_TREND, BEAR_TREND, RANGE_BOUND, CHOPPY
                "confidence": int,      # 0-100
                "adx": float,
                "bb_width": float,
                "trend_direction": str, # UP, DOWN, FLAT
                "description": str,
                "trading_mode": str,    # AGGRESSIVE, NORMAL, CAUTIOUS, MINIMAL
            }
        """
        result = {
            "regime": "UNKNOWN",
            "confidence": 0,
            "adx": 0,
            "bb_width": 0,
            "trend_direction": "FLAT",
            "description": "Veri yetersiz",
            "trading_mode": "CAUTIOUS",
        }

        if spy_df is None or len(spy_df) < 30 or not TA_AVAILABLE:
            return result

        try:
            close = spy_df["close"].astype(float)
            high = spy_df["high"].astype(float)
            low = spy_df["low"].astype(float)

            # === 1. ADX — Trend gucu ===
            adx_ind = ADXIndicator(high, low, close, window=14)
            adx = float(adx_ind.adx().iloc[-1])

            # === 2. EMA Cross — Trend yonu ===
            ema9 = float(EMAIndicator(close, window=9).ema_indicator().iloc[-1])
            ema21 = float(EMAIndicator(close, window=21).ema_indicator().iloc[-1])
            ema50 = float(EMAIndicator(close, window=50).ema_indicator().iloc[-1]) if len(close) >= 50 else ema21

            trend_up = ema9 > ema21 > ema50
            trend_down = ema9 < ema21 < ema50

            if trend_up:
                trend_direction = "UP"
            elif trend_down:
                trend_direction = "DOWN"
            else:
                trend_direction = "FLAT"

            # === 3. Bollinger Band genisligi — Volatilite ===
            bb = BollingerBands(close, window=20, window_dev=2)
            bb_upper = float(bb.bollinger_hband().iloc[-1])
            bb_lower = float(bb.bollinger_lband().iloc[-1])
            bb_mid = float(bb.bollinger_mavg().iloc[-1])
            bb_width = (bb_upper - bb_lower) / bb_mid if bb_mid > 0 else 0

            # === 4. Rejim belirleme ===
            confidence = 50  # Baslangic

            if adx > self.ADX_TREND_THRESHOLD:
                if trend_direction == "UP":
                    regime = "BULL_TREND"
                    confidence = min(50 + int(adx), 95)
                    description = f"Yukselis trendi (ADX={adx:.0f}, EMA9>21>50)"
                    trading_mode = "AGGRESSIVE"
                elif trend_direction == "DOWN":
                    regime = "BEAR_TREND"
                    confidence = min(50 + int(adx), 95)
                    description = f"Dusus trendi (ADX={adx:.0f}, EMA9<21<50)"
                    trading_mode = "AGGRESSIVE"
                else:
                    # ADX yuksek ama EMA'lar karisik = gecis donemi
                    regime = "CHOPPY"
                    confidence = 40
                    description = f"Gecis donemi (ADX={adx:.0f} ama EMA karisik)"
                    trading_mode = "CAUTIOUS"
            else:
                # ADX dusuk = trend yok
                if bb_width < self.BB_NARROW:
                    regime = "RANGE_BOUND"
                    confidence = 60
                    description = f"Sikisma (ADX={adx:.0f}, BB dar={bb_width:.1%})"
                    trading_mode = "MINIMAL"
                elif bb_width > self.BB_WIDE:
                    regime = "CHOPPY"
                    confidence = 55
                    description = f"Karisik volatil (ADX={adx:.0f}, BB genis={bb_width:.1%})"
                    trading_mode = "CAUTIOUS"
                else:
                    regime = "RANGE_BOUND"
                    confidence = 50
                    description = f"Yatay piyasa (ADX={adx:.0f})"
                    trading_mode = "NORMAL"

            # VIX ek bilgisi
            if vix > 30:
                trading_mode = "MINIMAL"
                confidence = max(confidence - 10, 20)
                description += f" | VIX={vix:.0f} YUKSEK"
            elif vix > 20:
                description += f" | VIX={vix:.0f} orta"

            result = {
                "regime": regime,
                "confidence": confidence,
                "adx": round(adx, 1),
                "bb_width": round(bb_width, 4),
                "trend_direction": trend_direction,
                "description": description,
                "trading_mode": trading_mode,
            }

            # Rejim degisti mi?
            if regime != self.current_regime:
                logger.info(
                    f"  REJIM DEGISIKLIGI: {self.current_regime} -> {regime} "
                    f"| {description}"
                )
                self.regime_history.append({
                    "from": self.current_regime,
                    "to": regime,
                    "timestamp": pd.Timestamp.now().isoformat(),
                })

            self.current_regime = regime
            self.regime_confidence = confidence

        except Exception as e:
            logger.debug(f"  Rejim algilama hatasi: {e}")

        return result

    def get_confidence_modifier(self, side: str = "LONG") -> Dict:
        """
        Rejime gore guven esigi ve pozisyon ayarlari.

        Returns:
            {
                "buy_conf_adj": int,      # BUY guven ekleme/cikarma
                "short_conf_adj": int,     # SHORT guven ekleme/cikarma
                "max_positions_adj": int,  # Max pozisyon ayari
                "position_size_mult": float, # Pozisyon boyutu carpani
            }
        """
        regime = self.current_regime

        if regime == "BULL_TREND":
            return {
                "buy_conf_adj": -5,        # BUY icin daha dusuk esik
                "short_conf_adj": 10,      # SHORT icin daha yuksek esik
                "max_positions_adj": 1,    # +1 pozisyon izni
                "position_size_mult": 1.1, # %10 daha buyuk
            }
        elif regime == "BEAR_TREND":
            return {
                "buy_conf_adj": 15,        # BUY icin cok yuksek esik
                "short_conf_adj": -10,     # SHORT icin daha dusuk esik
                "max_positions_adj": -1,   # -1 pozisyon
                "position_size_mult": 0.8, # %20 daha kucuk
            }
        elif regime == "RANGE_BOUND":
            return {
                "buy_conf_adj": 5,
                "short_conf_adj": 5,
                "max_positions_adj": 0,
                "position_size_mult": 0.9,
            }
        elif regime == "CHOPPY":
            return {
                "buy_conf_adj": 15,        # Karisik piyasada cok seici ol
                "short_conf_adj": 15,
                "max_positions_adj": -1,
                "position_size_mult": 0.7, # %30 kucuk pozisyon
            }

        return {
            "buy_conf_adj": 0,
            "short_conf_adj": 0,
            "max_positions_adj": 0,
            "position_size_mult": 1.0,
        }
