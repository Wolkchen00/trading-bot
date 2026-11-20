"""
Volume Analyzer — Anormal Hacim ve Smart Money Algılama

İstatistiksel anomali algılama ile büyük kurumsal hareketleri tespit eder:
  1. Z-score tabanlı unusual volume tespiti
  2. Volume-Price Divergence (fiyat düşer, hacim artar = akümülasyon)
  3. Erken seans hacim spike (smart money genellikle erken hareket eder)
  4. Dağıtım/Biriktirme (Accumulation/Distribution) tespiti

Hem LONG hem SHORT sinyalleri destekler.
"""
import numpy as np
import pandas as pd
from typing import Dict
from utils.logger import logger


class VolumeAnalyzer:
    """Anormal hacim ve smart money algılama motoru."""

    # Z-score eşikleri
    UNUSUAL_THRESHOLD = 2.0      # σ > 2.0 = anormal hacim
    HIGH_UNUSUAL_THRESHOLD = 3.0  # σ > 3.0 = çok yüksek anormal hacim

    # Erken seans (ilk 30dk) smart money eşiği — daha düşük
    EARLY_SESSION_THRESHOLD = 1.5

    def __init__(self):
        logger.info("VolumeAnalyzer başlatıldı — Smart Money algılama aktif")

    def analyze_volume(self, df: pd.DataFrame, lookback: int = 20) -> Dict:
        """
        Hacim analizi yap.

        Args:
            df: OHLCV DataFrame
            lookback: Karşılaştırma penceresi (gün)

        Returns:
            {
                "unusual": bool,
                "z_score": float,
                "volume_ratio": float,
                "signal": "ACCUMULATION" | "DISTRIBUTION" | "NORMAL",
                "confidence_boost": int,     # Agent sinyaline eklenecek güven
                "early_spike": bool,
                "block_trade_detected": bool,
                "reasoning": str,
            }
        """
        result = {
            "unusual": False,
            "z_score": 0.0,
            "volume_ratio": 1.0,
            "signal": "NORMAL",
            "confidence_boost": 0,
            "early_spike": False,
            "block_trade_detected": False,
            "reasoning": "Hacim normal",
        }

        if df is None or len(df) < lookback + 5:
            return result

        if "volume" not in df.columns:
            return result

        try:
            volume = df["volume"].astype(float)
            close = df["close"].astype(float)

            current_vol = volume.iloc[-1]
            recent_vol = volume.tail(lookback)

            if current_vol <= 0 or recent_vol.mean() <= 0:
                return result

            # === 1. Z-SCORE HESAPLAMA ===
            mean_vol = recent_vol.mean()
            std_vol = recent_vol.std()

            if std_vol > 0:
                z_score = (current_vol - mean_vol) / std_vol
            else:
                z_score = 0.0

            volume_ratio = current_vol / mean_vol if mean_vol > 0 else 1.0

            result["z_score"] = round(z_score, 2)
            result["volume_ratio"] = round(volume_ratio, 2)

            # === 2. ANORMAL HACİM TESPİTİ ===
            is_unusual = z_score >= self.UNUSUAL_THRESHOLD
            is_very_unusual = z_score >= self.HIGH_UNUSUAL_THRESHOLD

            # === 3. ERKEN SEANS SPIKE (Smart Money) ===
            early_spike = False
            if hasattr(df.index, 'hour'):
                try:
                    last_hour = df.index[-1].hour
                    last_minute = df.index[-1].minute
                    # Market açılışının ilk 30 dk (09:30-10:00 ET)
                    if last_hour == 9 or (last_hour == 10 and last_minute < 30):
                        early_spike = z_score >= self.EARLY_SESSION_THRESHOLD
                except (AttributeError, IndexError):
                    pass

            result["early_spike"] = early_spike

            # === 4. FIYAT-HACİM DİVERGENCE ===
            price_change = 0.0
            if len(close) >= 3:
                price_change = (close.iloc[-1] - close.iloc[-3]) / close.iloc[-3] * 100

            # Volume-Price Divergence analizi
            signal = "NORMAL"
            confidence_boost = 0
            reasons = []

            if is_unusual or is_very_unusual:
                result["unusual"] = True
                reasons.append(f"Vol:{volume_ratio:.1f}x (σ={z_score:.1f})")

                if price_change > 0.5 and is_unusual:
                    # Fiyat yukarı + yüksek hacim = güçlü alım (akümülasyon)
                    signal = "ACCUMULATION"
                    confidence_boost = 15 if is_very_unusual else 10
                    reasons.append(f"Fiyat+{price_change:.1f}% → Akümülasyon")

                elif price_change < -0.5 and is_unusual:
                    # Fiyat aşağı + yüksek hacim = güçlü satış (dağıtım)
                    signal = "DISTRIBUTION"
                    confidence_boost = 15 if is_very_unusual else 10
                    reasons.append(f"Fiyat{price_change:.1f}% → Dağıtım")

                elif abs(price_change) <= 0.5 and is_very_unusual:
                    # Fiyat sabit + çok yüksek hacim = yakında büyük hareket
                    signal = "ACCUMULATION"  # Genellikle akümülasyon
                    confidence_boost = 8
                    reasons.append("Fiyat sabit + Hacim PATLAMA → Gizli birikim?")

                if early_spike:
                    confidence_boost += 5
                    reasons.append("⚡ Erken seans spike (Smart Money?)")

            # === 5. BLOK TİCARET TESPİTİ ===
            # Son barda hacim, ortalamadan 5x+ fazlaysa → büyük kurumsal işlem
            block_detected = volume_ratio >= 5.0
            if block_detected:
                result["block_trade_detected"] = True
                confidence_boost += 5
                reasons.append(f"🏦 BLOK TİCARET! {volume_ratio:.0f}x ortalama")

            # === 6. ON-BALANCE VOLUME TREND ===
            try:
                obv = self._calculate_obv_trend(close, volume, lookback=10)
                if obv["divergence"]:
                    confidence_boost += 5
                    reasons.append(f"OBV Divergence: {obv['type']}")
            except Exception:
                pass

            result["signal"] = signal
            result["confidence_boost"] = min(confidence_boost, 25)
            result["reasoning"] = " | ".join(reasons) if reasons else "Hacim normal"

            if is_unusual:
                logger.info(
                    f"  📊 Volume: {signal} | "
                    f"Vol:{volume_ratio:.1f}x σ={z_score:.1f} | "
                    f"Boost:+{confidence_boost} | "
                    f"{result['reasoning']}"
                )

        except Exception as e:
            logger.debug(f"  Volume analiz hatası: {e}")

        return result

    def _calculate_obv_trend(self, close: pd.Series, volume: pd.Series,
                              lookback: int = 10) -> Dict:
        """On-Balance Volume trend ve divergence analizi."""
        obv = []
        obv_val = 0
        for i in range(1, len(close)):
            if close.iloc[i] > close.iloc[i-1]:
                obv_val += volume.iloc[i]
            elif close.iloc[i] < close.iloc[i-1]:
                obv_val -= volume.iloc[i]
            obv.append(obv_val)

        if len(obv) < lookback:
            return {"divergence": False, "type": "NONE"}

        recent_obv = obv[-lookback:]
        recent_price = close.tail(lookback).values

        # OBV artıyor ama fiyat düşüyor = Bullish divergence
        obv_rising = recent_obv[-1] > recent_obv[0]
        price_falling = recent_price[-1] < recent_price[0]

        # OBV düşüyor ama fiyat artıyor = Bearish divergence
        obv_falling = recent_obv[-1] < recent_obv[0]
        price_rising = recent_price[-1] > recent_price[0]

        if obv_rising and price_falling:
            return {"divergence": True, "type": "BULLISH"}
        elif obv_falling and price_rising:
            return {"divergence": True, "type": "BEARISH"}

        return {"divergence": False, "type": "NONE"}
