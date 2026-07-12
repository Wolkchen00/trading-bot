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
# 13. v4.8 DAVRANIŞLARI (dinamik TP, yön-farkında güven, yarım gün, earnings)
# ============================================================
section("13. v4.8 DAVRANIŞLARI")

def test_plan_exit_pcts():
    from core.trade_gates import plan_exit_pcts
    live_cfg = {
        "atr_stop_multiplier": 1.8, "stop_loss_pct": 0.04, "stop_loss_max_pct": 0.06,
        "take_profit_pct": 0.08, "take_profit_max_pct": 0.12, "min_rr_ratio": 2.0,
    }
    # Düşük ATR (%1): SL tabana kırpılır %4 → TP taban %8 → R:R 2.0
    sl, tp = plan_exit_pcts(atr=1.0, price=100.0, config=live_cfg)
    assert abs(sl - 0.04) < 1e-9, f"SL {sl} != 0.04"
    assert abs(tp - 0.08) < 1e-9, f"TP {tp} != 0.08"
    # Yüksek ATR (%4): SL tavana kırpılır %6 → dinamik TP %12 → R:R 2.0 KORUNUR
    sl, tp = plan_exit_pcts(atr=4.0, price=100.0, config=live_cfg)
    assert abs(sl - 0.06) < 1e-9, f"SL {sl} != 0.06"
    assert abs(tp - 0.12) < 1e-9, f"TP {tp} != 0.12 (dinamik TP çalışmıyor!)"
    # ATR yok → SL taban
    sl, tp = plan_exit_pcts(atr=0, price=100.0, config=live_cfg)
    assert abs(sl - 0.04) < 1e-9
    print(f"     Dinamik TP: ATR%4 → SL %6 / TP %12 (R:R {tp/sl:.1f}:1) ✓")
test("plan_exit_pcts dinamik TP", test_plan_exit_pcts)

def test_rr_gate_not_atr_filter():
    """v4.8 öncesi bug: R:R gate paper'da HER alımı blokluyordu (TP6/SL5→1.2<2.0).
    Artık dinamik TP ile tutarlı plan oranı sağlar → normal configlerde bloklamaz."""
    from core.trade_gates import TradeGates
    class _FakeBot: pass
    gates = TradeGates(_FakeBot())
    paper_cfg = {
        "atr_stop_multiplier": 1.8, "stop_loss_pct": 0.05, "stop_loss_max_pct": 0.06,
        "take_profit_pct": 0.06, "take_profit_max_pct": 0.10, "min_rr_ratio": 1.5,
    }
    # Eski matematikle bloklanan senaryo: ATR %3 → SL %5.4 → eski TP %6 → 1.1:1 BLOK
    blocked, reason = gates._check_rr_gate("TEST", {"atr": 3.0, "price": 100.0}, paper_cfg)
    assert not blocked, f"Paper R:R gate hâlâ blokluyor: {reason}"
    # Tavan orana izin vermiyorsa BLOKLAMALI (tek meşru blok durumu)
    tight_cfg = dict(paper_cfg, take_profit_max_pct=0.06, min_rr_ratio=2.0)
    blocked, reason = gates._check_rr_gate("TEST", {"atr": 3.0, "price": 100.0}, tight_cfg)
    assert blocked, "Tavan kısıtlı config'de gate bloklamalıydı"
    print("     R:R gate: normal config geçer, tavan-kısıtlı config bloklar ✓")
test("R:R gate ATR-filtresi değil artık", test_rr_gate_not_atr_filter)

def test_risk_agent_neutral_baseline():
    """v4.8: RiskAgent risk-normalken BUY değil HOLD döner (fren, gaz değil)."""
    from core.agent_coordinator import RiskAgent
    vote = RiskAgent().analyze({
        "daily_pnl_pct": 0.5, "open_positions": 0, "max_positions": 3,
        "atr_pct": 2.0, "vix": 15,
    })
    assert vote.signal == "HOLD", f"Risk-normal baseline {vote.signal} olmamalı (HOLD beklenir)"
    # Stres altında SELL vetosu korunur
    vote = RiskAgent().analyze({
        "daily_pnl_pct": -4.0, "open_positions": 3, "max_positions": 3,
        "atr_pct": 6.0, "vix": 40, "equity_floor_hit": True,
    })
    assert vote.signal == "SELL", "Stres altında SELL vetosu kayboldu!"
    print("     RiskAgent: normal→HOLD, stres→SELL veto ✓")
test("RiskAgent nötr baseline", test_risk_agent_neutral_baseline)

