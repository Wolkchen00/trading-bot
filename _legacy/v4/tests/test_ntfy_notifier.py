from __future__ import annotations

from datetime import datetime, timedelta
import json

import core.ntfy_notifier as ntfy_module
from core.notifier import CRITICAL_EVENT_INVENTORY, TelegramNotifier
from core.ntfy_notifier import CriticalAlarmPublisher, PublishResult
from stock_bot import StockBot


class _Response:
    def __init__(self, status_code):
        self.status_code = status_code


def _alarm_path(monkeypatch, tmp_path):
    import config

    path = tmp_path / "alarms.jsonl"
    monkeypatch.setattr(config, "state_path", lambda _name: str(path))
    return path


def _fake_http(monkeypatch, statuses):
    calls = []
    remaining = list(statuses)

    def post(url, **kwargs):
        calls.append((url, kwargs))
        if not remaining:
            raise AssertionError("unexpected HTTP call")
        status = remaining.pop(0)
        if isinstance(status, Exception):
            raise status
        return _Response(status)

    monkeypatch.setattr(ntfy_module.requests, "post", post)
    return calls


def _records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_ntfy_post_produced_and_delivery_marker_written(tmp_path, monkeypatch):
    alarm_path = _alarm_path(monkeypatch, tmp_path)
    monkeypatch.setenv("NTFY_TOPIC", "fake-test-topic")
    calls = _fake_http(monkeypatch, [204])
    publisher = CriticalAlarmPublisher()

    result = publisher.publish(
        "KORUMA", "PLTR naked", symbol="PLTR", state_code="naked"
    )

    assert result.persisted is True
    assert result.direct_delivered is True
    assert result.ntfy_delivered is True
    assert result.delivery_marker_written is True
    assert len(calls) == 1
    assert calls[0][0] == "https://ntfy.sh/fake-test-topic"
    assert calls[0][1]["timeout"] == 5
    records = _records(alarm_path)
    assert records[0]["kind"] == "KORUMA"
    assert records[0]["message"] == "PLTR naked"
    assert records[0]["id"]
    assert records[1] == {"kind": "DELIVERY", "ref": records[0]["id"]}


def test_post_failure_keeps_alarm_warns_bridge_and_logs_no_error(
    tmp_path, monkeypatch
):
    alarm_path = _alarm_path(monkeypatch, tmp_path)
    monkeypatch.setenv("NTFY_TOPIC", "fake-test-topic")
    calls = _fake_http(monkeypatch, [500, 503])
    errors = []
    warnings = []
    monkeypatch.setattr(ntfy_module.logger, "error", errors.append)
    monkeypatch.setattr(ntfy_module.logger, "warning", warnings.append)
    publisher = CriticalAlarmPublisher()

    result = publisher.publish(
        "KORUMA", "PLTR naked", symbol="PLTR", state_code="naked"
    )

    assert result.persisted is True
    assert result.direct_delivered is False
    assert len(calls) == 2
    assert errors == []
    assert len(warnings) == 1
    assert "VPS bridge backstop" in warnings[0]
    assert [record["kind"] for record in _records(alarm_path)] == ["KORUMA"]


def test_fail_then_success_retry_and_cooldown_starts_only_after_success(
    tmp_path, monkeypatch
):
    _alarm_path(monkeypatch, tmp_path)
    monkeypatch.setenv("NTFY_TOPIC", "fake-test-topic")
    calls = _fake_http(monkeypatch, [500, 503, 500, 200])
    publisher = CriticalAlarmPublisher()

    failed = publisher.publish(
        "KORUMA", "PLTR naked", symbol="PLTR", state_code="naked"
    )
    retried = publisher.publish(
        "KORUMA", "PLTR naked", symbol="PLTR", state_code="naked"
    )
    suppressed = publisher.publish(
        "KORUMA", "PLTR naked", symbol="PLTR", state_code="naked"
    )

    assert failed.direct_delivered is False
    assert retried.direct_delivered is True
    assert retried.ntfy_delivered is True
    assert suppressed.cooldown_suppressed is True
    assert suppressed.direct_delivered is False
    assert len(calls) == 4


