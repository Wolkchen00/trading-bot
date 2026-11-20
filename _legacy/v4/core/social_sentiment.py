"""
Social Sentiment Analyzer — Hisse Senedi Sosyal Medya Duygu Analizi
Reddit, X (Twitter), Google Trends analizi.

Kaynaklar:
  1. Reddit (r/stocks, r/wallstreetbets, r/investing, r/options)
  2. X (Twitter) — ntscraper ile ücretsiz $TICKER arama
  3. Google Trends — arama hacmi tespiti
  4. VADER NLP duygu analizi
"""
import os
import time
import json
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from config import STOCK_SEARCH_TERMS
from utils.logger import logger

# VADER sentiment
try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    VADER_AVAILABLE = True
except ImportError:
    VADER_AVAILABLE = False

# X (Twitter) scraper
try:
    from ntscraper import Nitter
    X_AVAILABLE = True
except ImportError:
    X_AVAILABLE = False


SOCIAL_CONFIG = {
    # Reddit — hisse senedi sub'ları
    "reddit_subs": [
        "stocks", "wallstreetbets", "investing",
        "options", "StockMarket", "Daytrading",
    ],
    "reddit_search_url": "https://www.reddit.com/r/{sub}/search.json",
    "reddit_hot_url": "https://www.reddit.com/r/{sub}/hot.json",

    # Cache
    "cache_minutes": 15,

    # Min post sayısı (güvenilirlik için)
    "min_posts_for_signal": 3,
}