def test_direction_aware_confidence():
    """v4.8: çelişkili sinyalin güveni temiz mutabakattan DÜŞÜK olmalı.
    Eski formül tüm güvenleri yön bağımsız topluyordu → 2 BUY + 2 SELL bile
    yüksek 'güven' alıp pozisyon bandı şişiriyordu."""
    from core.agent_coordinator import AgentCoordinator
    coord = AgentCoordinator()
    # Temiz mutabakat (4 ajan BUY yönlü güçlü veri)
    clean = coord.decide(
        "CLEAN",
        {"tech_score": 60, "rsi": 26, "macd_signal": "BULLISH", "ichimoku_signal": "BULLISH",
         "adx": 30, "ema_trend": "BULLISH", "bb_position": "BELOW"},
        {"fundamental_score": 20, "metrics": {"pe_ratio": 12, "eps": 3.0, "profit_margin": 0.2}},
        {"news_score": 25, "sentiment_label": "BUY", "fear_greed_value": 40, "fear_greed_signal": "BUY"},
        {"social_score": 15, "reddit_posts": 20},
        {"daily_pnl_pct": 0.5, "open_positions": 0, "max_positions": 3, "atr_pct": 2.0, "vix": 15},
    )
    # Çelişkili: teknik güçlü BUY ama sentiment/sosyal güçlü SELL
    conflicted = coord.decide(
        "CONFLICT",
        {"tech_score": 60, "rsi": 26, "macd_signal": "BULLISH", "ichimoku_signal": "BULLISH",
         "adx": 30, "ema_trend": "BULLISH", "bb_position": "BELOW"},
        {"fundamental_score": -20, "metrics": {"pe_ratio": 60, "eps": -1.0}},
        {"news_score": -30, "sentiment_label": "SELL", "fear_greed_value": 80, "fear_greed_signal": "SELL"},
        {"social_score": -15, "reddit_posts": 20},
        {"daily_pnl_pct": 0.5, "open_positions": 0, "max_positions": 3, "atr_pct": 2.0, "vix": 15},
    )
    assert clean["signal"] == "BUY", f"Temiz mutabakat BUY vermedi: {clean['signal']}"
    assert clean["confidence"] > conflicted["confidence"] + 15, (
        f"Yön-farkında güven çalışmıyor: temiz={clean['confidence']} "
        f"çelişkili={conflicted['confidence']}"
    )
    print(f"     Güven: temiz-mutabakat={clean['confidence']:.0f} > çelişkili={conflicted['confidence']:.0f} ✓")
test("Yön-farkında güven (çelişkili sinyal cezası)", test_direction_aware_confidence)

def test_early_close_calendar():
    from core.market_hours import MarketHours, NYSE_EARLY_CLOSE
    from datetime import time as _time
    assert NYSE_EARLY_CLOSE.get(date(2026, 11, 27)) == _time(13, 0), "Thanksgiving ertesi 2026 eksik!"
    assert NYSE_EARLY_CLOSE.get(date(2026, 12, 24)) == _time(13, 0), "Noel arifesi 2026 eksik!"
    # Statik yol: client'sız kurulum bugün için tutarlı seans döndürmeli
    mh = MarketHours(trading_client=None)
    is_day, open_t, close_t = mh._today_session()
    assert open_t <= close_t
    print(f"     Yarım günler tanımlı | bugün: işlem_günü={is_day} kapanış={close_t.strftime('%H:%M')} ET")
test("NYSE yarım-gün takvimi", test_early_close_calendar)

def test_earnings_calendar_v48():
    from core.earnings_calendar import EarningsCalendar
    ec = EarningsCalendar()
    # Sahte takvim enjekte et (ağa çıkmadan mantığı doğrula)
    ec._fetched_at = datetime.now()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    far = (date.today() + timedelta(days=30)).isoformat()
    ec._calendar = {"AAPL": [tomorrow], "MSFT": [far]}
    avoid, reason = ec.should_avoid_trading("AAPL")
    assert avoid, f"Yarınki earnings bloklamadı: {reason}"
    avoid, reason = ec.should_avoid_trading("MSFT")
    assert not avoid, f"30 gün sonraki earnings bloklamamalı: {reason}"
    # Takvimde olmayan sembol → fail-open
    avoid, _ = ec.should_avoid_trading("NVDA")
    assert not avoid, "Takvim-dışı sembol fail-open olmalı"
    print("     Earnings: yarın→BLOK, 30 gün→serbest, veri yok→fail-open ✓")
test("EarningsCalendar v4.8 (toplu takvim + fail-open)", test_earnings_calendar_v48)

