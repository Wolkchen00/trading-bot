import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from v5.backtest import replay
from v5.packages import PackageParams


GOLDEN = Path(__file__).parent / "golden"
META = json.loads((GOLDEN / "golden_meta.json").read_text(encoding="utf-8"))
FLOAT_COLUMNS = (
    "trend_score",
    "vol",
    "core_target",
    "ibs",
    "core",
    "overlay",
    "total",
    "turnover",
)
DISCRETE_COLUMNS = ("trade", "wanted", "core_trade")


def _params(name, values):
    return PackageParams(
        name=name,
        target_vol=values["target_vol"],
        sma=tuple(values["sma"]),
        vol_window=values["vol_window"],
        lmax=values["lmax"],
        leverage=values["leverage"],
        band=values["band"],
        overlay_enabled=values["ibs_enabled"],
        ibs_threshold=values["ibs_threshold"],
        ibs_add=values["ibs_add"],
        cap=values["cap"],
    )


@pytest.mark.parametrize("filename", META["files"])
def test_golden_manifest_and_replay(filename):
    item = META["files"][filename]
    path = GOLDEN / filename
    csv_bytes = gzip.decompress(path.read_bytes())
    assert hashlib.sha256(csv_bytes).hexdigest() == item["sha256_uncompressed_csv"]

    frame = pd.read_csv(path, index_col="date", parse_dates=True)
    assert len(frame) == item["rows"]
    assert ["date", *frame.columns] == item["columns"]
    assert frame.index[0].strftime("%Y-%m-%d") == item["warmup_from"]
    expected = frame.loc[pd.Timestamp(item["first_decision"]):]
    assert len(expected) == item["decisions"]
    assert expected.index[0].strftime("%Y-%m-%d") == item["first_decision"]

    actual = replay(frame, _params(item["package"], item["params"]), item["first_decision"])
    assert actual.index.equals(expected.index)
    assert actual.index.name == expected.index.name
    for column in FLOAT_COLUMNS:
        np.testing.assert_allclose(
            actual[column].to_numpy(dtype=float),
            expected[column].to_numpy(dtype=float),
            rtol=0.0,
            atol=1e-9,
            equal_nan=True,
        )
    for column in DISCRETE_COLUMNS:
        np.testing.assert_array_equal(
            actual[column].to_numpy(dtype=bool),
            expected[column].to_numpy(dtype=bool),
        )
    scaled_trend = actual["trend_score"].to_numpy(dtype=float) * 3.0
    np.testing.assert_allclose(scaled_trend, np.round(scaled_trend), rtol=0.0, atol=1e-12)
