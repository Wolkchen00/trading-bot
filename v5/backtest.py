"""Golden account-model replay for the version 5 strategy."""

import pandas as pd

from .strategy import READY, decide


def replay(frame, params, first_decision) -> pd.DataFrame:
    """Replay decisions with drift in weight space."""
    try:
        first_position = frame.index.get_loc(first_decision)
    except KeyError:
        first_position = frame.index.get_loc(pd.Timestamp(first_decision))
    if not isinstance(first_position, int):
        raise ValueError("first_decision must identify exactly one row")

    history = []
    core = 0.0
    overlay = 0.0
    records = []
    result_index = []

    for position, (index, row) in enumerate(frame.iterrows()):
        if position < first_position:
            history.append(row["c"])
            continue

        if position > first_position:
            raw_return = row["r_T"]
            product_return = 0.0 if pd.isna(raw_return) else float(raw_return)
            gain = (core + overlay) * product_return
            denominator = 1.0 + gain
            core = core * (1.0 + product_return) / denominator
            overlay = overlay * (1.0 + product_return) / denominator

        total_before = core + overlay
        fraction = overlay / total_before if total_before != 0.0 else 0.0
        decision = decide(
            history,
            row["px"],
            row["h"],
            row["l"],
            total_before,
            fraction,
            params,
        )
        if decision.status == READY and decision.trade:
            core = decision.core_new
            overlay = decision.ovl_new
        total_after = core + overlay
        turnover = abs(total_after - total_before) if decision.trade else 0.0
        records.append(
            {
                "trend_score": decision.trend_score,
                "vol": decision.vol,
                "core_target": decision.core_target,
                "ibs": decision.ibs,
                "core": core,
                "overlay": overlay,
                "total": total_after,
                "turnover": turnover,
                "trade": decision.trade,
                "wanted": decision.wanted,
                "core_trade": decision.core_trade,
            }
        )
        result_index.append(index)
        history.append(row["c"])

    result = pd.DataFrame(records, index=result_index)
    result.index.name = frame.index.name
    return result
