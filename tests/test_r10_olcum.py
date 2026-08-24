from datetime import datetime, timedelta, timezone
import subprocess
import sys
from types import SimpleNamespace

from tools import olcum_raporu as report


UTC = timezone.utc
SINCE = datetime(2026, 7, 30, tzinfo=UTC)
UNTIL = datetime(2026, 9, 30, 23, 59, tzinfo=UTC)


def _order(
    order_id="o-1", symbol="AMD", side="buy", qty=5,
    status="filled", order_type="market",
):
    return SimpleNamespace(
        id=order_id,
        symbol=symbol,
        side=side,
        filled_qty=str(qty),
        filled_avg_price="100",
        filled_at=SINCE + timedelta(days=1),
        updated_at=SINCE + timedelta(days=1),
        status=status,
        type=order_type,
        legs=[],
    )


def _ledger(
    order_id="o-1", execution_id="e-1", symbol="AMD", side="BUY", qty=5,
    provenance="strategy",
):
    return {
        "ts_utc": (SINCE + timedelta(days=1)).isoformat(),
        "order_id": order_id,
        "execution_id": execution_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "provenance": provenance,
    }


def _trade(
    index=0, *, symbol="AMD", pnl=10.0, peak=0.03,
    provenance="strategy",
):
    entry = SINCE + timedelta(days=index + 1)
    return report.ClosedTrade(
        symbol=symbol,
        side="LONG",
        entry_price=100,
        entry_at=entry,
        closed_at=entry + timedelta(hours=1),
        qty=1,
        pnl=pnl,
        peak_pct=peak,
        peak_at=entry + timedelta(minutes=30),
        provenance=provenance,
    )


def _profile(is_live=False):
    return report.ProfileInfo(
        name="LIVE" if is_live else "PAPER_AGGRESSIVE",
        is_live=is_live,
        config_hash="cfg-hash",
        git_sha="git-sha",
        config={"breakeven_trigger_pct": 0.025},
    )


def _print_status(capsys, trade_count, elapsed_days, *, profile=None):
    trades = [_trade(index) for index in range(trade_count)]
    status = report.print_report(
        trades=trades,
        since=SINCE,
        unknown_peaks=0,
        partial=report.PartialMetric(hits=3, opportunities=3),
        stop_rejections=0,
        state=report.AuthoritativeState(available=True),
        broker_available=True,
        elapsed_days=elapsed_days,
        until=UNTIL,
        reconciliation=report.Reconciliation(report.Status.PASS),
        profile=profile or _profile(),
        account_result=report.ReturnValue(0.01),
        spy_result=report.ReturnValue(0.02),
    )
    return status, capsys.readouterr().out


def test_broker_fill_missing_from_ledger_is_metric4_fail():
    result = report.reconcile_fill_ledger([_order()], [], SINCE, UNTIL)
    assert result.status is report.Status.FAIL
    assert sum(result.broker_missing_from_ledger.values()) == 1
    assert not result.ledger_missing_from_broker


def test_ledger_fill_missing_from_broker_is_metric4_fail():
    result = report.reconcile_fill_ledger([], [_ledger()], SINCE, UNTIL)
    assert result.status is report.Status.FAIL
    assert sum(result.ledger_missing_from_broker.values()) == 1
    assert not result.broker_missing_from_ledger


def test_canceled_and_rejected_without_fill_are_not_broker_executions():
    orders = [
        _order("cancel", qty=0, status="canceled"),
        _order("reject", qty=0, status="rejected", order_type="stop_limit"),
    ]
    result = report.reconcile_fill_ledger(orders, [], SINCE, UNTIL)
    assert result.status is report.Status.PASS
    assert not result.broker_missing_from_ledger


def test_identityless_legacy_ledger_row_is_unknown_not_pass():
    row = _ledger(order_id=None, execution_id=None)
    result = report.reconcile_fill_ledger([], [row], SINCE, UNTIL)
    assert result.status is report.Status.UNKNOWN
    assert result.anonymous_ledger_rows == 1


def test_unreadable_ledger_is_unknown_not_a_synthetic_mismatch():
    result = report.reconcile_fill_ledger(
        [_order()], [], SINCE, UNTIL,
        ledger_available=False,
        ledger_problem="fill_ledger.jsonl okunamadi",
    )
    assert result.status is report.Status.UNKNOWN
    assert not result.broker_missing_from_ledger


