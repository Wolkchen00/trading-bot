from __future__ import annotations

from datetime import date

from core.agent_coordinator import SocialAgent
from core.agent_stats import AgentStats, build_agent_data_ok
from stock_bot import StockBot
from tools.ajan_raporu import report_lines


DAY_ONE = date(2026, 8, 25)
DAY_TWO = date(2026, 8, 26)
AGENTS = (
    "TechAgent",
    "FundAgent",
    "SentAgent",
    "SocialAgent",
    "RiskAgent",
)
WEIGHTS = {
    "TechAgent": 0.25,
    "FundAgent": 0.20,
    "SentAgent": 0.20,
    "SocialAgent": 0.15,
    "RiskAgent": 0.20,
}


def _decision(
    *, confidence=19, weighted_score=9.4, final_signal="HOLD",
    signals=("BUY", "SELL", "HOLD", "HOLD", "HOLD"),
):
    votes = [
        {
            "agent": name,
            "signal": signal,
            "confidence": 10 + index * 10,
            "reasoning": "test",
        }
        for index, (name, signal) in enumerate(zip(AGENTS, signals))
    ]
    return {
        "signal": final_signal,
        "confidence": confidence,
        "weighted_score": weighted_score,
        "votes": votes,
        "majority": False,
        "risk_veto": False,
    }


def _all_data_ok(value=True):
    return {name: value for name in AGENTS}


def test_all_five_agent_votes_are_counted_including_hold(tmp_path):
    stats = AgentStats(
        path=str(tmp_path / "agent_stats.json"), today_fn=lambda: DAY_ONE
    )
    assert stats.record_decision(
        _decision(),
        data_ok=_all_data_ok(),
        dynamic_weights=WEIGHTS,
        min_confidence_score=50,
    )

    agents = stats.snapshot(DAY_ONE)["agents"]
    assert agents["TechAgent"]["votes"] == {"BUY": 1, "SELL": 0, "HOLD": 0}
    assert agents["FundAgent"]["votes"] == {"BUY": 0, "SELL": 1, "HOLD": 0}
    for name in ("SentAgent", "SocialAgent", "RiskAgent"):
        assert agents[name]["votes"] == {"BUY": 0, "SELL": 0, "HOLD": 1}
    assert agents["SocialAgent"]["last_dynamic_weight"] == 0.15


def test_social_data_ok_changes_without_changing_social_vote():
    agent = SocialAgent()
    no_data = {"social_score": 0, "reddit_posts": 0, "x_tweets": 0}
    reddit_data = {"social_score": 0, "reddit_posts": 3, "x_tweets": 0}

    vote_without_data = agent.analyze(dict(no_data))
    vote_with_data = agent.analyze(dict(reddit_data))
    flags_without = build_agent_data_ok({}, {}, no_data, {})
    flags_with = build_agent_data_ok({}, {}, reddit_data, {})

    assert flags_without["SocialAgent"] is False
    assert flags_with["SocialAgent"] is True
    assert vote_without_data.signal == vote_with_data.signal == "HOLD"
    assert vote_without_data.confidence == vote_with_data.confidence == 0


def test_fund_zero_score_and_explicit_missing_data_stays_data_not_ok():
    analysis = {
        "rsi": 50,
        "fundamental_score": 0,
        "fundamental_data_ok": False,
    }
    flags = build_agent_data_ok(analysis, {}, {}, {})
    assert flags["TechAgent"] is True
    assert flags["FundAgent"] is False


def test_sent_article_count_is_the_data_ok_source():
    missing = build_agent_data_ok({}, {"news_score": 0, "article_count": 0}, {}, {})
    present = build_agent_data_ok({}, {"news_score": 0, "article_count": 8}, {}, {})
    assert missing["SentAgent"] is False
    assert present["SentAgent"] is True


def test_coordinator_histograms_and_confidence_gate_count(tmp_path):
    stats = AgentStats(
        path=str(tmp_path / "agent_stats.json"), today_fn=lambda: DAY_ONE
    )
    stats.record_decision(
        _decision(confidence=19, weighted_score=9.4),
        data_ok=_all_data_ok(), dynamic_weights=WEIGHTS,
        min_confidence_score=50,
    )
    stats.record_decision(
        _decision(confidence=53, weighted_score=22.2, final_signal="BUY"),
        data_ok=_all_data_ok(), dynamic_weights=WEIGHTS,
        min_confidence_score=50,
    )

    coordinator = stats.snapshot(DAY_ONE)["coordinator"]
    assert coordinator["confidence_histogram"] == {"10-19": 1, "50-59": 1}
    assert coordinator["abs_weighted_score_histogram"] == {
        "0-9": 1,
        "20-29": 1,
    }
    assert coordinator["confidence_gte_threshold"] == 1
    assert coordinator["ws_gt_15"] == 1
    assert coordinator["ws_lt_neg15"] == 0
    assert coordinator["min_confidence_score"] == 50