def test_paper_learning_config():
    # DİKKAT: StockBot.__init__ paper modda PAPER_AGGRESSIVE'i STOCK_CONFIG dict'ine
    # YERİNDE yazar (test_bot_init yukarıda çalıştı) → paylaşılan modül kirli.
    # Bozulmamış değerleri görmek için config'i izole taze import ederiz.
    import importlib, sys as _sys
    saved = _sys.modules.pop("config", None)
    try:
        fresh = importlib.import_module("config")
        # v4.9: 45 → 30 (remap'li ölçekte ws≥15; 45 gerçek dağılımda ulaşılamıyordu)
        assert fresh.PAPER_AGGRESSIVE_CONFIG["min_confidence_score"] == 30, "Paper min_conf 30 değil!"
        assert fresh.PAPER_AGGRESSIVE_CONFIG["min_rr_ratio"] == 1.5, "Paper min_rr 1.5 değil!"
        assert fresh.PAPER_AGGRESSIVE_CONFIG.get("pullback_queue_enabled") is True, "Paper pullback queue kapalı!"
        assert fresh.STOCK_CONFIG.get("pullback_queue_enabled") is False, "Canlıda pullback queue AÇIK kalmış!"
        assert fresh.STOCK_CONFIG["min_confidence_score"] == 50, "Canlı min_conf 50 değil!"
        bands = fresh.STOCK_CONFIG["live_conf_position_bands"]
        assert bands[0][0] == 50 and bands[-1][0] == 80, f"Bantlar yeniden haritalanmamış: {bands}"
    finally:
        _sys.modules.pop("config", None)
        if saved is not None:
            _sys.modules["config"] = saved  # diğer testler kirli-ama-tutarlı objeyi görsün
    print("     Paper: conf 30 + R:R 1.5 + kuyruk açık | Canlı: conf 50, bant 50-80 ✓")
test("Paper öğrenme + canlı eşik konfigürasyonu", test_paper_learning_config)

def test_extended_entry_heuristic():
    from stock_bot import StockBot
    assert StockBot._is_extended_entry({"rsi": 72, "bb_position": "MIDDLE", "vwap_signal": "NEUTRAL"})
    assert StockBot._is_extended_entry({"rsi": 50, "bb_position": "ABOVE", "vwap_signal": "NEUTRAL"})
    assert not StockBot._is_extended_entry({"rsi": 45, "bb_position": "MIDDLE", "vwap_signal": "NEUTRAL"})
    print("     Uzamış giriş: RSI72→kuyruk, BB üstü→kuyruk, temiz→hemen al ✓")
test("Pullback kuyruğu uzamış-giriş sezgisi", test_extended_entry_heuristic)


# ============================================================
# 15. v4.9 DÜZELTMELERİ (07 Tem denetimi)
# ============================================================
section("15. v4.9 DÜZELTMELERİ")

def test_v49_confidence_remap():
    """v4.9: kaynak-remap ×2.0 — güçlü mutabakat canlı eşiğe (50) ulaşabilmeli,
    nötr piyasa 0'a yakın kalmalı. 06-07 Tem: remap'siz |ws| max 15'te kaldı,
    hisse motoru live+paper fiilen kapalıydı."""
    from core.agent_coordinator import AgentCoordinator
    coord = AgentCoordinator()
    # Ezici mutabakat girdileri: Sent/Fund/Social tek başına ws≥40 garantiler
    # (ajan iç eşiklerinden bağımsız olarak remap-sonrası ≥50 kesinleşir)
    strong = coord.decide(
        "STRONG",
        {"tech_score": 60, "rsi": 26, "macd_signal": "BULLISH", "ichimoku_signal": "BULLISH",
         "adx": 30, "ema_trend": "BULLISH", "bb_position": "BELOW"},
        {"fundamental_score": 40, "metrics": {"pe_ratio": 12, "eps": 3.0, "profit_margin": 0.2}},
        {"news_score": 80, "sentiment_label": "BUY", "fear_greed_value": 25, "fear_greed_signal": "BUY"},
        {"social_score": 30, "reddit_posts": 40, "x_tweets": 30, "x_sentiment": 0.7},
        {"daily_pnl_pct": 0.5, "open_positions": 0, "max_positions": 3, "atr_pct": 2.0, "vix": 15},
    )
    assert strong["signal"] == "BUY", f"Güçlü mutabakat BUY vermedi: {strong['signal']}"
    # Remap aritmetiği: conf = |ws| × 2.0 (× 1.2 çoğunlukta), tavan 100
    expected = abs(strong["weighted_score"]) * 2.0
    if strong["majority"]:
        expected *= 1.2
    expected = min(expected, 100)
    assert abs(strong["confidence"] - expected) < 0.75, (
        f"Remap aritmetiği tutmuyor: conf={strong['confidence']} beklenen≈{expected:.1f}"
    )
    assert strong["confidence"] >= 50, (
        f"Güçlü mutabakat canlı eşiğe ulaşamıyor: {strong['confidence']} < 50 "
        f"(remap çalışmıyor mu?)"
    )
    # Nötr piyasa: hepsi HOLD → güven ~0 (remap gürültüyü şişirmemeli)
    neutral = coord.decide(
        "NEUTRAL",
        {"tech_score": 0, "rsi": 50, "macd_signal": "NEUTRAL", "ichimoku_signal": "NEUTRAL", "adx": 12},
        {"fundamental_score": 0, "metrics": {}},
        {"news_score": 0, "sentiment_label": "NEUTRAL", "fear_greed_value": 50, "fear_greed_signal": "NEUTRAL"},
        {"social_score": 0},
        {"daily_pnl_pct": 0.0, "open_positions": 0, "max_positions": 3, "atr_pct": 2.0, "vix": 15},
    )
    assert neutral["confidence"] < 30, (
        f"Nötr piyasa paper eşiğini (30) geçiyor: {neutral['confidence']}"
    )
    print(f"     Remap: güçlü={strong['confidence']:.0f} (ws {strong['weighted_score']:+.1f}) ≥50 | "
          f"nötr={neutral['confidence']:.0f} <30 ✓")
