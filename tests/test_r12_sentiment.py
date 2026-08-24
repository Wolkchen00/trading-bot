"""R12 FinBERT işaret sözleşmesi ve SentAgent regresyon testleri."""

from dataclasses import dataclass

import pytest

from core.agent_coordinator import SentAgent
from core.news_analyzer import StockNewsAnalyzer


@dataclass
class FakeFinBERT:
    label: str
    score: float

    def analyze(self, _text):
        return {
            "label": self.label,
            "score": self.score,
            "confidence": abs(self.score),
            "source": "fake",
        }


def _news_analyzer(label="neutral", score=0.0):
    analyzer = StockNewsAnalyzer.__new__(StockNewsAnalyzer)
    analyzer.finbert = FakeFinBERT(label, score)
    analyzer.vader = None
    analyzer._breaking_detected = False
    analyzer._last_geo_risk = "NORMAL"
    analyzer._geo_risk_score = 0
    return analyzer


def _article_score(label, score):
    analyzer = _news_analyzer(label, score)
    analyzer._keyword_score = lambda _text: 0
    analyzer._get_time_weight = lambda _published: 1.0
    total, sentiments = analyzer._analyze_articles(
        [{"title": "Şirket açıklaması", "summary": "Yeni bilgi", "sentiment_score": 0}],
        "TEST",
    )
    assert total == sentiments[0]["score"]
    return total


@pytest.mark.parametrize(
    ("label", "score", "expected_nlp_score"),
    [
        ("negative", -0.88, -26.4),
        ("positive", 0.92, 27.6),
        ("neutral", 0.08, 0),
    ],
)
def test_signed_finbert_score_is_preserved_in_nlp_score(
    label, score, expected_nlp_score
):
    result = FakeFinBERT(label, score).analyze("haber")
    nlp_score = StockNewsAnalyzer._finbert_nlp_score(result)

    assert nlp_score == pytest.approx(expected_nlp_score)


def test_equal_positive_and_negative_scores_are_symmetric():
    positive = StockNewsAnalyzer._finbert_nlp_score(
        FakeFinBERT("positive", 0.88).analyze("iyi haber")
    )
    negative = StockNewsAnalyzer._finbert_nlp_score(
        FakeFinBERT("negative", -0.88).analyze("kötü haber")
    )

    assert positive == pytest.approx(-negative)
    assert abs(positive) == pytest.approx(abs(negative))


def test_strong_negative_elevated_news_triggers_elevated_risk():
    analyzer = _news_analyzer("negative", -0.88)
    articles = [{"title": "Sanctions and tariff dispute", "summary": ""}]

    assert analyzer._check_geopolitical_risk(articles) == "ELEVATED"


def test_weak_negative_elevated_news_does_not_trigger_elevated_risk():
    analyzer = _news_analyzer("negative", -0.60)
    articles = [{"title": "Sanctions and tariff dispute", "summary": ""}]

    assert analyzer._check_geopolitical_risk(articles) == "NORMAL"


def test_negative_news_reaches_sent_agent_with_negative_sign_and_cannot_buy():
    news_score = _article_score("negative", -0.88)

    assert news_score < 0
    vote = SentAgent().analyze(
        {
            "news_score": news_score,
            "sentiment_label": "BEARISH",
            "fear_greed_value": 50,
            "fear_greed_signal": "NEUTRAL",
        }
    )
    assert vote.signal != "BUY"
