GOAL
Build rock R2 of RF-PLAN-6.md: the pure strategy engine of v5. Done means `v5/packages.py`, `v5/strategy.py`
and `v5/backtest.py` exist, implement THE RULE exactly, and the proof command passes with the parity,
property and reason-key tests described below.

SPEC
Read RF-PLAN-6.md: the sections "THE RULE (canonical)" (all of it, including the golden replay account model
and the golden files paragraph), "APPENDIX A" (the reason keys only) and rock "R2". They are frozen and already
reviewed. Implement them exactly. If a detail is impossible as written but the intent is unambiguous, implement
the closest faithful version and report the deviation. If the impossibility is MATERIAL (it would change
behaviour, scope or an interface), do not improvise: stop, output `BLOCKED: <reason>` as your report, and wait.
Do not redesign.

Interfaces (fixed; later rocks import them):

1. `v5/__init__.py`: empty apart from a one-line docstring.

2. `v5/packages.py` (standard library only)
   - `@dataclass(frozen=True) class PackageParams` with fields, in this order:
     `name: str`, `target_vol: float`, `sma: tuple = (150, 200, 250)`, `vol_window: int = 20`, `lmax: float = 3.0`,
     `leverage: float = 3.0`, `band: float = 0.10`, `overlay_enabled: bool = True`, `ibs_threshold: float = 0.2`,
     `ibs_add: float = 1.0 / 3.0`, `cap: float = 0.99`, `signal_symbol: str = "QQQ"`, `product_symbol: str = "TQQQ"`.
   - `PACKAGES`: dict with exactly the keys `"moderate"` (target_vol 0.20), `"high"` (0.30), `"very_high"` (0.45).
   - `get_package(name) -> PackageParams`; an unknown name raises `ValueError` naming the valid keys.

3. `v5/strategy.py` (standard library only: no numpy, no pandas, no I/O, no clock, no logging, no mutable globals)
   - Constants `READY = "READY"`, `NOT_READY = "NOT_READY"`.
   - `@dataclass(frozen=True) class Decision` with fields: `status`, `reason_code`, `trend_score`, `vol`,
     `exposure`, `core_target`, `ibs`, `wanted`, `overlay_data_missing`, `core_trade`, `core_held`, `ovl_held`,
     `core_new`, `ovl_new`, `total_new`, `trade`, `overlay_fraction_new`, `reason_keys` (a tuple of str).
   - `decide(closes, px, session_high, session_low, product_weight, overlay_fraction, params) -> Decision`.
     * `closes` is any sequence; its elements and every other numeric argument may be a float, an int, None,
       NaN, +inf, -inf, zero or negative. `decide` never raises for such inputs (bool and str elements may be
       treated as invalid numbers).
     * READY: `reason_code` is None, every numeric field is a finite float except `ibs`, which is None when
       the IBS is undefined; `exposure` is E and `core_target` is ct of THE RULE.
     * NOT_READY: `reason_code` is one of `HISTORY_SHORT`, `HISTORY_BAD`, `PRICE_BAD`, `PRICE_JUMP`,
       `WEIGHT_BAD`, `FRACTION_BAD`, `DERIVED_BAD` (validation order V1 to V5 of THE RULE); `trade`, `wanted`,
       `core_trade`, `overlay_data_missing` are False; every numeric field is None; `reason_keys` is `()`.
     * V1 detail: fewer than 249 elements -> `HISTORY_SHORT`; any of the LAST 249 elements not a finite number
       > 0 -> `HISTORY_BAD`. Elements before the last 249 are never inspected.
     * `reason_keys` of a READY decision, in this fixed order:
       (1) exactly one of `TREND_UP` (trend_score == 1), `TREND_DOWN` (trend_score == 0), `TREND_MIXED`;
       (2) only if trend_score > 0: `VOL_HIGH` if `target_vol / vol < 1`; `VOL_CALM` if `vol == 0` or
           `target_vol / vol >= 3`; otherwise nothing;
       (3) `BAND_HOLD` if `core_trade` is False and (`core_held > 0` or `core_target > 0`);
       (4) `OVERLAY_ENTER` if `ovl_new > 1e-12` and `ovl_held <= 1e-12`; `OVERLAY_KEEP` if `ovl_new > 1e-12` and
           `ovl_held > 1e-12`; `OVERLAY_EXIT` if `ovl_new <= 1e-12` and `ovl_held > 1e-12`;
       (5) `OVERLAY_DATA_MISSING` if `overlay_data_missing`.
     * `overlay_enabled` False: `wanted` is always False and `overlay_data_missing` is always False.