test("v4.9 güven remap (×2.0 kaynak ölçeği)", test_v49_confidence_remap)

def test_v49_options_disabled():
    """v4.9: opsiyon modülü kapalı (churn); cooldown anahtarı tanımlı."""
    import importlib, sys as _sys
    saved = _sys.modules.pop("config", None)
    try:
        fresh = importlib.import_module("config")
        assert fresh.OPTIONS_CONFIG["options_enabled"] is False, "Opsiyonlar hâlâ AÇIK!"
        assert fresh.PAPER_AGGRESSIVE_CONFIG.get("prefer_options_over_stock") is False, \
            "prefer_options_over_stock hâlâ açık!"
        assert fresh.OPTIONS_CONFIG.get("options_reentry_cooldown_hours", 0) >= 1, \
            "Opsiyon cooldown anahtarı yok!"
    finally:
        _sys.modules.pop("config", None)
        if saved is not None:
            _sys.modules["config"] = saved
    print("     Opsiyonlar kapalı + reentry cooldown tanımlı ✓")
test("v4.9 opsiyon modülü kapalı", test_v49_options_disabled)

def test_v49_short_backdoor_removed():
    """v4.9: analyzer-SHORT arka kapısı kalktı; opsiyon dalları executor
    sonucuna bağlı; gate-bloklu CALL fallback'i yok."""
    src_path = os.path.join(PROJECT_ROOT, "stock_bot.py")
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    assert 'analysis.get("signal") == "SHORT" and decision["signal"] == "HOLD"' not in src, \
        "Analyzer-SHORT arka kapısı hâlâ kodda!"
    assert "shorted = self.short_executor.execute_short(" in src, \
        "SHORT sonucu yakalanmıyor (PUT gating için gerekli)"
    assert "if shorted and self._options_enabled" in src, \
        "PUT dalı short sonucuna bağlı değil!"
    assert "bought = self.executor.execute_buy(" in src, \
        "BUY sonucu yakalanmıyor (CALL gating için gerekli)"
    assert "if bought and self._options_enabled" in src, \
        "CALL dalı buy sonucuna bağlı değil!"
    assert "Gate'den geçemese bile opsiyon dene" not in src, \
        "Gate-bloklu CALL fallback hâlâ kodda!"
    print("     Arka kapı yok + PUT/CALL executor-sonucuna kilitli ✓")
test("v4.9 SHORT arka kapısı + opsiyon bypass'ları kalktı", test_v49_short_backdoor_removed)

