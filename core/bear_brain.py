"""
Bear Brain — Düşüş-Kazanç Beyni (v4.11)

Piyasa DÜŞERKEN de kazanç üretir. Canlı hesap ($487) Alpaca'nın $2.000 marj
şartı yüzünden gerçek short AÇAMAZ — bu modül düşüş tezini ters-ETF LONG'una
çevirir (cash hesapta çalışır):
  DEFENSE (skor >= 55) → SH   (1x ters S&P)   — orta şiddette düşüş
  ATTACK  (skor >= 72) → SQQQ (3x ters NASDAQ) — sert düşüş (havuz tech-ağırlıklı)

Eski ters-ETF yolu neden ölüydü (v4.8): yalnız SPY < günlük-EMA200 (BEAR)
rejiminde tetikleniyordu — bu sinyal düşüşün %10-15'i YAŞANDIKTAN sonra gelir;
üstelik BEAR'da BUY eşiği +10 yükseliyor ve long kapıları (ETF'nin KENDİ
EMA200'ü, ATR<=%5 volatilite tavanı) 3x ters-ETF'leri yapısal blokluyordu.
Bear Brain rejim etiketini beklemez: 4 bileşenli bileşik skor kullanır.

SKOR (0-100):
  trend    (0-30): SPY günlük EMA9/21/50 dizilimi + fiyatın EMA'lara konumu
  momentum (0-25): SPY 5g / 10g getirisi
  vix      (0-25): VIX seviyesi + gün-aşırı sıçrama
  breadth  (0-20): koordinatör ws dağılımı (evrenin % kaçı negatif) + rejim detektörü

GİRİŞ executor.execute_buy üzerinden yapılır → floor/rezerv/PDT/bracket-stop
korumaları AYNEN geçerli. Kendi ek kapıları: cooldown, günlük giriş tavanı,
maruziyet tavanı, skor-çıkışı (histerezis), 3x için zaman-stopu.

PAPER'da ek görev: gerçek short eşiğini bear moduna göre gevşetir (öğrenme
akışı düşüş dönemlerinde hızlansın).
"""
import json
import os
from datetime import datetime, date
from typing import Dict, List, Optional

import pandas as pd

from config import state_path
from utils.logger import logger


