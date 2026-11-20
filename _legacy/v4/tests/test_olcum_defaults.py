from datetime import date, datetime, timedelta, timezone
import json
from types import SimpleNamespace

from tools import olcum_raporu as report


UTC = timezone.utc


def _print_report(
    trades, since, unknown_peaks, _phantom, _duplicates, _unmatched,
    partial, stop_rejections, state, broker_available, elapsed_days, today,
):
    profile = report.ProfileInfo(
        name="PAPER_AGGRESSIVE",
        is_live=False,
        config_hash="test-config",
        git_sha="test-git",
        config={"breakeven_trigger_pct": 0.025},
    )
    until = datetime.combine(today, datetime.max.time(), tzinfo=UTC)
    return report.print_report(
        trades, since, unknown_peaks, partial, stop_rejections, state,
        broker_available, elapsed_days, until,
        report.Reconciliation(report.Status.PASS), profile,
        report.ReturnValue(None, "test"), report.ReturnValue(None, "test"),
    )


def _telemetry_marker(ts="2026-08-09T18:03:27+00:00"):
    return {"ts": ts, "kind": "SYSTEM_START"}


def _trade(
    symbol="PLTR", entry_at=None, closed_at=None, peak=0.04, peak_at=None,
    pnl=10.0,
):
    entry_at = entry_at or datetime(2026, 8, 7, 14, 0, tzinfo=UTC)
    closed_at = closed_at or entry_at + timedelta(hours=2)
    return report.ClosedTrade(
        symbol=symbol,
        side="LONG",
        entry_price=100.0,
        entry_at=entry_at,
        closed_at=closed_at,
        qty=10.0,
        pnl=pnl,
        peak_pct=peak,
        peak_at=peak_at or closed_at - timedelta(hours=1),
        provenance="strategy",
    )


def _complete_state(tmp_path, telemetry=(), alarms=(), history=()):
    state_dir = tmp_path / "state_paper"
    state_dir.mkdir()
    (state_dir / "telemetry.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in telemetry), encoding="utf-8"
    )
    (state_dir / "alarms.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in alarms), encoding="utf-8"
    )
    (state_dir / "trade_history.json").write_text(
        json.dumps(list(history)), encoding="utf-8"
    )
    (state_dir / "fill_ledger.jsonl").write_text("", encoding="utf-8")
    return state_dir


def test_default_since_is_frozen_measurement_start():
    args = report.parse_args([])
    assert report.MEASUREMENT_START == "2026-08-25"
    assert args.since == report.MEASUREMENT_START
    assert report.parse_args(["--since", "2026-08-01"]).since == "2026-08-01"


def test_header_projection_and_tempo_warning(capsys):
    trade = _trade()
    state = report.AuthoritativeState(available=True, files=[SimpleNamespace()] * 3)
    partial = report.PartialMetric(hits=0, opportunities=1, legacy_misses=1)

    status = _print_report(
        [trade],
        datetime(2026, 7, 30, 4, 0, tzinfo=UTC),
        0, 0, 0, 0, partial, 0, state, True, 7, date(2026, 8, 9),
    )

    output = capsys.readouterr().out
    assert "Olcum donemi (UTC): 2026-07-30T04:00:00+00:00" in output
    assert "islem gunu=7/30, strategy n=1/20" in output
    assert "Tempo projeksiyonu: 30 islem gunu sonunda strategy n=4.3" in output
    assert "TEMPO UYARISI" in output
    assert status is report.Status.FAIL


def test_metric2_denominator_is_bot_telemetry_and_fill_id_must_match():
    entry = datetime(2026, 8, 10, 14, 0, tzinfo=UTC)
    trade = _trade(entry_at=entry, closed_at=entry + timedelta(hours=2))
    event_time = (entry + timedelta(hours=1)).isoformat()
    telemetry = [
        {
            "ts": event_time, "kind": "PARTIAL_THRESHOLD", "symbol": "PLTR",
            "side": "LONG", "entry_price": 100, "pnl_pct": 0.031,
            "threshold_pct": 0.03,
        },
        {
            "ts": event_time, "kind": "PARTIAL_STATE", "symbol": "PLTR",
            "side": "LONG", "entry_price": 100, "intent_status": "FILLED",
            "order_id": "bot-partial-1", "target_qty": 5, "filled_qty": 5,
        },
    ]
    fills = [
        report.Fill("PLTR", "SELL", 5, 103, entry + timedelta(hours=1), "manual-1"),
    ]
    missed = report.evaluate_partial_metric([trade], telemetry, fills, entry)
    assert (missed.hits, missed.opportunities, missed.passed) == (0, 1, False)

    fills.append(
        report.Fill(
            "PLTR", "SELL", 5, 103, entry + timedelta(hours=1), "bot-partial-1"
        )
    )
    matched = report.evaluate_partial_metric([trade], telemetry, fills, entry)
    assert (matched.hits, matched.opportunities, matched.passed) == (1, 1, True)