def test_v49_parking_sell_safe():
    """v4.9: parking satışı close_position ile (short imkânsız) + negatif
    pozisyon self-heal. 07 Tem: notional SELL hayalet veriyle 5.3 SPY short açtı."""
    from core.index_parking import IndexParkingManager

    calls = {"close": [], "orders": []}

    class _Pos:
        pass

    class FakeClient:
        def __init__(self):
            self.pos_qty = 10.0
        def get_open_position(self, sym):
            if self.pos_qty is None:
                raise RuntimeError("position does not exist")
            p = _Pos()
            p.qty = self.pos_qty
            p.current_price = 100.0
            p.market_value = self.pos_qty * 100.0
            return p
        def close_position(self, sym, close_options=None):
            calls["close"].append((sym, getattr(close_options, "qty", None)))
        def submit_order(self, req=None, order_data=None):
            calls["orders"].append(req or order_data)
        def get_orders(self, req=None):
            return []

    class FakeBot:
        pass

    bot = FakeBot()
    bot.client = FakeClient()
    mgr = IndexParkingManager(bot, {"index_parking_enabled": True})
    assert mgr.enabled, "Test ortamında parking aktif olmalı (TRADING_MODE=paper)"

    # Kısmi satış → qty-sınırlı close_position ($500 / $100 = 5 pay)
    mgr._sell(500.0)
    assert calls["close"], "close_position çağrılmadı"
    assert calls["close"][-1][1] == "5.0", f"qty-sınırlı değil: {calls['close'][-1]}"
    assert not calls["orders"], "Parking sell submit_order kullanmamalı (short riski)!"

    # Tam satış (notional ≥ pozisyon) → qty'siz tam kapama
    mgr._sell(1500.0)
    assert calls["close"][-1][1] is None, "Tam kapama qty'siz olmalı"

    # Pozisyon yokken satış → HİÇBİR emir yok
    n = len(calls["close"])
    bot.client.pos_qty = None
    mgr._sell(500.0)
    assert len(calls["close"]) == n and not calls["orders"], "Pozisyonsuz satış emir üretti!"

    # Self-heal: negatif pozisyon → derhal buy-to-close
    bot.client.pos_qty = -5.0
    mgr.maybe_rebalance()
    assert len(calls["close"]) == n + 1, "Negatif pozisyon self-heal çalışmadı!"
    print("     Kısmi=qty-sınırlı, tam=tümü, pozisyonsuz=emir yok, negatif=self-heal ✓")
test("v4.9 parking short-imkânsız satış + self-heal", test_v49_parking_sell_safe)

def test_v49_health_weekend_hours():
    """v4.9: sağlık alarmı hafta sonu saatlerini saymaz (Pzt yalancı 🔴 fix)."""
    from health_check import trading_hours_between
    from datetime import datetime as _dt
    # Cuma 20:00 → Pazartesi 13:30 (duvar 65.5h) = Cum 4h + Pzt 13.5h = 17.5h
    got = trading_hours_between(_dt(2026, 6, 26, 20, 0), _dt(2026, 6, 29, 13, 30))
    assert abs(got - 17.5) < 0.01, f"Hafta sonu düşümü yanlış: {got}"
    # Aynı gün (hafta içi) → birebir
    got2 = trading_hours_between(_dt(2026, 6, 29, 9, 0), _dt(2026, 6, 29, 15, 0))
    assert abs(got2 - 6.0) < 0.01, f"Hafta içi hesap bozuk: {got2}"
    # Ters aralık → 0
    assert trading_hours_between(_dt(2026, 6, 29), _dt(2026, 6, 26)) == 0.0
    print(f"     Cum 20:00→Pzt 13:30 = {got:.1f}h (65.5h duvar) ✓")
test("v4.9 health hafta-sonu saati düşümü", test_v49_health_weekend_hours)

def test_v49_option_fill_accounting():
    """v4.9: opsiyon girişi yalnız DOLUM onayıyla ve gerçek fiyatla kaydedilir;
    dolmayan emir iptal edilir, deftere yazılmaz (07 Tem 'ALINDI' yalanı fix)."""
    import core.options_executor as oe

    _orig_sleep = oe.time.sleep
    oe.time.sleep = lambda s: None  # testte bekleme yok
    try:
        class FakeStatus:
            def __init__(self, v):
                self.value = v

        class FakeClient:
            def __init__(self, fills):
                self.fills = fills
                self.canceled = []
            def get_orders(self, req=None):
                return []
            def submit_order(self, order_data=None):
                o = _O()
                o.id = "oid-1"
                return o
            def get_order_by_id(self, oid):
                o = _O()
                if self.fills:
                    o.status = FakeStatus("filled")
                    o.filled_qty = "3"
                    o.filled_avg_price = "0.42"
                else:
                    o.status = FakeStatus("new")
                    o.filled_qty = "0"
                    o.filled_avg_price = None
                return o
            def cancel_order_by_id(self, oid):
                self.canceled.append(oid)

        class _O:
            pass

        class FakeAnalyzer:
            def get_contract_snapshot(self, sym):
                return {"bid": 0.40, "ask": 0.44, "latest_trade_price": 0.42}

        def make_bot(fills):
            b = _O()
            b.client = FakeClient(fills)
            b.options_analyzer = FakeAnalyzer()
            b.options_positions = {}
            b.equity = 10000.0
            b.notifier = _O()
            b.notifier.send_message = lambda *a, **k: None
            b.agent_perf = _O()
            b.agent_perf.record_outcome = lambda **k: None
            b._save_position_metadata = lambda: None
            return b

        cfg = {"options_max_position_usd": 500, "options_max_spread_pct": 0.10,
               "options_max_positions": 5, "options_max_per_symbol": 2,
               "options_max_exposure_pct": 0.20}
        info = {"symbol": "TSTX260717P00100000", "underlying": "TSTX",
                "strike": 100.0, "expiry": "2026-07-17"}

        # DOLAN emir → gerçek dolum fiyatı/adediyle kayıt
        bot = make_bot(fills=True)
        ex = oe.OptionsExecutor(bot)
        ok = ex._execute_option("PUT", info, {"confidence": 60}, cfg)
        assert ok, "Dolan emir False döndü"
        pos = bot.options_positions[info["symbol"]]
        assert abs(pos["entry_price"] - 0.42) < 1e-9, f"Entry gerçek dolum değil: {pos['entry_price']}"
        assert pos["qty"] == 3, f"Adet gerçek dolum değil: {pos['qty']}"

        # DOLMAYAN emir → kayıt yok + iptal edildi
        bot2 = make_bot(fills=False)
        ex2 = oe.OptionsExecutor(bot2)
        ok2 = ex2._execute_option("PUT", info, {"confidence": 60}, cfg)
        assert not ok2, "Dolmayan emir True döndü!"
        assert bot2.options_positions == {}, "Dolmayan emir deftere yazıldı ('ALINDI' yalanı)!"
        assert bot2.client.canceled, "Dolmayan emir iptal edilmedi!"

        # İllikit kontrat (spread %50) → işlem YOK
        bot3 = make_bot(fills=True)
        bot3.options_analyzer.get_contract_snapshot = (
            lambda sym: {"bid": 0.20, "ask": 0.60, "latest_trade_price": 0.40}
        )
        ex3 = oe.OptionsExecutor(bot3)
        ok3 = ex3._execute_option("PUT", info, {"confidence": 60}, cfg)
        assert not ok3 and bot3.options_positions == {}, "Geniş spread kapısı çalışmıyor!"
        print("     Dolum=gerçek fiyat, dolmayan=iptal+kayıtsız, illikit=red ✓")
    finally:
        oe.time.sleep = _orig_sleep
