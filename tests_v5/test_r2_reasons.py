import pytest

from v5.packages import PACKAGES
from v5.strategy import decide


PARAMS = PACKAGES["high"]


def _call(closes, px, high, low, weight=0.0, fraction=0.0):
    return decide(closes, px, high, low, weight, fraction, PARAMS)


UP_CALM = [100.0] * 249
UP_VOLATILE = [70.0] * 229 + [80.0, 120.0] * 10
MIXED = [80.0] * 100 + [120.0] * 149
DOWN = [100.0] * 249


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        (
            _call(DOWN, 99.0, 104.0, 98.0),
            ("TREND_DOWN", "OVERLAY_ENTER"),
        ),
        (
            _call(UP_VOLATILE, 100.0, 101.0, 99.0),
            ("TREND_UP", "VOL_HIGH"),
        ),
        (
            _call(MIXED, 110.0, 111.0, 109.0),
            ("TREND_MIXED",),
        ),
        (
            _call(DOWN, 99.0, 100.0, 98.0),
            ("TREND_DOWN",),
        ),
        (
            _call(UP_CALM, 101.0, 102.0, 100.0, weight=0.95),
            ("TREND_UP", "VOL_CALM", "BAND_HOLD"),
        ),
        (
            _call(DOWN, 99.0, 104.0, 98.0, weight=0.4, fraction=0.5),
            ("TREND_DOWN", "OVERLAY_KEEP"),
        ),
        (
            _call(DOWN, 99.0, 100.0, 98.0, weight=0.4, fraction=0.5),
            ("TREND_DOWN", "OVERLAY_EXIT"),
        ),
        (
            _call(UP_CALM, 101.0, None, None),
            ("TREND_UP", "VOL_CALM", "OVERLAY_DATA_MISSING"),
        ),
    ],
)
def test_exact_reason_key_order(decision, expected):
    assert decision.reason_keys == expected