def test_evidence_before_first_telemetry_is_legacy_miss_and_fails(capsys):
    trade = _trade(
        closed_at=datetime(2026, 8, 7, 18, 10, tzinfo=UTC),
        peak=0.063,
        peak_at=datetime(2026, 8, 7, 17, 0, tzinfo=UTC),
    )
    telemetry = [_telemetry_marker()]
    partial = report.evaluate_partial_metric(
        [trade], telemetry, [], datetime(2026, 7, 30, 4, 0, tzinfo=UTC)
    )
    assert partial.opportunities == 1
    assert partial.hits == 0
    assert partial.legacy_misses == 1
    assert partial.passed is False

    state = report.AuthoritativeState(available=True, files=[SimpleNamespace()] * 3)
    _print_report(
        [trade], datetime(2026, 7, 30, 4, 0, tzinfo=UTC), 0, 0, 0, 0,
        partial, 0, state, True, 7, date(2026, 8, 9),
    )
    assert "legacy miss=1" in capsys.readouterr().out


def test_mandatory_post_telemetry_bar_without_event_is_integrity_fail(capsys):
    entry = datetime(2026, 8, 10, 14, 0, tzinfo=UTC)
    trade = _trade(
        entry_at=entry,
        closed_at=entry + timedelta(hours=2),
        peak_at=entry + timedelta(hours=1),
    )
    telemetry = [_telemetry_marker()]
    partial = report.evaluate_partial_metric([trade], telemetry, [], entry)
    assert partial.event_completeness_misses == 1
    assert partial.passed is False

    state = report.AuthoritativeState(available=True, files=[SimpleNamespace()] * 3)
    _print_report(
        [trade], entry, 0, 0, 0, 0, partial, 0, state, True, 1,
        date(2026, 8, 10),
    )
    output = capsys.readouterr().out
    assert "event-completeness FAIL=1" in output
    assert "[FAIL] Metrik-2 veri butunlugu" in output


def test_authoritative_state_missing_source_makes_metric4_unknown(tmp_path, capsys):
    state_dir = tmp_path / "state_paper"
    state_dir.mkdir()
    (state_dir / "alarms.jsonl").write_text("", encoding="utf-8")
    (state_dir / "trade_history.json").write_text("[]", encoding="utf-8")
    state = report.load_authoritative_state(
        state_dir,
        datetime(2026, 7, 30, 4, 0, tzinfo=UTC),
        datetime(2026, 8, 10, 4, 0, tzinfo=UTC),
    )
    assert state.available is False
    assert "telemetry.jsonl eksik" in state.problems

    _print_report(
        [], datetime(2026, 7, 30, 4, 0, tzinfo=UTC), 0, 0, 0, 0,
        report.PartialMetric(), 0, state, True, 7, date(2026, 8, 9),
    )
    output = capsys.readouterr().out
    assert "[UNKNOWN] 4 kayit/stop butunlugu" in output
    assert "GENEL: UNKNOWN" in output


def test_backfill_and_broker_rejected_stop_are_authoritative(tmp_path):
    state_dir = _complete_state(tmp_path, telemetry=[_telemetry_marker()])
    state = report.load_authoritative_state(
        state_dir,
        datetime(2026, 7, 30, 4, 0, tzinfo=UTC),
        datetime(2026, 8, 10, 4, 0, tzinfo=UTC),
    )
    assert state.available is True
    assert state.backfill_label == "backfill"
    counts = report.invariant_counts(state.alarms, state.telemetry, state.backfill)
    assert counts.protection_alarms == 1
    assert counts.stop_regressions == 1
    assert counts.unique_collisions == 5

    rejected = SimpleNamespace(
        id="reject-1", legs=[], status="rejected", type="stop_limit",
        updated_at=datetime(2026, 8, 7, 17, 0, tzinfo=UTC),
    )
    market_reject = SimpleNamespace(
        id="reject-2", legs=[], status="rejected", type="market",
        updated_at=datetime(2026, 8, 7, 17, 0, tzinfo=UTC),
    )
    assert report.count_broker_stop_rejections(
        [rejected, market_reject], datetime(2026, 7, 30, tzinfo=UTC)
    ) == 1


