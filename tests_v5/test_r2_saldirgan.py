"""R2 adversarial suite (written by Claude, the Visionary, without looking at the implementation).

An independent re-implementation of THE RULE lives in this file (`_ref`). The suite compares `v5.strategy.decide`
with it on tens of thousands of random and boundary inputs, replays the golden files through `decide` with its
own account loop (not `v5.backtest.replay`), and checks invariances the rule implies.
"""
import gzip
import io
import json
import math
import os
import random

import pytest

from v5.packages import PACKAGES, PackageParams
from v5.strategy import NOT_READY, READY, decide

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, "golden")
EPS = 1e-12


# ----------------------------------------------------------------------------- independent reference
def _num(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def _ref(closes, px, hi, lo, w, f, p):
    """Returns ("NOT_READY", code) or ("READY", dict)."""
    try:
        n = len(closes)
    except TypeError:
        return NOT_READY, "HISTORY_SHORT"
    if n < 249:
        return NOT_READY, "HISTORY_SHORT"
    last = [_num(c) for c in list(closes)[-249:]]
    if any(c is None or c <= 0 for c in last):
        return NOT_READY, "HISTORY_BAD"
    pxn = _num(px)
    if pxn is None or pxn <= 0:
        return NOT_READY, "PRICE_BAD"
    if abs(pxn / last[-1] - 1.0) > 0.25:
        return NOT_READY, "PRICE_JUMP"
    wn = _num(w)
    if wn is None or wn < 0 or wn > 1.05:
        return NOT_READY, "WEIGHT_BAD"
    fn = _num(f)
    if fn is None or fn < 0 or fn > 1:
        return NOT_READY, "FRACTION_BAD"
    series = last + [pxn]
    try:
        states = []
        for m in p.sma:
            sma = math.fsum(series[-m:]) / m
            if not math.isfinite(sma):
                return NOT_READY, "DERIVED_BAD"
            states.append(1.0 if pxn > sma else 0.0)
        score = sum(states) / len(states)
        tail = series[-(p.vol_window + 1):]
        rets = [tail[i + 1] / tail[i] - 1.0 for i in range(p.vol_window)]
        if not all(math.isfinite(r) for r in rets):
            return NOT_READY, "DERIVED_BAD"
        mean = math.fsum(rets) / len(rets)
        var = math.fsum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        vol = math.sqrt(var * 252.0)
        if not math.isfinite(vol):
            return NOT_READY, "DERIVED_BAD"
        E = (p.lmax if vol == 0 else min(p.lmax, p.target_vol / vol)) * score
        ct = E / p.leverage
        if not (math.isfinite(E) and math.isfinite(ct)):
            return NOT_READY, "DERIVED_BAD"
    except OverflowError:
        return NOT_READY, "DERIVED_BAD"
    core_held = wn * (1.0 - fn)
    ovl_held = wn * fn
    core_trade = (ct <= 0 and core_held > 0) or (core_held <= 0 and ct > 0) or abs(ct - core_held) > p.band
    core_cand = max(ct, 0.0) if core_trade else core_held
    core_new = min(core_cand, p.cap)
    hin, lon = _num(hi), _num(lo)
    ibs = None
    if hin is not None and lon is not None and hin > 0 and lon > 0 and hin >= lon:
        top, bot = max(hin, pxn), min(lon, pxn)
        if top > bot:
            ibs = (pxn - bot) / (top - bot)
    wanted = bool(p.overlay_enabled and ibs is not None and round(ibs, 6) < p.ibs_threshold)
    if wanted and ovl_held <= EPS:
        ovl_new = max(0.0, min(p.ibs_add, p.cap - core_new))
    elif wanted:
        ovl_new = max(0.0, min(ovl_held, p.cap - core_new))
    else:
        ovl_new = 0.0
    total = core_new + ovl_new
    return READY, dict(trend_score=score, vol=vol, exposure=E, core_target=ct, ibs=ibs, wanted=wanted,
                       overlay_data_missing=bool(p.overlay_enabled and ibs is None), core_trade=bool(core_trade),
                       core_held=core_held, ovl_held=ovl_held, core_new=core_new, ovl_new=ovl_new, total_new=total,
                       trade=bool(core_trade or abs(ovl_new - ovl_held) > EPS),
                       overlay_fraction_new=(ovl_new / total if total != 0 else 0.0))


def _same(a, b, tol=1e-9):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def _compare(d, ref, ctx):
    status, payload = ref
    assert d.status == status, (ctx, d.status, d.reason_code, payload)
    if status == NOT_READY:
        assert d.reason_code == payload, (ctx, d.reason_code, payload)
        assert d.trade is False and d.wanted is False and d.core_trade is False and d.overlay_data_missing is False, ctx
        assert d.reason_keys == (), ctx
        for name in ("trend_score", "vol", "exposure", "core_target", "ibs", "core_held", "ovl_held", "core_new",
                     "ovl_new", "total_new", "overlay_fraction_new"):
            assert getattr(d, name) is None, (ctx, name)
        return
    assert d.reason_code is None, ctx
    for name in ("trend_score", "vol", "exposure", "core_target", "ibs", "core_held", "ovl_held", "core_new", "ovl_new",
                 "total_new", "overlay_fraction_new"):
        assert _same(getattr(d, name), payload[name]), (ctx, name, getattr(d, name), payload[name])
    for name in ("wanted", "overlay_data_missing", "core_trade", "trade"):
        assert getattr(d, name) is payload[name], (ctx, name, getattr(d, name), payload[name])


# ----------------------------------------------------------------------------- generators
def _walk(rng, n, start=None, vol=None):
    px = start if start is not None else rng.choice([0.37, 12.0, 57.68, 680.0, 31250.0])
    vol = vol if vol is not None else rng.choice([0.002, 0.008, 0.015, 0.03, 0.06])
    out = []
    for _ in range(n):
        px *= math.exp(rng.gauss(0.0003, vol))
        out.append(px)
    return out


def _params(rng):
    base = rng.choice(list(PACKAGES.values()))
    kind = rng.random()
    if kind < 0.15:
        return PackageParams(name="off", target_vol=base.target_vol, overlay_enabled=False)
    if kind < 0.25:
        return PackageParams(name="rnd", target_vol=rng.uniform(0.05, 0.9))
    return base


def _case(rng):
    p = _params(rng)
    closes = _walk(rng, rng.choice([249, 250, 251, 300, 420]))
    last = closes[-1]
    mode = rng.random()
    if mode < 0.04:      # price exactly on every moving average, exactly representable (tie counts as not above)
        flat = rng.choice([100.0, 64.0, 512.0])
        closes = closes[:-249] + [flat] * 249
        last = flat
        px = flat
    else:
        px = last * (1.0 + rng.gauss(0, 0.02))
    r = rng.random()
    if r < 0.10:
        hi = lo = None
    elif r < 0.15:
        hi = lo = px                                   # flat range
    elif r < 0.20:
        hi, lo = px * 0.99, px * 1.01                  # inverted
    elif r < 0.40:                                     # cent prices with decimal ties around the threshold
        lo_c = round(px * rng.uniform(0.97, 1.0), 2)
        steps = rng.choice([5, 10, 20, 25, 50, 150])
        hi_c = round(lo_c + steps * rng.choice([0.01, 0.05, 0.10]), 2)
        k = rng.choice([0.2, 0.2, 0.1, 0.3, 0.19, 0.21])
        px = round(lo_c + k * (hi_c - lo_c), 2)
        hi, lo = hi_c, lo_c
        if abs(px / last - 1.0) > 0.25 or px <= 0:
            px, hi, lo = last, last * 1.01, last * 0.99
    else:
        a, b = px * (1 + abs(rng.gauss(0, 0.012))), px * (1 - abs(rng.gauss(0, 0.012)))
        hi, lo = (a, b) if rng.random() < 0.9 else (px * rng.uniform(0.985, 1.0), px * rng.uniform(0.97, 0.985))
    w = rng.choice([0.0, 0.0, rng.uniform(0, 1.05), rng.uniform(0, 0.4), 0.99, 1.0, 1.05, 1 / 3])
    f = rng.choice([0.0, 0.0, 1.0, rng.random(), 0.5])
    return closes, px, hi, lo, w, f, p


# ----------------------------------------------------------------------------- tests
def test_differential_random_cases_match_the_reference():
    rng = random.Random(20261116)
    ready = 0
    seen = {"wanted": 0, "core_trade": 0, "squeeze": 0, "missing": 0, "tie": 0, "exit": 0}
    for i in range(30000):
        closes, px, hi, lo, w, f, p = _case(rng)
        ref = _ref(closes, px, hi, lo, w, f, p)
        d = decide(closes, px, hi, lo, w, f, p)
        _compare(d, ref, i)
        if ref[0] == READY:
            ready += 1
            r = ref[1]
            seen["wanted"] += r["wanted"]
            seen["core_trade"] += r["core_trade"]
            seen["missing"] += r["overlay_data_missing"]
            want_size = p.ibs_add if r["ovl_held"] <= EPS else r["ovl_held"]
            seen["squeeze"] += int(r["wanted"] and r["ovl_new"] < want_size - 1e-9)
            seen["tie"] += int(r["ibs"] is not None and abs(round(r["ibs"], 6) - p.ibs_threshold) < 1e-12)
            seen["exit"] += int(r["core_trade"] and r["core_new"] == 0.0 and r["core_held"] > 0)
            assert d.core_new >= 0 and d.ovl_new >= 0 and d.total_new <= p.cap + 1e-12, i
    assert ready > 25000
    for k, v in seen.items():
        assert v > 50, (k, v)      # the generator really reaches every branch


def test_band_boundary_and_cap_cases_match_the_reference():
    rng = random.Random(7)
    for i in range(4000):
        closes, px, hi, lo, _, _, p = _case(rng)
        ref0 = _ref(closes, px, hi, lo, 0.0, 0.0, p)
        if ref0[0] != READY:
            continue
        ct = ref0[1]["core_target"]
        for delta in (-p.band, p.band, -p.band - 1e-9, p.band + 1e-9, -p.band + 1e-9, p.band - 1e-9, 0.0):
            core = ct + delta
            if core < 0 or core > 1.05:
                continue
            for ovl in (0.0, 0.2, 1 / 3):
                w = core + ovl
                if w > 1.05 or w <= 0:
                    continue
                f = ovl / w
                d = decide(closes, px, hi, lo, w, f, p)
                ref = _ref(closes, px, hi, lo, w, f, p)
                if abs(abs(ref[1]["core_target"] - ref[1]["core_held"]) - p.band) < 1e-12:
                    # exactly on the band edge the outcome depends on the last bit of the volatility sum, which the
                    # rule does not fix; both answers are legal, the decision must only be self-consistent
                    assert d.status == READY
                    if d.core_trade:
                        assert _same(d.core_new, min(max(d.core_target, 0.0), p.cap), 1e-12), (i, delta, ovl)
                    else:
                        assert _same(d.core_new, min(d.core_held, p.cap), 1e-12), (i, delta, ovl)
                    assert d.total_new <= p.cap + 1e-12
                    continue
                _compare(d, ref, (i, delta, ovl))


INVALID = [None, float("nan"), float("inf"), float("-inf"), 0, 0.0, -1, -0.5, 1e308, -1e308, 5e-324]
WEIRD = [True, False, "680", "", [], {}, (1, 2), b"1", object()]


def test_never_raises_and_classifies_invalid_inputs():
    rng = random.Random(99)
    p = PACKAGES["high"]
    good = _walk(rng, 300, start=680.0, vol=0.01)
    px, hi, lo = good[-1] * 1.001, good[-1] * 1.01, good[-1] * 0.99
    for bad in INVALID:
        for pos in (0, 50, 51, 150, 298, 299):
            closes = list(good)
            closes[pos] = bad
            _compare(decide(closes, px, hi, lo, 0.3, 0.0, p), _ref(closes, px, hi, lo, 0.3, 0.0, p), ("closes", bad, pos))
        _compare(decide(good, bad, hi, lo, 0.3, 0.0, p), _ref(good, bad, hi, lo, 0.3, 0.0, p), ("px", bad))
        _compare(decide(good, px, bad, lo, 0.3, 0.0, p), _ref(good, px, bad, lo, 0.3, 0.0, p), ("hi", bad))
        _compare(decide(good, px, hi, bad, 0.3, 0.0, p), _ref(good, px, hi, bad, 0.3, 0.0, p), ("lo", bad))
        _compare(decide(good, px, hi, lo, bad, 0.0, p), _ref(good, px, hi, lo, bad, 0.0, p), ("w", bad))
        _compare(decide(good, px, hi, lo, 0.3, bad, p), _ref(good, px, hi, lo, 0.3, bad, p), ("f", bad))
    for closes in ([], good[:248], tuple(good), None):
        ref = _ref(closes, px, hi, lo, 0.0, 0.0, p)
        _compare(decide(closes, px, hi, lo, 0.0, 0.0, p), ref, ("closes container", type(closes)))
    # random numeric garbage everywhere: must match the reference classification exactly
    for i in range(3000):
        closes = [rng.choice(INVALID + good[:5]) if rng.random() < 0.02 else c for c in good]
        args = [rng.choice(INVALID + [px, hi, lo, 0.3, 1.0]) for _ in range(5)]
        d = decide(closes, *args, p)
        _compare(d, _ref(closes, *args, p), ("garbage", i))
    # non-numeric garbage (bool, str, containers, objects): the contract leaves the classification open,
    # but decide must return a Decision, must not raise, and must never trade on input it could not read
    for i in range(2000):
        closes = [rng.choice(WEIRD) if rng.random() < 0.03 else c for c in good]
        args = [rng.choice(WEIRD + INVALID + [px, hi, lo, 0.3, 1.0]) for _ in range(5)]
        d = decide(closes, *args, p)
        assert d.status in (READY, NOT_READY), i
        if d.status == NOT_READY:
            assert d.trade is False and d.reason_keys == (), i
    for closes in (5, "abc", object(), {"a": 1}):
        d = decide(closes, px, hi, lo, 0.0, 0.0, p)
        assert d.status == NOT_READY and d.trade is False, type(closes)


def test_overflowing_finite_inputs_are_not_ready():
    p = PACKAGES["high"]
    huge = [1.7e308] * 300
    d = decide(huge, 1.7e308, 1.7e308, 1.6e308, 0.0, 0.0, p)
    assert d.status == NOT_READY and d.reason_code == "DERIVED_BAD" and d.trade is False
    tiny = [5e-324] * 300
    d = decide(tiny, 5e-324, None, None, 0.0, 0.0, p)
    assert d.status in (READY, NOT_READY)     # must simply not raise; the reference decides which
    _compare(d, _ref(tiny, 5e-324, None, None, 0.0, 0.0, p), "tiny")


def test_only_the_last_250_values_matter_and_scale_does_not():
    rng = random.Random(5)
    for i in range(600):
        closes, px, hi, lo, w, f, p = _case(rng)
        base = decide(closes, px, hi, lo, w, f, p)
        if base.status != READY:
            continue
        longer = _walk(rng, 137) + [float("nan"), -3.0, None] + list(closes)[-249:]
        again = decide(longer, px, hi, lo, w, f, p)
        for name in ("trend_score", "vol", "core_target", "core_new", "ovl_new", "total_new"):
            assert _same(getattr(base, name), getattr(again, name), 1e-12), (i, name)
        assert (base.trade, base.wanted, base.core_trade) == (again.trade, again.wanted, again.core_trade)
        ib = base.ibs
        if ib is not None and abs(round(ib, 6) - p.ibs_threshold) < 1e-5:
            continue                                    # scaling may move a value across a decimal tie
        ct, ch = base.core_target, base.core_held
        if abs(abs(ct - ch) - p.band) < 1e-9:
            continue
        series = list(closes)[-249:] + [px]
        if any(abs(px - math.fsum(series[-m:]) / m) <= 1e-12 * px for m in p.sma):
            continue                                    # a price sitting exactly on an average may flip under scaling
        k = rng.choice([0.01, 0.5, 3.7, 1000.0])
        sc = decide([c * k for c in closes], px * k, None if hi is None else hi * k, None if lo is None else lo * k, w, f, p)
        assert sc.status == READY
        for name in ("trend_score", "vol", "core_target", "core_new", "ovl_new", "total_new"):
            assert _same(getattr(base, name), getattr(sc, name), 1e-9), (i, name, k)
        assert (base.trade, base.wanted, base.core_trade) == (sc.trade, sc.wanted, sc.core_trade), (i, k)


def test_overlay_is_a_one_day_position_and_is_never_increased():
    rng = random.Random(11)
    hits = 0
    for i in range(6000):
        closes, px, hi, lo, w, f, p = _case(rng)
        d = decide(closes, px, hi, lo, w, f, p)
        if d.status != READY:
            continue
        if d.ovl_held > EPS:
            assert d.ovl_new <= d.ovl_held + 1e-15, i
            hits += 1
        if not d.wanted:
            assert d.ovl_new == 0.0, i
        if not p.overlay_enabled:
            assert d.wanted is False and d.overlay_data_missing is False and d.ovl_new == 0.0, i
        assert 0.0 <= d.overlay_fraction_new <= 1.0, i
    assert hits > 500


def _load(name):
    with gzip.open(os.path.join(GOLDEN, name), "rb") as fh:
        text = fh.read().decode("ascii")
    rows = [line.split(",") for line in text.strip().splitlines()]
    head, body = rows[0], rows[1:]
    idx = {h: i for i, h in enumerate(head)}

    def col(name, conv=float):
        out = []
        for r in body:
            v = r[idx[name]]
            out.append(None if v == "" else conv(v))
        return out
    return {h: col(h, str if h == "date" else float) for h in head}


@pytest.mark.parametrize("fname", ["golden_high.csv.gz", "golden_moderate.csv.gz", "golden_very_high_core_only.csv.gz",
                                   "golden_high_rt.csv.gz"])
def test_golden_through_decide_with_an_independent_account_loop(fname):
    meta = json.load(open(os.path.join(GOLDEN, "golden_meta.json"), encoding="utf-8"))["files"][fname]
    gp = meta["params"]
    p = PackageParams(name="g", target_vol=gp["target_vol"], sma=tuple(gp["sma"]), vol_window=gp["vol_window"],
                      lmax=gp["lmax"], leverage=gp["leverage"], band=gp["band"], overlay_enabled=gp["ibs_enabled"],
                      ibs_threshold=gp["ibs_threshold"], ibs_add=gp["ibs_add"], cap=gp["cap"])
    g = _load(fname)
    n = len(g["date"])
    first = g["date"].index(meta["first_decision"])
    core = ovl = 0.0
    decisions = trades = 0
    for i in range(first, n):
        if i > first:
            r = g["r_T"][i] or 0.0
            grow = (core + ovl) * r
            core = core * (1 + r) / (1 + grow)
            ovl = ovl * (1 + r) / (1 + grow)
        w = core + ovl
        f = ovl / w if w != 0 else 0.0
        d = decide(g["c"][:i], g["px"][i], g["h"][i], g["l"][i], w, f, p)
        assert d.status == READY, (fname, g["date"][i], d.reason_code)
        if d.trade:
            core, ovl = d.core_new, d.ovl_new
            trades += 1
        decisions += 1
        assert _same(core, g["core"][i]) and _same(ovl, g["overlay"][i]), (fname, g["date"][i], core, ovl, g["core"][i], g["overlay"][i])
        assert _same(d.trend_score, g["trend_score"][i]) and _same(d.vol, g["vol"][i]) and _same(d.core_target, g["core_target"][i])
        assert int(d.trade) == int(g["trade"][i]) and int(d.wanted) == int(g["wanted"][i]) and int(d.core_trade) == int(g["core_trade"][i]), (fname, g["date"][i])
    assert decisions == meta["decisions"] and trades == meta["trade_days"]