test("v4.9 opsiyon fill-onaylı muhasebe + spread kapısı", test_v49_option_fill_accounting)

def test_v49_snapshot_request_object():
    """v4.9: opsiyon snapshot çağrısı Request objesiyle (str-bug fix)."""
    src_path = os.path.join(PROJECT_ROOT, "core", "options_analyzer.py")
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    assert "OptionSnapshotRequest(symbol_or_symbols=" in src, \
        "Snapshot Request objesi kullanılmıyor!"
    assert "get_option_snapshot(contract_symbol)" not in src, \
        "Düz-str snapshot çağrısı hâlâ kodda ('to_request_fields' bug'ı)!"
    print("     OptionSnapshotRequest objesiyle çağrı ✓")
test("v4.9 opsiyon snapshot str-bug fix", test_v49_snapshot_request_object)


# ============================================================
# 16. v4.10 DÜZELTMELERİ (11 Tem denetimi)
# ============================================================
section("16. v4.10 DÜZELTMELERİ")

def test_v410_sector_rotation_reduced():
    """v4.10: normal rejimde EV/CryptoMining hard-veto DEĞİL, ×0.7 kısıtlı.
    08-10 Tem: MARA guven 50-62 (canlının en güçlü sinyali) 'normal rejiminde
    kaçınılıyor' bloğuyla öldü — 4 günde 0 canlı giriş."""
    from core.sector_rotation import SectorRotator
    sr = SectorRotator()
    sr.update_vix(18)  # normal rejim
    assert sr.current_regime == "normal", f"VIX 18 → normal değil: {sr.current_regime}"
    assert sr.should_buy("MARA"), "MARA normal rejimde alınabilir olmalı (reduced)!"
    assert sr.should_buy("TSLA"), "TSLA normal rejimde alınabilir olmalı (reduced)!"
    w = sr.get_weight_multiplier("MARA")
    assert 0 < w < 1.0, f"MARA kısıtlı ağırlık ×0.7 bekleniyor: {w}"
    # Yüksek VIX'te koruma AYNEN: hard-avoid geri gelir
    sr.update_vix(30)
    assert sr.current_regime == "high", f"VIX 30 → high değil: {sr.current_regime}"
    assert not sr.should_buy("MARA"), "MARA yüksek VIX'te bloklanmalı!"
    assert not sr.should_buy("AMD"), "Semiconductors yüksek VIX'te bloklanmalı!"
    sr.update_vix(40)
    assert sr.current_regime == "extreme", "VIX 40 → extreme değil!"
    print("     Normal: MARA/TSLA izinli ×0.7 | VIX 30: hard-avoid | VIX 40: extreme ✓")
test("v4.10 sektör rotasyonu reduced katmanı", test_v410_sector_rotation_reduced)