def test_unreadable_authoritative_state_is_unknown(tmp_path):
    state_dir = _complete_state(tmp_path, telemetry=[_telemetry_marker()])
    (state_dir / "alarms.jsonl").write_text("{broken\n", encoding="utf-8")
    state = report.load_authoritative_state(
        state_dir,
        datetime(2026, 7, 30, 4, 0, tzinfo=UTC),
        datetime(2026, 8, 10, 4, 0, tzinfo=UTC),
    )
    assert state.available is False
    assert any("alarms.jsonl:1 okunamadi" in item for item in state.problems)


def test_empty_telemetry_has_no_completeness_miss_and_metric4_is_unknown(
    tmp_path, capsys,
):
    state_dir = _complete_state(tmp_path)
    since = datetime(2026, 7, 30, 4, 0, tzinfo=UTC)
    state = report.load_authoritative_state(
        state_dir, since, datetime(2026, 8, 10, 20, 0, tzinfo=UTC)
    )
    assert state.available is False
    assert "telemetry.jsonl bos; kapsama dogrulanamadi" in state.problems

    entry = datetime(2026, 8, 10, 14, 0, tzinfo=UTC)
    trade = _trade(
        entry_at=entry,
        closed_at=entry + timedelta(hours=2),
        peak_at=entry + timedelta(hours=1),
    )
    partial = report.evaluate_partial_metric([trade], state.telemetry, [], since)
    assert partial.legacy_misses == 1
    assert partial.event_completeness_misses == 0

    _print_report(
        [trade], since, 0, 0, 0, 0, partial, 0, state, True, 8,
        date(2026, 8, 10),
    )
    assert "[UNKNOWN] 4 kayit/stop butunlugu" in capsys.readouterr().out


def test_main_clients_are_mocked_and_no_order_mutation_api_is_used(
    tmp_path, monkeypatch, capsys,
):
    state_dir = _complete_state(tmp_path, telemetry=[_telemetry_marker()])

    class Trading:
        def get_orders(self, _request):
            return []

        def get_calendar(self, _request):
            return [SimpleNamespace()] * 7

    class Data:
        def get_stock_bars(self, _request):
            return SimpleNamespace(data={"SPY": []})

    monkeypatch.setattr(report, "TradingClient", lambda *_args, **_kwargs: Trading())
    monkeypatch.setattr(
        report, "StockHistoricalDataClient", lambda *_args, **_kwargs: Data()
    )
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "test-secret")

    # Bu test metrik-4 davranisini sinar, epoch zamanlamasini DEGIL. Varsayilan
    # MEASUREMENT_START gelecekte oldugunda (v4.16.1 epoch gecisi) gelecek-epoch
    # korumasi devreye girer; o davranisin kendi testi test_r10_olcum.py'de.
    assert report.main([
        "--since", "2026-07-30",
        "--state-dir", str(state_dir), "--log-dir", str(tmp_path / "missing-logs")
    ]) == 2
    output = capsys.readouterr().out
    assert "Olcum donemi (UTC): 2026-07-30" in output
    assert "[PASS] 4 kayit/stop butunlugu" in output
    # Otoriter kaynak hiyerarsisi HER KOSUMDA basilir. "+ backfill" soneki
    # kosula baglidir: tools/olcum_backfill.json satirlari yalniz olcum
    # penceresine DUSUYORSA eklenir (yeni epoch'ta dusmuyorlar). Backfill
    # davranisinin kendi testi var: test_backfill_and_broker_rejected_stop_are_authoritative
    assert "kaynak=broker filled orders + R9 fill ledger + persistent state" in output


def test_broker_closed_orders_failure_makes_metric4_unknown(
    tmp_path, monkeypatch, capsys,
):
    state_dir = _complete_state(tmp_path)

    class Trading:
        def get_orders(self, _request):
            raise RuntimeError("closed orders unavailable")

        def get_calendar(self, _request):
            return [SimpleNamespace()] * 7

    monkeypatch.setattr(report, "TradingClient", lambda *_args, **_kwargs: Trading())
    monkeypatch.setattr(
        report, "StockHistoricalDataClient", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "test-secret")

    # Bu test metrik-4 davranisini sinar, epoch zamanlamasini DEGIL. Varsayilan
    # MEASUREMENT_START gelecekte oldugunda (v4.16.1 epoch gecisi) gelecek-epoch
    # korumasi devreye girer; o davranisin kendi testi test_r10_olcum.py'de.
    assert report.main([
        "--since", "2026-07-30",
        "--state-dir", str(state_dir), "--log-dir", str(tmp_path / "missing-logs")
    ]) == 2
    captured = capsys.readouterr()
    assert "broker closed-orders alinamadi" in captured.err
    assert "[UNKNOWN] 4 kayit/stop butunlugu" in captured.out
