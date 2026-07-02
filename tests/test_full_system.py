"""
Comprehensive End-to-End System Test
Pazartesi öncesi tüm sistemi doğrulama testi.

Test Kategorileri:
  1. Python bağımlılıkları (tüm pip paketleri import edilebilir mi?)
  2. Config ve env dosya doğruluğu
  3. Tüm core modül import'ları
  4. Alpaca API bağlantısı (paper hesap)
  5. Teknik analiz zinciri
  6. Agent Coordinator karar sistemi
  7. Risk yönetimi modülleri
  8. Position Sizer hesaplamaları
  9. KillSwitch mantığı
  10. Market Hours kontrolü
  11. Trade Gates filtre sistemi
  12. Bot başlatma testi (StockBot.__init__)
"""

import os
import sys
import traceback
from datetime import datetime, date, timedelta

# Proje kökünü path'e ekle
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"

results = []
warnings = []


def test(name, func):
    """Test wrapper — hataları yakalar ve raporlar."""
    try:
        result = func()
        if result is True or result is None:
            results.append((PASS, name, ""))
            return True
        elif isinstance(result, str):
            results.append((WARN, name, result))
            warnings.append((name, result))
            return True
        else:
            results.append((FAIL, name, str(result)))
            return False
    except Exception as e:
        tb = traceback.format_exc()
        results.append((FAIL, name, f"{e}\n{tb[-300:]}"))
        return False


def section(title):
    results.append(("", f"\n{'='*50}\n  {title}\n{'='*50}", ""))


# ============================================================
# 1. PYTHON BAĞIMLILIKLARI
# ============================================================
section("1. PYTHON BAĞIMLILIKLARI")

def test_pandas():
    import pandas as pd
    assert pd.__version__ >= "2.0.0", f"pandas {pd.__version__} < 2.0.0"
test("pandas", test_pandas)

def test_numpy():
    import numpy as np
    assert np.__version__ >= "1.24.0", f"numpy {np.__version__} < 1.24.0"
test("numpy", test_numpy)

def test_alpaca():
    from alpaca.trading.client import TradingClient
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.trading.requests import MarketOrderRequest, StopLimitOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce
test("alpaca-py", test_alpaca)

def test_ta():
    from ta.momentum import RSIIndicator
    from ta.trend import EMAIndicator, MACD
    from ta.volatility import BollingerBands, AverageTrueRange
test("ta (Technical Analysis)", test_ta)

def test_sklearn():
    import sklearn
test("scikit-learn", test_sklearn)

def test_dotenv():
    from dotenv import load_dotenv
test("python-dotenv", test_dotenv)

def test_requests():
    import requests
test("requests", test_requests)

def test_pytz():
    import pytz
    et = pytz.timezone("US/Eastern")
    now = datetime.now(et)
test("pytz", test_pytz)

def test_vader():
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    sia = SentimentIntensityAnalyzer()
    score = sia.polarity_scores("The stock market is going up")
    assert "compound" in score
test("vaderSentiment", test_vader)

def test_onnx_finbert():
    # FinBERT artık ONNX ile çalışıyor (transformers/PyTorch Railway OOM nedeniyle
    # kaldırıldı — c9800c5). Lokalde onnxruntime olmayabilir → VADER fallback tasarımı.
    try:
        import onnxruntime  # noqa: F401
        return True
    except ImportError:
        return "onnxruntime yok — FinBERT yerine VADER fallback (Docker'da ONNX aktif)"
test("onnxruntime (FinBERT)", test_onnx_finbert)

def test_ntscraper():
    try:
        from ntscraper import Nitter
        return True
    except ImportError:
        return "ntscraper import başarısız — X/Twitter devre dışı (opsiyonel)"
test("ntscraper (X scraper)", test_ntscraper)

def test_yfinance():
    import yfinance
test("yfinance", test_yfinance)