def test_order_level_partial_fills_are_aggregated_before_multiset_compare():
    rows = [
        _ledger(execution_id="partial-1", qty=2),
        _ledger(execution_id="partial-2", qty=3),
    ]
    result = report.reconcile_fill_ledger([_order(qty=5)], rows, SINCE, UNTIL)
    assert result.status is report.Status.PASS


def test_multiset_does_not_collapse_equal_symbol_and_qty_orders():
    orders = [_order("o-1"), _order("o-2")]
    result = report.reconcile_fill_ledger(orders, [_ledger("o-1")], SINCE, UNTIL)
    assert result.status is report.Status.FAIL
    assert sum(result.broker_missing_from_ledger.values()) == 1


def test_all_metrics_pass_but_n6_is_not_ready_exit3(capsys):
    status, output = _print_status(capsys, 6, 30)
    assert status is report.Status.NOT_READY
    assert report.EXIT_CODES[status] == 3
    assert "GENEL: NOT_READY" in output
    assert "GENEL: PASS" not in output


def test_all_metrics_pass_but_only_8_trading_days_is_not_ready_exit3(capsys):
    status, output = _print_status(capsys, 20, 8)
    assert status is report.Status.NOT_READY
    assert report.EXIT_CODES[status] == 3
    assert "GENEL: NOT_READY" in output


def test_all_metrics_and_readiness_pass_exit0(capsys):
    status, output = _print_status(capsys, 20, 30)
    assert status is report.Status.PASS
    assert report.EXIT_CODES[status] == 0
    assert "GENEL: PASS" in output


def test_fail_has_priority_over_not_ready():
    status = report.gate_status(
        [report.Status.PASS, report.Status.FAIL,
         report.Status.PASS, report.Status.PASS],
        strategy_trade_count=6,
        elapsed_days=8,
    )
    assert status is report.Status.FAIL
    assert report.EXIT_CODES[status] == 1


def test_unknown_has_priority_over_not_ready():
    status = report.gate_status(
        [report.Status.PASS, report.Status.UNKNOWN,
         report.Status.PASS, report.Status.PASS],
        strategy_trade_count=6,
        elapsed_days=8,
    )
    assert status is report.Status.UNKNOWN
    assert report.EXIT_CODES[status] == 2


def test_pass_requires_all_metrics_and_both_sample_thresholds():
    status = report.gate_status(
        [report.Status.PASS] * 4,
        strategy_trade_count=20,
        elapsed_days=30,
    )
    assert status is report.Status.PASS
    assert report.EXIT_CODES[status] == 0


def test_unknown_provenance_closed_spy_trade_makes_metric1_unknown():
    result = report.evaluate_pnl_metric([
        _trade(symbol="SPY", pnl=100, provenance="UNKNOWN"),
    ])
    assert result.status is report.Status.UNKNOWN
    assert "provenance UNKNOWN kapali islem=1" in result.detail
    assert "R9 journal deploy oncesi bostu" in result.detail


def test_index_parking_profit_is_excluded_from_metric1_net_pnl():
    result = report.evaluate_pnl_metric([
        _trade(index=0, pnl=-10, provenance="strategy"),
        _trade(index=1, symbol="SPY", pnl=100, provenance="index_parking"),
    ])
    assert result.status is report.Status.FAIL
    assert "net PnL=$-10.00" in result.detail
    assert "haric tutulan=1" in result.detail


def test_peak_below_config_breakeven_trigger_is_never_green():
    result = report.evaluate_never_green_metric(
        [_trade(pnl=-128, peak=0.024)],
        breakeven_trigger_pct=0.025,
    )
    assert result.status is report.Status.FAIL
    assert "1/1 = 100.0%" in result.detail
    assert "esik 2.50%" in result.detail


def test_peak_equal_to_config_breakeven_trigger_counts_as_green():
    result = report.evaluate_never_green_metric(
        [_trade(pnl=10, peak=0.025)],
        breakeven_trigger_pct=0.025,
    )
    assert result.status is report.Status.PASS
    assert "0/1 = 0.0%" in result.detail


def test_none_peak_makes_metric3_unknown():
    result = report.evaluate_never_green_metric(
        [_trade(peak=None)],
        breakeven_trigger_pct=0.025,
    )
    assert result.status is report.Status.UNKNOWN
    assert "peak_pct UNKNOWN" in result.detail