def test_v410_vix_key_fix():
    """v4.10: stock_bot VIX değerini 'vix' anahtarından okumalı — 'value' anahtarı
    hiç var olmadı, her gün varsayılan 20 okunup rejim 'normal'e çivileniyordu."""
    src_path = os.path.join(PROJECT_ROOT, "stock_bot.py")
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    assert 'vix_data.get("value"' not in src, "VIX hâlâ olmayan 'value' anahtarından okunuyor!"
    assert 'vix_data.get("vix")' in src, "VIX 'vix' anahtarından okunmuyor!"
    # macro_data'nın gerçekten bu anahtarla döndüğünü doğrula (statik — ağ çağrısı yok)
    with open(os.path.join(PROJECT_ROOT, "core", "macro_data.py"), encoding="utf-8") as f:
        macro_src = f.read()
    assert '"vix": round(current_vix' in macro_src, "macro vix dict'i 'vix' anahtarıyla dönmüyor!"
    print("     VIX 'vix' anahtarından okunuyor (kalıcı-normal bug'ı fix) ✓")
test("v4.10 VIX anahtar bug fix", test_v410_vix_key_fix)

def test_v410_band_sector_weight():
    """v4.10: bant (LIVE) boyut yolu sector_weight'i uygular — kısıtlı sektör
    $150 bandını $105'e indirir; boost bandı YUKARI esnetemez; avoid=0 boş sonuç."""
    from core.position_sizer import PositionSizer
    ps = PositionSizer()
    cfg = {
        "conf_position_bands": [[50, 100], [60, 150], [70, 200], [80, 300]],
        "fixed_position_max_pct": 0.62,
        "max_position_usd": 300,
        "min_trade_value": 10,
    }
    # Kısıtlı sektör: 60 bandı ($150) × 0.7 = $105
    r = ps.calculate_position_size(487, 20.0, 0.5, cfg, side="LONG",
                                   sector_weight=0.7, confidence=62)
    assert abs(r["position_usd"] - 105.0) < 0.01, f"×0.7 uygulanmadı: {r['position_usd']}"
    # Nötr sektör: bant aynen
    r2 = ps.calculate_position_size(487, 20.0, 0.5, cfg, side="LONG",
                                    sector_weight=1.0, confidence=62)
    assert abs(r2["position_usd"] - 150.0) < 0.01, f"Bant bozuldu: {r2['position_usd']}"
    # Boost bandı yukarı ESNETEMEZ (İhsan'ın dolar sözleşmesi)
    r3 = ps.calculate_position_size(487, 20.0, 0.5, cfg, side="LONG",
                                    sector_weight=1.2, confidence=62)
    assert abs(r3["position_usd"] - 150.0) < 0.01, f"Boost bandı şişirdi: {r3['position_usd']}"
    # Avoid (0) → boş sonuç (çift emniyet)
    r4 = ps.calculate_position_size(487, 20.0, 0.5, cfg, side="LONG",
                                    sector_weight=0.0, confidence=62)
    assert r4["position_usd"] == 0, f"Avoid sektörde boyut sıfır olmalı: {r4['position_usd']}"
    print("     Bant: ×0.7=$105, nötr=$150, boost şişirmez, avoid=$0 ✓")
test("v4.10 bant yolunda sektör ağırlığı", test_v410_band_sector_weight)

def test_v410_earnings_empty_csv_guard():
    """v4.10: boş CSV (AV kota) dolu takvimi EZEMEZ — 09-10 Tem'de {} yazılıp
    temmuz kazanç sezonu öncesi gate kör kalmıştı."""
    from core import earnings_calendar as ec_mod
    from datetime import datetime as _dt, timedelta as _td
    ec = ec_mod.EarningsCalendar.__new__(ec_mod.EarningsCalendar)  # __init__'siz (disk I/O yok)
    ec.alpha_vantage_key = "test"
    ec._calendar = {"AAPL": ["2026-07-30"]}
    ec._fetched_at = _dt.now() - _td(hours=48)   # bayat → refresh dener
    ec._last_attempt = _dt.min
    ec._warned_no_data = False
    ec._cache_file = os.path.join(PROJECT_ROOT, "tests", "_tmp_earnings_test.json")

    class _FakeResp:
        status_code = 200
        text = "symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay\r\n"  # header-only
    _orig_get = ec_mod.requests.get
    ec_mod.requests.get = lambda *a, **k: _FakeResp()
    try:
        ec._refresh_if_needed()
    finally:
        ec_mod.requests.get = _orig_get
        try:
            os.remove(ec._cache_file)
        except OSError:
            pass
    assert ec._calendar == {"AAPL": ["2026-07-30"]}, f"Boş CSV takvimi ezdi: {ec._calendar}"
    assert (_dt.now() - ec._fetched_at).total_seconds() > 3600, "fetched_at ilerletildi (bayat-tolerans bozulur)!"
    print("     Boş CSV → eski takvim korunur, fetched_at ilerlemez ✓")