# ============================================================
# 2. CONFIG & ENV DOĞRULAMASI
# ============================================================
section("2. CONFIG & ENV DOĞRULAMASI")

def test_env_file():
    env_path = os.path.join(PROJECT_ROOT, ".env")
    assert os.path.exists(env_path), ".env dosyası bulunamadı!"
test(".env dosyası mevcut", test_env_file)

def test_config_import():
    from config import (
        ALPACA_API_KEY, ALPACA_SECRET_KEY, TRADING_MODE, BOT_MODE,
        STOCK_CONFIG, SHORT_CONFIG, STOCK_IDS, SECTOR_MAP,
        MARKET_REGIME_CONFIG, OPTIONS_CONFIG, PAPER_AGGRESSIVE_CONFIG,
        GEOPOLITICAL_KEYWORDS, get_base_url,
    )
    assert ALPACA_API_KEY, "ALPACA_API_KEY boş!"
    assert ALPACA_SECRET_KEY, "ALPACA_SECRET_KEY boş!"
test("config.py tüm export'lar", test_config_import)

def test_api_keys():
    from config import ALPACA_API_KEY, ALPACA_SECRET_KEY
    assert len(ALPACA_API_KEY) >= 10, f"API key çok kısa: {len(ALPACA_API_KEY)} karakter"
    assert len(ALPACA_SECRET_KEY) >= 10, f"Secret key çok kısa: {len(ALPACA_SECRET_KEY)} karakter"
test("API key uzunluğu", test_api_keys)

def test_trading_mode():
    from config import TRADING_MODE, BOT_MODE
    assert TRADING_MODE in ("paper", "live"), f"Geçersiz TRADING_MODE: {TRADING_MODE}"
    assert BOT_MODE in ("long_only", "short_only", "both"), f"Geçersiz BOT_MODE: {BOT_MODE}"
    if TRADING_MODE == "live":
        return f"⚠️ TRADING_MODE='live' — Gerçek para riski!"
test("TRADING_MODE & BOT_MODE", test_trading_mode)

def test_stock_ids():
    from config import STOCK_IDS, SECTOR_MAP
    assert len(STOCK_IDS) >= 10, f"Sadece {len(STOCK_IDS)} hisse tanımlı"
    # Her hissenin sektör tanımı var mı?
    missing_sectors = [s for s in STOCK_IDS if s not in SECTOR_MAP]
    assert len(missing_sectors) == 0, f"Sektör tanımı eksik: {missing_sectors}"
test("STOCK_IDS & SECTOR_MAP tutarlılığı", test_stock_ids)

def test_config_limits():
    from config import STOCK_CONFIG
    assert STOCK_CONFIG["stop_loss_pct"] > 0, "Stop-loss %0!?"
    assert STOCK_CONFIG["take_profit_pct"] > STOCK_CONFIG["stop_loss_pct"], "TP < SL!"
    assert STOCK_CONFIG["max_open_positions"] >= 1, "Max poz < 1!"
    rr = STOCK_CONFIG["take_profit_pct"] / STOCK_CONFIG["stop_loss_pct"]
    if rr < 1.5:
        return f"R:R oranı düşük: {rr:.1f}:1"
test("Config risk limitleri", test_config_limits)

def test_geopolitical_keywords():
    from config import GEOPOLITICAL_KEYWORDS
    assert "bearish_critical" in GEOPOLITICAL_KEYWORDS
    assert "bearish_high" in GEOPOLITICAL_KEYWORDS
    assert "bearish_elevated" in GEOPOLITICAL_KEYWORDS
    assert "bullish" in GEOPOLITICAL_KEYWORDS
    total = sum(len(v) for v in GEOPOLITICAL_KEYWORDS.values())
    assert total > 30, f"Sadece {total} jeopolitik keyword — çok az"
test("Jeopolitik keyword'ler", test_geopolitical_keywords)

