from dataclasses import replace
import math
import random

import pytest

from v5.packages import PACKAGES
from v5.strategy import NOT_READY, READY, decide


BASE = PACKAGES["high"]


def _decision(
    closes=None,
    px=100.0,
    high=101.0,
    low=99.0,
    weight=0.0,
    fraction=0.0,
    params=BASE,
):
    if closes is None:
        closes = [100.0] * 249
    return decide(closes, px, high, low, weight, fraction, params)


def test_20_000_seeded_ready_cases_obey_weight_properties():
    rng = random.Random(920_240_621)
    for _ in range(20_000):
        anchor = rng.uniform(5.0, 500.0)
        closes = [anchor * rng.uniform(0.94, 1.06) for _ in range(249)]
        px = closes[-1] * rng.uniform(0.80, 1.20)
        weight = rng.uniform(0.0, 1.05)
        fraction = rng.uniform(0.0, 1.0)
        if rng.random() < 0.15:
            high, low = None, None
        else:
            high = px * rng.uniform(1.0, 1.08)
            low = px * rng.uniform(0.92, 1.0)
        result = _decision(closes, px, high, low, weight, fraction)

        assert result.status == READY
        assert result.core_new >= 0.0
        assert result.ovl_new >= 0.0
        assert result.total_new >= 0.0
        assert result.core_new + result.ovl_new <= BASE.cap + 1e-12
        if result.core_held > 0.0 and result.core_target > 0.0:
            if abs(result.core_target - result.core_held) <= BASE.band:
                assert result.core_trade is False
        if not result.wanted:
            assert result.ovl_new == 0.0
        if result.ovl_held > 1e-12:
            assert result.ovl_new <= result.ovl_held
        assert 0.0 <= result.overlay_fraction_new <= 1.0
        expected_fraction = result.ovl_new / result.total_new if result.total_new else 0.0
        assert result.overlay_fraction_new == expected_fraction


def test_core_band_hold_and_exit_are_exact():
    rising = [80.0 + index * 0.08 for index in range(249)]
    initial = _decision(rising, 100.0)
    held = initial.core_target - 0.05
    inside = _decision(rising, 100.0, weight=held)
    assert held > 0.0
    assert abs(inside.core_target - inside.core_held) <= BASE.band
    assert inside.core_trade is False
    assert inside.core_new == inside.core_held

    falling = [200.0 - index * 0.4 for index in range(249)]
    exited = _decision(falling, falling[-1] - 0.2, weight=0.5)
    assert exited.trend_score == 0.0
    assert exited.core_trade is True
    assert exited.core_new == 0.0


@pytest.mark.parametrize(
    ("px", "low", "high"),
    [(100.30, 100.00, 101.50), (20.06, 20.00, 20.30)],
)
def test_ibs_rounding_ties_are_not_signals(px, low, high):
    result = _decision([px] * 249, px, high, low)
    assert round(result.ibs, 6) == 0.2
    assert result.wanted is False
    assert result.ovl_new == 0.0


def test_ibs_0_199999_is_a_signal():
    result = _decision([100.0] * 249, 100.199999, 101.0, 100.0)
    assert round(result.ibs, 6) == 0.199999
    assert result.wanted is True


def test_equal_to_every_sma_is_not_above():
    result = _decision([100.0] * 249, 100.0)
    assert result.status == READY
    assert result.trend_score == 0.0


@pytest.mark.parametrize(
    ("high", "low"),
    [(None, None), (math.nan, 99.0), (100.0, 100.0), (99.0, 100.0)],
)
def test_unknown_or_flat_range_is_ready_and_missing(high, low):
    result = _decision(high=high, low=low)
    assert result.status == READY
    assert result.ibs is None
    assert result.overlay_data_missing is True
    assert result.wanted is False
    assert result.ovl_new == 0.0


def test_disabled_overlay_never_wants_or_reports_missing_data():
    result = _decision(high=None, low=None, params=replace(BASE, overlay_enabled=False))
    assert result.status == READY
    assert result.wanted is False
    assert result.overlay_data_missing is False


@pytest.mark.parametrize("bad", [None, math.nan, math.inf, -math.inf, 0.0, -1.0, True, "100"])
def test_bad_history_values_are_rejected_without_trade(bad):
    closes = [100.0] * 249
    closes[-2] = bad
    result = _decision(closes)
    assert result.status == NOT_READY
    assert result.reason_code == "HISTORY_BAD"
    assert result.trade is result.wanted is result.core_trade is result.overlay_data_missing is False


def test_only_last_249_history_values_are_inspected():
    result = _decision([object(), *([100.0] * 249)])
    assert result.status == READY


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"closes": [100.0] * 248}, "HISTORY_SHORT"),
        ({"px": None}, "PRICE_BAD"),
        ({"px": math.nan}, "PRICE_BAD"),
        ({"px": math.inf}, "PRICE_BAD"),
        ({"px": -math.inf}, "PRICE_BAD"),
        ({"px": 0.0}, "PRICE_BAD"),
        ({"px": -1.0}, "PRICE_BAD"),
        ({"px": 126.0}, "PRICE_JUMP"),
        ({"weight": None}, "WEIGHT_BAD"),
        ({"weight": math.nan}, "WEIGHT_BAD"),
        ({"weight": math.inf}, "WEIGHT_BAD"),
        ({"weight": -math.inf}, "WEIGHT_BAD"),
        ({"weight": -0.01}, "WEIGHT_BAD"),
        ({"weight": 1.06}, "WEIGHT_BAD"),
        ({"fraction": None}, "FRACTION_BAD"),
        ({"fraction": math.nan}, "FRACTION_BAD"),
        ({"fraction": math.inf}, "FRACTION_BAD"),
        ({"fraction": -math.inf}, "FRACTION_BAD"),
        ({"fraction": -0.01}, "FRACTION_BAD"),
        ({"fraction": 1.01}, "FRACTION_BAD"),
        ({"closes": [1e308] * 249, "px": 1e308}, "DERIVED_BAD"),
    ],
)
def test_invalid_inputs_follow_validation_codes_and_never_trade(kwargs, reason):
    result = _decision(**kwargs)
    assert result.status == NOT_READY
    assert result.reason_code == reason
    assert result.trade is False
    assert result.wanted is False
    assert result.core_trade is False
    assert result.overlay_data_missing is False
    assert result.reason_keys == ()
    numeric = (
        result.trend_score,
        result.vol,
        result.exposure,
        result.core_target,
        result.ibs,
        result.core_held,
        result.ovl_held,
        result.core_new,
        result.ovl_new,
        result.total_new,
        result.overlay_fraction_new,
    )
    assert numeric == (None,) * len(numeric)
