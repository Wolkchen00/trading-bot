"""R4'e KARSI testler (Claude). Codex suite'inin sinir bosluklarini kapatir.

1. _FunnelTradeRows: BUY bump'lamaz; SELL/COVER bump'lar; bozuk funnel veya
   dict olmayan satir trade akisini ASLA kirmaz (satir yine listeye girer).
2. maybe_notify_no_trade: bozuk last_entry_date crash etmez; notifier
   patlarsa bile istisna sizmaz; hafta sonu devri is gunu saymaz.
3. business_days_between ters aralikta 0 doner.
4. dominant_stage esitlikte oncelik sirasini uygular (gate_block > signal_hold).
5. Kalici yazim: 60s gazi ara bump'i diske yazmaz, entries zorlar.
"""
from __future__ import annotations

import json
from datetime import date

from core.funnel import DailyFunnel
from stock_bot import _FunnelTradeRows


class _BoomFunnel:
    def bump(self, stage, reason=None):
        raise RuntimeError("telemetri patladi")


class _CountFunnel:
    def __init__(self):
        self.calls = []

    def bump(self, stage, reason=None):
        self.calls.append(stage)


class _RaisingNotifier:
    def notify_critical(self, kind, message):
        raise RuntimeError("kanal koptu")


def test_trade_rows_buy_not_counted_sell_cover_counted():
    funnel = _CountFunnel()
    rows = _FunnelTradeRows(funnel)
    rows.append({"action": "BUY", "symbol": "AAPL"})
    rows.append({"action": "SHORT", "symbol": "TSLA"})
    rows.append({"action": "SELL", "symbol": "AAPL"})
    rows.append({"action": "COVER", "symbol": "TSLA"})
    assert funnel.calls == ["exits", "exits"]
    assert len(rows) == 4  # her satir listede


def test_trade_rows_survive_broken_funnel_and_non_dict():
    rows = _FunnelTradeRows(_BoomFunnel())
    rows.append({"action": "SELL", "symbol": "AAPL"})  # bump patlar, append yasar
    rows.append("dict degil")
    rows.append(None)
    assert len(rows) == 3


def test_trade_rows_fresh_instance_after_daily_reset_still_counts():
    """_daily_reset yeni _FunnelTradeRows kurar; yeni ornek de saymali."""
    funnel = _CountFunnel()
    rows = _FunnelTradeRows(funnel)
    rows.append({"action": "SELL"})
    rows = _FunnelTradeRows(funnel)  # gun devri simulasyonu
    rows.append({"action": "COVER"})
    assert funnel.calls == ["exits", "exits"]


def test_no_trade_with_corrupt_last_entry_date_does_not_crash(tmp_path):
    funnel = DailyFunnel(
        path=str(tmp_path / "funnel.json"),
        today_fn=lambda: date(2026, 7, 30),
    )
    funnel.last_entry_date = "bozuk-tarih"
    notifier = _CountFunnel()  # notify_critical yok - cagrilirsa AttributeError
    assert funnel.maybe_notify_no_trade(
        closed_day="2026-07-29", today="2026-07-30", threshold=1,
        is_paper=True, notifier=notifier,
        history_path=str(tmp_path / "yok.json"),
    ) is False


def test_no_trade_raising_notifier_does_not_leak_and_dedupe_holds(tmp_path):
    """Kanal patlasa bile istisna sizmaz; dedup isareti yazildigi icin ayni gun
    ikinci deneme alarm uretmez (dayanikli kuyruk alarms.jsonl'de zaten var)."""
    funnel = DailyFunnel(
        path=str(tmp_path / "funnel.json"),
        today_fn=lambda: date(2026, 7, 30),
    )
    funnel.last_entry_date = "2026-07-20"
    assert funnel.maybe_notify_no_trade(
        closed_day="2026-07-29", today="2026-07-30", threshold=3,
        is_paper=True, notifier=_RaisingNotifier(),
        history_path=str(tmp_path / "yok.json"),
    ) is True
    assert funnel.last_no_trade_alarm_date == "2026-07-30"
    saved = json.loads((tmp_path / "funnel.json").read_text(encoding="utf-8"))
    assert saved["last_no_trade_alarm_date"] == "2026-07-30"


def test_weekend_rollover_does_not_count_business_days(tmp_path):
    """Giris Cuma 07-24; Pazar devrinde 0 is gunu (alarm yok), Pazartesi 1."""
    assert DailyFunnel.business_days_between("2026-07-24", "2026-07-26") == 0
    assert DailyFunnel.business_days_between("2026-07-24", "2026-07-27") == 1
    funnel = DailyFunnel(
        path=str(tmp_path / "funnel.json"),
        today_fn=lambda: date(2026, 7, 26),
    )
    funnel.last_entry_date = "2026-07-24"
    stub = _CountFunnel()  # notify_critical yok - cagrilirsa patlar
    assert funnel.maybe_notify_no_trade(
        closed_day="2026-07-26", today="2026-07-26", threshold=1,
        is_paper=True, notifier=stub,
        history_path=str(tmp_path / "yok.json"),
    ) is False


def test_business_days_reversed_range_is_zero():
    assert DailyFunnel.business_days_between("2026-07-27", "2026-07-24") == 0
    assert DailyFunnel.business_days_between("2026-07-27", "2026-07-27") == 0


def test_dominant_stage_priority_breaks_ties(tmp_path):
    """gate_block ile signal_hold ESIT sayida -> oncelik gate_block'ta.
    (max ilk buyugu dondurur; oncelik listesi bilincli olarak gate_block'u
    one koyar - esitlikte 'kapi blokladi' aciklamasi HOLD'dan degerlidir.)"""
    funnel = DailyFunnel(
        path=str(tmp_path / "funnel.json"),
        today_fn=lambda: date(2026, 7, 30),
    )
    for _ in range(3):
        funnel.bump("gate_block", reason="LOSS_STREAK")
        funnel.bump("signal_hold")
    stage, count = funnel.dominant_stage("2026-07-30")
    assert (stage, count) == ("gate_block", 3)


def test_persist_throttle_skips_midday_write_but_entries_forces(tmp_path):
    path = tmp_path / "funnel.json"
    funnel = DailyFunnel(
        path=str(path), today_fn=lambda: date(2026, 7, 30)
    )
    funnel.bump("scanned")  # ilk yazim: gaz devrede degil
    first = json.loads(path.read_text(encoding="utf-8"))
    assert first["days"]["2026-07-30"]["scanned"] == 1
    funnel.bump("scanned")  # 60s dolmadi -> diske yazilMAmali
    mid = json.loads(path.read_text(encoding="utf-8"))
    assert mid["days"]["2026-07-30"]["scanned"] == 1
    funnel.bump("entries")  # zorunlu yazim -> hepsi diske iner
    forced = json.loads(path.read_text(encoding="utf-8"))
    assert forced["days"]["2026-07-30"]["scanned"] == 2
    assert forced["days"]["2026-07-30"]["entries"] == 1
    assert forced["last_entry_date"] == "2026-07-30"