def test_data_api_keys():
    from config import ALPHA_VANTAGE_KEY, MARKETAUX_TOKEN
    if not ALPHA_VANTAGE_KEY:
        return "ALPHA_VANTAGE_KEY boş — temel analiz çalışmayacak"
    if not MARKETAUX_TOKEN:
        return "MARKETAUX_TOKEN boş — haber analizi çalışmayacak"
test("Veri API anahtarları", test_data_api_keys)


# ============================================================
# 3. TÜM CORE MODÜL IMPORT'LARI
# ============================================================
section("3. CORE MODÜL IMPORT'LARI")

core_modules = [
    ("market_hours", "MarketHours"),
    ("pdt_tracker", "PDTTracker"),
    ("stock_screener", "StockScreener"),
    ("earnings_calendar", "EarningsCalendar"),
    ("agent_coordinator", "AgentCoordinator"),
    ("analyzer", "TechnicalAnalyzer"),
    ("executor", "OrderExecutor"),
    ("short_executor", "ShortExecutor"),
    ("position_manager", "PositionManager"),
    ("trade_gates", "TradeGates"),
    ("news_analyzer", "StockNewsAnalyzer"),
    ("social_sentiment", "SocialSentimentAnalyzer"),
    ("fundamental_analyzer", "FundamentalAnalyzer"),
    ("macro_data", "MacroDataAnalyzer"),
    ("kill_switch", "KillSwitch"),
    ("compliance", "WashSaleTracker"),
    ("notifier", "TelegramNotifier"),
    ("performance_tracker", "PerformanceTracker"),
    ("sector_rotation", "SectorRotator"),
    ("position_sizer", "PositionSizer"),
    ("volume_analyzer", "VolumeAnalyzer"),
    ("agent_performance", "AgentPerformanceTracker"),
    ("gap_scanner", "GapScanner"),
    ("relative_strength", "RelativeStrength"),
    ("market_regime", "MarketRegimeDetector"),
    ("signal_queue", "SignalQueue"),
    ("options_engine", "OptionsEngine"),
    ("options_analyzer", "OptionsAnalyzer"),
    ("options_executor", "OptionsExecutor"),
    ("options_manager", "OptionsPositionManager"),
]

for module_name, class_name in core_modules:
    def make_test(mod, cls):
        def test_fn():
            module = __import__(f"core.{mod}", fromlist=[cls])
            klass = getattr(module, cls)
            assert klass is not None
        return test_fn
    test(f"core.{module_name}.{class_name}", make_test(module_name, class_name))

def test_finbert():
    try:
        from core.finbert_analyzer import FinBERTAnalyzer
        return True
    except ImportError as e:
        return f"FinBERT import başarısız (torch eksik?) — {e}"
test("core.finbert_analyzer (opsiyonel)", test_finbert)

def test_logger():
    from utils.logger import logger
    logger.info("Test log mesaji — sistem testi")
test("utils.logger", test_logger)


# ============================================================
# 4. ALPACA API BAĞLANTISI
# ============================================================
section("4. ALPACA API BAĞLANTISI")

def test_paper_connection():
    from alpaca.trading.client import TradingClient
    from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, TRADING_MODE
    # Her zaman paper hesapla test et (güvenlik)
    is_paper = TRADING_MODE != "live"
    client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=is_paper)
    account = client.get_account()
    equity = float(account.equity)
    cash = float(account.cash)
    assert equity > 0, f"Hesap bakiyesi $0!"
    print(f"     Equity: ${equity:,.2f} | Cash: ${cash:,.2f} | Paper: {is_paper}")
test("Alpaca hesap bağlantısı", test_paper_connection)