def test_open_position_partial_sell_does_not_enter_metric2_denominator():
    event_at = SINCE + timedelta(days=1, hours=1)
    telemetry = [{
        "ts": event_at.isoformat(),
        "kind": "PARTIAL_STATE",
        "symbol": "AMD",
        "side": "LONG",
        "entry_price": 100,
        "intent_status": "FILLED",
        "order_id": "partial-open",
        "target_qty": 5,
        "filled_qty": 5,
    }]
    fills = [report.Fill(
        "AMD", "SELL", 5, 103, event_at, "partial-open",
    )]
    result = report.evaluate_partial_metric([], telemetry, fills, SINCE)
    assert result.opportunities == 0
    assert result.hits == 0


def test_non_live_profile_report_says_it_is_not_live_evidence(capsys):
    _, output = _print_status(capsys, 6, 30, profile=_profile(is_live=False))
    assert "agresif paper sonucu canli R5 kilidini acmanin kaniti SAYILMAZ" in output
    assert "Olcum donemi (UTC):" in output
    assert "Olculen profil: PAPER_AGGRESSIVE" in output
    assert "config SHA-256=cfg-hash | git commit SHA=git-sha" in output
    assert "Hesap getirisi: +1.00%" in output
    assert "Strateji getirisi: +10.00%" in output
    assert "SPY getirisi: +2.00%" in output


def test_report_cli_can_be_invoked_directly_outside_repo_root(tmp_path):
    result = subprocess.run(
        [sys.executable, str(report.ROOT / "tools" / "olcum_raporu.py"), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "R10" in result.stdout


def test_spy_return_uses_explicit_iex_and_retries_sip_rejection(capsys):
    class DataClient:
        def __init__(self):
            self.requests = []

        def get_stock_bars(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                raise RuntimeError(
                    "subscription does not permit querying recent SIP data"
                )
            return SimpleNamespace(data={"SPY": [
                SimpleNamespace(close=100), SimpleNamespace(close=110),
            ]})

    client = DataClient()
    result = report.fetch_spy_return(client, SINCE, UNTIL)
    report._return_line("SPY getirisi", result)

    assert round(result.value, 10) == 0.1
    assert result.reason == "feed=iex"
    assert len(client.requests) == 2
    assert all(request.feed is report.DataFeed.IEX for request in client.requests)
    assert "SPY getirisi: +10.00% (feed=iex)" in capsys.readouterr().out


def test_spy_return_unknown_lists_attempted_feed_when_iex_fails():
    class DataClient:
        def get_stock_bars(self, request):
            assert request.feed is report.DataFeed.IEX
            raise RuntimeError("IEX gecici olarak kullanilamiyor")

    result = report.fetch_spy_return(DataClient(), SINCE, UNTIL)

    assert result.value is None
    assert "denenen feed'ler=iex" in result.reason
    assert "iex: IEX gecici olarak kullanilamiyor" in result.reason


def test_attach_peaks_uses_explicit_iex_feed():
    trade = _trade(peak=None)

    class DataClient:
        request = None

        def get_stock_bars(self, request):
            self.request = request
            return SimpleNamespace(data={"AMD": [SimpleNamespace(
                timestamp=trade.entry_at + timedelta(minutes=30),
                high=105,
                low=99,
            )]})

    client = DataClient()
    assert report.attach_peaks([trade], client) == 0
    assert client.request.feed is report.DataFeed.IEX
    assert round(trade.peak_pct, 10) == 0.05


def test_missing_ledger_keeps_fail_and_names_backfill_remedy(tmp_path):
    rows, available, problem = report.load_fill_ledger(tmp_path, SINCE)
    reconciliation = report.reconcile_fill_ledger(
        [_order()], rows, SINCE, UNTIL,
        ledger_available=available,
        ledger_problem=problem,
    )
    metric = report.evaluate_integrity_metric(
        reconciliation, report.AuthoritativeState(available=True), 0,
    )

    assert metric.status is report.Status.FAIL
    assert report.MISSING_LEDGER_REMEDY in metric.detail


def test_existing_empty_ledger_does_not_print_backfill_remedy(tmp_path):
    (tmp_path / "fill_ledger.jsonl").write_text("", encoding="utf-8")
    rows, available, problem = report.load_fill_ledger(tmp_path, SINCE)
    reconciliation = report.reconcile_fill_ledger(
        [_order()], rows, SINCE, UNTIL,
        ledger_available=available,
        ledger_problem=problem,
    )
    metric = report.evaluate_integrity_metric(
        reconciliation, report.AuthoritativeState(available=True), 0,
    )

    assert metric.status is report.Status.FAIL
    assert report.MISSING_LEDGER_REMEDY not in metric.detail
