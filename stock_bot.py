"""
Stock Trading Bot — Hisse Senedi Al-Sat Botu
Swing trading + sınırlı day trade stratejisi.

Özellikler:
  - NYSE/NASDAQ piyasa saatleri kontrolü
  - PDT kuralı koruması (max 2 day trade/hafta)
  - 5 uzman ajan sistemi (Tech, Fund, Sent, Social, Risk)
  - Dinamik sabah taraması
  - Earnings takvimi koruması
  - VIX + Petrol + Jeopolitik risk takibi
  - Alpaca Trading API (hisse senedi, komisyon $0)
  - KillSwitch acil durum koruması
  - Wash Sale kuralı takibi
  - Sektör korelasyon koruması
  - Pozisyon senkronizasyonu (restart-safe)
"""
import os
import sys
import time
import json
import logging
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional

import pandas as pd
import numpy as np

# Alpaca
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus, OrderSide
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

# Teknik Analiz
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import BollingerBands, AverageTrueRange

# Config
from config import (
    ALPACA_API_KEY, ALPACA_SECRET_KEY, TRADING_MODE, BOT_MODE,
    get_base_url, STOCK_CONFIG, SHORT_CONFIG, STOCK_IDS, STOCK_SEARCH_TERMS,
    SECTOR_MAP, MARKET_REGIME_CONFIG,
    OPTIONS_CONFIG, PAPER_AGGRESSIVE_CONFIG, state_path,
)

# Core modüller
from core.market_hours import MarketHours
from core.pdt_tracker import PDTTracker
from core.stock_screener import StockScreener
from core.earnings_calendar import EarningsCalendar
from core.agent_coordinator import AgentCoordinator
from core.analyzer import TechnicalAnalyzer
from core.executor import OrderExecutor
from core.short_executor import ShortExecutor
from core.position_manager import PositionManager
from core.trade_gates import TradeGates
from core.news_analyzer import StockNewsAnalyzer
from core.social_sentiment import SocialSentimentAnalyzer
from core.fundamental_analyzer import FundamentalAnalyzer
from core.macro_data import MacroDataAnalyzer
from core.kill_switch import KillSwitch
from core.compliance import WashSaleTracker
from core.notifier import TelegramNotifier
from core.performance_tracker import PerformanceTracker
from core.sector_rotation import SectorRotator
from core.position_sizer import PositionSizer
from core.volume_analyzer import VolumeAnalyzer
from core.agent_performance import AgentPerformanceTracker
from core.gap_scanner import GapScanner
from core.relative_strength import RelativeStrength
from core.market_regime import MarketRegimeDetector
from core.signal_queue import SignalQueue
from core.options_engine import OptionsEngine
from core.options_analyzer import OptionsAnalyzer
from core.options_executor import OptionsExecutor
from core.options_manager import OptionsPositionManager

# FinBERT opsiyonel
try:
    from core.finbert_analyzer import FinBERTAnalyzer
    FINBERT_AVAILABLE = True
except ImportError:
    FINBERT_AVAILABLE = False

from utils.logger import logger


# ============================================================
# FLUSH STREAM HANDLER (Docker/Coolify log çıktısı)
# ============================================================
class FlushStreamHandler(logging.StreamHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()

# Root logger'a flush handler ekle
_root = logging.getLogger()
if not any(isinstance(h, FlushStreamHandler) for h in _root.handlers):
    fh = FlushStreamHandler(sys.stdout)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    ))
    _root.addHandler(fh)
    _root.setLevel(logging.INFO)