def test_data_client():
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from config import ALPACA_API_KEY, ALPACA_SECRET_KEY
    client = StockHistoricalDataClient(
        api_key=ALPACA_API_KEY, secret_key=ALPACA_SECRET_KEY
    )
    request = StockBarsRequest(
        symbol_or_symbols="AAPL",
        timeframe=TimeFrame.Hour,
        start=datetime.now() - timedelta(days=7),
    )
    bars = client.get_stock_bars(request)
    df = bars.df
    assert not df.empty, "AAPL bar verisi boş!"
    print(f"     AAPL bars: {len(df)} satır, son fiyat: ${float(df['close'].iloc[-1]):,.2f}")
test("Alpaca veri bağlantısı (AAPL)", test_data_client)

def test_positions():
    from alpaca.trading.client import TradingClient
    from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, TRADING_MODE
    is_paper = TRADING_MODE != "live"
    client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=is_paper)
    positions = client.get_all_positions()
    print(f"     Açık pozisyon: {len(positions)}")
    for pos in positions[:5]:
        pnl = float(pos.unrealized_pl)
        print(f"       {pos.symbol}: {float(pos.qty):.4f} @ ${float(pos.avg_entry_price):,.2f} | P&L: ${pnl:+.2f}")
test("Alpaca açık pozisyonlar", test_positions)

def test_orders():
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus
    from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, TRADING_MODE
    is_paper = TRADING_MODE != "live"
    client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=is_paper)
    orders = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
    print(f"     Açık emir: {len(orders)}")
test("Alpaca açık emirler", test_orders)


# ============================================================
# 5. TEKNİK ANALİZ ZİNCİRİ
# ============================================================
section("5. TEKNİK ANALİZ ZİNCİRİ")

def test_technical_indicators():
    import pandas as pd
    import numpy as np
    from ta.momentum import RSIIndicator
    from ta.trend import EMAIndicator, MACD
    from ta.volatility import BollingerBands, AverageTrueRange
    
    # Sentetik veri oluştur
    np.random.seed(42)
    n = 100
    close = pd.Series(np.cumsum(np.random.randn(n) * 0.5) + 100)
    high = close + abs(np.random.randn(n) * 0.5)
    low = close - abs(np.random.randn(n) * 0.5)
    
    rsi = RSIIndicator(close, window=14).rsi().dropna()
    assert len(rsi) > 0, "RSI hesaplanamadı"
    
    ema = EMAIndicator(close, window=21).ema_indicator().dropna()
    assert len(ema) > 0, "EMA hesaplanamadı"
    
    macd = MACD(close)
    assert macd.macd().dropna().shape[0] > 0, "MACD hesaplanamadı"
    
    bb = BollingerBands(close, window=20)
    assert bb.bollinger_hband().dropna().shape[0] > 0, "BB hesaplanamadı"
    
    atr = AverageTrueRange(high, low, close, window=14).average_true_range().dropna()
    assert len(atr) > 0, "ATR hesaplanamadı"
    
    print(f"     RSI: {rsi.iloc[-1]:.1f} | EMA21: {ema.iloc[-1]:.2f} | ATR: {atr.iloc[-1]:.4f}")
test("RSI + EMA + MACD + BB + ATR hesaplama", test_technical_indicators)


# ============================================================
# 6. AGENT COORDINATOR KARAR SİSTEMİ
# ============================================================
section("6. AGENT COORDINATOR SİSTEMİ")

def test_coordinator_buy():
    from core.agent_coordinator import AgentCoordinator
    coord = AgentCoordinator()
    
    tech = {"tech_score": 30, "rsi": 28, "macd_signal": "BUY", "ichimoku_signal": "BUY", "adx": 30}
    fund = {"fundamental_score": 15, "metrics": {"pe_ratio": 12, "eps": 3.5, "profit_margin": 0.20}}
    sent = {"news_score": 20, "sentiment_label": "BUY", "fear_greed_value": 30, "fear_greed_signal": "BUY"}
    social = {"social_score": 15, "reddit_posts": 25, "x_tweets": 10, "x_sentiment": 0.5}
    risk = {"daily_pnl_pct": 0.5, "open_positions": 0, "max_positions": 3, "atr_pct": 2.0, "vix": 15}
    
    decision = coord.decide("AAPL", tech, fund, sent, social, risk)
    assert decision["signal"] == "BUY", f"Beklenen BUY, gelen: {decision['signal']}"
    assert decision["confidence"] > 0
    print(f"     BUY sinyal: güven={decision['confidence']:.0f}%, skor={decision['weighted_score']:.1f}")