class SocialSentimentAnalyzer:
    """Hisse senedi sosyal medya duygu analizi."""

    def __init__(self):
        self.vader = SentimentIntensityAnalyzer() if VADER_AVAILABLE else None
        self.nitter = None
        self.cache = {}
        self.last_fetch = {}

        if X_AVAILABLE:
            try:
                self.nitter = Nitter()
            except Exception as e:
                logger.debug(f"Nitter init hatasi (X devre disi): {e}")
                self.nitter = None

        logger.info(
            f"SocialSentiment baslatildi — "
            f"Reddit: aktif | X: {'aktif' if self.nitter else 'devre disi'} | "
            f"VADER: {'aktif' if self.vader else 'devre disi'}"
        )

    # ============================================================
    # ANA ANALİZ
    # ============================================================

    def analyze_social(self, symbol: str) -> Dict:
        """
        Hisse için sosyal medya duygu analizi.

        Returns:
            {
                'social_score': int (-50 ile +50),
                'signal': 'BULLISH' | 'BEARISH' | 'NEUTRAL',
                'reddit_posts': int,
                'x_tweets': int,
                'mentions_trend': 'UP' | 'DOWN' | 'STABLE',
                'wsb_hype': bool,
            }
        """
        cache_key = f"social_{symbol}"
        if self._is_cached(cache_key):
            return self.cache[cache_key]

        ticker = symbol.replace("/USD", "").replace("USD", "")
        search_terms = STOCK_SEARCH_TERMS.get(ticker, [ticker.lower()])

        # Reddit analizi
        reddit_data = self._analyze_reddit(ticker, search_terms)

        # X/Twitter analizi
        x_data = self._analyze_x(ticker)

        # Birleşik skor
        total_score = int(
            reddit_data.get("score", 0) * 0.6 +
            x_data.get("score", 0) * 0.4
        )

        if total_score >= 15:
            signal = "BULLISH"
        elif total_score <= -15:
            signal = "BEARISH"
        else:
            signal = "NEUTRAL"

        result = {
            "social_score": total_score,
            "signal": signal,
            "reddit_posts": reddit_data.get("post_count", 0),
            "reddit_score": reddit_data.get("score", 0),
            "x_tweets": x_data.get("tweet_count", 0),
            "x_score": x_data.get("score", 0),
            "wsb_hype": reddit_data.get("wsb_hype", False),
            "mentions_trend": reddit_data.get("trend", "STABLE"),
        }

        self.cache[cache_key] = result
        self.last_fetch[cache_key] = datetime.now()

        logger.info(
            f"  Sosyal {ticker}: Reddit({reddit_data.get('post_count', 0)} post, "
            f"skor:{reddit_data.get('score', 0)}) | "
            f"X({x_data.get('tweet_count', 0)} tweet, skor:{x_data.get('score', 0)}) | "
            f"WSB:{'EVET' if result['wsb_hype'] else 'hayır'} -> {signal}"
        )

        return result

    # ============================================================
    # REDDIT ANALİZİ
    # ============================================================

    def _analyze_reddit(self, ticker: str, search_terms: List[str]) -> Dict:
        """Reddit'te hisse hakkında post ara ve analiz et."""
        total_score = 0
        post_count = 0
        wsb_hype = False

        for sub in SOCIAL_CONFIG["reddit_subs"]:
            for term in search_terms[:2]:  # Rate limit koruması
                try:
                    url = SOCIAL_CONFIG["reddit_search_url"].format(sub=sub)
                    params = {
                        "q": term,
                        "sort": "new",
                        "limit": 5,
                        "t": "day",
                        "restrict_sr": "on",
                    }
                    headers = {"User-Agent": "StockBot/1.0"}
                    response = requests.get(url, params=params, headers=headers, timeout=10)

                    if response.status_code == 200:
                        data = response.json()
                        posts = data.get("data", {}).get("children", [])
                        for post in posts:
                            p = post.get("data", {})
                            title = p.get("title", "")
                            selftext = p.get("selftext", "")[:200]
                            ups = p.get("ups", 0)
                            text = f"{title} {selftext}"

                            # Sentiment analizi
                            if self.vader:
                                scores = self.vader.polarity_scores(text)
                                compound = scores["compound"]
                                # Upvote ağırlığı
                                weight = min(ups / 100, 3.0) if ups > 10 else 1.0
                                total_score += compound * 15 * weight
                                post_count += 1

                            # WSB hype kontrolü
                            if sub == "wallstreetbets" and ups > 100:
                                wsb_hype = True

                    time.sleep(1)  # Reddit rate limit

                except Exception as e:
                    logger.debug(f"Reddit {sub}/{term} hatasi: {e}")

        return {
            "score": max(min(int(total_score), 50), -50),
            "post_count": post_count,
            "wsb_hype": wsb_hype,
            "trend": "UP" if total_score > 10 else ("DOWN" if total_score < -10 else "STABLE"),
        }

    # ============================================================
    # X (TWITTER) ANALİZİ
    # ============================================================

    def _analyze_x(self, ticker: str) -> Dict:
        """X/Twitter'da $TICKER araması."""
        if not self.nitter:
            return {"score": 0, "tweet_count": 0}

        try:
            # Cashtag ile ara ($AAPL, $TSLA vs.)
            tweets = self.nitter.get_tweets(f"${ticker}", mode="term", number=5)

            if not tweets or "tweets" not in tweets:
                return {"score": 0, "tweet_count": 0}

            total_score = 0
            count = 0
            for tweet in tweets.get("tweets", [])[:5]:
                text = tweet.get("text", "")
                if self.vader and text:
                    scores = self.vader.polarity_scores(text)
                    total_score += scores["compound"] * 15
                    count += 1

            return {
                "score": max(min(int(total_score), 50), -50),
                "tweet_count": count,
            }

        except Exception as e:
            # ntscraper sık sık "Cannot choose from an empty sequence" hatası verir
            # Bu, Nitter instance listesinin boş olmasından kaynaklanır — beklenen davranış
            if "empty sequence" not in str(e).lower():
                logger.debug(f"X/Twitter {ticker} hatası: {e}")
            return {"score": 0, "tweet_count": 0}

    # ============================================================
    # CACHE
    # ============================================================

    def _is_cached(self, key: str) -> bool:
        if key not in self.cache or key not in self.last_fetch:
            return False
        elapsed = (datetime.now() - self.last_fetch[key]).total_seconds()
        return elapsed < SOCIAL_CONFIG["cache_minutes"] * 60