class StockBot:
    """
    Hisse Senedi Trading Bot — Swing + Sınırlı Day Trade.
    
    Günlük akış:
      1. Pre-market (09:00 ET): Sabah taraması + haber analizi
      2. Market open (09:30): İlk 30dk volatil, gözetle
      3. Safe zone (10:00-15:45): Analiz + alım
      4. Close (15:45-16:00): Trailing stop kontrol
      5. After-hours: Sadece olağanüstü fırsatlarda
    """

    POSITIONS_FILE = "bot_positions.json"

    def __init__(self):
        config = STOCK_CONFIG

        # State dosyaları live/paper için izole (A1)
        self.POSITIONS_FILE = state_path("bot_positions.json")
        self._daily_baseline_file = state_path("daily_baseline.json")
        # Yönetim bayrakları (partial_sold/breakeven_set/highest_price) pozisyon
        # geçici olarak sync'ten düşse bile kaybolmasın diye cache (A6 — cascade önleme)
        self._exit_flag_cache = {}
        self._floor_block = False  # Equity floor ihlalinde yeni alım durdurma (A3)

        # Alpaca istemcileri
        is_paper = TRADING_MODE != "live"
        self.is_paper = is_paper
        self.client = TradingClient(
            ALPACA_API_KEY, ALPACA_SECRET_KEY,
            paper=is_paper,
        )
        self.data_client = StockHistoricalDataClient(
            api_key=ALPACA_API_KEY, secret_key=ALPACA_SECRET_KEY,
        )

        # Hesap bilgileri
        account = self.client.get_account()
        equity = float(account.equity)
        # Günlük kayıp baz çizgisi: restart'ta SIFIRLANMAZ (A2).
        # Diskte bugünün (ET) bazı varsa onu kullan; yoksa Alpaca'nın
        # gün-başı (last_equity) değerini baz al ve kalıcı yaz.
        self.initial_equity = self._load_or_init_daily_baseline(account, equity)
        self.equity = equity

        # Pozisyon limitleri
        self.max_pos_usd = config.get("live_max_position_usd", 200)
        if is_paper:
            self.max_pos_usd = config.get("max_position_usd", 200)
        else:
            # LIVE: sizer/executor'ın okuduğu ortak anahtarları live değerleriyle doldur.
            # conf_position_bands → güvene göre kademeli boyut ($100-300);
            # fixed_position_usd > 0 → düz sabit boyut (eski mod, bantlar yoksa).
            # İkisi de Kelly tabanının ürettiği ~$25'lik işlemleri devre dışı bırakır.
            config["conf_position_bands"] = config.get("live_conf_position_bands") or []
            config["fixed_position_usd"] = config.get("live_fixed_position_usd", 0)
            config["max_position_usd"] = self.max_pos_usd

        self.equity_floor = equity * config.get("equity_floor_pct", 0.85)

        # Durum değişkenleri
        self.positions = {}
        self.short_positions = {}  # SHORT pozisyonlar
        self.last_trade_time = {}
        self.trades_today = []
        self.sell_cooldown = {}
        self.consecutive_errors = 0
        self._consecutive_losses = 0
        self._symbol_consecutive_losses = {}  # Hisse bazli ardisik zarar
        self._daily_buys_count = 0
        self._last_status_time = datetime.min
        self._heartbeat_counter = 0
        self._morning_scan_done = False
        self._morning_scan_date = None
        self._market_regime = "UNKNOWN"   # BULL / BEAR / UNKNOWN
        self._regime_check_time = datetime.min
        self._daily_reset_date = None

        # Core modüller
        self.market_hours = MarketHours()
        self.pdt_tracker = PDTTracker(equity=equity)
        self.screener = StockScreener(
            api_key=ALPACA_API_KEY, secret_key=ALPACA_SECRET_KEY
        )
        self.earnings_calendar = EarningsCalendar()
        self.coordinator = AgentCoordinator()
        self.executor = OrderExecutor(self)
        self.short_executor = ShortExecutor(self)  # SHORT executor
        self.position_manager = PositionManager(self)
        self.trade_gates = TradeGates(self)
        self.news_analyzer = StockNewsAnalyzer()
        self.social_analyzer = SocialSentimentAnalyzer()
        self.fundamental_analyzer = FundamentalAnalyzer()
        self.macro_analyzer = MacroDataAnalyzer()

        # KillSwitch — acil durum koruması
        self.kill_switch = KillSwitch(
            max_consecutive_errors=config.get("max_consecutive_errors", 5),
            max_daily_loss_pct=config.get("max_daily_loss_pct", 0.03),
        )
        self.kill_switch.set_callback(self._emergency_close_all)

        # Wash Sale takibi
        self.wash_sale_tracker = WashSaleTracker()

        # Telegram bildirimleri
        self.notifier = TelegramNotifier()

        # Performans takibi
        self.performance = PerformanceTracker()

        # Sektör rotasyonu (VIX bazlı)
        self.sector_rotator = SectorRotator()

        # FinBERT — news_analyzer'ın instance'ını paylaş (çift yüklemeyi önle, ~800MB RAM tasarrufu)
        self.finbert = getattr(self.news_analyzer, 'finbert', None)

        # Teknik analizci
        self.analyzer = TechnicalAnalyzer(self)

        # Iyilestirme modullleri (v2.0)
        self.position_sizer = PositionSizer(performance_tracker=self.performance)
        self.volume_analyzer = VolumeAnalyzer()
        self.agent_perf = AgentPerformanceTracker()

        # Iyilestirme modulleri (v3.0)
        self.gap_scanner = GapScanner()
        self.relative_strength = RelativeStrength()
        self.regime_detector = MarketRegimeDetector()
        self.signal_queue = SignalQueue()
        self._spy_df_cache = None
        self._spy_cache_time = datetime.min
        self._gap_scan_done_today = False
        self._gap_scan_date = date.min

        # Options modülleri (v4.0 — CALL/PUT)
        self.options_positions = {}  # Açık opsiyon pozisyonları
        self.options_analyzer = OptionsAnalyzer(
            trading_client=self.client,
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
        )
        self.options_engine = OptionsEngine(self)
        self.options_executor = OptionsExecutor(self)
        self.options_manager = OptionsPositionManager(self)
        self._options_enabled = (
            OPTIONS_CONFIG.get("options_enabled", False)
            and (is_paper or not OPTIONS_CONFIG.get("options_paper_only", True))
        )

        # === PAPER AGRESİF MOD ===
        if is_paper:
            for key, value in PAPER_AGGRESSIVE_CONFIG.items():
                if key.startswith("short_"):
                    SHORT_CONFIG[key] = value  # Short ayarları SHORT_CONFIG'a
                elif key.startswith("enable_") or key.startswith("prefer_"):
                    pass  # Bunlar sadece referans, config'a eklenmez
                else:
                    config[key] = value  # Geri kalan her şey STOCK_CONFIG'a
            # max_pos_usd init'te override'dan ÖNCE atanıyordu → banner/fallback
            # eski $200'ü gösteriyordu; aggressive değeriyle senkronla
            self.max_pos_usd = config.get("max_position_usd", self.max_pos_usd)
            logger.info("  📈 PAPER AGGRESSIVE MODE: Aktif")
            logger.info(f"     Max poz: {config.get('max_open_positions')} | "
                        f"Güven: {config.get('min_confidence_score')} | "
                        f"Pozisyon: ${config.get('max_position_usd')}")

        # === INDEX PARKING (boştaki nakit → SPY beta; paper-first, config-gated) ===
        # Sync'ten ÖNCE kurulmalı: sync parking pozisyonunu dışlamak için manager'ı kullanır.
        from core.index_parking import IndexParkingManager
        self.index_parking = IndexParkingManager(self, config)

        # === POZİSYON SENKRONİZASYONU (restart-safe) ===
        self._sync_positions_from_alpaca()
        self._load_position_metadata()

        # Sunucu-taraflı koruma emri garantisi: bracket DAY bacakları düşmüş,
        # emirsiz kalmış pozisyonlara stop yerleştir (bot çökse bile korunsun)
        try:
            self.position_manager.ensure_protective_stops(config)
        except Exception as e:
            logger.warning(f"  Koruma emri garantisi başarısız: {e}")

        mode_str = "PAPER" if is_paper else "🔴 LIVE"
        bot_mode_str = {"long_only": "📈 LONG ONLY", "short_only": "📉 SHORT ONLY", "both": "📊 LONG + SHORT"}.get(BOT_MODE, BOT_MODE)
        options_str = "✅ CALL/PUT" if self._options_enabled else "❌"
        logger.info("=" * 60)
        logger.info(f"  STOCK TRADING BOT BASLATILDI")
        logger.info(f"  Mod: {mode_str} | Bot: {bot_mode_str}")
        logger.info(f"  Equity: ${equity:,.2f}")
        bands = config.get("conf_position_bands") or []
        fixed_usd = config.get("fixed_position_usd", 0)
        if bands:
            size_mode = f"GÜVENE GÖRE ${bands[0][1]}-{bands[-1][1]}"
        elif fixed_usd:
            size_mode = f"SABİT ${fixed_usd}/alım"
        else:
            size_mode = "Kelly-ATR adaptif"
        logger.info(f"  Max pozisyon: ${self.max_pos_usd} | Boyut: {size_mode} | Floor: ${self.equity_floor:,.2f}")
        logger.info(f"  Hisse havuzu: {len(config['symbols'])} hisse")
        logger.info(f"  Acik pozisyon: {len(self.positions)} long | {len(self.short_positions)} short")
        logger.info(f"  Options: {options_str} | Opsiyon poz: {len(self.options_positions)}")
        logger.info(f"  PDT: {'EXEMPT' if equity >= 25000 else f'ACTIVE (max 2 DT/hafta)'}")
        logger.info(f"  KillSwitch: AKTIF | WashSale: AKTIF")
        logger.info("=" * 60)

    # ============================================================
    # ANA DÖNGÜ
    # ============================================================

    def run(self):
        """Ana trading döngüsü."""
        config = STOCK_CONFIG
        logger.info("Bot ana döngüye giriyor...")

        while True:
            try:
                # KillSwitch kontrolü
                if self.kill_switch.is_active:
                    logger.error(f"🚨 KILL SWITCH AKTİF: {self.kill_switch.kill_reason}")
                    logger.error("Bot durduruldu. kill_switch.json silinerek restart yapılabilir.")
                    time.sleep(60)
                    continue

                # Günlük reset
                self._daily_reset()

                # Heartbeat
                self._heartbeat_counter += 1
                if self._heartbeat_counter % config.get("heartbeat_interval", 30) == 0:
                    self._log_heartbeat()

                # Market durumu
                market_status = self.market_hours.get_market_status()

                # Piyasa AÇILIŞ geçişinde koruma emirlerini garantiye al:
                # kapalıyken verilen fractional stop'lar reddedilmiş olabilir,
                # dünkü DAY bracket bacakları da düşmüştür.
                if (market_status["status"] == "OPEN"
                        and getattr(self, "_last_market_status", "") != "OPEN"):
                    try:
                        self.position_manager.ensure_protective_stops(config)
                    except Exception as e:
                        logger.debug(f"  Açılış koruma emri kontrolü hatası: {e}")
                self._last_market_status = market_status["status"]

                # Piyasa kapalı → bekle
                if market_status["status"] == "CLOSED":
                    wait_secs = min(self.market_hours.seconds_until_open(), 300)
                    if self._heartbeat_counter % 60 == 0:
                        logger.info(f"  Piyasa kapalı ({market_status['reason']}) — {wait_secs//60}dk bekleniyor")
                    time.sleep(min(wait_secs, 60))
                    continue

                # Pre-market → sabah taraması
                if market_status["status"] == "PRE_MARKET":
                    self._do_morning_scan()
                    time.sleep(30)
                    continue

                # After-hours → sadece pozisyon yönetimi
                if market_status["status"] == "AFTER_HOURS":
                    self._manage_positions(config)
                    # Short pozisyon yönetimi (after-hours)
                    if BOT_MODE in ("short_only", "both") and SHORT_CONFIG.get("short_enabled", False):
                        try:
                            self.position_manager.manage_short_positions(config, SHORT_CONFIG)
                        except Exception as e:
                            logger.debug(f"  AH Short yonetim hatasi: {e}")
                    # Options pozisyon yönetimi (after-hours)
                    if self._options_enabled:
                        try:
                            self.options_manager.manage_positions(OPTIONS_CONFIG)
                        except Exception as e:
                            logger.debug(f"  AH Options yonetim hatasi: {e}")
                    time.sleep(30)
                    continue

                # === PİYASA AÇIK ===

                # Günlük kayıp kontrolü (KillSwitch)
                try:
                    account = self.client.get_account()
                    self.equity = float(account.equity)
                    if self.kill_switch.check_daily_loss(self.equity, self.initial_equity):
                        continue  # Kill tetiklendi, döngü başına dön
                    self.kill_switch.reset_error_count()
                except Exception as e:
                    if self.kill_switch.check_api_error(e):
                        continue

                # EQUITY FLOOR — live+paper: ihlalde YENİ ALIM durdurulur,
                # mevcut pozisyonlar yönetilmeye devam eder (A3).
                self._floor_block = self.equity_floor > 0 and self.equity < self.equity_floor
                if self._floor_block and self._heartbeat_counter % 30 == 0:
                    logger.warning(
                        f"  🛑 EQUITY FLOOR: ${self.equity:,.2f} < floor ${self.equity_floor:,.2f} "
                        f"— yeni alım DURDURULDU (pozisyonlar yönetiliyor)"
                    )

                # Piyasa rejim tespiti (her 30 dk) — v3.0 gelismis rejim
                self._update_market_regime()

                # Pre-Market Gap Scanner (gunde 1 kez, piyasa acilmadan)
                if not self._gap_scan_done_today or self._gap_scan_date != date.today():
                    has_open = len(self.positions) > 0 or len(self.short_positions) > 0
                    if has_open:
                        try:
                            gap_alerts = self.gap_scanner.scan_overnight_gaps(self)
                            if gap_alerts:
                                self.gap_scanner.execute_gap_actions(self, gap_alerts)
                        except Exception as e:
                            logger.debug(f"  Gap scan hatasi: {e}")
                    self._gap_scan_done_today = True
                    self._gap_scan_date = date.today()

                # Pozisyon yonetimi (her dongude)
                if BOT_MODE in ("long_only", "both"):
                    self._manage_positions(config)

                # Short pozisyon yonetimi (her dongude)
                if BOT_MODE in ("short_only", "both") and SHORT_CONFIG.get("short_enabled", False):
                    try:
                        self.position_manager.manage_short_positions(config, SHORT_CONFIG)
                    except Exception as e:
                        logger.debug(f"  Short pozisyon yonetim hatasi: {e}")

                # Options pozisyon yonetimi (her dongude)
                if self._options_enabled:
                    try:
                        self.options_manager.manage_positions(OPTIONS_CONFIG)
                    except Exception as e:
                        self._opt_mgr_errors = getattr(self, '_opt_mgr_errors', 0) + 1
                        if self._opt_mgr_errors >= 3:
                            logger.warning(f"  Options pozisyon yonetim hatasi (ardısık {self._opt_mgr_errors}x): {e}")
                            self._opt_mgr_errors = 0
                        else:
                            logger.debug(f"  Options pozisyon yonetim hatasi: {e}")

                # Idle-cash index parking (paper-first) — günde 1 rebalance, nakit → beta
                try:
                    self.index_parking.maybe_rebalance()
                except Exception as e:
                    logger.debug(f"  Index parking hatasi: {e}")

                # Signal Queue kontrolu — bekleyen sinyalleri kontrol et
                # (Equity floor ihlalinde yeni giriş yapılmaz — A3)
                try:
                    ready_signals = [] if self._floor_block else self.signal_queue.check_entries(self)
                    for sig in ready_signals:
                        sym = sig["symbol"]
                        sig_analysis = sig.get("analysis", {})
                        if sig["signal"] == "BUY" and BOT_MODE in ("long_only", "both"):
                            self.executor.execute_buy(sym, sig_analysis, config)
                            # Signal queue'dan gelen güçlü BUY sinyalinde opsiyon da ekle
                            if self._options_enabled and sig.get("confidence", 0) >= 60:
                                try:
                                    opt = self.options_engine.evaluate_option_trade(
                                        sym, sig_analysis,
                                        {"signal": "BUY", "confidence": sig.get("confidence", 0)},
                                        OPTIONS_CONFIG
                                    )
                                    if opt and opt["type"] == "CALL":
                                        self.options_executor.execute_call(opt, sig_analysis, OPTIONS_CONFIG)
                                except Exception:
                                    pass
                        elif sig["signal"] == "SHORT" and BOT_MODE in ("short_only", "both"):
                            self.short_executor.execute_short(sym, sig_analysis, config, SHORT_CONFIG)
                            # Signal queue'dan gelen güçlü SHORT sinyalinde PUT da ekle
                            if self._options_enabled and sig.get("confidence", 0) >= 60:
                                try:
                                    opt = self.options_engine.evaluate_option_trade(
                                        sym, sig_analysis,
                                        {"signal": "SHORT", "confidence": sig.get("confidence", 0)},
                                        OPTIONS_CONFIG
                                    )
                                    if opt and opt["type"] == "PUT":
                                        self.options_executor.execute_put(opt, sig_analysis, OPTIONS_CONFIG)
                                except Exception:
                                    pass
                except Exception as e:
                    logger.debug(f"  Signal queue hatasi: {e}")

                # Guvenli bolge kontrolu
                if not market_status["is_safe_zone"]:
                    time.sleep(10)
                    continue

                # Mevcut toplam pozisyon sayısı (long + short + options)
                open_count = len(self.positions) + len(self.short_positions) + len(self.options_positions)
                max_positions = config.get("max_open_positions", 3)

                if open_count >= max_positions:
                    time.sleep(config.get("scan_interval_seconds", 30))
                    continue

                # Sabah taraması yapılmadıysa yap
                if not self._morning_scan_done or self._morning_scan_date != date.today():
                    self._do_morning_scan()

                # 🌍 Jeopolitik risk taraması (her döngüde, 2dk cache)
                try:
                    geo_scan = self.news_analyzer.scan_geopolitical_breaking()
                    geo_level = geo_scan.get("geo_risk_level", "NORMAL")
                    geo_score = geo_scan.get("geo_risk_score", 0)

                    if geo_level == "CRITICAL":
                        logger.warning(
                            f"  🚨 JEOPOLİTİK KRİTİK! Skor: {geo_score} | "
                            f"Yeni alım ENGELLENDİ. Mevcut pozisyonlar korunuyor."
                        )
                        time.sleep(config.get("scan_interval_seconds", 30))
                        continue  # Yeni alım yapma, sadece pozisyon yönet
                    elif geo_level == "HIGH":
                        max_positions = min(max_positions, 1)
                        logger.info(f"  ⚠️ Jeopolitik HIGH — Max pozisyon 1'e düşürüldü")
                except Exception as e:
                    logger.debug(f"  Jeopolitik tarama hatası: {e}")

                # Hisse analizi (Equity floor ihlalinde yeni alım yapılmaz — A3)
                symbols = [] if self._floor_block else self._get_symbols_to_analyze()
                for symbol in symbols:
                    if len(self.positions) >= max_positions:
                        break
                    if symbol in self.positions:
                        continue
                    # Sektör korelasyon koruması
                    if self._sector_limit_reached(symbol, config):
                        continue
                    # Wash Sale kontrolü
                    is_wash, wash_reason = self.wash_sale_tracker.check_wash_sale(symbol)
                    if is_wash:
                        logger.info(f"  {symbol} WASH SALE: {wash_reason}")
                        continue
                    self._analyze_and_trade(symbol, config)

                # Durum raporu
                self._periodic_status_report(config)

                # Adaptif tarama araligi:
                # Acik pozisyon varken 15 saniye (hizli tepki)
                # Pozisyon yokken 30 saniye (API tasarrufu)
                has_positions = (
                    len(self.positions) > 0
                    or len(self.short_positions) > 0
                    or len(self.options_positions) > 0
                )
                interval = 15 if has_positions else config.get("scan_interval_seconds", 30)
                
                # Her döngü sonunda metadata kaydet ki bot çökerse state (partial_sold vs) kaybolmasın
                self._save_position_metadata()
                
                time.sleep(interval)

            except KeyboardInterrupt:
                logger.info("Bot durduruldu (Ctrl+C)")
                self._save_position_metadata()
                break
            except Exception as e:
                self.consecutive_errors += 1
                logger.error(f"Ana döngü hatası: {e}")
                if self.kill_switch.check_api_error(e):
                    continue
                if self.consecutive_errors >= config.get("max_consecutive_errors", 5):
                    logger.critical(f"  {self.consecutive_errors} ardışık hata! 5 dakika bekleniyor.")
                    time.sleep(300)
                    self.consecutive_errors = 0
                else:
                    time.sleep(config.get("error_retry_sleep", 30))

    # ============================================================
    # SABAH TARAMASI
    # ============================================================

    def _do_morning_scan(self):
        """Pre-market sabah taraması."""
        if self._morning_scan_done and self._morning_scan_date == date.today():
            return

        logger.info("=" * 50)
        logger.info("  🌅 SABAH TARAMASI")
        logger.info("=" * 50)

        # Makro analiz (VIX, petrol, faiz)
        try:
            macro = self.macro_analyzer.get_macro_score()
            logger.info(f"  Makro skor: {macro['macro_score']} ({macro['macro_signal']})")
            if "oil" in macro:
                logger.info(f"  Petrol: {macro['oil'].get('description', 'N/A')}")
            if "vix" in macro:
                vix_data = macro["vix"]
                vix_value = vix_data.get("value", 20)
                logger.info(f"  VIX: {vix_data.get('description', 'N/A')} ({vix_value:.1f})")
                # Sektör rotasyonu güncelle
                self.sector_rotator.update_vix(vix_value)
                sr_status = self.sector_rotator.get_status()
                logger.info(
                    f"  🔄 Sektör Rejim: {sr_status['regime'].upper()} | "
                    f"Max Poz: {sr_status['max_positions']} | "
                    f"Favori: {', '.join(sr_status['preferred_sectors']) or 'YOK'} | "
                    f"Kaçın: {', '.join(sr_status['avoid_sectors']) or 'YOK'}"
                )
        except Exception as e:
            logger.debug(f"  Makro analiz hatası: {e}")

        # Piyasa duyarlılığı
        try:
            sentiment = self.news_analyzer.get_market_sentiment()
            logger.info(
                f"  Piyasa: SPY={sentiment.get('spy_sentiment', 'N/A')}, "
                f"QQQ={sentiment.get('qqq_sentiment', 'N/A')}, "
                f"Jeopolitik={sentiment.get('geopolitical_risk', 'N/A')}"
            )
        except Exception as e:
            logger.debug(f"  Piyasa sentiment hatası: {e}")

        # Hisse taraması
        try:
            opportunities = self.screener.morning_scan()
            if opportunities:
                logger.info(f"  En iyi fırsatlar:")
                for opp in opportunities[:5]:
                    logger.info(f"    {opp['symbol']}: Skor={opp['score']:.0f}")
        except Exception as e:
            logger.debug(f"  Tarama hatası: {e}")

        self._morning_scan_done = True
        self._morning_scan_date = date.today()

    # ============================================================
    # PİYASA REJİM TESPİTİ
    # ============================================================

    def _update_market_regime(self):
        """SPY bazli piyasa rejim tespiti — v3.0 gelismis (ADX+BB+EMA)."""
        if not MARKET_REGIME_CONFIG.get("enabled", True):
            return

        # 30 dakikada bir kontrol et
        now = datetime.now()
        if (now - self._regime_check_time).total_seconds() < 1800:
            return

        self._regime_check_time = now
        benchmark = MARKET_REGIME_CONFIG.get("benchmark_symbol", "SPY")

        try:
            df = self.get_stock_bars(benchmark, days=250)
            if df.empty or len(df) < 50:
                return

            # SPY verisini cache'le (relative strength icin)
            self._spy_df_cache = df
            self._spy_cache_time = now

            close = df["close"]
            price = float(close.iloc[-1])

            # Eski EMA200 rejimi (backward compat)
            ema_period = MARKET_REGIME_CONFIG.get("ema_period", 200)
            ema_period = min(ema_period, len(close) - 1)
            ema200 = EMAIndicator(close, window=ema_period).ema_indicator().iloc[-1]

            old_regime = self._market_regime

            if price < ema200:
                self._market_regime = "BEAR"
            else:
                self._market_regime = "BULL"

            # v3.0 Gelismis 4-rejim algilama (ADX + BB + EMA)
            try:
                vix = getattr(self, '_last_vix', 0)
                enhanced = self.regime_detector.detect_regime(df, vix=vix)
                self._enhanced_regime = enhanced
                self._regime_trading_mode = enhanced.get("trading_mode", "NORMAL")

                if old_regime != self._market_regime or self.regime_detector.current_regime != enhanced["regime"]:
                    logger.info(
                        f"  REJIM: {self._market_regime} | "
                        f"Detay: {enhanced['regime']} ({enhanced['trading_mode']}) "
                        f"| {enhanced['description']}"
                    )
            except Exception as e:
                logger.debug(f"  Gelismis rejim hatasi: {e}")

        except Exception as e:
            logger.debug(f"  Rejim tespiti hatasi: {e}")

    # ============================================================
    # HİSSE ANALİZİ VE İŞLEM
    # ============================================================

    def _analyze_and_trade(self, symbol: str, config: Dict):
        """Tek bir hisseyi analiz et ve gerekirse islem yap (LONG veya SHORT).
        BOT_MODE: 'long_only' | 'short_only' | 'both'
        """
        try:
            # Parking sembolü asla trade edilmez (bot'un nakit-sleeve'i, agent'a girmez)
            if self.index_parking.is_parking_symbol(symbol):
                return

            # Teknik analiz
            analysis = self._get_technical_analysis(symbol, config)
            if analysis is None:
                return

            # Multi-agent karar
            decision = self._get_agent_decision(symbol, analysis, config)

            # SHORT sinyal mapping:
            # 1. analyzer.py zaten SHORT üretir (sell_score >= 45)
            # 2. Coordinator SELL döndürür ama SELL != SHORT:
            #    - Eğer elimizde long pozisyon varsa → gerçek SELL (kapat)
            #    - Eğer pozisyonumuz yoksa → SHORT (yeni kısa pozisyon aç)
            if decision["signal"] == "SELL" and symbol not in self.positions:
                decision["signal"] = "SHORT"
            # Teknik analizden gelen native SHORT sinyalini de coordinator'dan geçir
            if analysis.get("signal") == "SHORT" and decision["signal"] == "HOLD":
                decision["signal"] = "SHORT"
                decision["confidence"] = max(decision.get("confidence", 0), analysis.get("confidence", 0))

            # Ters ETF & Endeks filtresi
            _inverse_etfs = MARKET_REGIME_CONFIG.get("inverse_etf_symbols", [])
            _index_symbols = MARKET_REGIME_CONFIG.get("index_symbols", [])
            _is_inverse_etf = symbol in _inverse_etfs
            _is_index = symbol in _index_symbols

            # Endeksler asla trade edilmez (sadece rejim tespiti icin)
            if _is_index:
                return

            # Rejim bazli guven ayarlamasi
            effective_buy_conf = config.get("min_confidence_score", 50)
            effective_short_conf = SHORT_CONFIG.get("short_min_confidence", 45)

            if self._market_regime == "BEAR":
                # Bear modda: BUY icin daha yuksek esik, SHORT icin daha dusuk
                effective_buy_conf += MARKET_REGIME_CONFIG.get("bear_buy_conf_increase", 10)
                effective_short_conf -= MARKET_REGIME_CONFIG.get("bear_short_conf_reduction", 10)

            # Ters ETF'ler sadece BEAR modda BUY (long) olarak alinir
            if _is_inverse_etf:
                if self._market_regime != "BEAR":
                    return  # Bull/Unknown modda ters ETF alma
                # Ters ETF icin short sinyal ALMA (zaten ters)
                if decision["signal"] == "SHORT":
                    return

            # === OPTIONS DEĞERLENDİRMESİ (v4.0) ===
            # Güçlü sinyalde hisse yerine opsiyon tercih et
            if self._options_enabled:
                try:
                    if self.options_engine.should_prefer_options(
                        symbol, decision.get("confidence", 0), OPTIONS_CONFIG
                    ):
                        option_trade = self.options_engine.evaluate_option_trade(
                            symbol, analysis, decision, OPTIONS_CONFIG
                        )
                        if option_trade:
                            analysis["confidence"] = decision["confidence"]
                            analysis["reasons"] = [decision.get("reasoning", "")]
                            if option_trade["type"] == "CALL":
                                self.options_executor.execute_call(
                                    option_trade, analysis, OPTIONS_CONFIG
                                )
                            elif option_trade["type"] == "PUT":
                                self.options_executor.execute_put(
                                    option_trade, analysis, OPTIONS_CONFIG
                                )
                            return  # Opsiyon aldıysa hisse alma
                except Exception as e:
                    logger.debug(f"  {symbol} Options değerlendirme hatası: {e}")

            # === LONG (BUY) — BOT_MODE: 'long_only' veya 'both' ===
            if (decision["signal"] == "BUY"
                    and BOT_MODE in ("long_only", "both")
                    and decision["confidence"] >= effective_buy_conf):
                # Sektör rotasyonu kontrolü (VIX bazlı)
                if not self.sector_rotator.should_buy(symbol):
                    logger.debug(f"  {symbol} SEKTÖR ROTASYON BLOK: {self.sector_rotator.current_regime} rejiminde kaçınılıyor")
                    return

                # Gate kontrolü
                passed, block_reason = self.trade_gates.check_all_gates(symbol, analysis, config)
                if passed:
                    analysis["confidence"] = decision["confidence"]
                    analysis["reasons"] = [decision["reasoning"]]
                    analysis["sector_weight"] = self.sector_rotator.get_weight_multiplier(symbol)
                    if _is_inverse_etf:
                        analysis["reasons"].append("🐻 BEAR_MODE_INVERSE_ETF")
                    self.executor.execute_buy(symbol, analysis, config)

                    # Opsiyon da ekle (güçlü sinyalde hisse + opsiyon birlikte)
                    if self._options_enabled and decision["confidence"] >= 65:
                        try:
                            opt = self.options_engine.evaluate_option_trade(
                                symbol, analysis, decision, OPTIONS_CONFIG
                            )
                            if opt and opt["type"] == "CALL":
                                self.options_executor.execute_call(
                                    opt, analysis, OPTIONS_CONFIG
                                )
                        except Exception:
                            pass
                else:
                    logger.debug(f"  {symbol} GATE BLOK: {block_reason}")

                    # Gate'den geçemese bile opsiyon dene (daha az risk)
                    if self._options_enabled and decision["confidence"] >= 55:
                        try:
                            opt = self.options_engine.evaluate_option_trade(
                                symbol, analysis, decision, OPTIONS_CONFIG
                            )
                            if opt and opt["type"] == "CALL":
                                self.options_executor.execute_call(
                                    opt, analysis, OPTIONS_CONFIG
                                )
                        except Exception:
                            pass

            # === SHORT — BOT_MODE: 'short_only' veya 'both' ===
            elif (decision["signal"] == "SHORT"
                  and BOT_MODE in ("short_only", "both")
                  and SHORT_CONFIG.get("short_enabled", False)
                  and decision["confidence"] >= effective_short_conf):

                # Zaten short pozisyonumuz var mi?
                if symbol in self.short_positions:
                    return

                # Zaten long pozisyonumuz var mi? (ayni anda long+short yapma)
                if symbol in self.positions:
                    return

                logger.info(f"  🔻 {symbol} SHORT sinyal: Guven={decision['confidence']:.0f} | Rejim={self._market_regime} | {decision.get('reasoning', '')}")
                analysis["confidence"] = decision["confidence"]
                analysis["reasons"] = [decision.get("reasoning", "SHORT")]
                if self._market_regime == "BEAR":
                    analysis["reasons"].append("🐻 BEAR_MODE")
                self.short_executor.execute_short(symbol, analysis, config, SHORT_CONFIG)

                # SHORT sinyalinde PUT opsiyon da ekle
                if self._options_enabled and decision["confidence"] >= 55:
                    try:
                        opt = self.options_engine.evaluate_option_trade(
                            symbol, analysis, decision, OPTIONS_CONFIG
                        )
                        if opt and opt["type"] == "PUT":
                            self.options_executor.execute_put(
                                opt, analysis, OPTIONS_CONFIG
                            )
                    except Exception:
                        pass

        except Exception as e:
            logger.debug(f"  {symbol} analiz hatası: {e}")

    def _get_technical_analysis(self, symbol: str, config: Dict) -> Optional[Dict]:
        """Hisse için gelişmiş teknik analiz + volume analizi.

        İçerik: RSI, EMA, MACD, BB, ATR, Ichimoku, ADX, OBV, Fibonacci,
        RSI Divergence, VWAP, S/R + Unusual Volume + Smart Money algılama.
        SHORT sinyali de üretir (sell_score >= 45).
        """
        try:
            df = self.get_stock_bars(symbol, days=30)
            if df.empty or len(df) < 30:
                return None

            # Teknik analiz
            result = self.analyzer.analyze(df, config)

            # Volume analizi (Smart Money algılama)
            vol_data = self.volume_analyzer.analyze_volume(df)
            result["volume_analysis"] = vol_data

            # Volume sinyali confidence'a katki saglar
            if vol_data.get("confidence_boost", 0) > 0:
                boost = vol_data["confidence_boost"]
                vol_signal = vol_data.get("signal", "NORMAL")

                if vol_signal == "ACCUMULATION" and result["signal"] in ("BUY", "HOLD"):
                    result["confidence"] = min(result["confidence"] + boost, 100)
                    result["reasons"].append(f"SmartMoney:+{boost}({vol_signal})")
                elif vol_signal == "DISTRIBUTION" and result["signal"] in ("SHORT", "SELL", "HOLD"):
                    result["confidence"] = min(result.get("sell_score", 0) + boost, 100)
                    result["reasons"].append(f"SmartMoney:+{boost}({vol_signal})")

            # Relative Strength (SPY'a gore guc siralamasi)
            try:
                spy_df = self._spy_df_cache
                if spy_df is not None and not spy_df.empty:
                    rs_data = self.relative_strength.calculate_rs(df, spy_df)
                    result["relative_strength"] = rs_data

                    # RS bazli confidence ayarlama
                    side = "LONG" if result["signal"] in ("BUY",) else "SHORT"
                    rs_boost = self.relative_strength.get_rs_signal_boost(rs_data, side)
                    if rs_boost != 0:
                        result["confidence"] = max(0, min(result["confidence"] + rs_boost, 100))
                        result["reasons"].append(
                            f"RS:{rs_data['rank_label']}({rs_data['composite_rs']:+.1%})"
                        )
            except Exception:
                pass

            return result
        except Exception as e:
            logger.debug(f"  {symbol} teknik analiz hatası: {e}")
            return None

    def _get_agent_decision(self, symbol: str, analysis: Dict, config: Dict) -> Dict:
        """5 ajan karar sistemi."""
        try:
            # Tech data (zaten var)
            tech_data = analysis

            # Fund data
            fund_data = {"fundamental_score": 0, "metrics": {}}
            try:
                fund_data = self.fundamental_analyzer.analyze_fundamentals(symbol)
            except Exception:
                pass

            # Sentiment data
            sent_data = {"news_score": 0}
            try:
                news = self.news_analyzer.analyze_stock_news(symbol)
                sent_data = {
                    "news_score": news.get("news_score", 0),
                    "sentiment_label": news.get("signal", "NEUTRAL"),
                    "fear_greed_value": 50,
                    "fear_greed_signal": "NEUTRAL",
                }
            except Exception:
                pass

            # Social data
            social_data = {"social_score": 0}
            try:
                social_data = self.social_analyzer.analyze_social(symbol)
            except Exception:
                pass

            # Risk data
            risk_data = self._build_risk_data(analysis, config)

            # Dinamik ajan ağırlıkları (performans bazlı)
            try:
                dynamic_weights = self.agent_perf.get_dynamic_weights()
                self.coordinator.WEIGHTS = dynamic_weights
            except Exception:
                pass  # Hata durumunda varsayılan ağırlıklar kullanılır

            # Coordinator kararı
            decision = self.coordinator.decide(
                symbol, tech_data, fund_data,
                sent_data, social_data, risk_data
            )

            # Ajan tahminlerini kaydet (öz-değerlendirme için)
            try:
                if decision.get("signal") != "HOLD":
                    self.agent_perf.record_prediction(
                        symbol=symbol,
                        agent_votes=decision.get("votes", []),
                        coordinator_signal=decision["signal"],
                    )
            except Exception:
                pass

            return decision

        except Exception as e:
            logger.debug(f"  {symbol} ajan karar hatası: {e}")
            return {"signal": "HOLD", "confidence": 0}

    def _build_risk_data(self, analysis: Dict, config: Dict) -> Dict:
        """Risk ajanı için veri hazırla."""
        try:
            account = self.client.get_account()
            equity = float(account.equity)
            daily_pnl = equity - self.initial_equity
            daily_pnl_pct = (daily_pnl / self.initial_equity * 100) if self.initial_equity > 0 else 0

            # VIX
            vix = 0
            geo_risk = "NORMAL"
            oil_signal = "STABLE"
            try:
                macro = self.macro_analyzer.get_macro_score()
                vix = macro.get("vix", {}).get("vix", 0)
                oil_signal = macro.get("oil", {}).get("signal", "STABLE")
            except Exception:
                pass

            # Jeopolitik risk (haberlerden)
            try:
                market_sent = self.news_analyzer.get_market_sentiment()
                geo_risk = market_sent.get("geopolitical_risk", "NORMAL")
            except Exception:
                pass

            return {
                "daily_pnl_pct": daily_pnl_pct,
                "open_positions": len(self.positions),
                "max_positions": config.get("max_open_positions", 3),
                "atr_pct": (analysis.get("atr", 0) / analysis.get("price", 1) * 100) if analysis.get("price", 0) > 0 else 0,
                "equity_floor_hit": equity < self.equity_floor,
                "vix": vix,
                "geopolitical_risk": geo_risk,
                "oil_signal": oil_signal,
            }
        except Exception:
            return {}

    # ============================================================
    # POZİSYON YÖNETİMİ
    # ============================================================

    def _manage_positions(self, config: Dict):
        """Açık pozisyonları yönet — trailing stop, take profit, break-even."""
        try:
            self.position_manager.manage_positions(config)
        except Exception as e:
            logger.error(f"  Pozisyon yönetim hatası: {e}")

    # ============================================================
    # VERİ ÇEKME
    # ============================================================

    def get_stock_bars(self, symbol: str, days: int = 30) -> pd.DataFrame:
        """Alpaca'dan hisse bar verisi çek."""
        try:
            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Hour,
                start=datetime.now() - timedelta(days=days),
            )
            bars = self.data_client.get_stock_bars(request)
            df = bars.df

            if hasattr(df.index, 'droplevel'):
                try:
                    df = df.droplevel("symbol")
                except (KeyError, ValueError):
                    pass

            return df

        except Exception as e:
            logger.debug(f"  {symbol} bar verisi hatası: {e}")
            return pd.DataFrame()

    # ============================================================
    # YARDIMCI
    # ============================================================

    def _get_symbols_to_analyze(self) -> List[str]:
        """Analiz edilecek hisseleri döndür — tarama sonucuna göre sırala."""
        # Tarama sonuçları varsa öncelikli
        if self.screener.scan_cache:
            sorted_symbols = sorted(
                self.screener.scan_cache.keys(),
                key=lambda s: self.screener.scan_cache[s].get("score", 0),
                reverse=True,
            )
            return sorted_symbols[:10]

        # Yoksa varsayılan havuz
        return STOCK_CONFIG.get("symbols", list(STOCK_IDS.keys()))[:10]

    def _sector_limit_reached(self, symbol: str, config: Dict) -> bool:
        """Aynı sektörde max pozisyon kontrolü."""
        max_per_sector = config.get("max_positions_per_sector", 2)
        symbol_sector = SECTOR_MAP.get(symbol, "Unknown")
        if symbol_sector == "Unknown":
            return False

        sector_count = 0
        for pos_symbol in self.positions:
            if SECTOR_MAP.get(pos_symbol, "") == symbol_sector:
                sector_count += 1

        if sector_count >= max_per_sector:
            logger.debug(
                f"  {symbol} SEKTÖR LİMİT: {symbol_sector} sektöründe "
                f"{sector_count}/{max_per_sector} pozisyon dolu"
            )
            return True
        return False

    def _log_heartbeat(self):
        """Gelişmiş heartbeat logu."""
        try:
            account = self.client.get_account()
            equity = float(account.equity)
            cash = float(account.cash)
            self.equity = equity
            pnl = equity - self.initial_equity
            pnl_pct = (pnl / self.initial_equity * 100) if self.initial_equity > 0 else 0

            market_status = self.market_hours.get_market_status()
            pdt_status = self.pdt_tracker.get_status()

            # Pozisyon detayları
            pos_details = []
            for sym, data in self.positions.items():
                entry = data.get("entry_price", 0)
                pos_details.append(f"{sym}@${entry:.2f}")

            logger.info(
                f"  💓 ${equity:,.2f} ({pnl:+.2f}/{pnl_pct:+.1f}%) | "
                f"Cash: ${cash:,.2f} | "
                f"Poz: {len(self.positions)}L/{len(self.short_positions)}S/{len(self.options_positions)}O "
                f"[{', '.join(pos_details) or 'yok'}] | "
                f"İşlem: {len(self.trades_today)} | "
                f"DT: {pdt_status['week_day_trades']}/{pdt_status['max_day_trades']} | "
                f"Zarar serisi: {self._consecutive_losses} | "
                f"Piyasa: {market_status['status']} {market_status.get('time_et', '')} | "
                f"Kill: {'⚠️AKTİF' if self.kill_switch.is_active else 'OK'}"
            )

            # PDT güncelle
            self.pdt_tracker.update_equity(equity)

            # Periyodik pozisyon sync (her 10 heartbeat'te)
            if self._heartbeat_counter % 300 == 0:
                self._sync_positions_from_alpaca()

            # Pozisyon metadata kaydet
            self._save_position_metadata()

        except Exception as e:
            logger.error(f"  Heartbeat hatası: {e}")

    def _periodic_status_report(self, config: Dict):
        """Periyodik durum raporu + gunluk kapanış raporu."""
        interval = config.get("status_report_interval", 5) * 60
        if (datetime.now() - self._last_status_time).total_seconds() < interval:
            return
        self._last_status_time = datetime.now()
        self._log_heartbeat()

        # Gunluk Telegram raporu (gunde 1 kez, NYSE kapanisina yakin)
        # ET timezone kullan (sunucu timezone'undan bagimsiz)
        now = datetime.now()
        try:
            import pytz
            et_tz = pytz.timezone('US/Eastern')
            now_et = datetime.now(et_tz)
            is_report_time = (now_et.hour == 15 and now_et.minute >= 50)
        except ImportError:
            # pytz yoksa UTC-4 tahmini (DST doneminde)
            utc_hour = now.utcnow().hour
            est_hour = (utc_hour - 4) % 24
            is_report_time = (est_hour == 15 and now.minute >= 50)

        if (is_report_time and
            getattr(self, '_daily_report_date', None) != date.today()):
            try:
                if hasattr(self, 'notifier'):
                    self.notifier.send_daily_report(self)
                    self._daily_report_date = date.today()
                    logger.info("  Gunluk Telegram raporu gonderildi")
            except Exception as e:
                logger.debug(f"  Gunluk rapor hatasi: {e}")

    # ============================================================
    # POZİSYON SENKRONİZASYONU & PERSISTENCE
    # ============================================================

    def _sync_positions_from_alpaca(self):
        """Alpaca'dan açık pozisyonları senkronize et (restart-safe).
        
        Alpaca'da qty > 0 = LONG, qty < 0 = SHORT pozisyon.
        BOT_MODE'a göre sadece ilgili pozisyonlar sync edilir.
        Options kontratları asset_class = 'us_option' olarak gelir.
        """
        try:
            alpaca_positions = self.client.get_all_positions()
            synced_long = 0
            synced_short = 0
            synced_options = 0

            for pos in alpaca_positions:
                symbol = pos.symbol
                qty = float(pos.qty)
                entry_price = float(pos.avg_entry_price)
                current_price = float(pos.current_price)
                unrealized_pl = float(pos.unrealized_pl)
                asset_class = getattr(pos, 'asset_class', 'us_equity')

                # Index parking pozisyonu — bot'un nakit-sleeve'i, normal pozisyon DEĞİL.
                # Agent/stop-loss/max-pozisyon mantığından dışla (sleeve, trade değil).
                # Daha önce yanlışlıkla positions'a girdiyse temizle (self-heal).
                if self.index_parking.is_parking_symbol(symbol):
                    self.positions.pop(symbol, None)
                    continue

                # Kripto/diğer asset sınıfları bu botun işi değil (XRPUSD dust vakası:
                # kripto pozisyon LONG diye sync'lenip sayaç/heartbeat kirletiyordu)
                if asset_class not in ('us_equity', 'us_option'):
                    self.positions.pop(symbol, None)
                    self.short_positions.pop(symbol, None)
                    continue

                # OPTIONS pozisyon (us_option asset class)
                if asset_class == 'us_option' or (len(symbol) > 10 and any(c in symbol for c in 'CP')):
                    if symbol not in self.options_positions:
                        # Kontrat sembolünden underlying çıkar (AAPL260425C00200000 -> AAPL)
                        underlying = ''
                        for i, c in enumerate(symbol):
                            if c.isdigit():
                                underlying = symbol[:i]
                                break
                        opt_type = 'CALL' if 'C' in symbol[len(underlying):len(underlying)+7] else 'PUT'
                        self.options_positions[symbol] = {
                            "underlying": underlying,
                            "type": opt_type,
                            "strike": 0,  # Alpaca'dan alınamıyor, metadata'dan yüklenecek
                            "expiry": "",
                            "qty": int(abs(qty)),
                            "entry_price": entry_price,
                            "cost_basis": entry_price * 100 * int(abs(qty)),
                            "entry_time": datetime.now().isoformat(),
                            "synced_from_alpaca": True,
                            "highest_price": current_price,
                            "lowest_price": current_price,
                        }
                        synced_options += 1
                        logger.info(
                            f"  🔄 OPTIONS sync: {symbol} ({underlying} {opt_type}) | "
                            f"P&L: ${unrealized_pl:+.2f}"
                        )
                    continue

                if qty > 0:
                    # LONG pozisyon
                    if symbol not in self.positions and BOT_MODE in ("long_only", "both"):
                        # A6: pozisyon geçici düşüp geri geldiyse yönetim bayraklarını koru
                        cached = self._exit_flag_cache.get(symbol, {})
                        self.positions[symbol] = {
                            "entry_price": entry_price,
                            "qty": qty,
                            "entry_time": cached.get("entry_time") or datetime.now().isoformat(),
                            "synced_from_alpaca": True,
                            "highest_price": max(current_price, cached.get("highest_price", 0) or 0),
                            "breakeven_set": cached.get("breakeven_set", False),
                            "partial_sold": cached.get("partial_sold", False),
                        }
                        if cached.get("stop_loss_pct") is not None:
                            self.positions[symbol]["stop_loss_pct"] = cached["stop_loss_pct"]
                        synced_long += 1
                        logger.info(
                            f"  🔄 LONG sync: {symbol} | "
                            f"{qty:.4f} @ ${entry_price:,.2f} | "
                            f"P&L: ${unrealized_pl:+.2f}"
                        )
                elif qty < 0:
                    # SHORT pozisyon (Alpaca negatif qty = short)
                    if symbol not in self.short_positions and BOT_MODE in ("short_only", "both"):
                        # A6: yönetim bayraklarını koru (partial_covered/breakeven)
                        cached = self._exit_flag_cache.get(symbol, {})
                        lc = cached.get("lowest_price", 0) or 0
                        self.short_positions[symbol] = {
                            "entry_price": entry_price,
                            "qty": abs(qty),
                            "entry_time": cached.get("entry_time") or datetime.now().isoformat(),
                            "synced_from_alpaca": True,
                            "lowest_price": min(current_price, lc) if lc > 0 else current_price,
                            "breakeven_set": cached.get("breakeven_set", False),
                            "partial_covered": cached.get("partial_covered", False),
                        }
                        if cached.get("stop_loss_pct") is not None:
                            self.short_positions[symbol]["stop_loss_pct"] = cached["stop_loss_pct"]
                        synced_short += 1
                        logger.info(
                            f"  🔄 SHORT sync: {symbol} | "
                            f"{abs(qty):.4f} @ ${entry_price:,.2f} | "
                            f"P&L: ${unrealized_pl:+.2f}"
                        )

            # Bot'ta var ama Alpaca'da olmayan pozisyonları temizle
            alpaca_long_symbols = {
                pos.symbol for pos in alpaca_positions
                if float(pos.qty) > 0 and getattr(pos, 'asset_class', 'us_equity') != 'us_option'
            }
            alpaca_short_symbols = {
                pos.symbol for pos in alpaca_positions
                if float(pos.qty) < 0 and getattr(pos, 'asset_class', 'us_equity') != 'us_option'
            }
            alpaca_option_symbols = {
                pos.symbol for pos in alpaca_positions
                if getattr(pos, 'asset_class', '') == 'us_option' or (len(pos.symbol) > 10)
            }

            for symbol in list(self.positions.keys()):
                if symbol not in alpaca_long_symbols:
                    # Dış kapanış (bracket bacağı/manuel) — muhasebeyi işleyerek düşür
                    self._reconcile_external_exit(symbol, side="LONG")

            for symbol in list(self.short_positions.keys()):
                if symbol not in alpaca_short_symbols:
                    self._reconcile_external_exit(symbol, side="SHORT")

            for symbol in list(self.options_positions.keys()):
                if symbol not in alpaca_option_symbols:
                    logger.warning(f"  🗑️ OPTIONS temizlendi (Alpaca'da yok): {symbol}")
                    self.options_positions.pop(symbol)

            total = synced_long + synced_short + synced_options
            if total > 0:
                logger.info(f"  Sync: {synced_long} long + {synced_short} short + {synced_options} options = {total} pozisyon")

        except Exception as e:
            logger.error(f"  Pozisyon sync hatası: {e}")

    def _reconcile_external_exit(self, symbol: str, side: str = "LONG"):
        """Bot dışında kapanan pozisyonun (bracket TP/SL bacağı, manuel satış)
        muhasebesini işleyip pozisyonu düşürür.

        Önceki davranış: pozisyon sync'te sessizce siliniyordu → P&L kaydı,
        kayıp serisi, wash-sale ve PDT sayacı atlanıyor, Kelly/öğrenme verisi
        sistematik eksik kalıyordu. Şimdi son dolan çıkış emrinden gerçek fill
        fiyatı bulunur ve normal satış muhasebesi uygulanır.
        """
        book = self.positions if side == "LONG" else self.short_positions
        pos = book.get(symbol)
        if not pos:
            return

        # Taze girişleri kapanmış sayma: yeni verilen BUY henüz dolmamışken
        # pozisyon Alpaca'da görünmez (özellikle kapalı piyasada) — 10dk bekle
        try:
            entry_dt = datetime.fromisoformat(str(pos.get("entry_time", "")))
            if (datetime.now() - entry_dt).total_seconds() < 600:
                return
        except (ValueError, TypeError):
            pass

        entry = float(pos.get("entry_price", 0) or 0)
        qty = float(pos.get("qty", 0) or 0)
        entry_time = pos.get("entry_time", "")

        self._stash_exit_flags(symbol, pos)  # A6: bayrakları koru
        book.pop(symbol, None)

        # Son dolan çıkış emrini bul (CLOSED emirler en yeniden eskiye gelir)
        fill_price, order_type = 0.0, ""
        try:
            exit_side = OrderSide.SELL if side == "LONG" else OrderSide.BUY
            req = GetOrdersRequest(
                status=QueryOrderStatus.CLOSED, symbols=[symbol], limit=20
            )
            for o in self.client.get_orders(req):
                if o.side != exit_side:
                    continue
                if float(o.filled_qty or 0) <= 0 or o.filled_avg_price is None:
                    continue
                fill_price = float(o.filled_avg_price)
                order_type = str(getattr(o, "order_type", "") or getattr(o, "type", ""))
                break
        except Exception as e:
            logger.debug(f"  {symbol} dış kapanış emri sorgulanamadı: {e}")

        if fill_price <= 0 or entry <= 0 or qty <= 0:
            logger.warning(f"  🗑️ {side} temizlendi (Alpaca'da yok, fill bulunamadı): {symbol}")
            return

        if side == "LONG":
            pnl_usd = (fill_price - entry) * qty
        else:
            pnl_usd = (entry - fill_price) * qty

        ot = order_type.lower()
        if "stop" in ot:
            reason = "STOP_LOSS(BRACKET/EXT)"
        elif "limit" in ot:
            reason = "TAKE_PROFIT(BRACKET/EXT)"
        else:
            reason = "EXTERNAL_CLOSE"

        pnl_pct = (pnl_usd / (entry * qty) * 100) if entry * qty > 0 else 0
        logger.info(
            f"  ✅ DIŞ KAPANIŞ {side} {symbol}: {qty:.4f} @ ${fill_price:,.2f} | "
            f"P&L: ${pnl_usd:+.2f} ({pnl_pct:+.1f}%) | {reason}"
        )

        self.trades_today.append({
            "action": "SELL" if side == "LONG" else "COVER",
            "symbol": symbol, "price": fill_price, "pnl": pnl_usd,
            "reason": reason, "time": datetime.now().isoformat(),
        })

        # Kayıp/kazanç serisi (execute_sell ile aynı semantik)
        is_loss_exit = "STOP_LOSS" in reason or (reason == "EXTERNAL_CLOSE" and pnl_usd < 0)
        if is_loss_exit:
            self._consecutive_losses += 1
            self._symbol_consecutive_losses[symbol] = (
                self._symbol_consecutive_losses.get(symbol, 0) + 1
            )
            if side == "LONG" and pnl_usd < 0:
                try:
                    self.wash_sale_tracker.record_loss_sale(
                        symbol, pnl_usd, datetime.now().isoformat()[:10]
                    )
                except Exception:
                    pass
        elif pnl_usd > 0:
            self._consecutive_losses = 0
            self._symbol_consecutive_losses[symbol] = 0

        # PDT: aynı gün açılıp kapandıysa day-trade say
        try:
            if entry_time and self.pdt_tracker.is_same_day_position(symbol, entry_time):
                self.pdt_tracker.record_day_trade(
                    symbol, entry_time, datetime.now().isoformat()
                )
        except Exception:
            pass

        # Performans + ajan öğrenme kaydı
        try:
            self.performance.record_trade(
                symbol=symbol, action="SELL" if side == "LONG" else "COVER",
                qty=qty, price=fill_price, pnl=pnl_usd, reason=reason,
                sector=SECTOR_MAP.get(symbol, "Unknown"),
            )
        except Exception:
            pass
        try:
            outcome = "WIN" if pnl_usd > 0 else "LOSS" if pnl_usd < 0 else "NEUTRAL"
            self.agent_perf.record_outcome(symbol, outcome, pnl_usd)
        except Exception:
            pass

    def _stash_exit_flags(self, symbol: str, pos_data: Dict):
        """Pozisyon geçici olarak sync'ten düşerse yönetim bayraklarını sakla (A6).
        Böylece geri geldiğinde partial_sold/breakeven_set sıfırlanıp cascade satış olmaz."""
        if not isinstance(pos_data, dict):
            return
        keep = {}
        for k in ("highest_price", "lowest_price", "breakeven_set",
                  "partial_sold", "partial_covered", "stop_loss_pct", "entry_time"):
            v = pos_data.get(k)
            if v is not None:
                keep[k] = v
        if keep:
            self._exit_flag_cache[symbol] = keep

    def _save_position_metadata(self):
        """Pozisyon metadata'sını dosyaya kaydet (restart-safe)."""
        try:
            data = {
                "positions": self.positions,
                "short_positions": self.short_positions,
                "options_positions": self.options_positions,
                "last_trade_time": {
                    k: (v.isoformat() if hasattr(v, 'isoformat') else str(v))
                    for k, v in self.last_trade_time.items()
                },
                "consecutive_losses": self._consecutive_losses,
                "symbol_consecutive_losses": self._symbol_consecutive_losses,
                "daily_buys_count": self._daily_buys_count,
                "trades_today": self.trades_today,
                "last_update": datetime.now().isoformat(),
            }
            with open(self.POSITIONS_FILE, "w") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            logger.debug(f"  Pozisyon kayıt hatası: {e}")

    def _load_position_metadata(self):
        """Kaydedilmiş pozisyon metadata'sını yükle."""
        try:
            if os.path.exists(self.POSITIONS_FILE):
                with open(self.POSITIONS_FILE, "r") as f:
                    data = json.load(f)
                # Sadece metadata'yı güncelle (pozisyonlar Alpaca'dan geldi)
                for sym, meta in data.get("positions", {}).items():
                    if sym in self.positions:
                        # Mevcut pozisyona ek bilgileri aktar
                        self.positions[sym].update({
                            "entry_time": meta.get("entry_time", self.positions[sym].get("entry_time")),
                            "highest_price": meta.get("highest_price", self.positions[sym].get("highest_price", 0)),
                            "breakeven_set": meta.get("breakeven_set", False),
                            "partial_sold": meta.get("partial_sold", False),
                            "synced_from_alpaca": False,
                        })
                        # stop_loss_pct=null enjekte etme — None değer position_manager'da
                        # "-None" TypeError'a yol açıp TÜM pozisyon yönetimini durduruyordu
                        if meta.get("stop_loss_pct") is not None:
                            self.positions[sym]["stop_loss_pct"] = meta["stop_loss_pct"]
                # Short pozisyon metadata'sını yükle
                for sym, meta in data.get("short_positions", {}).items():
                    if sym in self.short_positions:
                        self.short_positions[sym].update({
                            "entry_time": meta.get("entry_time", self.short_positions[sym].get("entry_time")),
                            "lowest_price": meta.get("lowest_price", self.short_positions[sym].get("lowest_price", 0)),
                            "breakeven_set": meta.get("breakeven_set", False),
                            "partial_covered": meta.get("partial_covered", False),
                            "synced_from_alpaca": False,
                        })
                        if meta.get("stop_loss_pct") is not None:  # None enjekte etme
                            self.short_positions[sym]["stop_loss_pct"] = meta["stop_loss_pct"]
                # Options pozisyon metadata'sını yükle
                saved_options = data.get("options_positions", {})
                if saved_options:
                    self.options_positions = saved_options
                self._consecutive_losses = data.get("consecutive_losses", 0)
                self._symbol_consecutive_losses = data.get("symbol_consecutive_losses", {})
                opt_count = len(self.options_positions)
                logger.info(f"  📁 Metadata yüklendi ({len(self.positions)} long + {len(self.short_positions)} short + {opt_count} options)")
        except Exception as e:
            logger.debug(f"  Pozisyon metadata yüklenemedi: {e}")

    # ============================================================
    # GÜNLÜK RESET & ACİL DURUM
    # ============================================================

    def _et_today(self) -> date:
        """Borsanın (US/Eastern) bugünkü tarihi — gün-sınırları sunucu saatinden bağımsız (A2/C5)."""
        try:
            import pytz
            return datetime.now(pytz.timezone('US/Eastern')).date()
        except Exception:
            return date.today()

    def _save_daily_baseline(self, et_date: str, baseline_equity: float):
        """Günlük kayıp baz çizgisini kalıcı yaz (restart-safe)."""
        try:
            with open(self._daily_baseline_file, "w") as f:
                json.dump({"date": et_date, "baseline_equity": baseline_equity}, f)
        except Exception as e:
            logger.debug(f"  Günlük baz kaydı hatası: {e}")

    def _load_or_init_daily_baseline(self, account, current_equity: float) -> float:
        """
        Günlük kayıp baz çizgisini döndürür.
        - Diskte bugünün (ET) bazı varsa onu kullan (restart'ta sıfırlanmaz).
        - Yoksa Alpaca last_equity (gün-başı equity) baz alınır, diske yazılır.
        """
        et_today = self._et_today().isoformat()
        try:
            if os.path.exists(self._daily_baseline_file):
                with open(self._daily_baseline_file, "r") as f:
                    data = json.load(f)
                if data.get("date") == et_today and float(data.get("baseline_equity", 0)) > 0:
                    base = float(data["baseline_equity"])
                    logger.info(f"  📆 Günlük baz diskten yüklendi: ${base:,.2f} (ET {et_today})")
                    return base
        except Exception as e:
            logger.debug(f"  Günlük baz okuma hatası: {e}")
        # Yeni gün / dosya yok → gün-başı (SOD) equity
        try:
            sod = float(getattr(account, "last_equity", 0) or 0)
        except Exception:
            sod = 0.0
        baseline = sod if sod > 0 else current_equity
        self._save_daily_baseline(et_today, baseline)
        return baseline

    def _daily_reset(self):
        """Her yeni gün başında değişkenleri sıfırla."""
        today = self._et_today()
        if self._daily_reset_date == today:
            return

        # Önceki günün özetini gönder (ilk çalıştırma hariç)
        if self._daily_reset_date is not None:
            pnl = self.equity - self.initial_equity
            wins = len([t for t in self.trades_today if "TAKE_PROFIT" in str(t) or "TRAILING_STOP" in str(t)])
            losses = len([t for t in self.trades_today if "STOP_LOSS" in str(t)])
            self.notifier.notify_daily_summary(
                equity=self.equity, pnl=pnl,
                trades_count=len(self.trades_today),
                positions=self.positions,
                wins=wins, losses=losses,
            )

        self._daily_reset_date = today
        self.trades_today = []
        self._daily_buys_count = 0
        self._morning_scan_done = False
        # Yeni günün baz çizgisi: gün-başı (SOD) equity, kalıcı yazılır (A2)
        try:
            account = self.client.get_account()
            sod = float(getattr(account, "last_equity", 0) or 0)
            self.initial_equity = sod if sod > 0 else self.equity
        except Exception:
            self.initial_equity = self.equity
        self._save_daily_baseline(today.isoformat(), self.initial_equity)
        logger.info(f"  📆 Günlük reset (ET): {today} | Başlangıç equity: ${self.initial_equity:,.2f}")

        # Yeni gün: dünkü DAY çıkış emirleri (bracket bacakları) düştü —
        # emirsiz kalan pozisyonlara koruyucu stop yeniden yerleştirilir
        try:
            self.position_manager.ensure_protective_stops(STOCK_CONFIG)
        except Exception as e:
            logger.debug(f"  Günlük koruma emri kontrolü hatası: {e}")

    def _emergency_close_all(self, reason: str):
        """KillSwitch tarafından çağrılır — tüm pozisyonları kapat."""
        logger.error(f"🚨 ACİL KAPANIŞ: {reason}")
        self.notifier.notify_kill_switch(reason, self.equity)
        try:
            self.client.close_all_positions(cancel_orders=True)
            logger.error("  Tüm pozisyonlar kapatıldı, emirler iptal edildi.")
            self.positions.clear()
            self.short_positions.clear()
            self.options_positions.clear()
        except Exception as e:
            logger.error(f"  Acil kapanış hatası: {e}")


# ============================================================
# ENTRY POINT
# ============================================================
if __name__ == "__main__":
    bot = StockBot()
    bot.run()