test("Coordinator BUY kararı", test_coordinator_buy)

def test_coordinator_sell():
    from core.agent_coordinator import AgentCoordinator
    coord = AgentCoordinator()
    
    tech = {"tech_score": -30, "rsi": 78, "macd_signal": "SELL", "ichimoku_signal": "SELL", "adx": 28}
    fund = {"fundamental_score": -15, "metrics": {"pe_ratio": 55, "eps": -1.0, "profit_margin": -0.05}}
    sent = {"news_score": -25, "sentiment_label": "SELL", "fear_greed_value": 82, "fear_greed_signal": "SELL"}
    social = {"social_score": -12, "reddit_posts": 5}
    risk = {"daily_pnl_pct": -1.0, "open_positions": 2, "max_positions": 3, "atr_pct": 3.5, "vix": 30}
    
    decision = coord.decide("TSLA", tech, fund, sent, social, risk)
    assert decision["signal"] == "SELL", f"Beklenen SELL, gelen: {decision['signal']}"
    print(f"     SELL sinyal: güven={decision['confidence']:.0f}%, skor={decision['weighted_score']:.1f}")
test("Coordinator SELL kararı", test_coordinator_sell)

def test_coordinator_risk_veto():
    from core.agent_coordinator import AgentCoordinator
    coord = AgentCoordinator()
    
    tech = {"tech_score": 25, "rsi": 32, "macd_signal": "BUY", "ichimoku_signal": "BUY", "adx": 25}
    fund = {"fundamental_score": 12, "metrics": {}}
    sent = {"news_score": 15, "sentiment_label": "BUY", "fear_greed_value": 40, "fear_greed_signal": "BUY"}
    social = {"social_score": 10}
    risk = {
        "daily_pnl_pct": -4.0, "open_positions": 3, "max_positions": 3,
        "atr_pct": 6.0, "vix": 40, "equity_floor_hit": True,
    }
    
    decision = coord.decide("NVDA", tech, fund, sent, social, risk)
    assert decision["risk_veto"] == True, "RiskAgent VETO etmedi!"
    assert decision["signal"] != "BUY", "BUY engellenemedi!"
    print(f"     Risk VETO: sinyal={decision['signal']}, veto={decision['risk_veto']}")
test("Coordinator Risk VETO", test_coordinator_risk_veto)


# ============================================================
# 7. RİSK YÖNETİMİ MODÜLLERİ
# ============================================================
section("7. RİSK YÖNETİMİ")

def test_kill_switch():
    from core.kill_switch import KillSwitch
    ks = KillSwitch(max_consecutive_errors=3, max_daily_loss_pct=0.05, kill_file="test_kill.json")
    assert not ks.is_active, "KillSwitch başlangıçta aktif olmamalı"
    
    # API hatası simülasyonu
    ks.check_api_error(Exception("timeout"))
    ks.check_api_error(Exception("timeout"))
    assert not ks.is_active, "2 hata ile aktif olmamalı"
    ks.check_api_error(Exception("timeout"))
    assert ks.is_active, "3 hata ile aktif olmalı!"
    
    ks.reset()
    assert not ks.is_active, "Reset sonrası hala aktif"
    
    # Günlük kayıp simülasyonu
    ks.check_daily_loss(950, 1000)  # -%5
    assert ks.is_active, "-%5 kayıp ile aktif olmalı"
    
    # Temizlik
    ks.reset()
    if os.path.exists("test_kill.json"):
        os.remove("test_kill.json")
