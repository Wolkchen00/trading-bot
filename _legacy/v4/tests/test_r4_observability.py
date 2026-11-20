import json
from datetime import date

import pytest

import core.fill_ledger
from core.funnel import DailyFunnel


class StubNotifier:
    def __init__(self):
        self.critical_calls = []

    def notify_critical(self, kind, message):
        self.critical_calls.append((kind, message))
        return True


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("2026-07-10", "2026-07-10", 0),
        ("2026-07-10", "2026-07-13", 1),
        ("2026-07-06", "2026-07-13", 5),
    ],
)
def test_business_days_between(start, end, expected):
    assert DailyFunnel.business_days_between(start, end) == expected


@pytest.mark.parametrize(
    ("last_entry", "expected_calls"),
    [
        ("2026-07-08", 1),  # Thu, Fri, Mon: exactly N=3
        ("2026-07-09", 0),  # Fri, Mon: N-1=2
    ],
)
def test_no_trade_threshold_boundary(tmp_path, last_entry, expected_calls):
    notifier = StubNotifier()
    funnel = DailyFunnel(
        path=str(tmp_path / "funnel.json"),
        today_fn=lambda: date(2026, 7, 14),
    )
    funnel.last_entry_date = last_entry
    funnel.bump("signal_hold")

    alarmed = funnel.maybe_notify_no_trade(
        closed_day="2026-07-13",
        today="2026-07-14",
        threshold=3,
        is_paper=True,
        notifier=notifier,
        history_path=str(tmp_path / "trade_history.json"),
    )

    assert alarmed is bool(expected_calls)
    assert len(notifier.critical_calls) == expected_calls


def test_alarm_dedup_and_no_trade_kind_exactly_once(tmp_path):
    notifier = StubNotifier()
    funnel = DailyFunnel(
        path=str(tmp_path / "funnel.json"),
        today_fn=lambda: date(2026, 7, 14),
    )
    funnel.last_entry_date = "2026-07-08"
    funnel.days["2026-07-13"] = funnel._empty_day()
    funnel.days["2026-07-13"]["gate_block"] = 4
    funnel.days["2026-07-13"]["gate_block_reasons"] = {"LOSS_STREAK": 4}

    for _ in range(2):
        funnel.maybe_notify_no_trade(
            closed_day="2026-07-13",
            today="2026-07-14",
            threshold=3,
            is_paper=False,
            notifier=notifier,
            history_path=str(tmp_path / "trade_history.json"),
        )

    assert len(notifier.critical_calls) == 1
    kind, message = notifier.critical_calls[0]
    assert kind == "NO_TRADE"
    assert "CANLI" in message
    assert "3" in message
    assert "2026-07-08" in message
    assert "gate_block" in message
    assert "LOSS_STREAK" in message


def test_migration_uses_last_strategy_buy_fill(tmp_path, monkeypatch):
    history_path = tmp_path / "trade_history.json"
    monkeypatch.setattr(
        core.fill_ledger,
        "read_fills",
        lambda: [
            {
                "side": "BUY",
                "provenance": "strategy",
                "ts_utc": "2026-07-02T15:00:00+00:00",
            },
            {
                "side": "BUY",
                "provenance": "bear_etf",
                "ts_utc": "2026-07-08T15:30:00+00:00",
            },
        ],
    )
    notifier = StubNotifier()
    funnel = DailyFunnel(
        path=str(tmp_path / "funnel.json"),
        today_fn=lambda: date(2026, 7, 14),
    )

    assert funnel.maybe_notify_no_trade(
        closed_day="2026-07-13",
        today="2026-07-14",
        threshold=3,
        is_paper=True,
        notifier=notifier,
        history_path=str(history_path),
    )
    assert funnel.last_entry_date == "2026-07-02"
    assert len(notifier.critical_calls) == 1


