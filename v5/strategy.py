"""Pure version 5 strategy decision engine."""

from dataclasses import dataclass
import math


READY = "READY"
NOT_READY = "NOT_READY"


@dataclass(frozen=True)
class Decision:
    status: str
    reason_code: str | None
    trend_score: float | None
    vol: float | None
    exposure: float | None
    core_target: float | None
    ibs: float | None
    wanted: bool
    overlay_data_missing: bool
    core_trade: bool
    core_held: float | None
    ovl_held: float | None
    core_new: float | None
    ovl_new: float | None
    total_new: float | None
    trade: bool
    overlay_fraction_new: float | None
    reason_keys: tuple[str, ...]


def _number(value):
    if isinstance(value, (bool, str, bytes)):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _not_ready(reason_code):
    return Decision(
        NOT_READY,
        reason_code,
        None,
        None,
        None,
        None,
        None,
        False,
        False,
        False,
        None,
        None,
        None,
        None,
        None,
        False,
        None,
        (),
    )


def decide(
    closes,
    px,
    session_high,
    session_low,
    product_weight,
    overlay_fraction,
    params,
) -> Decision:
    """Apply the canonical strategy rule to one decision point."""
    try:
        count = len(closes)
    except (TypeError, ValueError, OverflowError):
        return _not_ready("HISTORY_SHORT")
    if count < 249:
        return _not_ready("HISTORY_SHORT")

    history = []
    try:
        tail = closes[-249:]
        for value in tail:
            number = _number(value)
            if number is None or number <= 0.0:
                return _not_ready("HISTORY_BAD")
            history.append(number)
    except (TypeError, ValueError, OverflowError, IndexError):
        return _not_ready("HISTORY_BAD")
    if len(history) != 249:
        return _not_ready("HISTORY_BAD")

    price = _number(px)
    if price is None or price <= 0.0:
        return _not_ready("PRICE_BAD")
    try:
        price_change = price / history[-1] - 1.0
    except (ArithmeticError, OverflowError):
        return _not_ready("PRICE_BAD")
    if not math.isfinite(price_change) or abs(price_change) > 0.25:
        return _not_ready("PRICE_JUMP")

    weight = _number(product_weight)
    if weight is None or not 0.0 <= weight <= 1.05:
        return _not_ready("WEIGHT_BAD")

    fraction = _number(overlay_fraction)
    if fraction is None or not 0.0 <= fraction <= 1.0:
        return _not_ready("FRACTION_BAD")

    series = history + [price]
    try:
        means = [sum(series[-window:]) / float(window) for window in params.sma]
        returns = [
            series[index] / series[index - 1] - 1.0
            for index in range(len(series) - params.vol_window, len(series))
        ]
        returns_mean = sum(returns) / float(params.vol_window)
        variance = sum((value - returns_mean) ** 2 for value in returns) / float(
            params.vol_window - 1
        )
        vol = math.sqrt(variance) * math.sqrt(252.0)
        trend_score = sum(price > mean for mean in means) / float(len(means))
        if vol == 0.0:
            exposure = params.lmax * trend_score
        else:
            exposure = min(params.lmax, params.target_vol / vol) * trend_score
        core_target = exposure / params.leverage
        derived = means + returns + [vol, exposure, core_target]
        if not all(math.isfinite(value) for value in derived):
            return _not_ready("DERIVED_BAD")
    except (ArithmeticError, OverflowError, TypeError, ValueError):
        return _not_ready("DERIVED_BAD")

    core_held = weight * (1.0 - fraction)
    ovl_held = weight * fraction
    core_trade = (
        (core_target <= 0.0 and core_held > 0.0)
        or (core_held <= 0.0 and core_target > 0.0)
        or abs(core_target - core_held) > params.band
    )
    core_cand = max(core_target, 0.0) if core_trade else core_held
    core_new = min(core_cand, params.cap)

    high = _number(session_high)
    low = _number(session_low)
    ibs = None
    if high is not None and low is not None and high > 0.0 and low > 0.0 and high >= low:
        hi = max(high, price)
        lo = min(low, price)
        width = hi - lo
        if width > 0.0 and math.isfinite(width):
            candidate = (price - lo) / width
            if math.isfinite(candidate):
                ibs = candidate

    wanted = bool(
        params.overlay_enabled
        and ibs is not None
        and round(ibs, 6) < params.ibs_threshold
    )
    if wanted and ovl_held <= 1e-12:
        ovl_new = max(0.0, min(params.ibs_add, params.cap - core_new))
    elif wanted and ovl_held > 1e-12:
        ovl_new = max(0.0, min(ovl_held, params.cap - core_new))
    else:
        ovl_new = 0.0

    total_new = core_new + ovl_new
    trade = core_trade or abs(ovl_new - ovl_held) > 1e-12
    overlay_fraction_new = ovl_new / total_new if total_new != 0.0 else 0.0
    overlay_data_missing = bool(params.overlay_enabled and ibs is None)

    keys = []
    if trend_score == 1.0:
        keys.append("TREND_UP")
    elif trend_score == 0.0:
        keys.append("TREND_DOWN")
    else:
        keys.append("TREND_MIXED")
    if trend_score > 0.0:
        if vol != 0.0 and params.target_vol / vol < 1.0:
            keys.append("VOL_HIGH")
        elif vol == 0.0 or params.target_vol / vol >= 3.0:
            keys.append("VOL_CALM")
    if not core_trade and (core_held > 0.0 or core_target > 0.0):
        keys.append("BAND_HOLD")
    if ovl_new > 1e-12 and ovl_held <= 1e-12:
        keys.append("OVERLAY_ENTER")
    elif ovl_new > 1e-12 and ovl_held > 1e-12:
        keys.append("OVERLAY_KEEP")
    elif ovl_new <= 1e-12 and ovl_held > 1e-12:
        keys.append("OVERLAY_EXIT")
    if overlay_data_missing:
        keys.append("OVERLAY_DATA_MISSING")

    return Decision(
        READY,
        None,
        float(trend_score),
        float(vol),
        float(exposure),
        float(core_target),
        None if ibs is None else float(ibs),
        wanted,
        overlay_data_missing,
        bool(core_trade),
        float(core_held),
        float(ovl_held),
        float(core_new),
        float(ovl_new),
        float(total_new),
        bool(trade),
        float(overlay_fraction_new),
        tuple(keys),
    )