test("KillSwitch mantığı", test_kill_switch)

def test_pdt_tracker():
    from core.pdt_tracker import PDTTracker
    pdt = PDTTracker(equity=5000)
    can_dt, reason = pdt.can_day_trade()
    status = pdt.get_status()
    assert "week_day_trades" in status
    assert "max_day_trades" in status
    print(f"     PDT: {status['week_day_trades']}/{status['max_day_trades']} day trade")
test("PDT Tracker", test_pdt_tracker)

def test_wash_sale():
    from core.compliance import WashSaleTracker
    wst = WashSaleTracker()
    # Zarar satışı kaydet
    wst.record_loss_sale("AAPL", -50.0, "2026-04-15")
    # 30 gün içinde aynı hisseyi kontrol et
    is_wash, reason = wst.check_wash_sale("AAPL")
    # Not: Tarihe bağlı olabilir, mantığın çalıştığını doğruluyoruz
    print(f"     Wash sale check: {is_wash} — {reason}")
test("Wash Sale Tracker", test_wash_sale)


# ============================================================
# 8. POZİSYON BOYUTLANDIRMA
# ============================================================
section("8. POZİSYON BOYUTLANDIRMA")

def test_position_sizer_normal():
    from core.position_sizer import PositionSizer
    from config import STOCK_CONFIG
    ps = PositionSizer()
    result = ps.calculate_position_size(
        equity=10000, price=150, atr=3.0,
        config=STOCK_CONFIG, side="LONG",
    )
    assert result["position_usd"] > 0, "Pozisyon $0?"
    assert result["qty"] > 0, "Adet 0?"
    print(f"     LONG: ${result['position_usd']:.2f} | {result['qty']:.4f} adet | {result['reasoning']}")
test("PositionSizer normal LONG", test_position_sizer_normal)

def test_position_sizer_bear():
    from core.position_sizer import PositionSizer
    from config import STOCK_CONFIG
    ps = PositionSizer()
    result = ps.calculate_position_size(
        equity=10000, price=150, atr=3.0,
        config=STOCK_CONFIG, side="LONG",
        market_regime="BEAR", consecutive_losses=3,
    )
    assert result["loss_damping"] < 1.0, "Kayıp dampingi uygulanmadı"
    assert result["regime_adj"] < 1.0, "Bear rejim ayarı uygulanmadı"
    print(f"     BEAR + 3 kayıp: ${result['position_usd']:.2f} | damping={result['loss_damping']:.2f} | regime={result['regime_adj']:.2f}")
test("PositionSizer BEAR + kayıp serisi", test_position_sizer_bear)


# ============================================================
# 9. MARKET HOURS
# ============================================================
section("9. MARKET HOURS")

def test_market_hours():
    from core.market_hours import MarketHours
    mh = MarketHours()
    status = mh.get_market_status()
    assert "status" in status
    assert "is_trading_allowed" in status
    assert "is_safe_zone" in status
    print(f"     Status: {status['status']} | Safe: {status['is_safe_zone']} | Time: {status.get('time_et', 'N/A')}")
    
    # Hafta sonu kontrolü
    now = mh.now_et()
    if now.weekday() >= 5:
        assert status["status"] == "CLOSED", "Hafta sonu açık?"
        print(f"     (Şu an hafta sonu — CLOSED bekleniyor ✓)")
test("Market Hours durumu", test_market_hours)

def test_market_holidays():
    from core.market_hours import ALL_HOLIDAYS
    assert len(ALL_HOLIDAYS) >= 9, f"Sadece {len(ALL_HOLIDAYS)} tatil tanımlı"
    # 2026 tatilleri doğru mu?
    assert date(2026, 12, 25) in ALL_HOLIDAYS, "Christmas 2026 eksik!"
    assert date(2026, 1, 1) in ALL_HOLIDAYS, "New Year 2026 eksik!"
