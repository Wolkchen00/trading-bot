"""
Stock Screener — Dinamik Hisse Tarayıcı

Her sabah piyasa açılmadan çalışarak en iyi fırsatları bulur:
  1. Volume spike tespiti (günlük ortalamaya göre)
  2. Pre-market gap analizi (gap-up/gap-down)
  3. Momentum skoru (RSI + MACD + EMA crossover)
  4. Sektör bazlı filtreleme
  5. Jeopolitik farkındalık (Hürmüz Boğazı, petrol, savaş riski)
"""
import requests
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from utils.logger import logger

# Alpaca imports
try:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest, StockSnapshotRequest
    from alpaca.data.timeframe import TimeFrame
    ALPACA_DATA_AVAILABLE = True
except ImportError:
    ALPACA_DATA_AVAILABLE = False
    logger.debug("alpaca stock data modülleri yüklenemedi")

# Teknik analiz
try:
    from ta.momentum import RSIIndicator
    from ta.trend import EMAIndicator, MACD
    TA_AVAILABLE = True
except ImportError:
    TA_AVAILABLE = False


# ============================================================
# HİSSE HAVUZLARI
# ============================================================
STOCK_UNIVERSE = {
    # TIER 1 — Yüksek hacim, güvenilir, swing için ideal
    "mega_cap": {
        "symbols": ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"],
        "weight": 0.40,
        "description": "Mega cap — düşük risk, güvenilir trend",
    },
    # TIER 2 — Orta hacim, iyi volatilite
    "growth": {
        "symbols": ["AMD", "SOFI", "PLTR", "COIN", "SQ", "SHOP", "CRWD"],
        "weight": 0.35,
        "description": "Growth — orta risk, yüksek kazanç potansiyeli",
    },
    # TIER 3 — Yüksek volatilite, momentum play
    "momentum": {
        "symbols": ["RIVN", "NIO", "LCID", "MARA", "RIOT", "SMCI"],
        "weight": 0.25,
        "description": "Momentum — yüksek risk, yüksek ödül",
    },
    # SEKTÖR ETF'leri — makro trend takibi
    "sector_etfs": {
        "symbols": ["SPY", "QQQ", "XLE", "XLF", "XLK", "ARKK"],
        "weight": 0,  # İşlem için değil, trend göstergesi olarak
        "description": "Sektör ETF — piyasa yönü göstergesi",
    },
}

# Jeopolitik risk hisseleri (Hürmüz Boğazı, petrol krizi vs.)
GEOPOLITICAL_WATCHLIST = {
    "oil_energy": ["XLE", "XOM", "CVX", "OXY", "USO"],
    "defense": ["LMT", "RTX", "NOC", "GD", "BA"],
    "gold_safe_haven": ["GLD", "GDX", "NEM", "GOLD"],
}


