"""
Hisse Senedi Haber Takip & Gelişmiş Duygu Analizi Modülü
- Alpha Vantage News API
- Marketaux API  
- Google News RSS (ücretsiz, gerçek zamanlı)
- Finviz haber tarama
- Fear & Greed Index (CNN)
- FinBERT + VADER duygu analizi
- Jeopolitik risk takibi (savaş, ateşkes ihlali, enerji krizi)
- Breaking News dedektörü
"""
import os
import time
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from utils.logger import logger
from config import STOCK_SEARCH_TERMS, GEOPOLITICAL_KEYWORDS

# FinBERT (opsiyonel)
try:
    from core.finbert_analyzer import FinBERTAnalyzer
    FINBERT_AVAILABLE = True
except ImportError:
    FINBERT_AVAILABLE = False

# VADER fallback
try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    VADER_AVAILABLE = True
except ImportError:
    VADER_AVAILABLE = False


# ============================================================
# HABER KONFİGÜRASYONU
# ============================================================
NEWS_CONFIG = {
    # Duygu analizi anahtar kelimeleri
    "bullish_keywords": [
        # Hisse pozitif
        "earnings beat", "revenue growth", "guidance raised", "buyback",
        "stock buyback", "dividend increase", "upgrade", "outperform",
        "strong buy", "price target raised", "record revenue", "beats estimates",
        "exceeded expectations", "upside surprise", "market rally",
        "bull market", "all-time high", "ipo success", "merger", "acquisition",
        "partnership", "contract win", "fda approval", "patent granted",
        # Makro pozitif
        "rate cut", "inflation cools", "jobs growth", "stimulus",
        "fed dovish", "soft landing", "ceasefire agreed", "peace deal",
        "trade deal", "sanctions lifted", "de-escalation",
    ],
    "bearish_keywords": [
        # Hisse negatif
        "earnings miss", "revenue decline", "guidance lowered", "downgrade",
        "underperform", "sell rating", "price target cut", "missed estimates",
        "profit warning", "layoffs", "restructuring", "sec investigation",
        "class action lawsuit", "data breach", "product recall", "ceo resign",
        "insider selling", "dilution", "secondary offering",
        # Makro negatif
        "rate hike", "inflation surge", "recession", "unemployment rise",
        "fed hawkish", "default risk", "banking crisis", "yield inversion",
        "bear market", "sell-off", "crash", "panic",
        # Jeopolitik (duplike olsa da keyword_score için gerekli)
        "war escalat", "military strike", "missile", "strait of hormuz",
        "oil surge", "oil spike", "sanctions", "embargo", "tariff war",
        "invasion", "bombing", "nuclear", "blockade", "supply disruption",
        "drone attack", "ceasefire violat", "ceasefire collapse",
        "resumed attack", "broke ceasefire", "ground offensive",
        "airstrike", "shelling", "terrorist", "hostage",
    ],

    # Cache süresi
    "cache_minutes": 5,           # Normal haberler: 5 dk
    "geo_cache_minutes": 2,       # Jeopolitik: 2 dk (daha hızlı yenile)
    "breaking_cache_minutes": 1,  # Breaking news: 1 dk

    # API rate limit koruması
    "alpha_vantage_cooldown": 15,
    "marketaux_cooldown": 10,
}