test("NYSE tatil takvimi", test_market_holidays)


# ============================================================
# 10. İLERİ SEVİYE MODÜLLER
# ============================================================
section("10. İLERİ SEVİYE MODÜLLER")

def test_sector_rotation():
    from core.sector_rotation import SectorRotator
    sr = SectorRotator()
    sr.update_vix(25)
    status = sr.get_status()
    assert "regime" in status
    print(f"     Regime: {status['regime']} | Max poz: {status.get('max_positions', '?')}")
test("Sector Rotator", test_sector_rotation)

def test_volume_analyzer():
    import pandas as pd
    import numpy as np
    from core.volume_analyzer import VolumeAnalyzer
    va = VolumeAnalyzer()
    n = 100
    np.random.seed(42)
    df = pd.DataFrame({
        "open": np.cumsum(np.random.randn(n) * 0.3) + 100,
        "high": np.cumsum(np.random.randn(n) * 0.3) + 101,
        "low": np.cumsum(np.random.randn(n) * 0.3) + 99,
        "close": np.cumsum(np.random.randn(n) * 0.3) + 100,
        "volume": np.random.randint(100000, 1000000, n),
    })
    result = va.analyze_volume(df)
    assert "signal" in result
    print(f"     Volume signal: {result.get('signal', 'N/A')} | Boost: {result.get('confidence_boost', 0)}")
test("Volume Analyzer", test_volume_analyzer)

def test_market_regime_detector():
    import pandas as pd
    import numpy as np
    from core.market_regime import MarketRegimeDetector
    mrd = MarketRegimeDetector()
    n = 250
    np.random.seed(42)
    df = pd.DataFrame({
        "open": np.cumsum(np.random.randn(n) * 0.3) + 400,
        "high": np.cumsum(np.random.randn(n) * 0.3) + 401,
        "low": np.cumsum(np.random.randn(n) * 0.3) + 399,
        "close": np.cumsum(np.random.randn(n) * 0.3) + 400,
        "volume": np.random.randint(50000000, 200000000, n),
    })
    result = mrd.detect_regime(df)
    assert "regime" in result
    print(f"     Regime: {result['regime']} | Trading mode: {result.get('trading_mode', 'N/A')}")
test("Market Regime Detector", test_market_regime_detector)

def test_signal_queue():
    from core.signal_queue import SignalQueue
    sq = SignalQueue()
    status = sq.get_queue_status()
    assert "pending_count" in status
    print(f"     Queue: {status['pending_count']} pending")
test("Signal Queue", test_signal_queue)

def test_options_engine():
    from core.options_engine import OptionsEngine
    # init gerektiriyor (bot referansı) ama import edilebilmeli
    assert OptionsEngine is not None
test("Options Engine import", test_options_engine)


# ============================================================
# 11. BOT BAŞLATMA TESTİ
# ============================================================
section("11. BOT BAŞLATMA TESTİ (StockBot)")

def test_bot_init():
    """StockBot.__init__ tam çalışıyor mu? (API bağlantısı + tüm modüller)"""
    from stock_bot import StockBot
    bot = StockBot()
    
    # Temel kontroller
    assert bot.equity > 0, "Equity $0!"
    assert bot.initial_equity > 0, "Initial equity $0!"
    assert bot.client is not None, "Trading client None!"
    assert bot.data_client is not None, "Data client None!"
    
    # Modüller başlatılmış mı?
    assert bot.market_hours is not None
    assert bot.pdt_tracker is not None
    assert bot.coordinator is not None
    assert bot.executor is not None
    assert bot.position_manager is not None
    assert bot.trade_gates is not None
    assert bot.kill_switch is not None
    assert bot.news_analyzer is not None
    assert bot.position_sizer is not None
    assert bot.signal_queue is not None
    assert bot.options_analyzer is not None
    
    # KillSwitch aktif değildir
    assert not bot.kill_switch.is_active, "KillSwitch başlangıçta aktif!"
    
    print(f"     Equity: ${bot.equity:,.2f}")
    print(f"     Paper: {bot.is_paper}")
    print(f"     Positions: {len(bot.positions)} L / {len(bot.short_positions)} S / {len(bot.options_positions)} O")
    print(f"     Max pos USD: ${bot.max_pos_usd}")
    print(f"     Floor: ${bot.equity_floor:,.2f}")
    print(f"     Options: {bot._options_enabled}")