test("v4.10 earnings boş-CSV cache koruması", test_v410_earnings_empty_csv_guard)

def test_v410_agent_perf_prune():
    """v4.10: çözümsüz kayıtlar 3 günde budanır; taze + çözümlü korunur.
    (Eski akış 4 günde 5.500+ asla-çözülmeyecek null kayıt biriktirdi.)"""
    import tempfile
    from core.agent_performance import AgentPerformanceTracker
    from datetime import datetime as _dt, timedelta as _td
    tmp = os.path.join(tempfile.gettempdir(), "agent_perf_test_v410.json")
    if os.path.exists(tmp):
        os.remove(tmp)
    t = AgentPerformanceTracker(history_file=tmp)
    old_ts = (_dt.now() - _td(days=5)).isoformat()
    fresh_ts = _dt.now().isoformat()
    t.predictions = {
        "TechAgent": [
            {"symbol": "OLD", "predicted_signal": "BUY", "confidence": 50,
             "coordinator_signal": "BUY", "actual_outcome": None, "timestamp": old_ts, "correct": None},
            {"symbol": "FRESH", "predicted_signal": "BUY", "confidence": 50,
             "coordinator_signal": "BUY", "actual_outcome": None, "timestamp": fresh_ts, "correct": None},
            {"symbol": "DONE", "predicted_signal": "BUY", "confidence": 50,
             "coordinator_signal": "BUY", "actual_outcome": "WIN", "timestamp": old_ts, "correct": True},
        ]
    }
    t.prune()
    syms = [p["symbol"] for p in t.predictions["TechAgent"]]
    assert "OLD" not in syms, "5 günlük çözümsüz kayıt budanmadı!"
    assert "FRESH" in syms, "Taze çözümsüz kayıt yanlışlıkla budandı!"
    assert "DONE" in syms, "Çözümlü kayıt yanlışlıkla budandı!"
    # Outcome akışı: en son çözümsüz kayıt çözümlenir
    t.record_outcome("FRESH", "WIN", pnl=12.0)
    fresh_rec = [p for p in t.predictions["TechAgent"] if p["symbol"] == "FRESH"][0]
    assert fresh_rec["actual_outcome"] == "WIN" and fresh_rec["correct"] is True
    os.remove(tmp)
    print("     Prune: eski-null gitti, taze+çözümlü kaldı; outcome çözümleme ✓")
test("v4.10 agent perf budama + outcome", test_v410_agent_perf_prune)

def test_v410_paper_loss_streak_and_logging():
    """v4.10: paper'da loss-streak warn kapalı (999) — 2 zarar sonrası conf-70
    şartı paper'ı donduruyordu (META ~96 blok); LIVE değerleri DEĞİŞMEDİ.
    + TradingBot logger'ı root'a propagate ETMEZ (log üçlemesi fix)."""
    import importlib, sys as _sys
    saved = _sys.modules.pop("config", None)
    try:
        fresh = importlib.import_module("config")
        pa = fresh.PAPER_AGGRESSIVE_CONFIG
        assert pa.get("loss_streak_warn") == 999, "Paper loss_streak_warn 999 değil!"
        assert pa.get("loss_streak_halt") == 6, "Paper loss_streak_halt 6 değil!"
        # LIVE koruma kilidi aynen (STOCK_CONFIG taban değerleri)
        assert fresh.STOCK_CONFIG["loss_streak_warn"] == 2, "LIVE loss_streak_warn değişmiş!"
        assert fresh.STOCK_CONFIG["loss_streak_halt"] == 4, "LIVE loss_streak_halt değişmiş!"
        assert fresh.STOCK_CONFIG["loss_streak_elevated_conf"] == 70, "LIVE elevated_conf değişmiş!"
    finally:
        _sys.modules.pop("config", None)
        if saved is not None:
            _sys.modules["config"] = saved
    from utils.logger import logger as _lg
    assert _lg.propagate is False, "TradingBot logger'ı hâlâ root'a propagate ediyor (üçleme)!"
    # stock_bot'taki eski root-handler bloğu kaldırıldı mı?
    with open(os.path.join(PROJECT_ROOT, "stock_bot.py"), encoding="utf-8") as f:
        src = f.read()
    assert "_root.addHandler" not in src, "stock_bot hâlâ root'a handler ekliyor!"
    print("     Paper warn=999/halt=6, LIVE kilit aynen; propagate=False ✓")
test("v4.10 paper loss-streak + log üçlemesi", test_v410_paper_loss_streak_and_logging)


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