class StockNewsAnalyzer:
    """Hisse senedi haberleri analizi — Alpha Vantage + Marketaux + Google News RSS."""

    def __init__(self):
        self.alpha_vantage_key = os.getenv("ALPHA_VANTAGE_KEY", "")
        self.marketaux_token = os.getenv("MARKETAUX_TOKEN", "")
        self.cache = {}
        self.last_fetch = {}
        self.finbert = None
        self.vader = None
        self._last_geo_risk = "NORMAL"
        self._geo_risk_score = 0
        self._breaking_detected = False
        self._marketaux_daily_calls = 0
        self._marketaux_daily_reset = datetime.now().date()
        self._marketaux_max_daily = 50  # Free tier: 100/gun, biz 50 ile sinirlariz

        # FinBERT veya VADER başlat
        if FINBERT_AVAILABLE:
            try:
                self.finbert = FinBERTAnalyzer()
                # Gerçek kaynağı logla — FinBERTAnalyzer, onnxruntime yoksa sessizce
                # iç VADER'a düşer; koşulsuz "FinBERT aktif" demek yanıltıcıydı.
                _src = self.finbert.get_status().get("active_source", "?")
                logger.info(f"StockNewsAnalyzer: sentiment kaynagi = {_src}")
            except Exception:
                pass

        if self.finbert is None and VADER_AVAILABLE:
            self.vader = SentimentIntensityAnalyzer()
            logger.info("StockNewsAnalyzer: VADER fallback aktif")

        sources = []
        if self.alpha_vantage_key:
            sources.append("Alpha Vantage")
        if self.marketaux_token:
            sources.append("Marketaux")
        logger.info(f"StockNewsAnalyzer baslatildi — Kaynaklar: {', '.join(sources) or 'YOK'}")

    # ============================================================
    # 1. ANA ANALİZ FONKSİYONU
    # ============================================================

    def analyze_stock_news(self, symbol: str) -> Dict:
        """
        Hisse bazlı haber analizi.
        
        Returns:
            {
                'news_score': int (-100 ile +100),
                'signal': 'BULLISH' | 'BEARISH' | 'NEUTRAL',
                'article_count': int,
                'top_headlines': list,
                'geopolitical_risk': str,
            }
        """
        cache_key = f"news_{symbol}"
        if self._is_cached(cache_key):
            return self.cache[cache_key]

        articles = []

        # Alpha Vantage'den haber çek
        if self.alpha_vantage_key:
            av_articles = self._fetch_alpha_vantage_news(symbol)
            articles.extend(av_articles)

        # Google News RSS (ucretsiz, gercek zamanli — BIRINCIL KAYNAK)
        gn_articles = self._fetch_google_news(symbol)
        articles.extend(gn_articles)

        # Marketaux (YEDEK — sadece Google News bos donerse, gunluk limit var)
        if self.marketaux_token and len(gn_articles) == 0:
            if self._marketaux_daily_reset != datetime.now().date():
                self._marketaux_daily_calls = 0
                self._marketaux_daily_reset = datetime.now().date()
            if self._marketaux_daily_calls < self._marketaux_max_daily:
                mx_articles = self._fetch_marketaux_news(symbol)
                articles.extend(mx_articles)
                self._marketaux_daily_calls += 1
            else:
                logger.debug(f"  Marketaux gunluk limit ({self._marketaux_max_daily}) doldu, atlaniyor")

        # Analiz et
        if not articles:
            result = {
                "news_score": 0,
                "signal": "NEUTRAL",
                "article_count": 0,
                "top_headlines": [],
                "geopolitical_risk": "UNKNOWN",
            }
        else:
            score, sentiments = self._analyze_articles(articles, symbol)
            geo_risk = self._check_geopolitical_risk(articles)

            if score >= 15:
                signal = "BULLISH"
            elif score <= -15:
                signal = "BEARISH"
            else:
                signal = "NEUTRAL"

            result = {
                "news_score": score,
                "signal": signal,
                "article_count": len(articles),
                "top_headlines": [a.get("title", "")[:80] for a in articles[:3]],
                "geopolitical_risk": geo_risk,
                "sentiments": sentiments,
            }

        self.cache[cache_key] = result
        self.last_fetch[cache_key] = datetime.now()

        logger.info(
            f"  Haber {symbol}: {result['article_count']} haber, "
            f"skor={result['news_score']}, sinyal={result['signal']}, "
            f"jeopolitik={result['geopolitical_risk']}"
        )
        return result

    # ============================================================
    # 2. ALPHA VANTAGE NEWS
    # ============================================================

    def _fetch_alpha_vantage_news(self, symbol: str) -> List[Dict]:
        """Alpha Vantage News Sentiment API."""
        try:
            url = "https://www.alphavantage.co/query"
            params = {
                "function": "NEWS_SENTIMENT",
                "tickers": symbol,
                "limit": 10,
                "apikey": self.alpha_vantage_key,
            }
            response = requests.get(url, params=params, timeout=15)
            time.sleep(NEWS_CONFIG["alpha_vantage_cooldown"])

            if response.status_code == 200:
                data = response.json()
                feed = data.get("feed", [])
                articles = []
                for item in feed[:10]:
                    articles.append({
                        "title": item.get("title", ""),
                        "summary": item.get("summary", ""),
                        "source": item.get("source", ""),
                        "published": item.get("time_published", ""),
                        "sentiment_score": float(item.get("overall_sentiment_score", 0)),
                        "sentiment_label": item.get("overall_sentiment_label", "Neutral"),
                        "api": "alpha_vantage",
                    })
                return articles
        except Exception as e:
            logger.debug(f"Alpha Vantage haber hatası {symbol}: {e}")
        return []

    # ============================================================
    # 3. MARKETAUX NEWS
    # ============================================================

    def _fetch_marketaux_news(self, symbol: str) -> List[Dict]:
        """Marketaux News API."""
        try:
            url = "https://api.marketaux.com/v1/news/all"
            params = {
                "symbols": symbol,
                "filter_entities": "true",
                "language": "en",
                "limit": 10,
                "api_token": self.marketaux_token,
            }
            response = requests.get(url, params=params, timeout=15)
            time.sleep(NEWS_CONFIG["marketaux_cooldown"])

            if response.status_code == 200:
                data = response.json()
                articles = []
                for item in data.get("data", [])[:10]:
                    articles.append({
                        "title": item.get("title", ""),
                        "summary": item.get("description", ""),
                        "source": item.get("source", ""),
                        "published": item.get("published_at", ""),
                        "sentiment_score": 0,  # Kendi analiz edeceğiz
                        "api": "marketaux",
                    })
                return articles
        except Exception as e:
            logger.debug(f"Marketaux haber hatası {symbol}: {e}")
        return []

    # ============================================================
    # 4. DUYGU ANALİZİ
    # ============================================================

    def _analyze_articles(self, articles: List[Dict], symbol: str) -> tuple:
        """Haber duygu analizi — FinBERT/VADER + keyword."""
        total_score = 0
        sentiments = []

        for article in articles:
            text = f"{article.get('title', '')} {article.get('summary', '')}"
            if not text.strip():
                continue

            # API'den gelen sentiment skoru (Alpha Vantage)
            api_score = article.get("sentiment_score", 0)

            # Keyword analizi
            keyword_score = self._keyword_score(text)

            # NLP analizi (FinBERT veya VADER)
            nlp_score = 0
            if self.finbert:
                try:
                    result = self.finbert.analyze(text[:512])
                    if result["label"] == "positive":
                        nlp_score = result["score"] * 30
                    elif result["label"] == "negative":
                        nlp_score = -result["score"] * 30
                except Exception:
                    pass
            elif self.vader:
                scores = self.vader.polarity_scores(text)
                nlp_score = scores["compound"] * 25

            # Birleşik skor (ağırlıklı)
            article_score = int(
                api_score * 20 * 0.3 +    # API skoru %30
                keyword_score * 0.3 +       # Keyword %30
                nlp_score * 0.4             # NLP %40
            )

            # Zaman ağırlığı (yeni haberler daha önemli)
            time_weight = self._get_time_weight(article.get("published", ""))
            article_score = int(article_score * time_weight)

            total_score += article_score
            sentiments.append({
                "title": article.get("title", "")[:60],
                "score": article_score,
                "source": article.get("api", "unknown"),
            })

        # Normalize (-100 ile +100 arası)
        if len(articles) > 0:
            total_score = max(min(total_score, 100), -100)

        return total_score, sentiments

    def _keyword_score(self, text: str) -> float:
        """Anahtar kelime bazlı skor."""
        text_lower = text.lower()
        score = 0

        for keyword in NEWS_CONFIG["bullish_keywords"]:
            if keyword in text_lower:
                score += 10
        for keyword in NEWS_CONFIG["bearish_keywords"]:
            if keyword in text_lower:
                score -= 10

        return max(min(score, 50), -50)

    def _get_time_weight(self, published: str) -> float:
        """Yeni haberler daha ağırlıklı."""
        try:
            if not published:
                return 0.5
            # Çeşitli format desteği
            for fmt in ["%Y%m%dT%H%M%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"]:
                try:
                    pub_dt = datetime.strptime(published[:19], fmt)
                    hours_ago = (datetime.now() - pub_dt).total_seconds() / 3600
                    if hours_ago < 1:
                        return 1.0
                    elif hours_ago < 6:
                        return 0.8
                    elif hours_ago < 24:
                        return 0.5
                    else:
                        return 0.3
                except ValueError:
                    continue
        except Exception:
            pass
        return 0.5

    # ============================================================
    # 5. JEOPOLİTİK RİSK TAKİBİ
    # ============================================================

    def _check_geopolitical_risk(self, articles: List[Dict]) -> str:
        """
        AKILLI JEOPOLİTİK AJAN — Keyword BULUR, FinBERT ANLAR.
        
        3 Katmanlı Akıllı Sistem:
        1. CRITICAL keywords → Otomatik tehlike (savaş, nükleer)
        2. HIGH keywords → FinBERT'e sor, negatifse say
        3. ELEVATED keywords → SADECE FinBERT negatif derse say
        
        Ek: Bullish keywords pozitif skor verir (barış, anlaşma)
        """
        all_text = " ".join(
            f"{a.get('title', '')} {a.get('summary', '')}" for a in articles
        ).lower()

        # === ADIM 1: Keyword'lerle haber eşleştirme ===
        critical_found = []
        high_found = []
        elevated_found = []
        bullish_found = []

        for keyword in GEOPOLITICAL_KEYWORDS.get("bearish_critical", []):
            if keyword in all_text:
                critical_found.append(keyword)

        for keyword in GEOPOLITICAL_KEYWORDS.get("bearish_high", []):
            if keyword in all_text:
                high_found.append(keyword)

        for keyword in GEOPOLITICAL_KEYWORDS.get("bearish_elevated", []):
            if keyword in all_text:
                elevated_found.append(keyword)

        for keyword in GEOPOLITICAL_KEYWORDS.get("bullish", []):
            if keyword in all_text:
                bullish_found.append(keyword)

        # Hiç keyword yoksa → NORMAL
        if not critical_found and not high_found and not elevated_found:
            self._last_geo_risk = "NORMAL"
            self._geo_risk_score = max(0, -len(bullish_found) * 5)
            return "NORMAL"

        # === ADIM 2: FinBERT ile haberlerin gerçek anlamını anla ===
        risk_score = 0
        confirmed_risks = []
        dismissed_risks = []

        # CRITICAL → sorgulanmaz, doğrudan tehlike
        for kw in critical_found:
            risk_score += 3
            confirmed_risks.append(f"🔴 CRITICAL: {kw}")

        # HIGH → FinBERT'e sor
        for kw in high_found:
            matching_articles = [
                a for a in articles
                if kw in f"{a.get('title', '')} {a.get('summary', '')}".lower()
            ]
            if matching_articles and self.finbert:
                # En alakalı haberin sentiment'ini ölç
                text = f"{matching_articles[0].get('title', '')} {matching_articles[0].get('summary', '')}"
                try:
                    result = self.finbert.analyze(text[:512])
                    if result["label"] == "negative":
                        risk_score += 2
                        confirmed_risks.append(f"🟠 HIGH (FinBERT:{result['label']}): {kw}")
                    else:
                        dismissed_risks.append(f"✅ HIGH dismissed ({result['label']}): {kw}")
                except Exception:
                    risk_score += 1  # FinBERT hata verirse yarım puan
                    confirmed_risks.append(f"🟡 HIGH (FinBERT N/A): {kw}")
            else:
                # FinBERT yoksa keyword'ü doğrudan say ama düşük puanla
                risk_score += 1
                confirmed_risks.append(f"🟡 HIGH (no FinBERT): {kw}")

        # ELEVATED → SADECE FinBERT negatif derse say
        for kw in elevated_found:
            matching_articles = [
                a for a in articles
                if kw in f"{a.get('title', '')} {a.get('summary', '')}".lower()
            ]
            if matching_articles and self.finbert:
                text = f"{matching_articles[0].get('title', '')} {matching_articles[0].get('summary', '')}"
                try:
                    result = self.finbert.analyze(text[:512])
                    if result["label"] == "negative" and result["score"] > 0.6:
                        risk_score += 1
                        confirmed_risks.append(
                            f"🟡 ELEVATED (FinBERT:{result['label']} {result['score']:.0%}): {kw}"
                        )
                    else:
                        dismissed_risks.append(
                            f"✅ ELEVATED dismissed ({result['label']} {result['score']:.0%}): {kw}"
                        )
                except Exception:
                    dismissed_risks.append(f"✅ ELEVATED dismissed (FinBERT err): {kw}")
            else:
                # FinBERT yoksa ELEVATED keyword'leri SAYMA
                dismissed_risks.append(f"✅ ELEVATED ignored (no FinBERT): {kw}")

        # Bullish keyword'ler skoru düşürür
        bullish_reduction = len(bullish_found) * 1
        risk_score = max(0, risk_score - bullish_reduction)

        # === ADIM 3: Risk seviyesini belirle ===
        if risk_score >= 8 or len(critical_found) >= 2:
            level = "CRITICAL"
            self._geo_risk_score = min(risk_score * 12, 100)
        elif risk_score >= 5:
            level = "HIGH"
            self._geo_risk_score = risk_score * 10
        elif risk_score >= 2:
            level = "ELEVATED"
            self._geo_risk_score = risk_score * 8
        elif bullish_found:
            level = "LOW"
            self._geo_risk_score = -len(bullish_found) * 5
        else:
            level = "NORMAL"
            self._geo_risk_score = 0

        self._last_geo_risk = level

        # === ADIM 4: Akıllı loglama ===
        if confirmed_risks or dismissed_risks:
            logger.info(
                f"  🧠 JEOPOLİTİK AJAN: {level} (skor:{self._geo_risk_score}) | "
                f"Onaylanan: {len(confirmed_risks)} | Reddedilen: {len(dismissed_risks)}"
            )
            for risk in confirmed_risks[:3]:
                logger.info(f"    {risk}")
            for dismiss in dismissed_risks[:3]:
                logger.info(f"    {dismiss}")

        # Breaking news dedektörü
        breaking_terms = [
            "ceasefire violat", "ceasefire collapse", "broke ceasefire",
            "resumed attack", "resumed fighting", "broke truce",
            "breaking:", "just in:", "developing:",
        ]
        for term in breaking_terms:
            if term in all_text:
                self._breaking_detected = True
                level = "CRITICAL"
                self._geo_risk_score = max(self._geo_risk_score, 80)
                logger.warning(
                    f"  🚨 BREAKING GEO EVENT: '{term}' tespit edildi! "
                    f"Risk: {self._geo_risk_score}"
                )
                break

        return level

    def get_market_sentiment(self) -> Dict:
        """
        Genel piyasa duyarlılığı — SPY/QQQ haberleri + Fear & Greed.
        """
        spy_news = self.analyze_stock_news("SPY")
        qqq_news = self.analyze_stock_news("QQQ")

        # CNN Fear & Greed Index (ücretsiz endpoint)
        fear_greed = self._get_fear_greed_index()

        combined_score = int(
            spy_news["news_score"] * 0.4 +
            qqq_news["news_score"] * 0.3 +
            fear_greed.get("score", 50) * 0.3 - 15  # normalize (0-100 → -15 ile +15)
        )

        return {
            "market_sentiment": combined_score,
            "spy_sentiment": spy_news["signal"],
            "qqq_sentiment": qqq_news["signal"],
            "fear_greed": fear_greed,
            "geopolitical_risk": spy_news.get("geopolitical_risk", "UNKNOWN"),
        }

    def _get_fear_greed_index(self) -> Dict:
        """CNN Fear & Greed Index."""
        cache_key = "fear_greed"
        if self._is_cached(cache_key):
            return self.cache[cache_key]

        try:
            url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
            headers = {"User-Agent": "Mozilla/5.0"}
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                score = data.get("fear_and_greed", {}).get("score", 50)
                rating = data.get("fear_and_greed", {}).get("rating", "Neutral")
                result = {"score": score, "rating": rating}
                self.cache[cache_key] = result
                self.last_fetch[cache_key] = datetime.now()
                return result
        except Exception as e:
            logger.debug(f"Fear & Greed hatası: {e}")
        return {"score": 50, "rating": "Neutral"}

    # ============================================================
    # CACHE
    # ============================================================

    def _is_cached(self, key: str) -> bool:
        if key not in self.cache or key not in self.last_fetch:
            return False
        elapsed = (datetime.now() - self.last_fetch[key]).total_seconds()
        # Breaking event varsa cache'i kısalt
        if self._breaking_detected:
            return elapsed < NEWS_CONFIG.get("breaking_cache_minutes", 1) * 60
        # Jeopolitik haberler daha sık yenilenir
        if "geo_" in key or "market" in key:
            return elapsed < NEWS_CONFIG.get("geo_cache_minutes", 2) * 60
        return elapsed < NEWS_CONFIG["cache_minutes"] * 60

    # ============================================================
    # GOOGLE NEWS RSS (ÜCRETSIZ, GERÇEK ZAMANLI)
    # ============================================================

    def _fetch_google_news(self, symbol: str) -> List[Dict]:
        """
        Google News RSS — ücretsiz, API key gerekmez, gerçek zamanlı.
        Rate limit yok, sadece HTML parse.
        """
        try:
            url = f"https://news.google.com/rss/search?q={symbol}+stock&hl=en-US&gl=US&ceid=US:en"
            headers = {"User-Agent": "Mozilla/5.0"}
            response = requests.get(url, headers=headers, timeout=10)

            if response.status_code != 200:
                return []

            # Basit XML parse (xml.etree)
            import xml.etree.ElementTree as ET
            root = ET.fromstring(response.content)
            articles = []

            for item in root.findall(".//item")[:8]:
                title = item.findtext("title", "")
                pub_date = item.findtext("pubDate", "")
                source = item.findtext("source", "Google News")

                # PubDate format: "Wed, 09 Apr 2026 18:30:00 GMT"
                published = ""
                try:
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(pub_date)
                    published = dt.strftime("%Y-%m-%dT%H:%M:%S")
                except Exception:
                    published = pub_date

                articles.append({
                    "title": title,
                    "summary": title,  # RSS'de summary yok, title kullan
                    "source": source if isinstance(source, str) else "Google News",
                    "published": published,
                    "sentiment_score": 0,
                    "api": "google_news",
                })

            return articles
        except Exception as e:
            logger.debug(f"Google News RSS hatası {symbol}: {e}")
            return []

    # ============================================================
    # GENEL JEOPOLİTİK TARAMA (sıfıra bağlı değil)
    # ============================================================

    def scan_geopolitical_breaking(self) -> Dict:
        """
        Genel dünya haberleri taraması — sembol bağımsız.
        Savaş, ateşkes ihlali, enerji krizi tespit eder.
        """
        cache_key = "geo_breaking_scan"
        if self._is_cached(cache_key):
            return self.cache[cache_key]

        articles = []

        # Genel piyasa/dünya haberleri
        for query in ["stock market", "geopolitical", "war", "oil price"]:
            try:
                url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
                headers = {"User-Agent": "Mozilla/5.0"}
                resp = requests.get(url, headers=headers, timeout=8)
                if resp.status_code == 200:
                    import xml.etree.ElementTree as ET
                    root = ET.fromstring(resp.content)
                    for item in root.findall(".//item")[:5]:
                        title = item.findtext("title", "")
                        articles.append({
                            "title": title,
                            "summary": title,
                            "source": "Google News",
                            "published": "",
                            "api": "google_news_geo",
                        })
            except Exception:
                pass

        geo_risk = self._check_geopolitical_risk(articles)

        result = {
            "geo_risk_level": geo_risk,
            "geo_risk_score": self._geo_risk_score,
            "breaking_detected": self._breaking_detected,
            "article_count": len(articles),
        }

        self.cache[cache_key] = result
        self.last_fetch[cache_key] = datetime.now()
        return result