def test_cooldown_keys_kind_symbol_and_state_code(tmp_path, monkeypatch):
    _alarm_path(monkeypatch, tmp_path)
    monkeypatch.setenv("NTFY_TOPIC", "fake-test-topic")
    calls = _fake_http(monkeypatch, [200, 200, 200])
    publisher = CriticalAlarmPublisher()

    first = publisher.publish(
        "KORUMA", "PLTR naked", symbol="PLTR", state_code="naked"
    )
    same = publisher.publish(
        "KORUMA", "PLTR naked again", symbol="PLTR", state_code="naked"
    )
    other_symbol = publisher.publish(
        "KORUMA", "AMZN naked", symbol="AMZN", state_code="naked"
    )
    other_state = publisher.publish(
        "KORUMA", "PLTR drift", symbol="PLTR", state_code="drift"
    )

    assert first.ntfy_delivered is True
    assert same.cooldown_suppressed is True
    assert other_symbol.ntfy_delivered is True
    assert other_state.ntfy_delivered is True
    assert len(calls) == 3


def test_no_trade_cooldown_is_at_most_once_per_day(tmp_path, monkeypatch):
    _alarm_path(monkeypatch, tmp_path)
    monkeypatch.setenv("NTFY_TOPIC", "fake-test-topic")
    calls = _fake_http(monkeypatch, [200, 200])
    clock = [datetime(2026, 8, 9, 12, 0, 0)]
    publisher = CriticalAlarmPublisher(now_fn=lambda: clock[0])

    first = publisher.publish(
        "NO_TRADE", "No entry", symbol="ACCOUNT", state_code="inactive"
    )
    clock[0] += timedelta(hours=23)
    next_day = publisher.publish(
        "NO_TRADE", "Still no entry", symbol="ACCOUNT", state_code="inactive"
    )
    same_day = publisher.publish(
        "NO_TRADE", "Still no entry", symbol="ACCOUNT", state_code="inactive"
    )

    assert first.ntfy_delivered is True
    assert next_day.ntfy_delivered is True
    assert same_day.cooldown_suppressed is True
    assert len(calls) == 2


def test_at_least_once_contract_crash_between_post_and_delivery_marker_may_duplicate_never_drop(
    tmp_path, monkeypatch
):
    alarm_path = _alarm_path(monkeypatch, tmp_path)
    monkeypatch.setenv("NTFY_TOPIC", "fake-test-topic")
    _fake_http(monkeypatch, [200])
    publisher = CriticalAlarmPublisher()
    append_record = publisher._append_record

    def crash_window(record):
        if record.get("kind") == "DELIVERY":
            return False
        return append_record(record)

    monkeypatch.setattr(publisher, "_append_record", crash_window)
    result = publisher.publish(
        "KORUMA", "PLTR naked", symbol="PLTR", state_code="naked"
    )

    assert result.persisted is True
    assert result.direct_delivered is True
    assert result.delivery_marker_written is False
    records = _records(alarm_path)
    assert len(records) == 1
    assert records[0]["kind"] == "KORUMA"
    assert records[0]["id"] == result.alarm_id


class _FakePublisher:
    def __init__(self):
        self.calls = []

    def publish(self, kind, message, **kwargs):
        self.calls.append((kind, message, kwargs))
        return PublishResult(
            alarm_id=f"id-{len(self.calls)}",
            persisted=True,
            direct_delivered=True,
        )


def test_critical_event_inventory_each_path_calls_common_publisher(monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert CRITICAL_EVENT_INVENTORY == (
        "notify_critical",
        "notify_kill_switch",
        "stock_bot.main_loop_consecutive_error",
    )

    notifier = TelegramNotifier()
    publisher = _FakePublisher()
    notifier.critical_publisher = publisher

    assert notifier.notify_critical(
        "KORUMA", "PLTR naked", symbol="PLTR", state_code="naked"
    ) is True
    assert notifier.notify_kill_switch("API failures", 12345.0) is True

    bot = object.__new__(StockBot)
    bot.notifier = notifier
    assert bot._notify_main_loop_consecutive_error(3, RuntimeError("boom")) is True

    assert [call[0] for call in publisher.calls] == [
        "KORUMA",
        "KILL_SWITCH",
        "MAIN_LOOP_ERROR",
    ]
