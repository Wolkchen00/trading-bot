import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from v5.clock import SessionCalendar, clock_skew_seconds, parse_calendar, window_for


FIXTURES = Path(__file__).parent / "fixtures" / "alpaca"


def test_parse_calendar_previous_cover_and_round_trip():
    rows = json.loads((FIXTURES / "calendar_window.json").read_text(encoding="utf-8"))["body"]
    rows += [{"date": "2026-11-27", "open": "09:30", "close": "13:00"}, rows[-1]]
    sessions = parse_calendar(rows)
    assert sessions == sorted(sessions, key=lambda item: item.date)
    assert len({item.date for item in sessions}) == len(sessions)
    calendar = SessionCalendar(sessions)
    assert calendar.get(date(2026, 11, 27)).close.hour == 18
    assert calendar.previous(date(2026, 11, 28), 2) == [date(2026, 11, 25), date(2026, 11, 27)]
    assert calendar.covers(date(2026, 11, 22))
    restored = SessionCalendar.from_json(calendar.to_json())
    assert restored.to_json() == calendar.to_json()


def test_windows_normal_early_close_and_dst_offsets():
    normal, early = parse_calendar([
        {"date": "2026-10-30", "open": "09:30", "close": "16:00"},
        {"date": "2026-11-27", "open": "09:30", "close": "13:00"},
    ])
    assert normal.close.hour == 20
    assert early.close.hour == 18
    normal_window = window_for(normal)
    early_window = window_for(early)
    assert (normal.close - normal_window.decide_from).total_seconds() == 600
    assert (early.close - early_window.cancel_at).total_seconds() == 90
    assert (early_window.reconcile_until - early.close).total_seconds() == 300


def test_clock_skew_parses_nine_fractional_digits():
    item = json.loads((FIXTURES / "clock.json").read_text(encoding="utf-8"))["body"]
    local = datetime(2026, 11, 16, 16, 10, 40, 446130, tzinfo=timezone.utc)
    assert clock_skew_seconds(item["timestamp"], local) == pytest.approx(1.0)


@pytest.mark.parametrize("row", [{}, {"date": "bad", "open": "09:30", "close": "16:00"}, {"date": "2026-01-01", "open": "bad", "close": "16:00"}])
def test_malformed_calendar_rows_raise(row):
    with pytest.raises(ValueError):
        parse_calendar([row])