4. `v5/backtest.py` (pandas and numpy allowed)
   - `replay(frame, params, first_decision) -> pandas.DataFrame`.
     `frame`: ascending session-date index with columns `c`, `px`, `h`, `l`, `r_T` as described in
     `tests_v5/golden/golden_meta.json`. Rows before `first_decision` only feed the history. For every row from
     `first_decision` on: history = column `c` of ALL earlier rows of the frame (oldest first); decision price =
     the row's `px`; session range = the row's `h` and `l`; holdings drift with the row's `r_T` by the golden
     replay account model (no drift before the first decision row; a NaN `r_T` counts as 0). A NOT_READY decision
     changes nothing.
     Returns one row per decision row with columns `trend_score`, `vol`, `core_target`, `ibs`, `core`,
     `overlay`, `total`, `turnover`, `trade`, `wanted`, `core_trade` (`core`, `overlay`, `total` = weights held
     AFTER the decision; `turnover` = `abs(total after - total before)` on trade rows, else 0).
   - No other public function is required.

Tests to write (tests may use pandas, numpy and the standard library; no network; deterministic seeds):
- `tests_v5/test_r2_parity.py`: for each of the 7 files in `golden_meta.json["files"]`: (a) the sha256 of the
  uncompressed CSV bytes, `rows`, `decisions`, `columns` and `first_decision` equal the manifest; (b) `replay`
  with the file's `params` (take `target_vol`, `sma`, `vol_window`, `lmax`, `leverage`, `band`,
  `ibs_enabled` -> `overlay_enabled`, `ibs_threshold`, `ibs_add`, `cap` from the manifest) reproduces, on every
  decision row, the float columns `trend_score`, `vol`, `core_target`, `ibs`, `core`, `overlay`, `total`,
  `turnover` within 1e-9 (a NaN golden `ibs` must be NaN / None in the replay) and the discrete columns
  `trade`, `wanted`, `core_trade` exactly; (c) `trend_score * 3` is an integer on every row.
- `tests_v5/test_r2_properties.py`: at least 20,000 seeded random READY cases plus targeted cases, asserting:
  weights >= 0; `core_new + ovl_new <= cap + 1e-12`; inside the band (`abs(ct - core_held) <= band`, both > 0)
  the core never trades; a core exit is exactly 0.0; without a valid fresh signal `ovl_new == 0`; an overlay
  already held is never increased; `overlay_fraction_new` is in [0, 1] and equals `ovl_new / total_new`;
  `round(ibs, 6)` ties (for example px 100.30, low 100.00, high 101.50; and px 20.06, low 20.00, high 20.30) are
  not signals while 0.199999 is; `px == SMA` exactly counts as not above; an unknown or flat range never makes
  the decision NOT_READY and sets `overlay_data_missing` when the overlay is enabled; every invalid-input class
  of V1 to V5 (None, NaN, +inf, -inf, 0, negative, too short, a 26% price jump, weight 1.06, fraction -0.01 and
  1.01, finite closes around 1e308 that overflow) returns NOT_READY with the right code, no trade, no exception.
- `tests_v5/test_r2_reasons.py`: a table of hand-made cases asserting the exact `reason_keys` tuple.

CONSTRAINTS
- Python 3.12. Do not create, modify or delete anything outside `v5/` and `tests_v5/`. Never touch
  `tests_v5/golden/*`, any `RF*.md`, `.gitignore`, or any v4 file (`stock_bot.py`, `config.py`, `core/`, `tests/`,
  `tools/`, `utils/`, `Dockerfile`, `docker-compose.yml`, `requirements.txt`, ...).
- No network access. No new dependencies. No em dash characters anywhere. English identifiers and comments.
- Turkish sentences do not belong in this rock: `decide` returns keys only.
- Keep it small and plain: two dataclasses, plain functions, no caching, no class hierarchy, no CLI.
- Do not commit.

NON-GOALS
Broker, market data, executor, rails, notifications, runner, health, ops, Docker: other rocks.

PROOF
Run `python -m pytest tests_v5 -q -p no:cacheprovider --basetemp=.pytest_tmp` and include its full output in
your report.

OUTPUT
End with a report: files changed (one line each: path + what and why), the proof output, and any deviations
from the spec with reasons.
