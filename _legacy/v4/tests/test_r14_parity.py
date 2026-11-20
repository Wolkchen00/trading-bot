from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from core.decision_trace import DecisionTrace, GateTrace
from tools import parity_harness as parity


TAPE_PATH = Path(__file__).parent / "fixtures" / "parity_tape.json"
CLOCK = datetime.fromisoformat("2026-08-26T12:00:00-07:00")


def _write_tape(tmp_path: Path, tape: dict, name: str = "tape.json") -> Path:
    path = tmp_path / name
    path.write_text(
        json.dumps(tape, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )
    return path


def _flat_evaluator(path: str):
    def evaluate(symbol, record, cutoff, clock):
        del clock
        return DecisionTrace(
            symbol=symbol,
            as_of=record["bars"][cutoff]["timestamp"],
            bar_count=cutoff + 1,
            tech_signal="HOLD",
            tech_confidence=0.0,
            agent_votes=None,
            weighted_score=None,
            coordinator_confidence=None,
            coordinator_signal=None,
            gates=(
                GateTrace("max_open_positions", True, "fixture"),
                GateTrace("min_confidence_score", None, "uygulanamaz:HOLD"),
                GateTrace("market_regime_adjustment", True, "BULL"),
            ),
            final_action="HOLD",
            path=path,
            notes=("test",),
        )
    return evaluate


def test_same_tape_and_clock_produce_bit_identical_report():
    first = parity.run_harness(TAPE_PATH, clock=CLOCK)
    second = parity.run_harness(TAPE_PATH, clock=CLOCK)

    assert first.exit_code == second.exit_code
    assert first.report.encode("utf-8") == second.report.encode("utf-8")
    assert [trace.to_json() for trace in first.live_traces] == [
        trace.to_json() for trace in second.live_traces
    ]
    assert [trace.to_json() for trace in first.backtest_traces] == [
        trace.to_json() for trace in second.backtest_traces
    ]


def test_gate_missing_only_on_backtest_is_none_not_false():
    outcome = parity.run_harness(TAPE_PATH, clock=CLOCK)
    assert outcome.exit_code == 1

    for trace in outcome.backtest_traces:
        wash_sale = next(gate for gate in trace.gates if gate.name == "wash_sale")
        assert wash_sale.passed is None
        assert wash_sale.reason == "kapi_yok"
        assert wash_sale.passed is not False


def test_coverage_names_all_five_agents_missing_from_backtest():
    outcome = parity.run_harness(TAPE_PATH, clock=CLOCK)
    for agent in (
        "TechAgent", "FundAgent", "SentAgent", "SocialAgent", "RiskAgent"
    ):
        assert f"{agent} | VAR | YOK" in outcome.report


def test_intentionally_diverged_tape_counts_final_action_difference(tmp_path):
    tape = parity.load_tape(TAPE_PATH)
    # Altin bantta iki yolun HOLD/HOLD oldugu TSLA'yi yalniz canli tarafta bulunan
    # kill-switch kapisiyla bilerek ayir. Backtest bu kapiyi sessizce taklit edemez.
    tape["symbols"]["TSLA"]["gate_inputs"]["kill_switch_active"] = True
    path = _write_tape(tmp_path, tape, "diverged.json")

    outcome = parity.run_harness(path, clock=CLOCK)
    live = next(trace for trace in outcome.live_traces if trace.symbol == "TSLA")
    backtest = next(
        trace for trace in outcome.backtest_traces if trace.symbol == "TSLA"
    )
    final_row = next(
        row for row in outcome.field_parity if row.field == "final_action"
    )

    assert live.final_action == "BLOCKED"
    assert backtest.final_action == "HOLD"
    assert final_row.different >= 1
    assert "TSLA" in final_row.different_symbols
    assert outcome.exit_code == 1


def test_exit_codes_full_match_divergence_and_missing_tape(tmp_path):
    full_match = parity.run_harness(
        TAPE_PATH,
        clock=CLOCK,
        live_evaluator=_flat_evaluator("live"),
        backtest_evaluator=_flat_evaluator("backtest"),
        coverage=(parity.CoverageRow("ortak_asama", True, True),),
    )
    divergence = parity.run_harness(TAPE_PATH, clock=CLOCK)
    missing = parity.run_harness(tmp_path / "yok.json", clock=CLOCK)

    assert full_match.exit_code == 0
    assert "SONUC: TAM MUTABAKAT" in full_match.report
    assert divergence.exit_code == 1
    assert "SONUC: SAPMA VAR" in divergence.report
    assert missing.exit_code == 2
    assert "veri_yok" in missing.report


def test_lookahead_clean_paths_and_injected_future_reader_is_caught():
    tape = parity.load_tape(TAPE_PATH)
    live_clean = parity.check_lookahead(tape, parity.evaluate_live, "live", CLOCK)
    backtest_clean = parity.check_lookahead(
        tape, parity.evaluate_backtest, "backtest", CLOCK
    )

    def future_reader(symbol, record, cutoff, clock):
        del clock
        future_close = float(record["bars"][-1]["close"])
        return DecisionTrace(
            symbol=symbol,
            as_of=record["bars"][cutoff]["timestamp"],
            bar_count=cutoff + 1,
            tech_signal="BUY" if future_close > 0 else "HOLD",
            tech_confidence=future_close,
            agent_votes=None,
            weighted_score=None,
            coordinator_confidence=None,
            coordinator_signal=None,
            gates=(),
            final_action="BUY" if future_close > 0 else "HOLD",
            path="live",
            notes=("kasten_gelecege_bakar",),
        )

    caught = parity.check_lookahead(tape, future_reader, "fake", CLOCK)

    assert live_clean.clean
    assert backtest_clean.clean
    assert not caught.clean
    assert caught.checked == 8
    assert len(caught.violations) == 8


def test_normal_run_never_touches_injected_network_or_broker_clients():
    class ExplodingClient:
        def __getattribute__(self, name):
            if name.startswith("__"):
                return object.__getattribute__(self, name)
            raise AssertionError(f"dis istemci cagirildi: {name}")

    outcome = parity.run_harness(
        TAPE_PATH,
        clock=CLOCK,
        network_client=ExplodingClient(),
        broker_client=ExplodingClient(),
    )

    assert outcome.exit_code == 1
    assert "R14 PARITY RAPORU" in outcome.report


def test_corrupt_tape_does_not_crash_and_returns_data_missing_exit2(tmp_path):
    corrupt = tmp_path / "bozuk.json"
    corrupt.write_text("{bozuk-json", encoding="utf-8")

    outcome = parity.run_harness(corrupt, clock=CLOCK)

    assert outcome.exit_code == 2
    assert "veri_yok" in outcome.report
    assert "Cikis kodu: 2" in outcome.report


def test_versioned_tape_has_required_size_and_agent_inputs():
    tape = parity.load_tape(TAPE_PATH)

    assert tape["schema_version"] == 1
    assert tape["tape_kind"] in {"gercek", "sentetik"}
    assert len(tape["symbols"]) >= 8
    assert TAPE_PATH.stat().st_size < 2_000_000
    for record in tape["symbols"].values():
        assert len(record["bars"]) >= 120
        assert set(parity.REQUIRED_AGENT_FIELDS) <= set(record["agent_inputs"])


def test_effective_action_maps_hold_and_blocked_to_same_behaviour():
    """Etiket farki davranis farki DEGILDIR , BLOCKED ve HOLD ikisi de islem yok."""
    assert parity.effective_action("BUY") == "ISLEM"
    assert parity.effective_action("SELL") == "ISLEM"
    assert parity.effective_action("HOLD") == "ISLEM_YOK"
    assert parity.effective_action("BLOCKED") == "ISLEM_YOK"
    assert parity.effective_action("blocked") == "ISLEM_YOK"
    assert parity.effective_action(None) == "ISLEM_YOK"
    assert parity.effective_action("") == "ISLEM_YOK"


def test_report_separates_label_divergence_from_behaviour_divergence():
    """Gercek bantta iki yol da islem acmiyor; rapor bunu ACIKCA soylemeli."""
    outcome = parity.run_harness(TAPE_PATH, clock=CLOCK)
    report = outcome.report

    assert "ETKIN AKSIYON MUTABAKATI" in report
    # Etiket mutabakati dusuk ama davranis mutabakati TAM olmali.
    assert "NIHAI AKSIYON MUTABAKATI" in report
    etkin = report.split("ETKIN AKSIYON MUTABAKATI")[1].splitlines()[1].strip()
    assert etkin.endswith("100.00%"), etkin
    assert "davranis farki DEGILDIR" in report
    # Kapsam boslugu hala sapma sayilir , cikis kodu 1 KALMALI.
    assert outcome.exit_code == 1


def test_blocked_symbols_name_the_gate_that_blocked_them():
    """BLOCKED demek yetmez; HANGI kapi bloklamis, raporda yazmali."""
    outcome = parity.run_harness(TAPE_PATH, clock=CLOCK)
    blocked_lines = [
        line for line in outcome.report.splitlines()
        if "CANLI=BLOCKED" in line
    ]
    assert blocked_lines, "gercek bantta en az bir BLOCKED bekleniyor"
    for line in blocked_lines:
        assert "bloklayan=" in line, line
        gate_text = line.split("bloklayan=")[1].strip()
        assert gate_text and gate_text != "-", line