class BearBrain:
    """Bileşik düşüş-skoru üretir ve ters-ETF pozisyonlarını yönetir."""

    MODE_ORDER = {"OFF": 0, "WATCH": 1, "DEFENSE": 2, "ATTACK": 3}

    def __init__(self, bot, config: Dict):
        self.bot = bot
        self.cfg = dict(config)
        is_paper = bool(getattr(bot, "is_paper", True))
        self.is_paper = is_paper
        self.enabled = bool(
            config.get("enabled", False)
            and (is_paper or config.get("allow_live", False))
        )
        self.score = 0.0
        self.mode = "OFF"
        self.parts: Dict = {}
        self._last_update = datetime.min
        self._last_exit_attempt: Dict[str, datetime] = {}
        self._last_error_log: Dict[str, datetime] = {}

        # Kalıcı durum: cooldown/günlük sayaç restart'ta kaybolmasın
        self._state_file = state_path("bear_brain.json")
        self._state = {"last_entry_ts": "", "entries": {}}
        self._load_state()
        # Restart devamlılığı: son skor/mod geri yüklenir ki ilk güncelleme
        # gelmeden skor-çıkışı açık bear pozisyonunu yanlışlıkla kapatmasın
        # (taze giriş yine de canlı güncelleme bekler — _maybe_enter bayatlık
        # kapısı). _last_update datetime.min kalır = "bu süreçte hiç ölçülmedi".
        try:
            self.score = float(self._state.get("last_score", 0) or 0)
            restored = self._state.get("last_mode", "OFF")
            if restored in self.MODE_ORDER:
                self.mode = restored
        except (TypeError, ValueError):
            pass

        if self.enabled:
            logger.info(
                f"BearBrain başlatıldı — düşüş-kazanç modu AKTİF "
                f"(DEFENSE>={config.get('score_defense', 55)}→"
                f"{config.get('defense_symbol', 'SH')}, "
                f"ATTACK>={config.get('score_attack', 72)}→"
                f"{config.get('attack_symbol', 'SQQQ')})"
            )
        else:
            logger.info("BearBrain devre dışı (enabled/allow_live bayrağı)")

    # ============================================================
    # SKOR HESABI (saf fonksiyon — test edilebilir)
    # ============================================================

    @staticmethod
    def compute_score(
        spy_daily_df: Optional[pd.DataFrame],
        vix: float = 0.0,
        vix_change: float = 0.0,
        breadth_neg_ratio: Optional[float] = None,
        enhanced_regime: Optional[Dict] = None,
    ) -> Dict:
        """Bileşik düşüş skoru (0-100) + bileşen dökümü döndürür.

        Veri eksikse ilgili bileşen 0 sayılır (fail-neutral) — skor asla
        veri hatasıyla yapay yükselmez.
        """
        parts = {"trend": 0.0, "momentum": 0.0, "vix": 0.0, "breadth": 0.0}

        # --- 1. TREND YAPISI (0-30): günlük EMA dizilimi ---
        try:
            if spy_daily_df is not None and len(spy_daily_df) >= 60:
                close = spy_daily_df["close"].astype(float)
                price = float(close.iloc[-1])
                ema9 = float(close.ewm(span=9, adjust=False).mean().iloc[-1])
                ema21 = float(close.ewm(span=21, adjust=False).mean().iloc[-1])
                ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
                ema_long_win = min(200, len(close) - 1)
                ema200 = float(
                    close.ewm(span=ema_long_win, adjust=False).mean().iloc[-1]
                )
                t = 0.0
                if ema9 < ema21:
                    t += 10
                if ema21 < ema50:
                    t += 8
                if price < ema50:
                    t += 6
                if price < ema200:
                    t += 6
                parts["trend"] = t

                # --- 2. MOMENTUM (0-25): 5g / 10g getiri ---
                m = 0.0
                if len(close) >= 11:
                    r5 = price / float(close.iloc[-6]) - 1.0
                    r10 = price / float(close.iloc[-11]) - 1.0
                    if r5 <= -0.06:
                        m += 15
                    elif r5 <= -0.04:
                        m += 12
                    elif r5 <= -0.025:
                        m += 9
                    elif r5 <= -0.015:
                        m += 6
                    elif r5 <= -0.005:
                        m += 3
                    if r10 <= -0.08:
                        m += 10
                    elif r10 <= -0.05:
                        m += 8
                    elif r10 <= -0.03:
                        m += 5
                    elif r10 <= -0.01:
                        m += 2
                parts["momentum"] = m
        except Exception:
            pass

        # --- 3. VIX (0-25): seviye + sıçrama ---
        try:
            v = 0.0
            vix = float(vix or 0)
            vix_change = float(vix_change or 0)
            if vix >= 35:
                v += 15
            elif vix >= 28:
                v += 12
            elif vix >= 24:
                v += 9
            elif vix >= 20:
                v += 5
            elif vix >= 18:
                v += 2
            if vix_change >= 4.0:
                v += 10
            elif vix_change >= 2.5:
                v += 7
            elif vix_change >= 1.5:
                v += 4
            elif vix_change >= 0.8:
                v += 2
            parts["vix"] = v
        except Exception:
            pass

        # --- 4. GENİŞLİK + REJİM DETEKTÖRÜ (0-20) ---
        try:
            b = 0.0
            if breadth_neg_ratio is not None:
                r = float(breadth_neg_ratio)
                if r >= 0.7:
                    b += 12
                elif r >= 0.5:
                    b += 9
                elif r >= 0.35:
                    b += 6
                elif r >= 0.2:
                    b += 3
            if isinstance(enhanced_regime, dict):
                regime = enhanced_regime.get("regime", "")
                direction = enhanced_regime.get("trend_direction", "")
                if regime == "BEAR_TREND":
                    b += 8
                elif direction == "DOWN":
                    b += 4
                elif regime == "CHOPPY":
                    b += 2
            parts["breadth"] = min(b, 20.0)
        except Exception:
            pass

        score = min(100.0, sum(parts.values()))
        return {"score": round(score, 1), "parts": parts}

    def mode_for_score(self, score: float) -> str:
        """Skor → mod eşlemesi (giriş eşikleri; çıkış ayrı histerezis kullanır)."""
        if score >= self.cfg.get("score_attack", 72):
            return "ATTACK"
        if score >= self.cfg.get("score_defense", 55):
            return "DEFENSE"
        if score >= self.cfg.get("score_watch", 40):
            return "WATCH"
        return "OFF"

    def mode_at_least(self, mode: str) -> bool:
        return self.MODE_ORDER.get(self.mode, 0) >= self.MODE_ORDER.get(mode, 99)

    # ============================================================
    # GÜNCELLEME (30 dk'da bir — _update_market_regime'den çağrılır)
    # ============================================================

    def update(
        self,
        spy_daily_df: Optional[pd.DataFrame],
        vix: float = 0.0,
        vix_change: float = 0.0,
        breadth_neg_ratio: Optional[float] = None,
        enhanced_regime: Optional[Dict] = None,
    ):
        if not self.enabled:
            return
        result = self.compute_score(
            spy_daily_df, vix, vix_change, breadth_neg_ratio, enhanced_regime
        )
        old_mode = self.mode
        self.score = result["score"]
        self.parts = result["parts"]
        self.mode = self.mode_for_score(self.score)
        self._last_update = datetime.now()

        if self.mode != old_mode:
            p = self.parts
            logger.info(
                f"  🐻 BEAR BRAIN MOD: {old_mode} → {self.mode} | skor {self.score:.0f} "
                f"(trend {p.get('trend', 0):.0f} + momo {p.get('momentum', 0):.0f} + "
                f"vix {p.get('vix', 0):.0f} + genişlik {p.get('breadth', 0):.0f}) | "
                f"VIX={vix:.1f}({vix_change:+.1f})"
            )
        self._save_state()

    def short_conf_relief(self) -> int:
        """PAPER gerçek-short eşiği için gevşetme puanı (bear modunda daha kolay short)."""
        if self.mode == "ATTACK":
            return 10
        if self.mode == "DEFENSE":
            return 5
        return 0

    def parking_directive(self) -> Optional[str]:
        """Index parking'e talimat: ATTACK'ta sleeve çözülür, DEFENSE'te yeni park yok.

        Düşen piyasada SPY betası tutup aynı anda ters-ETF almak kendini iptal
        eder (long SPY + short-delta = fee'ye çalışmak) — beyin ikisini birden
        yönetir.
        """
        if not self.enabled:
            return None
        if self.mode == "ATTACK":
            return "unwind"
        if self.mode == "DEFENSE":
            return "pause"
        return None

    # ============================================================
    # POZİSYON SAHİPLİĞİ
    # ============================================================

    def bear_symbols(self) -> List[str]:
        """Bu beynin sahip olduğu semboller — ters-ETF'ler YALNIZ buradan alınır.
        SPXS legacy listede: elde kalan eski pozisyon varsa beyin devralır."""
        syms = [
            self.cfg.get("defense_symbol", "SH"),
            self.cfg.get("attack_symbol", "SQQQ"),
        ]
        for extra in self.cfg.get("extra_inverse_symbols", ["SPXS"]):
            if extra not in syms:
                syms.append(extra)
        return syms

    def open_bear_positions(self) -> List[str]:
        return [s for s in getattr(self.bot, "positions", {}) if s in self.bear_symbols()]

    def _is_3x(self, symbol: str) -> bool:
        return symbol in self.cfg.get("leverage3_symbols", ["SQQQ", "SPXS"])

    # ============================================================
    # ANA DÖNGÜ KANCASI (piyasa açıkken her turda)
    # ============================================================

    def run_cycle(self, config: Dict):
        if not self.enabled:
            return
        try:
            self._manage_exits()
        except Exception as e:
            self._log_cycle_error("çıkış yönetimi", e)
        try:
            self._maybe_enter(config)
        except Exception as e:
            self._log_cycle_error("giriş", e)

    def _log_cycle_error(self, stage: str, e: Exception):
        """Kalıcı arıza üretim logunda GÖRÜNSÜN (v4.10 dersi: debug'a gömülen
        hata = günlerce sessiz ölü giriş hunisi). 30dk'da bir WARNING, arası debug
        — döngü 15-30sn'de bir döndüğü için spam'e izin verilmez."""
        now = datetime.now()
        if (now - self._last_error_log.get(stage, datetime.min)).total_seconds() >= 1800:
            self._last_error_log[stage] = now
            logger.warning(f"  🐻 BearBrain {stage} hatası: {e}")
        else:
            logger.debug(f"  BearBrain {stage} hatası: {e}")

    # ------------------------------------------------------------
    # ÇIKIŞLAR — skor histerezisi + zaman-stopu + rejim dönüşü
    # (SL/TP/trailing zaten position_manager + bracket bacaklarında)
    # ------------------------------------------------------------

    def exit_reason(self, symbol: str, pos: Dict, now: Optional[datetime] = None) -> Optional[str]:
        """Bear pozisyonu için beyin-özel çıkış gerekçesi (yoksa None). Saf karar."""
        now = now or datetime.now()
        # Skor-çıkışı yalnız BU SÜREÇTE ölçülmüş skorla verilir — restart sonrası
        # ilk güncelleme gelmeden (veya SPY verisi çekilemezken) skor 0/bayat diye
        # sağlıklı pozisyon kapatılmaz (bracket SL/TP zaten Alpaca'da korur).
        score_exit = self.cfg.get("score_exit", 45)
        if self._last_update != datetime.min and self.score < score_exit:
            return f"BEAR_SCORE_EXIT skor {self.score:.0f}<{score_exit} (tez bitti)"

        # Zaman-stopu: kaldıraçlı ters-ETF'ler uzun tutulmaz (günlük rebalans
        # erimesi + vol drag). 1x daha toleranslı.
        entry_iso = pos.get("entry_time", "")
        try:
            entry_dt = datetime.fromisoformat(entry_iso)
            held_days = (now - entry_dt).days
            cap = (
                self.cfg.get("time_stop_days_3x", 7)
                if self._is_3x(symbol)
                else self.cfg.get("time_stop_days_1x", 15)
            )
            if held_days >= cap:
                return f"BEAR_TIME_STOP {held_days}g>={cap}g (kaldıraç erimesi)"
        except (ValueError, TypeError):
            pass
        return None

    def _manage_exits(self):
        open_bears = self.open_bear_positions()
        if not open_bears:
            return
        now = datetime.now()
        for sym in open_bears:
            pos = self.bot.positions.get(sym) or {}
            reason = self.exit_reason(sym, pos, now)
            if not reason:
                continue
            # PDT/cooldown reddi her 15-30sn'de spam üretmesin: 30dk'da bir dene
            last_try = self._last_exit_attempt.get(sym, datetime.min)
            if (now - last_try).total_seconds() < 1800:
                continue
            self._last_exit_attempt[sym] = now
            logger.info(f"  🐻 {sym} bear-çıkış: {reason}")
            self.bot.executor.execute_sell(sym, reason)

    # ------------------------------------------------------------
    # GİRİŞLER
    # ------------------------------------------------------------

    def _entries_today(self) -> int:
        return int(self._state.get("entries", {}).get(date.today().isoformat(), 0))

    def _cooldown_ok(self, now: datetime) -> bool:
        ts = self._state.get("last_entry_ts", "")
        if not ts:
            return True
        try:
            last = datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            return True
        hours = self.cfg.get("entry_cooldown_hours", 4)
        return (now - last).total_seconds() >= hours * 3600

    def pick_instrument(self) -> Optional[str]:
        if self.mode == "ATTACK":
            return self.cfg.get("attack_symbol", "SQQQ")
        if self.mode == "DEFENSE":
            return self.cfg.get("defense_symbol", "SH")
        return None

    def _size_bands(self) -> List:
        if self.is_paper:
            return self.cfg.get("paper_size_bands") or self.cfg.get("size_bands") or []
        return self.cfg.get("size_bands") or []

    def _maybe_enter(self, config: Dict):
        symbol = self.pick_instrument()
        if symbol is None:
            return
        bot = self.bot

        # Bayatlık kapısı: giriş için skor BU SÜREÇTE ve son 3 saatte ölçülmüş
        # olmalı (SPY/VIX verisi çekilemiyorsa restore edilmiş eski modla yeni
        # risk AÇILMAZ; mevcut pozisyonların yönetimi etkilenmez).
        if self._last_update == datetime.min:
            return
        if (datetime.now() - self._last_update).total_seconds() > 3 * 3600:
            return

        # Küresel frenler (executor floor'u ayrıca kontrol eder — çift emniyet)
        if getattr(bot, "_floor_block", False):
            return
        ks = getattr(bot, "kill_switch", None)
        if ks is not None and getattr(ks, "is_active", False):
            return

        # Pozisyon/tavan kontrolleri
        open_bears = self.open_bear_positions()
        max_pos = (
            self.cfg.get("paper_max_bear_positions", 2)
            if self.is_paper
            else self.cfg.get("max_bear_positions", 1)
        )
        if len(open_bears) >= max_pos:
            return
        if symbol in bot.positions or symbol in getattr(bot, "short_positions", {}):
            return

        now = datetime.now()
        if not self._cooldown_ok(now):
            return
        max_daily = (
            self.cfg.get("paper_max_entries_per_day", 2)
            if self.is_paper
            else self.cfg.get("max_entries_per_day", 1)
        )
        if self._entries_today() >= max_daily:
            return

        # Maruziyet tavanı: mevcut bear değeri + planlanan boyut <= tavan
        bands = self._size_bands()
        planned_usd = 0.0
        for band_conf, band_usd in bands:
            if self.score >= band_conf and float(band_usd) > planned_usd:
                planned_usd = float(band_usd)
        if planned_usd <= 0:
            return
        equity = float(getattr(bot, "equity", 0) or 0)
        if equity > 0:
            exposure = sum(
                float(p.get("entry_price", 0)) * float(p.get("qty", 0))
                for s, p in bot.positions.items()
                if s in self.bear_symbols()
            )
            cap = equity * self.cfg.get("max_bear_exposure_pct", 0.35)
            if exposure + planned_usd > cap:
                logger.debug(
                    f"  BearBrain {symbol}: maruziyet tavanı "
                    f"(${exposure:.0f}+${planned_usd:.0f} > ${cap:.0f})"
                )
                return

        # Wash-sale: varsayılan sadece uyarı (30g kilit stratejiyi öldürür;
        # $487 hesapta vergi etkisi kuruş — bilinçli risk kabulü). Bayrakla sertleşir.
        try:
            is_wash, wash_reason = bot.wash_sale_tracker.check_wash_sale(symbol)
            if is_wash:
                if self.cfg.get("respect_wash_sale", False):
                    logger.info(f"  🐻 {symbol} girişi wash-sale bloğunda: {wash_reason}")
                    return
                logger.info(f"  🐻 {symbol} wash-sale UYARISI (giriş sürüyor): {wash_reason}")
        except Exception:
            pass

        # Teknik veri (fiyat/ATR) — normal analiz zinciri
        analysis = bot._get_technical_analysis(symbol, config)
        if not analysis or not analysis.get("price"):
            return

        p = self.parts
        analysis["confidence"] = self.score
        analysis["sector_weight"] = 1.0
        analysis["reasons"] = [
            f"🐻 BEAR_BRAIN {self.mode} skor={self.score:.0f}",
            f"trend:{p.get('trend', 0):.0f} momo:{p.get('momentum', 0):.0f} "
            f"vix:{p.get('vix', 0):.0f} genişlik:{p.get('breadth', 0):.0f}",
        ]

        # Enstrümana özel emir planı: bantlar skoru $ boyuta çevirir; stop planı
        # kaldıraca göre genişler. Kopya config → global ayarlara dokunulmaz.
        call_cfg = dict(config)
        call_cfg["conf_position_bands"] = bands
        call_cfg["fixed_position_usd"] = 0
        call_cfg["max_position_usd"] = max(float(b[1]) for b in bands)
        call_cfg["min_rr_ratio"] = self.cfg.get("rr_target", 1.5)
        if self._is_3x(symbol):
            call_cfg["stop_loss_pct"] = self.cfg.get("sl_3x", 0.06)
            call_cfg["stop_loss_max_pct"] = self.cfg.get("sl_max_3x", 0.08)
            call_cfg["take_profit_pct"] = self.cfg.get("tp_floor_3x", 0.09)
            call_cfg["take_profit_max_pct"] = self.cfg.get("tp_cap_3x", 0.15)
            call_cfg["atr_stop_multiplier"] = self.cfg.get("atr_mult_3x", 2.0)
        else:
            call_cfg["stop_loss_pct"] = self.cfg.get("sl_1x", 0.04)
            call_cfg["stop_loss_max_pct"] = self.cfg.get("sl_max_1x", 0.05)
            call_cfg["take_profit_pct"] = self.cfg.get("tp_floor_1x", 0.06)
            call_cfg["take_profit_max_pct"] = self.cfg.get("tp_cap_1x", 0.08)

        logger.info(
            f"  🐻 BEAR BRAIN GİRİŞ: {symbol} ({'3x' if self._is_3x(symbol) else '1x'} ters) "
            f"| mod={self.mode} skor={self.score:.0f} → hedef ${planned_usd:.0f}"
        )
        bought = bot.executor.execute_buy(symbol, analysis, call_cfg)
        if bought:
            try:
                bot.positions[symbol]["bear_brain"] = True
                bot.positions[symbol]["entry_score"] = round(self.score, 1)
            except Exception:
                pass
            self._state["last_entry_ts"] = now.isoformat()
            entries = self._state.setdefault("entries", {})
            key = date.today().isoformat()
            entries[key] = int(entries.get(key, 0)) + 1
            self._save_state()

    # ============================================================
    # KALICI DURUM
    # ============================================================

    def _load_state(self):
        try:
            if os.path.exists(self._state_file):
                with open(self._state_file, "r") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._state.update(data)
        except Exception:
            pass

    def _save_state(self):
        try:
            # Eski gün sayaçlarını buda (7 günden eski)
            entries = self._state.get("entries", {})
            if len(entries) > 7:
                for k in sorted(entries.keys())[:-7]:
                    entries.pop(k, None)
            self._state["last_score"] = self.score
            self._state["last_mode"] = self.mode
            with open(self._state_file, "w") as f:
                json.dump(self._state, f)
        except Exception:
            pass
