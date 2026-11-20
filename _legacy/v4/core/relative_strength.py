"""
Relative Strength — Hisse Gucu Siralamasi (SPY'a Gore)

Profesyonel trader'larin en onemli araci: piyasadan guclu hisseyi sec.
  - RS > 0 : Piyasadan guclu (LONG adayi)
  - RS < 0 : Piyasadan zayif (SHORT adayi)

Composite RS = agirlikli ortalama (5g %40 + 10g %35 + 20g %25)
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional
from utils.logger import logger


class RelativeStrength:
    """Hisse gucu siralamasi — SPY'a gore relatif performans."""

    # RS esikleri
    LEADER_THRESHOLD = 0.02    # %2+ fazla getiri = leader
    LAGGARD_THRESHOLD = -0.02  # %2+ az getiri = laggard

    def __init__(self):
        self._spy_cache = None
        self._spy_cache_date = None
        logger.info("RelativeStrength baslatildi — SPY bazli RS ranking aktif")

    def calculate_rs(self, symbol_df: pd.DataFrame,
                      spy_df: pd.DataFrame,
                      periods: List[int] = None) -> Dict:
        """
        Hissenin SPY'a gore relatif gucunu hesapla.

        Args:
            symbol_df: Hisse OHLCV DataFrame
            spy_df: SPY OHLCV DataFrame
            periods: Karsilastirma periyotlari (gun)

        Returns:
            {
                "composite_rs": float,
                "is_leader": bool,
                "is_laggard": bool,
                "rs_5d": float,
                "rs_10d": float,
                "rs_20d": float,
                "rank_label": str,
            }
        """
        if periods is None:
            periods = [5, 10, 20]

        result = {
            "composite_rs": 0.0,
            "is_leader": False,
            "is_laggard": False,
            "rank_label": "NEUTRAL",
        }

        if symbol_df is None or spy_df is None:
            return result
        if len(symbol_df) < max(periods) + 1 or len(spy_df) < max(periods) + 1:
            return result

        try:
            stock_close = symbol_df["close"].astype(float)
            spy_close = spy_df["close"].astype(float)

            rs_scores = {}
            for period in periods:
                if len(stock_close) >= period + 1 and len(spy_close) >= period + 1:
                    stock_ret = (stock_close.iloc[-1] / stock_close.iloc[-period] - 1)
                    spy_ret = (spy_close.iloc[-1] / spy_close.iloc[-period] - 1)
                    rs_scores[f"rs_{period}d"] = round(stock_ret - spy_ret, 4)
                else:
                    rs_scores[f"rs_{period}d"] = 0.0

            # Composite RS = agirlikli ortalama
            weights = {5: 0.40, 10: 0.35, 20: 0.25}
            composite = sum(
                rs_scores.get(f"rs_{p}d", 0) * weights.get(p, 0.33)
                for p in periods
            )

            result.update(rs_scores)
            result["composite_rs"] = round(composite, 4)
            result["is_leader"] = composite > self.LEADER_THRESHOLD
            result["is_laggard"] = composite < self.LAGGARD_THRESHOLD

            if result["is_leader"]:
                result["rank_label"] = "LEADER"
            elif result["is_laggard"]:
                result["rank_label"] = "LAGGARD"
            else:
                result["rank_label"] = "NEUTRAL"

        except Exception as e:
            logger.debug(f"  RS hesaplama hatasi: {e}")

        return result

    def rank_symbols(self, symbols_data: Dict[str, pd.DataFrame],
                      spy_df: pd.DataFrame) -> List[Dict]:
        """
        Tum hisseleri RS'ye gore sirala.

        Args:
            symbols_data: {symbol: DataFrame} dict
            spy_df: SPY DataFrame

        Returns:
            RS skor sirasina gore sirali liste
        """
        rankings = []
        for symbol, df in symbols_data.items():
            rs = self.calculate_rs(df, spy_df)
            rs["symbol"] = symbol
            rankings.append(rs)

        # Composite RS'ye gore sirala (en guclu basta)
        rankings.sort(key=lambda x: x["composite_rs"], reverse=True)

        # Log top/bottom
        if rankings:
            top = rankings[:3]
            bottom = rankings[-3:]
            logger.info("  RS Ranking:")
            for r in top:
                logger.info(
                    f"    LEADER: {r['symbol']} RS={r['composite_rs']:+.2%}"
                )
            for r in bottom:
                if r["is_laggard"]:
                    logger.info(
                        f"    LAGGARD: {r['symbol']} RS={r['composite_rs']:+.2%}"
                    )

        return rankings

    def get_rs_signal_boost(self, rs_data: Dict, side: str = "LONG") -> int:
        """
        RS verisinden sinyal guveni boost'u hesapla.

        Args:
            rs_data: calculate_rs() ciktisi
            side: "LONG" veya "SHORT"

        Returns:
            Confidence boost (-10 to +15)
        """
        composite = rs_data.get("composite_rs", 0)

        if side == "LONG":
            if composite > 0.04:     # %4+ RS = cok guclu
                return 15
            elif composite > 0.02:   # %2+ RS = guclu
                return 10
            elif composite < -0.02:  # Piyasadan zayif — long riskli
                return -10
        elif side == "SHORT":
            if composite < -0.04:    # %4+ zayif = cok iyi short
                return 15
            elif composite < -0.02:  # %2+ zayif = iyi short
                return 10
            elif composite > 0.02:   # Piyasadan guclu — short riskli
                return -10

        return 0