def test_corrupt_and_old_schema_never_crash_and_can_continue(tmp_path):
    corrupt_path = tmp_path / "corrupt_agent_stats.json"
    corrupt_path.write_text("{bozuk-json", encoding="utf-8")
    corrupt = AgentStats(path=str(corrupt_path), today_fn=lambda: DAY_ONE)
    assert corrupt.days == {}
    assert corrupt.record_decision(
        _decision(), data_ok=_all_data_ok(), dynamic_weights=WEIGHTS,
        min_confidence_score=50,
    )
    assert corrupt.snapshot(DAY_ONE)["coordinator"]["decisions"] == 1

    old_path = tmp_path / "old_agent_stats.json"
    old_path.write_text(
        '{"schema_version":0,"days":{"2026-08-25":'
        '{"agents":{"TechAgent":{"votes":{"BUY":"2"}}}}}}',
        encoding="utf-8",
    )
    old = AgentStats(path=str(old_path), today_fn=lambda: DAY_ONE)
    assert old.snapshot(DAY_ONE)["agents"]["TechAgent"]["votes"]["BUY"] == 2
    assert old.snapshot(DAY_ONE)["coordinator"]["decisions"] == 0


def test_stats_exception_does_not_change_coordinator_decision_or_sent_vote_input():
    expected = _decision(confidence=53, weighted_score=22.2, final_signal="BUY")

    class Coordinator:
        WEIGHTS = dict(WEIGHTS)

        def __init__(self):
            self.sent_data = None

        def decide(self, _symbol, _tech, _fund, sent, _social, _risk):
            self.sent_data = dict(sent)
            return expected

    class BrokenStats:
        def record_decision(self, *args, **kwargs):
            raise RuntimeError("disk full")

    class AgentPerf:
        def get_dynamic_weights(self):
            return dict(WEIGHTS)

    bot = StockBot.__new__(StockBot)
    bot.fundamental_analyzer = type("Fund", (), {
        "analyze_fundamentals": lambda self, symbol: {
            "fundamental_score": 0, "metrics": {}
        }
    })()
    bot.news_analyzer = type("News", (), {
        "analyze_stock_news": lambda self, symbol: {
            "news_score": 0, "signal": "NEUTRAL", "article_count": 8
        }
    })()
    bot.social_analyzer = type("Social", (), {
        "analyze_social": lambda self, symbol: {
            "social_score": 0, "reddit_posts": 0, "x_tweets": 0
        }
    })()
    bot._build_risk_data = lambda analysis, config: {
        "daily_pnl_pct": 0,
        "open_positions": 0,
        "max_positions": 3,
    }
    bot.agent_perf = AgentPerf()
    bot.coordinator = Coordinator()
    bot.agent_stats = BrokenStats()
    analysis = {"rsi": 50}

    actual = bot._get_agent_decision(
        "AAPL", analysis, {"min_confidence_score": 50}
    )

    assert actual is expected
    assert actual["signal"] == "BUY"
    assert actual["confidence"] == 53
    assert bot.coordinator.sent_data["article_count"] == 8


def test_day_rollover_keeps_days_in_separate_buckets(tmp_path):
    current_day = [DAY_ONE]
    stats = AgentStats(
        path=str(tmp_path / "agent_stats.json"),
        today_fn=lambda: current_day[0],
    )
    stats.record_decision(
        _decision(confidence=19), data_ok=_all_data_ok(),
        dynamic_weights=WEIGHTS, min_confidence_score=50,
    )
    current_day[0] = DAY_TWO
    stats.record_decision(
        _decision(confidence=53), data_ok=_all_data_ok(False),
        dynamic_weights=WEIGHTS, min_confidence_score=50,
    )

    assert stats.snapshot(DAY_ONE)["coordinator"]["decisions"] == 1
    assert stats.snapshot(DAY_TWO)["coordinator"]["decisions"] == 1
    # R15: data_ok ikiliden ucluye cikti (sema surumu 2). "disabled", politika
    # geregi kapatilmis ajani "kaynak sustu"dan ayirir. TechAgent hicbir zaman
    # kapatilamadigi icin burada her zaman 0.
    assert stats.snapshot(DAY_ONE)["agents"]["TechAgent"]["data_ok"] == {
        "true": 1, "false": 0, "disabled": 0,
    }
    assert stats.snapshot(DAY_TWO)["agents"]["TechAgent"]["data_ok"] == {
        "true": 0, "false": 1, "disabled": 0,
    }


def test_agent_report_says_veri_yok_instead_of_inventing_data(tmp_path):
    stats = AgentStats(path=str(tmp_path / "missing.json"), today_fn=lambda: DAY_ONE)
    assert report_lines(stats.days) == ["veri_yok"]
