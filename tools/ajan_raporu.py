"""R13 gunluk ajan sessizligi raporu; yalniz agent_stats.json okur."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import state_path
from core.agent_stats import AGENT_NAMES, AgentStats


def _bucket_start(label: str) -> int:
    try:
        return int(str(label).split("-", 1)[0].rstrip("+"))
    except (TypeError, ValueError):
        return 10**9


def _histogram_text(histogram: dict) -> str:
    parts = [
        f"{bucket}:{count}"
        for bucket, count in sorted(
            (histogram or {}).items(), key=lambda item: _bucket_start(item[0])
        )
        if int(count or 0) > 0
    ]
    return ", ".join(parts) or "veri_yok"


def _median_bucket(histogram: dict) -> str:
    ordered = sorted(
        (histogram or {}).items(), key=lambda item: _bucket_start(item[0])
    )
    total = sum(max(0, int(count or 0)) for _, count in ordered)
    if total <= 0:
        return "veri_yok"
    target = (total + 1) // 2
    running = 0
    for bucket, count in ordered:
        running += max(0, int(count or 0))
        if running >= target:
            return str(bucket)
    return "veri_yok"


def report_lines(days: dict, selected_day: str | None = None) -> list[str]:
    available = sorted(days)
    if selected_day is not None:
        available = [selected_day] if selected_day in days else []
    if not available:
        return ["veri_yok"]

    lines: list[str] = []
    for day_text in available:
        day = days.get(day_text, {})
        lines.append(f"AJAN ISTATISTIGI {day_text}")
        agents = day.get("agents", {}) if isinstance(day, dict) else {}
        for name in AGENT_NAMES:
            agent = agents.get(name, {}) if isinstance(agents, dict) else {}
            votes = agent.get("votes", {}) if isinstance(agent, dict) else {}
            ok = agent.get("data_ok", {}) if isinstance(agent, dict) else {}
            ok_true = int(ok.get("true", 0) or 0)
            ok_false = int(ok.get("false", 0) or 0)
            ok_total = ok_true + ok_false
            missing = (
                f"{ok_false / ok_total * 100:.1f}%" if ok_total else "veri_yok"
            )
            histogram = agent.get("confidence_histogram", {})
            weight = agent.get("last_dynamic_weight")
            weight_text = "veri_yok" if weight is None else f"{float(weight):.4f}"
            lines.append(
                f"  {name}: BUY={int(votes.get('BUY', 0) or 0)} "
                f"SELL={int(votes.get('SELL', 0) or 0)} "
                f"HOLD={int(votes.get('HOLD', 0) or 0)} | "
                f"veri_yok={missing} | guven_medyan_kovasi="
                f"{_median_bucket(histogram)} | guven_kovalari="
                f"{_histogram_text(histogram)} | son_agirlik={weight_text}"
            )

        coord = day.get("coordinator", {}) if isinstance(day, dict) else {}
        decisions = int(coord.get("decisions", 0) or 0)
        conf_hist = coord.get("confidence_histogram", {})
        ws_hist = coord.get("abs_weighted_score_histogram", {})
        threshold = coord.get("min_confidence_score")
        threshold_text = "veri_yok" if threshold is None else str(threshold)
        final = coord.get("final_signal", {})
        lines.append(
            f"  Koordinator: karar={decisions} | guven_medyan_kovasi="
            f"{_median_bucket(conf_hist)} | guven_kovalari="
            f"{_histogram_text(conf_hist)} | abs_ws_kovalari="
            f"{_histogram_text(ws_hist)}"
        )
        lines.append(
            f"    guven>=esik={int(coord.get('confidence_gte_threshold', 0) or 0)} "
            f"(esik={threshold_text}) | ws>15={int(coord.get('ws_gt_15', 0) or 0)} "
            f"| ws<-15={int(coord.get('ws_lt_neg15', 0) or 0)} | "
            f"majority={int(coord.get('majority', 0) or 0)} | "
            f"risk_veto={int(coord.get('risk_veto', 0) or 0)}"
        )
        lines.append(
            f"    final_signal: BUY={int(final.get('BUY', 0) or 0)} "
            f"SELL={int(final.get('SELL', 0) or 0)} "
            f"HOLD={int(final.get('HOLD', 0) or 0)}"
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="R13 gunluk ajan telemetri raporu")
    parser.add_argument("--day", help="Yalniz YYYY-MM-DD gununu goster")
    parser.add_argument(
        "--path",
        default=state_path("agent_stats.json"),
        help="agent_stats.json yolu",
    )
    args = parser.parse_args(argv)
    stats = AgentStats(path=args.path)
    for line in report_lines(stats.days, args.day):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
