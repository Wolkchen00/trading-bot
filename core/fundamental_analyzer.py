"""
Fundamental Analyzer — Hisse Senedi Temel Analiz Modülü
Alpha Vantage ile P/E, EPS, Revenue, Dividend analizi.

Kaynaklar:
  1. Alpha Vantage Company Overview (ücretsiz)
  2. Yahoo Finance (fallback)
  3. Sektör karşılaştırması
"""
import os
import time
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from core.av_quota import AVOutcome, classify_response, shared_store
from core.fundamentals_cache import FundamentalsCache
from utils.logger import logger


FUNDAMENTAL_CONFIG = {
    # Cache
    "cache_hours": 12,  # Temel veriler yavaş değişir

    # Eşikler
    "pe_cheap_threshold": 15,      # P/E < 15 = ucuz
    "pe_expensive_threshold": 40,  # P/E > 40 = pahalı
    "eps_growth_threshold": 10,    # EPS büyüme > %10 = iyi
    "dividend_yield_good": 2.0,    # Dividend > %2 = bonus
    "debt_equity_danger": 2.0,     # D/E > 2 = riskli
}


class FundamentalAnalyzer:
    """Hisse senedi temel analiz — P/E, EPS, Revenue, Margins."""

    def __init__(self, quota=None, disk_cache=None, funnel=None):
        self.alpha_vantage_key = os.getenv("ALPHA_VANTAGE_KEY", "")
        self.cache = {}
        self.last_fetch = {}

        # R16: kota ve disk cache AYRI dosyalarda. Cache bozulursa temiz baslanir
        # (veri kaybi), kota bozulursa gun tukenmis sayilir (fail-closed).
        self.quota = quota if quota is not None else shared_store()
        self.disk_cache = disk_cache if disk_cache is not None else FundamentalsCache()
        self._kota_uyarildi = False
        try:
            from core.funnel import DailyFunnel
            self._funnel = funnel if funnel is not None else DailyFunnel()
        except Exception:
            self._funnel = funnel
        try:
            from config import AV_QUOTA_CONFIG
            self._call_sleep = float(AV_QUOTA_CONFIG.get("call_sleep_seconds", 15))
        except Exception:
            self._call_sleep = 15.0

        if self.alpha_vantage_key:
            logger.info(
                f"FundamentalAnalyzer baslatildi , Alpha Vantage aktif "
                f"(kota {self.quota.profile}: {self.quota.remaining()}/"
                f"{self.quota.budget} kaldi)"
            )
        else:
            logger.info("FundamentalAnalyzer baslatildi , API key yok, sınırlı mod")

    # ============================================================
    # 1. ŞİRKET GENEL BAKIŞ
    # ============================================================

    def get_company_overview(
        self, symbol: str, force_refresh: bool = False
    ) -> Optional[Dict]:
        """Alpha Vantage Company Overview , kota + disk cache + tipli sonuclar (R16).

        Sira onemli ve her adim bir agi/uykuyu ONLUYOR:
          1. Bellek cache  -> ag yok, uyku yok
          2. Disk cache    -> ag yok, uyku yok (restart'i atlatir)
          3. Negatif cache -> kota bugun tukendi, AG CAGRISI YAPMA
          4. Kota rezervi  -> butce doluysa AG CAGRISI YAPMA
          5. Gercek cagri  -> ANCAK BURADA uyku var
        Eski kod 2-4'u hic yapmiyordu: her sembol her turda AV'yi cagirip
        `time.sleep(15)`u basarisiz cagrida bile odeyordu.
        """
        cache_key = f"overview_{symbol}"
        # force_refresh: on-cekim yolu BAYAT bir sembolu tazelemek icin cagirir.
        # Cache dallarini atlamazsa bayat yuku aninda geri dondurur, "basarili"
        # sayar ve HICBIR SEYI tazelemez , imlec yine olu kalirdi (Codex bulgusu).
        if not force_refresh and self._is_cached(cache_key):
            return self.cache[cache_key]

        # 2) DISK CACHE , bayatlik sozlesmesiyle.
        # BELLEK CACHE'I DOLDURULMAZ: doldurulunca last_fetch=now yaziliyor ve
        # 23.9 saatlik bir kayit bellek cache'inde 12 saat daha "taze" gorunup
        # bayatlik damgasini kaybediyordu (Codex bulgusu). Disk cache zaten
        # bellekte yuklu bir sozluk; her cagrida yas YENIDEN hesaplanir.
        yuk, yas, bolge = self.disk_cache.get(symbol)
        if not force_refresh and bolge in ("TAZE", "BAYAT") and yuk:
            yuk = dict(yuk)
            yuk["data_age_hours"] = round(yas, 1)
            yuk["is_stale"] = (bolge == "BAYAT")
            return yuk
        if bolge == "SOURCE_UNAVAILABLE":
            # Cok bayat: veri VAR ama kullanilmaz. Yokluk gibi davranilir.
            logger.debug(
                f"{symbol} temel verisi {yas:.0f} saatlik , max bayatlik asildi, "
                f"SOURCE_UNAVAILABLE"
            )

        if not self.alpha_vantage_key:
            return self._get_yahoo_fallback(symbol)

        # 3a) ANAHTAR GENELI TUKENME , bir tuketici kotanin bittigini ogrendiyse
        # digerleri bosa cagri yapip 15 sn uyumamali. Onceki surumde isaret
        # yalnizca SEMBOL bazindaydi ve kalan butce her sembol icin yeniden
        # bosa harcaniyordu (Codex bulgusu).
        if self.quota.is_exhausted():
            self._kota_uyar(symbol)
            return None

        # 3b) SEMBOL BAZLI NEGATIF CACHE
        if self.disk_cache.is_negative_cached(symbol):
            return None

        # 4) KOTA REZERVI , cagridan ONCE, surecler arasi kilit altinda.
        # SEBEBE gore davran: kilit/yazma hatasi KOTA TUKENMESI DEGILDIR ve
        # sembolu gun boyu negatif cache'lememeli (Codex bulgusu). Onceki surum
        # dort ayri sebebi tek `False`'a cokertip hepsini tukenme sayiyordu.
        verildi, sebep = self.quota.reserve("fundamental")
        if not verildi:
            from core.av_quota import ReserveReason
            if sebep in (ReserveReason.BUDGET_EXHAUSTED,
                         ReserveReason.PROVIDER_EXHAUSTED):
                self._kota_uyar(symbol)
                self.disk_cache.mark_quota_exhausted(symbol)
            else:
                # GECICI ya da yapisal: negatif cache YOK, ayni gun tekrar denenir.
                logger.debug(
                    f"{symbol} temel veri rezervasyonu verilmedi ({sebep.value}) , "
                    f"gecici sayiliyor, negatif cache yazilmadi"
                )
            return None

        try:
            url = "https://www.alphavantage.co/query"
            params = {
                "function": "OVERVIEW",
                "symbol": symbol,
                "apikey": self.alpha_vantage_key,
            }
            response = requests.get(url, params=params, timeout=15)
            # 5) Uyku YALNIZ gercek ag cagrisindan sonra.
            time.sleep(self._call_sleep)

            try:
                data = response.json()
            except ValueError:
                data = {}
            sonuc = classify_response(response.status_code, response.text, data)

            if sonuc is AVOutcome.QUOTA_EXHAUSTED:
                # HTTP 200 + kota uyari govdesi. Sadece status_code'a bakan kod
                # bunu "veri yok" sanip her turda yeniden cagirirdi.
                # Isaret PAYLASILAN kayda yazilir: diger semboller ve diger
                # tuketiciler de bugun bosa cagri yapmasin.
                self.quota.mark_exhausted()
                self._kota_uyar(symbol)
                self.disk_cache.mark_quota_exhausted(symbol)
                return None
            if sonuc is AVOutcome.RETRYABLE_ERROR:
                # Gecici ariza , negatif cache'lenmez, ayni gun tekrar denenir.
                logger.debug(
                    f"AV gecici hata {symbol} (HTTP {response.status_code}) , "
                    f"tekrar denenecek"
                )
                return None
            if sonuc is AVOutcome.NO_DATA or "Symbol" not in data:
                return None

            overview = {
                "symbol": data.get("Symbol", symbol),
                "name": data.get("Name", ""),
                "sector": data.get("Sector", ""),
                "industry": data.get("Industry", ""),
                "market_cap": self._safe_float(data.get("MarketCapitalization", 0)),
                "pe_ratio": self._safe_float(data.get("PERatio", 0)),
                "peg_ratio": self._safe_float(data.get("PEGRatio", 0)),
                "eps": self._safe_float(data.get("EPS", 0)),
                "revenue_per_share": self._safe_float(data.get("RevenuePerShareTTM", 0)),
                "profit_margin": self._safe_float(data.get("ProfitMargin", 0)),
                "dividend_yield": self._safe_float(data.get("DividendYield", 0)) * 100,
                "beta": self._safe_float(data.get("Beta", 1)),
                "52_week_high": self._safe_float(data.get("52WeekHigh", 0)),
                "52_week_low": self._safe_float(data.get("52WeekLow", 0)),
                "50_day_avg": self._safe_float(data.get("50DayMovingAverage", 0)),
                "200_day_avg": self._safe_float(data.get("200DayMovingAverage", 0)),
                "analyst_target": self._safe_float(data.get("AnalystTargetPrice", 0)),
                "forward_pe": self._safe_float(data.get("ForwardPE", 0)),
            }

            self.cache[cache_key] = overview
            self.last_fetch[cache_key] = datetime.now()
            self.disk_cache.put(symbol, overview)   # restart'i atlatsin
            return overview

        except Exception as e:
            # Ag/ayristirma istisnasi GECICI sayilir; negatif cache'lenmez.
            logger.debug(f"Alpha Vantage overview hatası {symbol}: {e}")
        return None

    def prefetch_due(self, universe, limit: int = None) -> dict:
        """Tur basinda, EN-ESKI-ONCE sirayla bayat sembolleri tazeler.

        Bu, imleci uretime baglayan tek cagri noktasidir. Talep uzerine cekim
        tek basina yeterli degil: sabit sembol sirasi butceyi her gun bastaki
        sembollere harcar ve kuyruk HIC tazelenmez.

        Butce kadar cagri yapar, sonra durur. Kota tukendiyse hic cagri yapmaz.
        Donen sozluk kapsama telemetrisidir; cagiran raporlayabilir.
        """
        rapor = {"denenen": 0, "basarili": 0, "atlanan_kota": 0}
        try:
            evren = [s for s in universe]
            if not evren:
                return rapor
            if self.quota.is_exhausted():
                rapor["atlanan_kota"] = 1
                rapor["kapsama"] = self.disk_cache.coverage(evren)
                return rapor

            # TUR BASINA SERT TAVAN , bu cagri ANA ISLEM DONGUSUNDE kosuyor.
            # Butun butceyi (12 cagri x 15 sn uyku + timeout'lar) tek turda
            # harcamak dongu ~6 DAKIKA bloklar ve acik pozisyonlarin stop/koruma
            # yonetimini geciktirir , gercek parada kabul edilemez (Codex bulgusu).
            # Kucuk bir tavan gun boyunca yine butun butceyi kullanir.
            tavan = self._prefetch_max_per_round() if limit is None else int(limit)
            kalan = min(tavan, self.quota.remaining())
            adaylar = self.disk_cache.next_refresh_candidates(evren, kalan)
            for sembol in adaylar:
                rapor["denenen"] += 1
                # force_refresh ZORUNLU: aksi halde bayat yuk aninda geri doner,
                # "basarili" sayilir ve hicbir sey tazelenmez.
                if self.get_company_overview(sembol, force_refresh=True) is not None:
                    rapor["basarili"] += 1
                elif self.quota.is_exhausted():
                    rapor["atlanan_kota"] += 1
                    break          # kota bitti, kalanlari deneme
            rapor["kapsama"] = self.disk_cache.coverage(evren)
        except Exception as exc:
            # Onceden-cekim BEST-EFFORT: arizasi tarama turunu durduramaz.
            logger.debug(f"Temel veri on-cekimi hatasi: {exc}")
        return rapor

    @staticmethod
    def _prefetch_max_per_round() -> int:
        """Tur basina en fazla kac sembol tazelenir (ana dongu bloklamasi)."""
        try:
            from config import AV_QUOTA_CONFIG
            return max(0, int(AV_QUOTA_CONFIG.get("prefetch_max_per_round", 2)))
        except Exception:
            return 2

    def _kota_uyar(self, symbol: str) -> None:
        """Kota tukenmesi SESSIZ kalmaz , eski kod logger.debug ile yutuyordu."""
        if not self._kota_uyarildi:
            kalan = self.quota.remaining()
            logger.warning(
                f"ALPHA VANTAGE KOTASI TUKENDI ({self.quota.profile} profili, "
                f"butce {self.quota.budget}/gun, kalan {kalan}) , FundAgent bugun "
                f"yeni temel veri CEKEMEYECEK. Cache'teki veriler kullanilmaya "
                f"devam eder."
            )
            self._kota_uyarildi = True
        self._funnel_isaretle(symbol)

    def _funnel_isaretle(self, symbol: str) -> None:
        """R11 huni etiketi. `fund_source_quota` STAGES'te YOK, bu yuzden
        `gate_block` sebebi olarak kaydedilir , aksi halde sessizce yutulurdu."""
        try:
            from core.funnel import DailyFunnel
            if "fund_source_quota" in getattr(DailyFunnel, "STAGES", ()):
                self._funnel.bump("fund_source_quota", symbol=symbol)
            else:
                self._funnel.bump(
                    "gate_block", reason="fund_source_quota", symbol=symbol
                )
        except Exception:
            pass

    def _get_yahoo_fallback(self, symbol: str) -> Optional[Dict]:
        """Yahoo Finance yedegi , OLCULEN DURUM: BUGUN CALISMIYOR.

        2026-09-03 olcumu: `query1.finance.yahoo.com/v10/finance/quoteSummary`
        HTTP 401 donuyor (Yahoo artik crumb/cookie istiyor). Ayrica bu fonksiyon
        yalnizca AV anahtari YOKSA cagriliyor (get_company_overview), yani kota
        tukendiginde ZATEN devreye girmiyordu , iki kat olu.

        Kod silinmedi ama sessiz kalmiyor: ilk cagrida bir kez UYARIR ve
        basarisizlik durustce None (NO_DATA) doner.
        """
        if not getattr(self, "_yahoo_uyarildi", False):
            logger.warning(
                "Yahoo temel veri yedegi cagrildi , bu kaynak 2026-09-03 itibariyle "
                "HTTP 401 donuyor (crumb/cookie gerekiyor). Gercek bir yedek kaynak "
                "isteniyorsa RF-ISSUES-4.md::YAHOO-YEDEGI-OLU maddesine bakin."
            )
            self._yahoo_uyarildi = True
        try:
            url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
            params = {"modules": "summaryDetail,defaultKeyStatistics,financialData"}
            headers = {"User-Agent": "Mozilla/5.0"}
            response = requests.get(url, params=params, headers=headers, timeout=10)

            if response.status_code == 200:
                data = response.json()
                result = data.get("quoteSummary", {}).get("result", [{}])[0]
                summary = result.get("summaryDetail", {})
                stats = result.get("defaultKeyStatistics", {})
                financials = result.get("financialData", {})

                return {
                    "symbol": symbol,
                    "pe_ratio": summary.get("trailingPE", {}).get("raw", 0),
                    "forward_pe": summary.get("forwardPE", {}).get("raw", 0),
                    "dividend_yield": summary.get("dividendYield", {}).get("raw", 0) * 100,
                    "beta": summary.get("beta", {}).get("raw", 1),
                    "market_cap": summary.get("marketCap", {}).get("raw", 0),
                    "52_week_high": summary.get("fiftyTwoWeekHigh", {}).get("raw", 0),
                    "52_week_low": summary.get("fiftyTwoWeekLow", {}).get("raw", 0),
                    "eps": stats.get("trailingEps", {}).get("raw", 0),
                    "peg_ratio": stats.get("pegRatio", {}).get("raw", 0),
                    "profit_margin": financials.get("profitMargins", {}).get("raw", 0),
                    "analyst_target": financials.get("targetMeanPrice", {}).get("raw", 0),
                    "source": "yahoo",
                }
        except Exception as e:
            logger.debug(f"Yahoo fallback hatası {symbol}: {e}")
        return None

    # ============================================================
    # 2. TEMEL ANALİZ SKORU
    # ============================================================

    def analyze_fundamentals(self, symbol: str) -> Dict:
        """
        Hisse temel analiz skoru.
        
        Returns:
            {
                'fundamental_score': int (-30 ile +30),
                'signal': 'BULLISH' | 'BEARISH' | 'NEUTRAL',
                'metrics': dict,
                'reasons': list[str],
            }
        """
        overview = self.get_company_overview(symbol)
        if not overview:
            return {
                "fundamental_score": 0,
                "signal": "NEUTRAL",
                "data_age_hours": None,
                "is_stale": False,
                "data_source": "SOURCE_UNAVAILABLE",
                "metrics": {},
                "reasons": ["Temel veri bulunamadı"],
            }

        score = 0
        reasons = []

        # --- P/E Oranı ---
        pe = overview.get("pe_ratio", 0)
        if pe > 0:
            if pe < FUNDAMENTAL_CONFIG["pe_cheap_threshold"]:
                score += 10
                reasons.append(f"P/E düşük ({pe:.1f}) — değer fırsatı")
            elif pe > FUNDAMENTAL_CONFIG["pe_expensive_threshold"]:
                score -= 10
                reasons.append(f"P/E yüksek ({pe:.1f}) — pahalı")

        # --- EPS ---
        eps = overview.get("eps", 0)
        if eps > 0:
            score += 5
            reasons.append(f"EPS pozitif ({eps:.2f})")
        elif eps < 0:
            score -= 10
            reasons.append(f"EPS negatif ({eps:.2f}) — zarar ediyor")

        # --- Profit Margin ---
        margin = overview.get("profit_margin", 0)
        if margin > 0.15:
            score += 5
            reasons.append(f"Kâr marjı güçlü ({margin:.0%})")
        elif margin < 0:
            score -= 5
            reasons.append(f"Kâr marjı negatif ({margin:.0%})")

        # --- Dividend ---
        div_yield = overview.get("dividend_yield", 0)
        if div_yield > FUNDAMENTAL_CONFIG["dividend_yield_good"]:
            score += 3
            reasons.append(f"Temettü verimi iyi ({div_yield:.1f}%)")

        # --- 52-hafta pozisyonu ---
        high = overview.get("52_week_high", 0)
        low = overview.get("52_week_low", 0)
        if high > 0 and low > 0:
            price_range = high - low
            if price_range > 0:
                current_pos = overview.get("50_day_avg", (high + low) / 2)
                pct_from_low = (current_pos - low) / price_range
                if pct_from_low < 0.3:
                    score += 5
                    reasons.append("52-hafta dibine yakın — potansiyel fırsat")
                elif pct_from_low > 0.9:
                    score -= 3
                    reasons.append("52-hafta zirvesine yakın — dikkat")

        # --- Analist hedef fiyatı ---
        target = overview.get("analyst_target", 0)
        avg_50 = overview.get("50_day_avg", 0)
        if target > 0 and avg_50 > 0:
            upside = (target - avg_50) / avg_50 * 100
            if upside > 15:
                score += 5
                reasons.append(f"Analist hedef %{upside:.0f} yukarıda")
            elif upside < -10:
                score -= 5
                reasons.append(f"Analist hedef %{abs(upside):.0f} aşağıda")

        # Sinyal
        if score >= 10:
            signal = "BULLISH"
        elif score <= -10:
            signal = "BEARISH"
        else:
            signal = "NEUTRAL"

        result = {
            "fundamental_score": max(min(score, 30), -30),
            "signal": signal,
            # R16: veri yasi karara ULASIR. Bayat veriyle verilen karar, bayat
            # oldugunu bilerek verilmis olmali; sessizce taze saymak yasak.
            "data_age_hours": overview.get("data_age_hours"),
            "is_stale": bool(overview.get("is_stale", False)),
            "data_source": overview.get("source", "alpha_vantage"),
            "metrics": {
                "pe_ratio": pe,
                "eps": eps,
                "profit_margin": margin,
                "dividend_yield": div_yield,
                "beta": overview.get("beta", 1),
                "sector": overview.get("sector", "Unknown"),
            },
            "reasons": reasons,
        }

        logger.info(
            f"  Temel {symbol}: P/E={pe:.1f} EPS={eps:.2f} "
            f"Marj={margin:.0%} -> Skor={score} {signal}"
        )
        return result

    # ============================================================
    # YARDIMCI
    # ============================================================

    def _safe_float(self, val) -> float:
        try:
            return float(val) if val and val != "None" and val != "-" else 0.0
        except (ValueError, TypeError):
            return 0.0

    def _is_cached(self, key: str) -> bool:
        if key not in self.cache or key not in self.last_fetch:
            return False
        elapsed = (datetime.now() - self.last_fetch[key]).total_seconds()
        return elapsed < FUNDAMENTAL_CONFIG["cache_hours"] * 3600