def test_migration_ignores_short_and_non_strategy_entries(tmp_path, monkeypatch):
    history_path = tmp_path / "trade_history.json"
    monkeypatch.setattr(
        core.fill_ledger,
        "read_fills",
        lambda: [
            {
                "side": "BUY",
                "provenance": "strategy",
                "ts_utc": "2026-07-02T15:00:00+00:00",
            },
            {
                "side": "SELL",
                "provenance": "strategy",
                "ts_utc": "2026-07-08T15:00:00+00:00",
            },
            {
                "side": "BUY",
                "provenance": "short",
                "ts_utc": "2026-07-09T15:00:00+00:00",
            },
        ],
    )
    notifier = StubNotifier()
    funnel = DailyFunnel(
        path=str(tmp_path / "funnel.json"),
        today_fn=lambda: date(2026, 7, 14),
    )

    assert funnel.maybe_notify_no_trade(
        closed_day="2026-07-13",
        today="2026-07-14",
        threshold=3,
        is_paper=True,
        notifier=notifier,
        history_path=str(history_path),
    )
    assert funnel.last_entry_date == "2026-07-02"
    assert len(notifier.critical_calls) == 1


def test_migration_without_ledger_keeps_entry_unknown_and_does_not_alarm(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(core.fill_ledger, "read_fills", lambda: [])
    notifier = StubNotifier()
    funnel = DailyFunnel(
        path=str(tmp_path / "funnel.json"),
        today_fn=lambda: date(2026, 7, 14),
    )

    alarmed = funnel.maybe_notify_no_trade(
        closed_day="2026-07-13",
        today="2026-07-14",
        threshold=3,
        is_paper=True,
        notifier=notifier,
        history_path=str(tmp_path / "missing.json"),
    )

    assert alarmed is False
    assert funnel.last_entry_date is None
    assert notifier.critical_calls == []


def test_persistence_survives_restart_same_day(tmp_path):
    path = tmp_path / "funnel.json"
    today_fn = lambda: date(2026, 7, 30)
    first = DailyFunnel(path=str(path), today_fn=today_fn)
    first.bump("scanned")
    first.bump("scanned")
    first.bump("entries")
    first.save()

    restarted = DailyFunnel(path=str(path), today_fn=today_fn)
    snapshot = restarted.snapshot("2026-07-30")
    assert snapshot["scanned"] == 2
    assert snapshot["entries"] == 1
    assert restarted.last_entry_date == "2026-07-30"


def test_save_prunes_days_older_than_30_days(tmp_path):
    path = tmp_path / "funnel.json"
    path.write_text(
        json.dumps(
            {
                "days": {
                    "2026-06-29": {"scanned": 1},
                    "2026-06-30": {"scanned": 2},
                    "2026-07-30": {"scanned": 3},
                },
                "last_entry_date": None,
                "last_no_trade_alarm_date": None,
            }
        ),
        encoding="utf-8",
    )
    funnel = DailyFunnel(
        path=str(path), today_fn=lambda: date(2026, 7, 30)
    )
    assert funnel.save()

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "2026-06-29" not in saved["days"]
    assert "2026-06-30" in saved["days"]
    assert "2026-07-30" in saved["days"]


def test_gate_block_reasons_accumulate_per_reason(tmp_path):
    funnel = DailyFunnel(
        path=str(tmp_path / "funnel.json"),
        today_fn=lambda: date(2026, 7, 30),
    )
    funnel.bump("gate_block", reason="LOSS_STREAK")
    funnel.bump("gate_block", reason="LOSS_STREAK")
    funnel.bump("gate_block", reason="EMA200")

    snapshot = funnel.snapshot("2026-07-30")
    assert snapshot["gate_block"] == 3
    assert snapshot["gate_block_reasons"] == {
        "LOSS_STREAK": 2,
        "EMA200": 1,
    }


def test_disabled_funnel_has_no_file_report_or_alarm(tmp_path):
    path = tmp_path / "funnel.json"
    notifier = StubNotifier()
    funnel = DailyFunnel(
        enabled=False,
        path=str(path),
        today_fn=lambda: date(2026, 7, 30),
    )

    funnel.bump("entries")
    assert funnel.save() is False
    assert funnel.snapshot("2026-07-30") == {}
    assert funnel.report_lines("2026-07-30") == []
    assert funnel.maybe_notify_no_trade(
        closed_day="2026-07-29",
        today="2026-07-30",
        threshold=3,
        is_paper=True,
        notifier=notifier,
        history_path=str(tmp_path / "trade_history.json"),
    ) is False
    assert not path.exists()
    assert notifier.critical_calls == []
