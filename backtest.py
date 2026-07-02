"""
6-Aylık Backtest Motoru — Trading Bot Sistem Testi

Gerçek Alpaca tarihsel verisi kullanarak botun teknik analiz, 
pozisyon yönetimi ve options stratejisini simüle eder.

Çıktı: Trade listesi, PnL, win rate, max drawdown, Sharpe ratio
"""
import os
import sys
import json
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional
import pandas as pd
import numpy as np

# Alpaca data client
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

# Teknik analiz kütüphaneleri
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import BollingerBands, AverageTrueRange

# Config
from config import (
    ALPACA_API_KEY, ALPACA_SECRET_KEY, STOCK_CONFIG, SHORT_CONFIG,
    OPTIONS_CONFIG, PAPER_AGGRESSIVE_CONFIG, STOCK_IDS, SECTOR_MAP,
    MARKET_REGIME_CONFIG, COMMISSION_CONFIG,
)


# ============================================================
# BACKTEST ENGINE
# ============================================================

class BacktestEngine:
    """6 aylık tarihsel backtest motoru."""

    def __init__(self, initial_capital: float = 100000.0, use_paper_aggressive: bool = True):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.equity_peak = initial_capital
        self.total_costs = 0.0           # Toplam işlem maliyeti (slippage + SEC + FINRA)
        self.use_paper_aggressive = use_paper_aggressive
        self.spy_buyhold_pct = None      # SPY al-tut benchmark (aynı dönem)

        # Alpaca data client
        self.data_client = StockHistoricalDataClient(
            api_key=ALPACA_API_KEY, secret_key=ALPACA_SECRET_KEY,
        )

        # Config — paper aggressive override (yalnızca paper modunda)
        self.config = dict(STOCK_CONFIG)
        if use_paper_aggressive:
            for key, value in PAPER_AGGRESSIVE_CONFIG.items():
                if key.startswith("short_"):
                    pass  # Short ayarları ayrı
                elif key.startswith("enable_") or key.startswith("prefer_"):
                    pass
                else:
                    self.config[key] = value
        else:
            # LIVE config: pozisyon cap'i live_max_position_usd'den (konservatif)
            self.config["max_position_usd"] = self.config.get("live_max_position_usd", 200)

        # Edge-research deney hook'u: eşikleri env'den override et (config'i kirletmeden sweep)
        _mc = os.getenv("BT_MIN_CONF")
        if _mc:
            self.config["min_confidence_score"] = int(_mc)
        _msc = os.getenv("BT_MIN_SHORT_CONF")
        if _msc:
            SHORT_CONFIG["short_min_confidence"] = int(_msc)
        # Exp2: LONG trend-gate (sadece teyitli uptrend'de uzun gir)
        self.long_trend_gate = os.getenv("BT_LONG_TREND_GATE", "") == "1"
        # Exp3: universe prune (kronik kaybedenleri çıkar)
        self.exclude_symbols = set(
            s.strip().upper() for s in os.getenv("BT_EXCLUDE", "").split(",") if s.strip()
        )
        # Çoklu-pencere doğrulama: bitiş tarihini geriye kaydır (out-of-sample)
        self.end_offset_days = int(os.getenv("BT_END_OFFSET", "0"))

        # Rejim-koşullu katılım deneyi (walk_forward / regime_experiment ile test edilir):
        #   base    = mevcut davranış (BEAR'da buy_conf+10, short-10)
        #   off     = rejim etkisi yok (temiz baseline)
        #   flat    = BEAR'da YENİ LONG açma (risk-off); short serbest
        #   scale   = pozisyon boyutunu rejime göre ölçekle (bull/bear mult)
        #   overlay = boştaki nakit BEAR-dışı rejimde SPY getirisi kazanır (beta capture)
        self.regime_mode = os.getenv("BT_REGIME_MODE", "base").lower()
        self.bear_size_mult = float(os.getenv("BT_BEAR_SIZE_MULT", "0.5"))
        self.bull_size_mult = float(os.getenv("BT_BULL_SIZE_MULT", "1.0"))
        # Deney hızlandırma: bar verisini diske cache'le (yalnız BT_CACHE=1)
        self.use_cache = os.getenv("BT_CACHE", "") == "1"

        # Pozisyonlar
        self.positions = {}       # symbol -> {entry_price, qty, entry_date, stop_loss_pct, highest}
        self.short_positions = {}
        self.options_positions = {}

        # İstatistikler
        self.trades = []          # Tüm trade kayıtları
        self.daily_equity = []    # Günlük equity eğrisi
        self.max_drawdown = 0
        self.max_positions_held = 0

        # Sonuçlar
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_pnl = 0
        self.overlay_pnl = 0.0   # rejim overlay sleeve — ayrı muhasebe, sizing'i etkilemez
        self.gross_profit = 0
        self.gross_loss = 0

    def run(self, months: int = 6):
        """Ana backtest döngüsü."""
        print("=" * 70)
        print(f"  📊 BACKTEST BAŞLATILIYOR — {months} AYLIK")
        print(f"  Sermaye: ${self.initial_capital:,.2f}")
        print(f"  Max pozisyon: {self.config.get('max_open_positions', 8)}")
        print(f"  Güven eşiği: {self.config.get('min_confidence_score', 40)}")
        print(f"  Pozisyon boyutu: ${self.config.get('max_position_usd', 5000)}")
        print("=" * 70)

        # Tarih aralığı
        end_date = date.today() - timedelta(days=self.end_offset_days)
        start_date = end_date - timedelta(days=months * 30)

        # Hisse havuzu (endeksler ve ters ETF'ler hariç)
        symbols = [
            s for s in STOCK_IDS.keys()
            if s not in ["SPY", "QQQ", "SQQQ", "SH", "SPXS"]
            and s not in self.exclude_symbols
        ]

        print(f"\n  Tarih: {start_date} → {end_date}")
        print(f"  Hisse havuzu: {len(symbols)} hisse")
        print(f"  Hisseler: {', '.join(symbols)}")

        # SPY verisi — rejim tespiti için
        print("\n  SPY verisi çekiliyor (rejim tespiti)...")
        spy_df = self._get_bars("SPY", start_date, end_date)
        if spy_df.empty:
            print("  ❌ SPY verisi çekilemedi!")
            return

        # SPY al-tut benchmark (aynı dönem) — botu indeks geçiyor mu?
        try:
            self.spy_buyhold_pct = float(
                (spy_df["close"].iloc[-1] - spy_df["close"].iloc[0])
                / spy_df["close"].iloc[0] * 100
            )
        except Exception:
            self.spy_buyhold_pct = None

        # Her hisse için veri çek
        all_data = {}
        for sym in symbols:
            print(f"  {sym} verisi çekiliyor...", end=" ")
            df = self._get_bars(sym, start_date, end_date)
            if not df.empty and len(df) >= 50:
                all_data[sym] = df
                print(f"✅ {len(df)} bar")
            else:
                print(f"❌ ({len(df)} bar)")

        print(f"\n  Toplam {len(all_data)} hisse yeterli veriye sahip")

        # Trading günlerini al (SPY'dan)
        trading_days = spy_df.index.normalize().unique().sort_values()
        print(f"  Trading günleri: {len(trading_days)}")

        # Ana simülasyon döngüsü
        print("\n" + "=" * 70)
        print("  SİMÜLASYON BAŞLIYOR...")
        print("=" * 70)

        day_count = 0
        prev_spy_close = None  # rejim overlay için gün-üstü SPY getirisi
        for day in trading_days:
            day_count += 1
            day_str = day.strftime("%Y-%m-%d") if hasattr(day, 'strftime') else str(day)[:10]

            # Piyasa rejimi
            spy_slice = spy_df[spy_df.index <= day]
            regime = self._detect_regime(spy_slice)

            # 1. Pozisyon yönetimi (açık pozisyonları kontrol et)
            self._manage_all_positions(day, all_data)

            # 2. Yeni pozisyon tarama
            total_open = len(self.positions) + len(self.short_positions) + len(self.options_positions)
            max_pos = self.config.get("max_open_positions", 8)

            if total_open < max_pos:
                for sym in all_data:
                    if total_open >= max_pos:
                        break
                    if sym in self.positions or sym in self.short_positions:
                        continue

                    # Teknik analiz (o güne kadar olan verinin son 100 barı)
                    sym_slice = all_data[sym][all_data[sym].index <= day]
                    if len(sym_slice) < 30:
                        continue

                    analysis = self._technical_analysis(sym_slice.tail(100), self.config)
                    if analysis is None:
                        continue

                    signal = analysis["signal"]
                    confidence = analysis["confidence"]
                    price = analysis["price"]

                    # Rejim bazlı katılım ayarı (regime_mode'a göre)
                    effective_buy_conf = self.config.get("min_confidence_score", 40)
                    effective_short_conf = SHORT_CONFIG.get("short_min_confidence", 45)
                    size_mult = 1.0

                    if self.regime_mode != "off":
                        if regime == "BEAR":
                            effective_buy_conf += 10
                            effective_short_conf -= 10
                            if self.regime_mode == "scale":
                                size_mult = self.bear_size_mult
                        elif regime == "BULL" and self.regime_mode == "scale":
                            size_mult = self.bull_size_mult

                    # flat mode: BEAR'da yeni long YOK (risk-off)
                    bear_block_long = (self.regime_mode == "flat" and regime == "BEAR")

                    # BUY sinyali
                    if signal == "BUY" and confidence >= effective_buy_conf and not bear_block_long:
                        # Exp2: trend-gate — sadece teyitli uptrend'de LONG (düşen bıçak engeli)
                        if self.long_trend_gate and analysis.get("trend") != "UPTREND":
                            continue
                        self._execute_buy(sym, price, confidence, day_str, analysis, size_mult)
                        total_open += 1

                    # SHORT sinyali
                    elif signal == "SHORT" and confidence >= effective_short_conf:
                        self._execute_short(sym, price, confidence, day_str, analysis)
                        total_open += 1

            # Rejim overlay (beta capture): boştaki nakit BEAR-dışında SPY ile büyür.
            # "Aktif trading SPY'ın ÜSTÜNE alpha katıyor mu?" sorusunu dürüst test eder.
            # Overlay = DÜRÜST "boştaki nakdi index'te tut" benchmark'ı:
            #   - HER GÜN uygulanır (buy&hold SPY bear'da da tutar; rejim-kapısı koymak
            #     down günleri atlayıp seçim-yanlılığı yaratır → YASAK)
            #   - ayrı accumulator → kazanç pozisyon sizing'ini BÜYÜTMEZ (para iki kez çalışmaz)
            #   - short notional idle'dan düşülür (short da buying-power tutar)
            try:
                curr_spy_close = float(spy_slice["close"].iloc[-1])
                if self.regime_mode == "overlay" and prev_spy_close:
                    short_notional = sum(
                        p["entry_price"] * p["qty"]
                        for p in self.short_positions.values()
                    )
                    idle = max(0.0, self.capital - short_notional)
                    self.overlay_pnl += idle * (curr_spy_close / prev_spy_close - 1.0)
                prev_spy_close = curr_spy_close
            except Exception:
                pass

            # Günlük equity kaydı
            equity = self._calculate_equity(day, all_data)
            self.daily_equity.append({"date": day_str, "equity": equity})

            # Max drawdown güncelle
            if equity > self.equity_peak:
                self.equity_peak = equity
            dd = (self.equity_peak - equity) / self.equity_peak
            if dd > self.max_drawdown:
                self.max_drawdown = dd

            # Max eş zamanlı pozisyon
            total_now = len(self.positions) + len(self.short_positions)
            if total_now > self.max_positions_held:
                self.max_positions_held = total_now

            # İlerleme
            if day_count % 20 == 0:
                pnl = equity - self.initial_capital
                print(
                    f"  [{day_str}] Equity: ${equity:,.0f} | "
                    f"PnL: ${pnl:+,.0f} ({pnl/self.initial_capital*100:+.1f}%) | "
                    f"Poz: {total_now} | Trades: {self.total_trades} | "
                    f"Rejim: {regime} | DD: {dd:.1%}"
                )

        # Son açık pozisyonları kapat
        self._close_all(trading_days[-1], all_data)

        # Rejim overlay sleeve'ini final getiriye ekle — pozisyon sizing'ini etkilemeden
        # (ayrı muhasebe → para iki kez çalışmaz; daily_equity'ye girmediği için Sharpe da şişmez)
        self.total_pnl += self.overlay_pnl

        # Sonuçları raporla
        self._print_report()

    def _get_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Alpaca'dan saatlik bar verisi çek (BT_CACHE=1 ile diske cache'lenir)."""
        cache_file = None
        if self.use_cache:
            import hashlib
            os.makedirs(".bt_cache", exist_ok=True)
            key = f"{symbol}_{start}_{end}_H"
            cache_file = os.path.join(
                ".bt_cache", hashlib.md5(key.encode()).hexdigest() + ".pkl"
            )
            if os.path.exists(cache_file):
                try:
                    return pd.read_pickle(cache_file)
                except Exception:
                    pass
        try:
            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Hour,
                start=datetime.combine(start, datetime.min.time()),
                end=datetime.combine(end, datetime.min.time()),
            )
            bars = self.data_client.get_stock_bars(request)
            df = bars.df
            if hasattr(df.index, 'droplevel'):
                try:
                    df = df.droplevel("symbol")
                except (KeyError, ValueError):
                    pass
            if cache_file is not None and not df.empty:
                try:
                    df.to_pickle(cache_file)
                except Exception:
                    pass
            return df
        except Exception as e:
            print(f"  Veri hatası {symbol}: {e}")
            return pd.DataFrame()

    def _detect_regime(self, spy_df: pd.DataFrame) -> str:
        """SPY EMA200 bazlı rejim tespiti."""
        if len(spy_df) < 50:
            return "UNKNOWN"
        try:
            close = spy_df["close"]
            ema_period = min(200, len(close) - 1)
            ema200 = EMAIndicator(close, window=ema_period).ema_indicator().iloc[-1]
            price = close.iloc[-1]
            return "BEAR" if price < ema200 else "BULL"
        except Exception:
            return "UNKNOWN"

    def _technical_analysis(self, df: pd.DataFrame, config: Dict) -> Optional[Dict]:
        """Basitleştirilmiş teknik analiz (analyzer.py mantığı)."""
        if len(df) < 30:
            return None

        try:
            close = df["close"]
            volume = df["volume"] if "volume" in df.columns else None

            # Göstergeler
            rsi = RSIIndicator(close, window=14).rsi().iloc[-1]
            ema_9 = EMAIndicator(close, window=9).ema_indicator().iloc[-1]
            ema_21 = EMAIndicator(close, window=21).ema_indicator().iloc[-1]
            ema_50 = EMAIndicator(close, window=min(50, len(close)-1)).ema_indicator().iloc[-1]

            macd = MACD(close)
            macd_hist = macd.macd_diff().iloc[-1]
            prev_macd_hist = macd.macd_diff().iloc[-2]

            bb = BollingerBands(close, window=20, window_dev=2)
            bb_lower = bb.bollinger_lband().iloc[-1]
            bb_upper = bb.bollinger_hband().iloc[-1]

            atr = AverageTrueRange(
                df["high"], df["low"], df["close"], window=14
            ).average_true_range().iloc[-1]

            current_price = float(close.iloc[-1])

            # Trend
            if current_price > ema_50 and ema_9 > ema_21:
                trend = "UPTREND"
            elif current_price < ema_50 and ema_9 < ema_21:
                trend = "DOWNTREND"
            else:
                trend = "SIDEWAYS"

            # Volume
            volume_ratio = 1.0
            if volume is not None and len(volume) > 20:
                avg_vol = volume.rolling(20).mean().iloc[-1]
                if avg_vol > 0:
                    volume_ratio = float(volume.iloc[-1] / avg_vol)

            momentum_5 = float((close.iloc[-1] - close.iloc[-5]) / close.iloc[-5] * 100)
            momentum_1 = float((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100)
            momentum_up = momentum_5 > 0 and momentum_1 > 0

            # BUY scoring
            buy_score = 0
            reasons = []

            if rsi < config.get("rsi_oversold", 30):
                buy_score += 25
                reasons.append(f"RSI={rsi:.0f}")
            if ema_9 > ema_21:
                buy_score += 15
                reasons.append("EMA+")
            if macd_hist > 0 and prev_macd_hist <= 0:
                buy_score += 20
                reasons.append("MACD+")
            if current_price < bb_lower * (1 + config.get("bb_proximity_pct", 0.02)):
                buy_score += 20
                reasons.append("BB_dip")
            if trend == "UPTREND":
                buy_score += 10
                reasons.append("Trend+")
            elif trend == "DOWNTREND":
                buy_score -= 15
            if volume_ratio >= 1.5:
                buy_score += 10
                reasons.append(f"Vol:{volume_ratio:.1f}x")
            if momentum_up:
                buy_score += 5
            if trend == "UPTREND" and 40 <= rsi <= 65 and momentum_up:
                buy_score += 15
                reasons.append("Mom_BUY")

            # SELL scoring
            sell_score = 0
            if rsi > config.get("rsi_overbought", 70):
                sell_score += 25
                reasons.append(f"RSI={rsi:.0f}")
            if ema_9 < ema_21:
                sell_score += 15
            if macd_hist < 0 and prev_macd_hist >= 0:
                sell_score += 20
                reasons.append("MACD-")
            if current_price > bb_upper:
                sell_score += 20
            if trend == "DOWNTREND":
                sell_score += 10
            if momentum_5 < -3:
                sell_score += 10
                reasons.append(f"Mom-:{momentum_5:.1f}%")
            if volume_ratio > 1.5 and momentum_1 < -1:
                sell_score += 10
                reasons.append("Vol_Sell")

            # Karar
            if buy_score >= 45:
                signal = "BUY"
                confidence = min(buy_score, 100)
            elif sell_score >= 45:
                signal = "SHORT"
                confidence = min(sell_score, 100)
            else:
                signal = "HOLD"
                confidence = 0

            return {
                "signal": signal,
                "confidence": confidence,
                "price": current_price,
                "atr": float(atr),
                "rsi": float(rsi),
                "trend": trend,
                "reasons": reasons,
                "volume_ratio": volume_ratio,
            }

        except Exception as e:
            return None

    def _trade_cost(self, qty: float, price: float, side: str) -> float:
        """Tek bacak işlem maliyeti: slippage (her iki taraf) + SEC/FINRA (sadece satış).
        Alpaca hisse komisyonu $0; gerçekçi sürtünme için slippage + düzenleyici ücretler.
        """
        notional = qty * price
        cost = notional * COMMISSION_CONFIG.get("estimated_slippage_pct", 0.001)
        if side == "sell":
            cost += notional * COMMISSION_CONFIG.get("sec_fee_per_dollar", 0.0)
            cost += qty * COMMISSION_CONFIG.get("finra_taf_per_share", 0.0)
        return cost

    def _execute_buy(self, symbol: str, price: float, confidence: float,
                     day: str, analysis: Dict, size_mult: float = 1.0):
        """Simüle BUY emri."""
        max_usd = min(
            self.config.get("max_position_usd", 5000),
            self.capital * self.config.get("max_position_pct", 0.15),
            self.capital * 0.9,  # Max %90 sermaye kullan
        ) * size_mult
        if max_usd < 50:
            return

        qty = max_usd / price
        cost = qty * price
        entry_fee = self._trade_cost(qty, price, "buy")

        # ATR bazlı stop-loss
        atr = analysis.get("atr", 0)
        if atr > 0 and price > 0:
            atr_pct = atr / price
            sl_pct = atr_pct * self.config.get("atr_stop_multiplier", 2.0)
            sl_pct = max(sl_pct, self.config.get("stop_loss_pct", 0.05))
            sl_pct = min(sl_pct, self.config.get("stop_loss_max_pct", 0.12))
        else:
            sl_pct = self.config.get("stop_loss_pct", 0.05)

        self.positions[symbol] = {
            "entry_price": price,
            "qty": qty,
            "cost": cost,
            "entry_date": day,
            "stop_loss_pct": sl_pct,
            "highest_price": price,
            "confidence": confidence,
            "reasons": analysis.get("reasons", []),
            "breakeven_set": False,
            "partial_sold": False,
            "entry_fee": entry_fee,
        }
        self.capital -= (cost + entry_fee)
        self.total_costs += entry_fee

    def _execute_short(self, symbol: str, price: float, confidence: float,
                       day: str, analysis: Dict):
        """Simüle SHORT emri."""
        max_usd = min(
            SHORT_CONFIG.get("short_max_position_usd", 3000),
            self.capital * 0.10,
        )
        if max_usd < 50:
            return

        qty = max_usd / price
        self.short_positions[symbol] = {
            "entry_price": price,
            "qty": qty,
            "entry_date": day,
            "lowest_price": price,
            "confidence": confidence,
            "reasons": analysis.get("reasons", []),
        }

    def _manage_all_positions(self, day, all_data: Dict):
        """Tüm açık pozisyonları yönet."""
        # LONG pozisyonlar
        for sym in list(self.positions.keys()):
            if sym not in all_data:
                continue
            df = all_data[sym]
            current_bars = df[df.index <= day]
            if current_bars.empty:
                continue

            current_price = float(current_bars["close"].iloc[-1])
            pos = self.positions[sym]
            entry = pos["entry_price"]
            pnl_pct = (current_price - entry) / entry

            # Highest güncelle
            if current_price > pos["highest_price"]:
                pos["highest_price"] = current_price

            highest = pos["highest_price"]
            trailing_drop = (highest - current_price) / highest if highest > 0 else 0

            # Break-even
            if pnl_pct >= 0.015 and not pos.get("breakeven_set", False):
                pos["stop_loss_pct"] = 0.001
                pos["breakeven_set"] = True

            # Stop-loss
            sl_pct = pos["stop_loss_pct"]
            if pnl_pct <= -sl_pct:
                self._close_long(sym, current_price, day, f"STOP_LOSS ({pnl_pct:.1%})")
                continue

            # Take profit
            tp_pct = self.config.get("take_profit_pct", 0.06)
            if pnl_pct >= tp_pct:
                self._close_long(sym, current_price, day, f"TAKE_PROFIT (+{pnl_pct:.1%})")
                continue

            # Trailing stop
            trail_pct = self.config.get("trailing_stop_pct", 0.03)
            if pnl_pct > 0.01 and trailing_drop >= trail_pct:
                self._close_long(sym, current_price, day, f"TRAILING_STOP (peak -{trailing_drop:.1%})")
                continue

        # SHORT pozisyonlar
        for sym in list(self.short_positions.keys()):
            if sym not in all_data:
                continue
            df = all_data[sym]
            current_bars = df[df.index <= day]
            if current_bars.empty:
                continue

            current_price = float(current_bars["close"].iloc[-1])
            pos = self.short_positions[sym]
            entry = pos["entry_price"]
            pnl_pct = (entry - current_price) / entry  # Short: fiyat düşerse kar

            # Lowest güncelle
            if current_price < pos["lowest_price"]:
                pos["lowest_price"] = current_price

            # Stop-loss (fiyat yükseldiyse zarar)
            short_sl = SHORT_CONFIG.get("short_stop_loss_pct", 0.06)
            if pnl_pct <= -short_sl:
                self._close_short(sym, current_price, day, f"SHORT_STOP ({pnl_pct:.1%})")
                continue

            # Take profit
            short_tp = SHORT_CONFIG.get("short_take_profit_pct", 0.05)
            if pnl_pct >= short_tp:
                self._close_short(sym, current_price, day, f"SHORT_TP (+{pnl_pct:.1%})")
                continue

            # Trailing
            lowest = pos["lowest_price"]
            trailing_rise = (current_price - lowest) / lowest if lowest > 0 else 0
            short_trail = SHORT_CONFIG.get("short_trailing_stop_pct", 0.04)
            if pnl_pct > 0.01 and trailing_rise >= short_trail:
                self._close_short(sym, current_price, day, f"SHORT_TRAIL (+{trailing_rise:.1%})")
                continue

    def _close_long(self, symbol: str, price: float, day: str, reason: str):
        """LONG pozisyon kapat."""
        pos = self.positions.pop(symbol)
        exit_fee = self._trade_cost(pos["qty"], price, "sell")
        pnl = (price - pos["entry_price"]) * pos["qty"] - pos.get("entry_fee", 0.0) - exit_fee

        self.capital += pos["qty"] * price - exit_fee
        self.total_costs += exit_fee
        self.total_trades += 1
        self.total_pnl += pnl

        if pnl > 0:
            self.winning_trades += 1
            self.gross_profit += pnl
        else:
            self.losing_trades += 1
            self.gross_loss += abs(pnl)

        self.trades.append({
            "type": "LONG",
            "symbol": symbol,
            "entry": pos["entry_price"],
            "exit": price,
            "qty": pos["qty"],
            "pnl": pnl,
            "pnl_pct": (price - pos["entry_price"]) / pos["entry_price"] * 100,
            "entry_date": pos["entry_date"],
            "exit_date": day,
            "reason": reason,
            "confidence": pos.get("confidence", 0),
        })

    def _close_short(self, symbol: str, price: float, day: str, reason: str):
        """SHORT pozisyon kapat."""
        pos = self.short_positions.pop(symbol)
        exit_fee = self._trade_cost(pos["qty"], price, "buy")  # cover = alış
        pnl = (pos["entry_price"] - price) * pos["qty"] - pos.get("entry_fee", 0.0) - exit_fee

        self.total_costs += exit_fee
        self.total_trades += 1
        self.total_pnl += pnl

        if pnl > 0:
            self.winning_trades += 1
            self.gross_profit += pnl
        else:
            self.losing_trades += 1
            self.gross_loss += abs(pnl)

        self.trades.append({
            "type": "SHORT",
            "symbol": symbol,
            "entry": pos["entry_price"],
            "exit": price,
            "qty": pos["qty"],
            "pnl": pnl,
            "pnl_pct": (pos["entry_price"] - price) / pos["entry_price"] * 100,
            "entry_date": pos["entry_date"],
            "exit_date": day,
            "reason": reason,
            "confidence": pos.get("confidence", 0),
        })

    def _close_all(self, day, all_data: Dict):
        """Tüm açık pozisyonları kapat."""
        for sym in list(self.positions.keys()):
            if sym in all_data:
                df = all_data[sym]
                current_bars = df[df.index <= day]
                if not current_bars.empty:
                    price = float(current_bars["close"].iloc[-1])
                    self._close_long(sym, price, str(day)[:10], "BACKTEST_END")

        for sym in list(self.short_positions.keys()):
            if sym in all_data:
                df = all_data[sym]
                current_bars = df[df.index <= day]
                if not current_bars.empty:
                    price = float(current_bars["close"].iloc[-1])
                    self._close_short(sym, price, str(day)[:10], "BACKTEST_END")

    def _calculate_equity(self, day, all_data: Dict) -> float:
        """Toplam equity hesapla (cash + pozisyonlar)."""
        equity = self.capital

        for sym, pos in self.positions.items():
            if sym in all_data:
                df = all_data[sym]
                current_bars = df[df.index <= day]
                if not current_bars.empty:
                    price = float(current_bars["close"].iloc[-1])
                    equity += pos["qty"] * price

        for sym, pos in self.short_positions.items():
            if sym in all_data:
                df = all_data[sym]
                current_bars = df[df.index <= day]
                if not current_bars.empty:
                    price = float(current_bars["close"].iloc[-1])
                    unrealized = (pos["entry_price"] - price) * pos["qty"]
                    equity += unrealized

        return equity

    def _print_report(self):
        """Detaylı sonuç raporu."""
        print("\n")
        print("=" * 70)
        print("  📊 BACKTEST SONUÇLARI — 6 AYLIK")
        print("=" * 70)

        final_equity = self.initial_capital + self.total_pnl
        total_return = self.total_pnl / self.initial_capital * 100
        win_rate = (self.winning_trades / max(self.total_trades, 1)) * 100

        avg_win = self.gross_profit / max(self.winning_trades, 1)
        avg_loss = self.gross_loss / max(self.losing_trades, 1)
        profit_factor = self.gross_profit / max(self.gross_loss, 1)

        # Sharpe Ratio (basitleştirilmiş)
        if len(self.daily_equity) > 2:
            equity_series = pd.Series([d["equity"] for d in self.daily_equity])
            daily_returns = equity_series.pct_change().dropna()
            if daily_returns.std() > 0:
                sharpe = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252)
            else:
                sharpe = 0
        else:
            sharpe = 0

        print(f"\n  {'─' * 40}")
        print(f"  💰 PERFORMANS")
        print(f"  {'─' * 40}")
        print(f"  Başlangıç:      ${self.initial_capital:>12,.2f}")
        print(f"  Bitiş:          ${final_equity:>12,.2f}")
        print(f"  Net P&L:        ${self.total_pnl:>+12,.2f}")
        print(f"  Getiri:         {total_return:>+11.2f}%")
        print(f"  Yıllık Getiri:  {total_return * 2:>+11.2f}%  (6ay→12ay)")
        print(f"  İşlem Maliyeti: ${self.total_costs:>12,.2f}  (slippage+SEC+FINRA, net düşüldü)")
        if self.spy_buyhold_pct is not None:
            alpha = total_return - self.spy_buyhold_pct
            print(f"  SPY Al-Tut:     {self.spy_buyhold_pct:>+11.2f}%  (aynı dönem benchmark)")
            print(f"  Alpha (vs SPY): {alpha:>+11.2f}%  ({'GECIYOR +' if alpha > 0 else 'GERIDE -'})")

        print(f"\n  {'─' * 40}")
        print(f"  📈 TRADE İSTATİSTİKLERİ")
        print(f"  {'─' * 40}")
        print(f"  Toplam trade:   {self.total_trades:>12}")
        print(f"  Kazanan:        {self.winning_trades:>12}  ({win_rate:.1f}%)")
        print(f"  Kaybeden:       {self.losing_trades:>12}  ({100-win_rate:.1f}%)")
        print(f"  Ort. kazanç:    ${avg_win:>+12,.2f}")
        print(f"  Ort. kayıp:     ${avg_loss:>12,.2f}")
        print(f"  Profit Factor:  {profit_factor:>12.2f}")

        print(f"\n  {'─' * 40}")
        print(f"  📉 RİSK METRİKLERİ")
        print(f"  {'─' * 40}")
        print(f"  Max Drawdown:   {self.max_drawdown:>11.2%}")
        print(f"  Sharpe Ratio:   {sharpe:>12.2f}")
        print(f"  Max Eş Poz.:    {self.max_positions_held:>12}")
        print(f"  Brüt Kar:       ${self.gross_profit:>12,.2f}")
        print(f"  Brüt Zarar:     ${self.gross_loss:>12,.2f}")

        # Trade tipi analizi
        long_trades = [t for t in self.trades if t["type"] == "LONG"]
        short_trades = [t for t in self.trades if t["type"] == "SHORT"]

        print(f"\n  {'─' * 40}")
        print(f"  📊 TİP ANALİZİ")
        print(f"  {'─' * 40}")
        if long_trades:
            long_wins = sum(1 for t in long_trades if t["pnl"] > 0)
            long_pnl = sum(t["pnl"] for t in long_trades)
            print(f"  LONG:  {len(long_trades)} trade | "
                  f"Win: {long_wins}/{len(long_trades)} ({long_wins/len(long_trades)*100:.0f}%) | "
                  f"PnL: ${long_pnl:+,.0f}")
        if short_trades:
            short_wins = sum(1 for t in short_trades if t["pnl"] > 0)
            short_pnl = sum(t["pnl"] for t in short_trades)
            print(f"  SHORT: {len(short_trades)} trade | "
                  f"Win: {short_wins}/{len(short_trades)} ({short_wins/len(short_trades)*100:.0f}%) | "
                  f"PnL: ${short_pnl:+,.0f}")

        # Hisse bazlı performans
        print(f"\n  {'─' * 40}")
        print(f"  🏆 HİSSE BAZLI (EN İYİ / EN KÖTÜ)")
        print(f"  {'─' * 40}")
        symbol_pnl = {}
        for t in self.trades:
            sym = t["symbol"]
            symbol_pnl[sym] = symbol_pnl.get(sym, 0) + t["pnl"]

        sorted_syms = sorted(symbol_pnl.items(), key=lambda x: x[1], reverse=True)
        for sym, pnl in sorted_syms[:5]:
            count = sum(1 for t in self.trades if t["symbol"] == sym)
            print(f"  ✅ {sym:>6}: ${pnl:>+9,.2f}  ({count} trade)")
        print(f"  {'...' :>8}")
        for sym, pnl in sorted_syms[-5:]:
            count = sum(1 for t in self.trades if t["symbol"] == sym)
            print(f"  ❌ {sym:>6}: ${pnl:>+9,.2f}  ({count} trade)")

        # Kapanış nedeni analizi
        print(f"\n  {'─' * 40}")
        print(f"  📋 KAPANIŞ NEDENLERİ")
        print(f"  {'─' * 40}")
        reason_stats = {}
        for t in self.trades:
            r = t["reason"].split(" ")[0]
            if r not in reason_stats:
                reason_stats[r] = {"count": 0, "pnl": 0, "wins": 0}
            reason_stats[r]["count"] += 1
            reason_stats[r]["pnl"] += t["pnl"]
            if t["pnl"] > 0:
                reason_stats[r]["wins"] += 1

        for reason, stats in sorted(reason_stats.items(), key=lambda x: x[1]["count"], reverse=True):
            wr = stats["wins"] / max(stats["count"], 1) * 100
            print(f"  {reason:>20}: {stats['count']:>4} trade | "
                  f"WR: {wr:.0f}% | PnL: ${stats['pnl']:+,.0f}")

        # Son 10 trade
        print(f"\n  {'─' * 40}")
        print(f"  📜 SON 10 TRADE")
        print(f"  {'─' * 40}")
        for t in self.trades[-10:]:
            emoji = "✅" if t["pnl"] > 0 else "❌"
            print(
                f"  {emoji} {t['type']:>5} {t['symbol']:>6} | "
                f"${t['entry']:.2f}→${t['exit']:.2f} | "
                f"PnL: ${t['pnl']:+.2f} ({t['pnl_pct']:+.1f}%) | "
                f"{t['entry_date']}→{t['exit_date']} | {t['reason']}"
            )

        print(f"\n{'=' * 70}")

        # JSON kaydet
        try:
            result = {
                "initial_capital": self.initial_capital,
                "final_equity": final_equity,
                "total_pnl": self.total_pnl,
                "total_return_pct": total_return,
                "total_trades": self.total_trades,
                "winning_trades": self.winning_trades,
                "losing_trades": self.losing_trades,
                "win_rate": win_rate,
                "profit_factor": profit_factor,
                "sharpe_ratio": sharpe,
                "max_drawdown": self.max_drawdown,
                "avg_win": avg_win,
                "avg_loss": avg_loss,
                "total_costs": self.total_costs,
                "spy_buyhold_pct": self.spy_buyhold_pct,
                "config_mode": "paper_aggressive" if self.use_paper_aggressive else "live",
                "trades": self.trades[-50:],  # Son 50 trade
            }
            with open("backtest_results.json", "w") as f:
                json.dump(result, f, indent=2, default=str)
            print("  📁 Sonuçlar backtest_results.json dosyasına kaydedildi")
        except Exception as e:
            print(f"  JSON kayıt hatası: {e}")


# ============================================================
# ENTRY POINT
# ============================================================
if __name__ == "__main__":
    months = 6
    capital = 100000.0
    live_mode = False

    args = list(sys.argv[1:])
    if "--live" in args:
        live_mode = True
        args.remove("--live")

    if len(args) > 0:
        try:
            months = int(args[0])
        except ValueError:
            pass
    if len(args) > 1:
        try:
            capital = float(args[1])
        except ValueError:
            pass

    mode_str = "LIVE config (konservatif)" if live_mode else "PAPER AGGRESSIVE config"
    print(f"  ⚙️  Backtest config modu: {mode_str}")
    engine = BacktestEngine(initial_capital=capital, use_paper_aggressive=not live_mode)
    engine.run(months=months)