class StockScreener:
    """Dinamik hisse tarayıcı — her sabah en iyi fırsatları bulur."""

    def __init__(self, api_key: str = "", secret_key: str = ""):
        self.data_client = None
        if ALPACA_DATA_AVAILABLE and api_key:
            self.data_client = StockHistoricalDataClient(
                api_key=api_key, secret_key=secret_key
            )
            logger.info("StockScreener baslatildi — Alpaca Data API aktif")
        else:
            logger.info("StockScreener baslatildi — veri istemcisi yok, sınırlı mod")

        self.scan_cache = {}
        self.last_scan_time = None

    def get_all_symbols(self) -> List[str]:
        """Tüm izlenen hisselerin listesi."""
        symbols = []
        for tier_name, tier in STOCK_UNIVERSE.items():
            if tier["weight"] > 0:  # ETF'leri hariç tut
                symbols.extend(tier["symbols"])
        return symbols

    def get_tier_weight(self, symbol: str) -> float:
        """Hissenin tier ağırlığını döndür."""
        for tier in STOCK_UNIVERSE.values():
            if symbol in tier["symbols"]:
                return tier["weight"]
        return 0.15  # Varsayılan

    def morning_scan(self) -> List[Dict]:
        """
        Sabah taraması — piyasa açılmadan en iyi fırsatları bul.
        
        Returns:
            Sıralı fırsat listesi: [{symbol, score, reasons, tier, ...}]
        """
        logger.info("=" * 50)
        logger.info("  SABAH TARAMASI BASLIYOR")
        logger.info("=" * 50)

        opportunities = []
        all_symbols = self.get_all_symbols()

        for symbol in all_symbols:
            try:
                score, reasons = self._analyze_stock(symbol)
                if score > 0:
                    opportunities.append({
                        "symbol": symbol,
                        "score": score,
                        "reasons": reasons,
                        "tier_weight": self.get_tier_weight(symbol),
                    })
            except Exception as e:
                logger.debug(f"  Tarama hatası {symbol}: {e}")

        # Skora göre sırala
        opportunities.sort(key=lambda x: x["score"], reverse=True)

        logger.info(f"  Tarama tamamlandi: {len(opportunities)} firsat / {len(all_symbols)} hisse")
        for opp in opportunities[:5]:
            logger.info(
                f"    {opp['symbol']}: Skor={opp['score']:.0f} | "
                f"{', '.join(opp['reasons'][:3])}"
            )

        self.scan_cache = {o["symbol"]: o for o in opportunities}
        self.last_scan_time = datetime.now()
        return opportunities

    def _analyze_stock(self, symbol: str) -> tuple:
        """Tek bir hisseyi analiz et, skor ve nedenler döndür."""
        score = 0
        reasons = []

        if not self.data_client:
            return 0, []

        try:
            # Son 30 günlük günlük veri çek
            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Day,
                start=datetime.now() - timedelta(days=30),
            )
            bars = self.data_client.get_stock_bars(request)
            df = bars.df

            if hasattr(df.index, 'droplevel'):
                try:
                    df = df.droplevel("symbol")
                except (KeyError, ValueError):
                    pass

            if df.empty or len(df) < 10:
                return 0, ["Yetersiz veri"]

            close = df["close"]
            volume = df["volume"]
            current_price = float(close.iloc[-1])

            # --- Volume Spike ---
            avg_volume = float(volume.iloc[-20:].mean()) if len(volume) >= 20 else float(volume.mean())
            current_volume = float(volume.iloc[-1])
            if avg_volume > 0:
                vol_ratio = current_volume / avg_volume
                if vol_ratio >= 2.0:
                    score += 20
                    reasons.append(f"Volume spike {vol_ratio:.1f}x")
                elif vol_ratio >= 1.5:
                    score += 10
                    reasons.append(f"Volume artış {vol_ratio:.1f}x")

            if TA_AVAILABLE and len(close) >= 14:
                # --- RSI ---
                rsi = RSIIndicator(close, window=14).rsi().iloc[-1]
                if rsi < 30:
                    score += 25
                    reasons.append(f"RSI aşırı satım ({rsi:.0f})")
                elif rsi < 40:
                    score += 10
                    reasons.append(f"RSI düşük ({rsi:.0f})")
                elif rsi > 70:
                    score -= 15
                    reasons.append(f"RSI aşırı alım ({rsi:.0f})")

                # --- EMA Crossover ---
                if len(close) >= 21:
                    ema9 = EMAIndicator(close, window=9).ema_indicator().iloc[-1]
                    ema21 = EMAIndicator(close, window=21).ema_indicator().iloc[-1]
                    if ema9 > ema21:
                        score += 15
                        reasons.append("EMA9 > EMA21 (yükseliş)")
                    else:
                        score -= 10
                        reasons.append("EMA9 < EMA21 (düşüş)")

                # --- MACD ---
                macd = MACD(close)
                macd_line = macd.macd().iloc[-1]
                signal_line = macd.macd_signal().iloc[-1]
                if macd_line > signal_line:
                    score += 15
                    reasons.append("MACD pozitif crossover")

            # --- Fiyat trendi (son 5 gün) ---
            if len(close) >= 5:
                pct_5d = (float(close.iloc[-1]) - float(close.iloc[-5])) / float(close.iloc[-5]) * 100
                if -5 < pct_5d < -2:  # Düşüş sonrası dip fırsatı
                    score += 10
                    reasons.append(f"5 günlük düşüş ({pct_5d:.1f}%) — dip fırsatı")
                elif pct_5d > 3:  # Güçlü momentum
                    score += 5
                    reasons.append(f"Güçlü momentum ({pct_5d:+.1f}%)")

        except Exception as e:
            logger.debug(f"  {symbol} analiz hatası: {e}")
            return 0, [f"Hata: {e}"]

        return score, reasons

    def check_geopolitical_risk(self) -> Dict:
        """
        Jeopolitik risk kontrolü — petrol, Hürmüz Boğazı, savaş haberleri.
        Yüksek jeopolitik risk → defansif hisselere yönel.
        """
        # Bu metod news_analyzer ile entegre çalışacak
        return {
            "risk_level": "NORMAL",
            "oil_watchlist": GEOPOLITICAL_WATCHLIST["oil_energy"],
            "defense_watchlist": GEOPOLITICAL_WATCHLIST["defense"],
            "safe_haven": GEOPOLITICAL_WATCHLIST["gold_safe_haven"],
        }

    def get_scan_result(self, symbol: str) -> Optional[Dict]:
        """Önbellekten tarama sonucu döndür."""
        return self.scan_cache.get(symbol)