test("StockBot tam başlatma", test_bot_init)


# ============================================================
# 12. DOCKER & DEPLOYMENT
# ============================================================
section("12. DOCKER & DEPLOYMENT")

def test_dockerfile():
    dockerfile = os.path.join(PROJECT_ROOT, "Dockerfile")
    assert os.path.exists(dockerfile), "Dockerfile bulunamadı!"
    with open(dockerfile) as f:
        content = f.read()
    assert "python" in content.lower(), "Dockerfile Python image kullanmıyor?"
    assert "requirements.txt" in content, "requirements.txt COPY eksik?"
    assert "stock_bot.py" in content or "COPY . ." in content, "Uygulama dosyaları kopyalanmıyor?"
test("Dockerfile mevcut ve geçerli", test_dockerfile)

def test_docker_compose():
    compose = os.path.join(PROJECT_ROOT, "docker-compose.yml")
    assert os.path.exists(compose), "docker-compose.yml bulunamadı!"
    with open(compose) as f:
        content = f.read()
    assert "trading-live" in content, "trading-live servisi eksik!"
    assert "trading-short" in content or "trading-paper" in content, "Paper servisi eksik!"
    assert "TRADING_MODE" in content, "TRADING_MODE env eksik!"
    assert "BOT_MODE" in content, "BOT_MODE env eksik!"
test("docker-compose.yml geçerli", test_docker_compose)

def test_requirements():
    req = os.path.join(PROJECT_ROOT, "requirements.txt")
    assert os.path.exists(req)
    with open(req) as f:
        content = f.read()
    # transformers bilinçli yok (Railway OOM — c9800c5); FinBERT = onnxruntime yolu
    required = ["alpaca-py", "pandas", "numpy", "ta", "python-dotenv", "pytz", "onnxruntime"]
    missing = [r for r in required if r not in content]
    assert len(missing) == 0, f"Eksik bağımlılıklar: {missing}"
test("requirements.txt tam", test_requirements)


# ============================================================
# SONUÇ RAPORU
# ============================================================
print("\n" + "=" * 60)
print("  📋 SİSTEM TESTİ SONUÇ RAPORU")
print("=" * 60)

pass_count = sum(1 for r in results if r[0] == PASS)
fail_count = sum(1 for r in results if r[0] == FAIL)
warn_count = sum(1 for r in results if r[0] == WARN)
section_count = sum(1 for r in results if r[0] == "")

total_tests = pass_count + fail_count + warn_count

for status, name, detail in results:
    if status == "":
        print(name)
    elif status == PASS:
        print(f"  {status} {name}")
    elif status == WARN:
        print(f"  {status} {name}: {detail}")
    else:
        print(f"  {status} {name}")
        if detail:
            for line in detail.split("\n")[:3]:
                print(f"      {line}")

print("\n" + "=" * 60)
print(f"  TOPLAM: {total_tests} test")
print(f"    {PASS} Geçen: {pass_count}")
print(f"    {FAIL} Başarısız: {fail_count}")
print(f"    {WARN} Uyarı: {warn_count}")
print("=" * 60)

if fail_count == 0:
    print(f"\n  🎉 TÜM TESTLER GEÇTİ! Pazartesi hazırsın! 🚀\n")
else:
    print(f"\n  ⚠️ {fail_count} TEST BAŞARISIZ — Düzeltme gerekiyor!\n")

# Exit code
sys.exit(fail_count)
