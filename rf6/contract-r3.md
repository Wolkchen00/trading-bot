GOAL
Build rock R3 of RF-PLAN-6.md: the decision-time data layer (`v5/data.py`) and the executor (`v5/executor.py`):
from a broker and a decision to exactly one idempotent order chain, a verified outcome and a committed overlay
fraction. Done means the proof command passes with the data, sizing, state-machine, crash, ambiguity and
end-to-end tests below. Nothing in this rock touches the network; everything runs on `tests_v5/fakebroker.py`.
This is the part of the system that moves real money: when a rule here and your intuition disagree, the rule
wins; when a case is not covered, choose the behaviour that sends NO order and say so in the report.

SPEC
Read RF-PLAN-6.md sections DESIGN PRINCIPLES, THE RULE, DECISION DATA, SESSION TIMELINE, EXECUTOR (all of it,
including the status table and the verification and bookkeeping rules) and rock R3, and RF6-PROBE.md (the
measured broker behaviour). They are frozen and reviewed. Rocks R1 and R2 are in the repo and reviewed: read
`v5/broker.py`, `v5/clock.py`, `v5/state.py`, `v5/config.py`, `v5/packages.py`, `v5/strategy.py`,
`v5/backtest.py` and `tests_v5/fakebroker.py` in full before writing anything, and use them; do not change them
(exception: `tests_v5/fakebroker.py`, see CONSTRAINTS). If one of them makes this rock impossible, answer
`BLOCKED: <reason>`. Implement exactly; a MATERIAL impossibility -> `BLOCKED: <reason>`, do not redesign.

Facts about R1 you must rely on:
- `submit_order` raises `ValueError` (validation, nothing sent), `BrokerRejected` (a definite refusal, with
  `.status`, `.code`, `.message`) or `AmbiguousSubmit` (the order may or may not exist). Nothing else.
- Reads raise only `BrokerError` subclasses (`BrokerUnavailable` after retries; `BrokerRejected`; plain
  `BrokerError` for an unusable answer). `get_order_by_client_id` returns None only when the broker says the id
  is unknown.
- Quantities are the broker's decimal strings (at most 9 decimals); order sizes must be plain decimal strings
  (`validate_order_request`): quantities with at most 9 decimals, notionals with at most 2.
- `v5.state.atomic_write_json` / `read_json` (`StateCorrupt`), `v5.clock.Window`.

Interfaces (fixed; later rocks import them):

1. `v5/data.py`
   - `@dataclass(frozen=True) class DecisionInputs`: `closes` (tuple of float, oldest first, sessions up to the
     previous session), `close_dates` (tuple of datetime.date), `px` (float), `px_time` (aware datetime),
     `px_source` ("iex_trade" or "iex_bar"), `session_high`, `session_low` (float or None), `range_known`
     (bool), `range_reason` (None or one of `RANGE_EMPTY`, `RANGE_LATE_START`, `RANGE_STALE`, `RANGE_THIN`),
     `range_bars` (int), `range_coverage` (float). `to_bundle() -> dict` / `from_bundle(dict)` round trip
     (JSON-safe, exact: floats survive `json.dumps` / `json.loads` unchanged) so a decision can be replayed bit
     for bit.
   - `@dataclass(frozen=True) class InputsNotReady`: `code`, `detail`. Codes: `CALENDAR_MISSING`,
     `HISTORY_GAP`, `HISTORY_STALE`, `PRICE_STALE`, `BROKER_DOWN`, `DATA_ERROR`.
   - `build_inputs(broker, calendar, session, now, params) -> DecisionInputs | InputsNotReady`:
     * session list = `calendar.previous(session.date, 260)`; fewer than 249 -> `CALENDAR_MISSING`.
     * ONE `broker.get_daily_bars(params.signal_symbol, start=<first date of the list>, adjustment="all",
       feed="sip")`. Bar date = the bar time converted to ET. Keep only bars whose date is in the session
       list (this drops today's partial bar); dates must be strictly increasing without duplicates (a duplicate
       or an out-of-order date among the kept bars -> `HISTORY_GAP`); the previous session missing ->
       `HISTORY_STALE`; any other of the last 249 sessions missing -> `HISTORY_GAP`; a close that is not a
       finite number > 0 -> `HISTORY_GAP`. `closes` = the kept closes in order (at least 249).
     * ONE `broker.get_minute_bars(params.signal_symbol, start=session.open, feed="iex")`. Keep bars with
       `session.open <= t < now`, de-duplicated by `t`, with finite positive high and low and `high >= low`
       (other bars are dropped). Range KNOWN iff there is at least one bar, the first bar starts within 10
       minutes of the open, the last bar starts within 5 minutes before `now`, and the number of bars is at
       least 70% of the whole minutes elapsed since the open. High = max of the bar highs, low = min of the bar
       lows. Otherwise the range is unknown with the matching `range_reason` (checked in the order EMPTY,
       LATE_START, STALE, THIN) and `session_high` / `session_low` are None.
     * Decision price: `broker.get_latest_trade(symbol, feed="iex")` if its time is within [now - 120 s,
       now + 5 s] and its price is a finite number > 0; otherwise the close of the last kept minute bar if it
       started within the last 5 minutes; otherwise `PRICE_STALE`.
     * `BrokerUnavailable` from any call -> `BROKER_DOWN`; any other `BrokerError` -> `DATA_ERROR`. It never
       raises for bad market data or broker trouble.

2. `v5/executor.py`
   - Status constants (strings equal to their names): `NEW` (the plan's "(none)": nothing decided yet),
     `NOT_READY`, `NO_TRADE`, `PLANNED`, `SUBMITTED`, `RECONCILING`, `DONE`, `CAPPED`, `NETTED`, `PARTIAL`,
     `FAILED`, `NOT_SUBMITTED`, `UNRESOLVED`, `MISSED`, `HOLD_NOT_READY`, `DRY_RUN`.
     `FINAL_STATUSES = {NO_TRADE, DONE, CAPPED, NETTED, FAILED, NOT_SUBMITTED, MISSED, HOLD_NOT_READY, DRY_RUN}`
     plus PARTIAL when the record's `closed` flag is true. PARTIAL with `closed` false means "an attempt ended
     without a complete fill and another attempt may follow" (also used when that attempt filled nothing);
     the executor sets `closed` when no further attempt will be made (three attempts used, `now >=
     submit_until`, or a refusal that may not be retried); a chain that closes with nothing filled at all is
     FAILED, not PARTIAL. UNRESOLVED is never final. `record.is_final` implements exactly this.
   - `@dataclass(frozen=True) class Snapshot`: `equity`, `cash` (float), `product_qty` (str, "0" when flat),
     `product_qty_available` (str), `product_price` (float or None), `product_weight` (float, the broker's
     position market value / equity, 0.0 when flat), `foreign_positions` (tuple of human-readable strings:
     every position in another symbol with `abs(market_value) >= 1.0`, and a product position with a negative
     quantity), `foreign_orders` (every open order that is not ours, any symbol), `foreign_product_orders`
     (the subset of `foreign_orders` in the product symbol, plus the negative-product-position item),
     `own_open_orders` (tuple of `Order`). Property `foreign` = `foreign_positions + foreign_orders`.
     `take_snapshot(broker, params, own_client_ids) -> Snapshot`: account, positions and open orders read once
     each. An open order is OURS only if its symbol is the product AND its client id is in `own_client_ids`.
     Raises `SnapshotInvalid(ValueError)` when the account is not `ACTIVE`, is blocked, equity is not a finite
     number > 0, or the product weight is outside [0, 1.05]; broker exceptions pass through unchanged.
   - `client_order_id(mode, session_date, purpose, attempt) -> str`: `v5-{mode}-{YYYYMMDD}-{purpose}-{attempt}`
     (`purpose` "dec" or "flr", `attempt` 1..3; anything else raises ValueError).
   - Sizing, pure: `plan_order(snapshot, total_new, params) -> OrderPlan` with
     `OrderPlan(kind, side, qty, notional, desired_usd, desired_qty, capped)`:
     * `w = snapshot.product_weight`, `delta = total_new - w`, `desired_usd = abs(delta) * equity`.
     * `total_new <= 1e-12` and a position exists (`Decimal(product_qty) > 0`): kind "sell_all", `qty` = the
       broker's quantity string verbatim, `desired_qty` = that quantity. Always sent, however small.
     * otherwise `desired_usd < 1.00`: kind "none", not capped (a bookkeeping-only change -> NETTED).
     * `delta > 0`: kind "buy"; `notional = floor to cents of min(delta * equity, cash * 0.995)`;
       `capped = cash * 0.995 < delta * equity`; a resulting notional under 1.00 -> kind "none" with
       `capped=True` (-> CAPPED, nothing sent). `desired_usd` stays the uncapped, unfloored `delta * equity`.
     * `delta < 0`: kind "sell"; `desired_qty = position_qty * (w - total_new) / w`;
       `qty = floor to 9 decimals of desired_qty`, never more than the position; a zero quantity -> kind "none"
       with `capped=True`.
     Quantities and money use `decimal.Decimal` built from the broker's strings / `repr` of floats; `qty` and
     `notional` in the plan are plain decimal strings accepted by `validate_order_request`.
   - Allocation bookkeeping, pure (plan Revision 9):
     `allocate(core_b, ovl_b, core_new, ovl_new, theta, equity) -> (core_x, ovl_x, fraction)`.
     With `dc = core_new - core_b`, `do = ovl_new - ovl_b`, `net = dc + do`:
     * `dc * do < 0` (the parts move in opposite directions): the smaller move is a re-assignment that needs no
       trade and is always complete, only the net is scaled: if `abs(dc) >= abs(do)`: `ovl_x = ovl_new`,
       `core_x = core_b - do + theta * net`; else `core_x = core_new`, `ovl_x = ovl_b - dc + theta * net`.
     * otherwise: `core_x = core_b + theta * dc`, `ovl_x = ovl_b + theta * do`.
     * negative results are clamped to 0. Then dust: if `ovl_x * equity < 1.00`: `core_x += ovl_x`, `ovl_x = 0`;
       else if `core_x * equity < 1.00`: `ovl_x += core_x`, `core_x = 0` (a part is exactly zero or tradable).
     * `fraction = ovl_x / (core_x + ovl_x)`, 0.0 when the sum is not positive.
   - `class SessionRecord`: the persisted JSON document of ONE order chain of one session. Path:
     purpose "dec" -> `v5.state.session_record_path(state_dir, session_date, dry_run)`; purpose "flr" -> the
     same folder, file `YYYY-MM-DD.flr.json`. API: `SessionRecord.load(state_dir, session_date, purpose="dec",
     dry_run=False) -> SessionRecord | None` (a corrupt file raises `StateCorrupt`), `SessionRecord.new(
     state_dir, session_date, mode, purpose="dec", dry_run=False)`, `save()` (atomic), `data` (the dict),
     `status` property, `is_final`. Fields at least: `session`, `mode`, `purpose`, `dry_run`, `status`,
     `closed` (bool), `status_detail`,
     `inputs` (the bundle or the not-ready code), `decision` (all fields of the Decision, or None), `target`
     (`core_new`, `ovl_new`, `total_new`), `before` (snapshot numbers and the committed fraction before the
     chain), `intents` (list of dicts, one per attempt: `client_order_id`, `purpose`, `attempt`, `kind`,
     `side`, `qty`, `notional`, `desired_usd`, `desired_qty`, `capped`, `before_qty`, `w_before`,
     `equity_before`, `core_b`,
     `ovl_b`, `submit_started`, `submit_count`, `broker_order_id`, `order_status`, `filled_qty`,
     `filled_avg_price`, `fill_ratio`, `theta`, `fraction_after`, `outcome`, `error`),
     `overlay_fraction_committed`, `after` (snapshot numbers), `residual`, `hold` (None or a reason:
     `FOREIGN_ASSET`, `UNRESOLVED`, `SNAPSHOT_INVALID`, `BROKER_DOWN`), `events` (append-only list of
     `{time, what}`), `updated_at`. A change that needs no order (NETTED, CAPPED without an order) and a dry-run
     plan are also appended to `intents` as an entry of their own (with `kind`, sizes, `theta`,
     `fraction_after`; `submit_started` False; no broker fields), so `intents[-1]` always describes the last
     thing the chain did.
     Every mutation is saved with `atomic_write_json` BEFORE the next external action (broker call).
   - `class Executor(broker, settings, state_dir, params, now_fn, sleep_fn)` (`now_fn()` -> aware UTC
     datetime; every wait goes through `sleep_fn`):
     * `load_overlay_fraction() -> float`: the last committed fraction from the file `overlay.json` inside
       `state_dir` (`{"fraction": <float>, "session": <date>, "updated_at": <time>}`), 0.0 if the file does
       not exist; a corrupt file, a missing or non-numeric `fraction`, or a value outside [0, 1] raises
       `StateCorrupt`.
     * `own_client_ids() -> set[str]`: the client ids of all intents in non-dry-run records that are not final.
     * `has_unresolved() -> bool`: any non-dry-run record with status UNRESOLVED (it blocks every new chain).
     * `mark_not_ready(record, code, detail="")`: status NOT_READY with the code (not final; the caller retries
       on the next tick); refuses (ValueError) when the record has a target or intents.
     * `finalize(record, status, detail="", inputs=None, decision=None)`: sets a final status that needs no
       order (NO_TRADE, MISSED, HOLD_NOT_READY) and saves; when given, the input bundle and every field of the
       decision are stored in the record (a NO_TRADE record keeps the decision that led to it); refuses
       (ValueError) when the record has a target, has intents or is final (a record with a target is closed by
       `run_chain` / `reconcile` only).
     * `attach_decision(record, inputs, decision)`: for a "dec" record: stores the input bundle, every field of
       the READY decision and the target `(core_new, ovl_new, total_new)`, saves. Refuses (ValueError) a
       decision that is not READY or has `trade` False, a record that already has a target, and a floor record.
       `attach_floor(record)`: for a "flr" record: target `(0.0, 0.0, 0.0)`.
       The target is decided ONCE per record: after a restart the chain continues towards the stored target,
       it is never re-decided from newer prices.
     * `run_chain(record, window) -> SessionRecord`: drives the record's chain one step further towards its
       stored target and returns when the chain is final or must wait for a later tick. Safe to call on every
       tick and after a restart: it resumes from the persisted record, whatever its status (a final record is
       returned unchanged, with no broker call). It must return within about 90 seconds of injected time per
       call. A record without a target raises ValueError. A record whose `dry_run` flag differs from
       `not settings.orders_enabled` raises ValueError before any broker call.
       Errors: `run_chain` and `reconcile` never raise `BrokerError` or `SnapshotInvalid` (they note the event,
       set `hold` where it applies and return the record with its status unchanged); an error while saving
       (`OSError`) and `StateCorrupt` propagate to the caller, and nothing is submitted after a failed save.
     * `reconcile(record, session) -> SessionRecord`: called after the close (`session` is the
       `v5.clock.Session`) for a record that is not final: resolves PLANNED / SUBMITTED / RECONCILING /
       UNRESOLVED / open PARTIAL from broker evidence only (see rules).
   - Chain rules (the plan's EXECUTOR section, made exact):
     a. New chain / new attempt: at or after `window.submit_until` no attempt is started: a record that has
        no intent yet becomes NOT_SUBMITTED (final; `status_detail` keeps the hold reason, if any).
        Refuse to start (return with `hold="UNRESOLVED"`, no order) while
        `has_unresolved()` is true for ANY record. Take a snapshot. Purpose "dec": any `snapshot.foreign` item
        -> `hold="FOREIGN_ASSET"` with the items listed, no order, not final (the hold clears by itself on a
        later call when the items are gone). Purpose "flr": only `foreign_product_orders` block.
        Then cancel our own leftover open orders, poll them until terminal (at most 20 one-second polls; still
        open -> return and try again on the next call) and take a fresh snapshot.
        A `SnapshotInvalid` or a broker exception here: `hold` names it, no order, retried on the next call.
     b. Split: `fraction` = the committed fraction; `core_b = w * (1 - fraction)`, `ovl_b = w * fraction` with
        `w` the fresh product weight. `plan_order(snapshot, core_new + ovl_new, params)`.
        Kind "none", not capped -> NETTED with `theta = 1`. Kind "none", capped -> CAPPED with `theta = 0`.
        A floor record on a flat account -> DONE.
        Otherwise persist the intent (attempt n, its client id, sizes, `before_qty`, `w_before`, `core_b`,
        `ovl_b`, `submit_started=False`, status PLANNED), then set `submit_started=True` and save, THEN call
        `submit_order`. In dry-run (`settings.orders_enabled` False) nothing is submitted: the plan is written
        to the dry-run record with status DRY_RUN; dry-run records are never read by the live path, never
        count for idempotency and never change `overlay.json`.
     c. Submit outcomes: success -> store the broker id, status SUBMITTED. `AmbiguousSubmit`, or
        `BrokerRejected` with code 40010001 (the id already exists) -> look the order up by client id once a
        second for up to 20 seconds; found -> SUBMITTED; not found and `now < window.submit_until` and fewer
        than 3 submissions of this id -> submit the SAME id again; otherwise status UNRESOLVED.
        `BrokerRejected` with status 403 and code 40310000 -> the attempt ends with `theta = 0`; ONE re-sized
        attempt from a fresh snapshot is allowed per chain; a second such refusal, or any other
        `BrokerRejected`, ends the chain (FAILED, or PARTIAL when an earlier attempt filled something).
        Nothing is ever submitted or re-submitted at or after `window.submit_until`.
     d. SUBMITTED: poll the order (once a second, at most 20 polls per call) until terminal. At or after
        `window.cancel_at` an order that is not terminal is cancelled, then polled until terminal.
     e. Terminal order -> status RECONCILING, then from the broker's fill report:
        `fill_ratio` = filled / submitted size (buy: `filled_qty * filled_avg_price / notional`; sell:
        `filled_qty / qty`); `theta` = exactly 1.0 when the broker status is "filled" and the plan was not
        capped (the shortfall is only the rounding to cents or 9 decimals); otherwise filled / DESIRED size
        (buy: `filled_qty * filled_avg_price / desired_usd`; sell: `filled_qty / desired_qty`), clipped to
        [0, 1], 0 when nothing filled;
        `(core_x, ovl_x, fraction_after) = allocate(core_b, ovl_b, core_new, ovl_new, theta, equity_before)`
        (the same function gives the result of a NETTED change with `theta = 1` and of a CAPPED / refused
        attempt with `theta = 0`). Save these on the intent,
        then write `overlay.json`, then decide the outcome from a FRESH snapshot with
        `tol = max(0.015, 2 / equity)` and `residual = w_after - total_new`:
        broker status "filled": DONE when `abs(residual) <= tol`, else CAPPED (final, residual recorded);
        any other terminal status: PARTIAL with `closed` false; when `now < window.submit_until` and fewer than
        3 attempts were made, the next attempt (rule a) follows for the remainder, in this call or a later one;
        otherwise the chain closes: PARTIAL (`closed` true) when any attempt of the chain filled something,
        FAILED when nothing filled. A later call at or after `submit_until` closes an open PARTIAL the same way.
        `fraction_after` is a pure function of what the intent stored before the submit and of the broker's
        fill report, so repeating RECONCILING after a crash writes the same value again.
     f. Restart / repeated call, by persisted state of the last intent:
        `submit_started` False: inside the window (`now < window.submit_until`) the attempt is re-planned from
        a fresh snapshot under the same attempt number and client id; otherwise NOT_SUBMITTED.
        `submit_started` True without a broker id: look up by client id (rule c).
        Broker id known: rule d. RECONCILING: rule e again. UNRESOLVED: look up by client id; found -> rule d.
     g. `reconcile(record, session)` (after the close): PLANNED with `submit_started` False -> NOT_SUBMITTED
        (or closed PARTIAL when an earlier attempt filled). An open PARTIAL -> closed (PARTIAL or FAILED).
        A started intent: found by client id -> rules d and e (a DAY order is terminal after the close; no
        further attempt). Not found: `broker.list_orders(after=session.open, symbol=<product>)`; only when
        the listing is complete AND does not contain the client id AND the position quantity equals the
        intent's `before_qty` exactly -> the attempt is recorded as nothing filled (`theta = 0`, fraction
        unchanged) and the chain ends FAILED (or closed PARTIAL when an earlier attempt filled). In every
        other case the record stays UNRESOLVED.
     h. A broker exception during a step never changes a status by itself: the step is retried on the next
        call. A corrupt state file raises `StateCorrupt` to the caller. Unknown order statuses are not terminal.
     i. At most 3 attempts per chain. The record is the only source of truth for what was sent: never submit
        when the record cannot be saved.

Tests to write (FakeBroker only, injected clock and sleep, deterministic seeds):
- `tests_v5/test_r3_data.py`: built from the fixtures `daily_bars_QQQ_sip_all` (291 bars, the last one is
  today's partial bar and must be dropped), `calendar_window`, plus hand-made minute bars and trades: happy path;
  today's bar dropped; previous session missing -> HISTORY_STALE; an older session missing -> HISTORY_GAP;
  duplicate and out-of-order bars -> HISTORY_GAP; short calendar -> CALENDAR_MISSING; stale trade but fresh bar
  -> px from the bar; both stale -> PRICE_STALE; trade from the future ignored; range rules (late start, stale
  last bar, thin coverage, empty, duplicates, bars outside the session, bad bars); broker down; a rejected data
  call -> DATA_ERROR; bundle round trip through real JSON text.
- `tests_v5/test_r3_sizing.py`: each `plan_order` kind incl. cent and 9-decimal flooring, the cash cap, the
  sub-dollar cases (small adjustment -> none / NETTED, capped below $1 -> CAPPED, dust full exit -> sell_all),
  never more than held, every produced size accepted by `validate_order_request`, `take_snapshot` (foreign
  position, dust foreign position ignored, foreign order, a stray `v5-` prefixed order is foreign, own order
  recognised only with product symbol AND persisted client id, `SnapshotInvalid` cases).
- `tests_v5/test_r3_chain.py`: every row of the status table; restart at EVERY persisted transition (simulate
  a process death by building a fresh Executor and re-loading the record from the same state dir) inside the
  window, after `submit_until` and after the close; assert the FakeBroker's order book never contains two
  orders for one intent and never an order the record does not know; ambiguity (`timeout_before_accept`,
  `timeout_after_accept`, `invisible(n)` beyond the 20 lookups, an order that appears only after the close),
  rejections (buying power re-size, qty re-size, a second refusal -> FAILED, other -> FAILED), partial fill
  then second attempt, never-fill then cancel at `cancel_at`, three attempts maximum, UNRESOLVED blocking a
  later session until reconciled, `reconcile` proving FAILED only with a complete listing and an unchanged
  quantity, foreign hold and its self-clearing, the floor chain (sells everything, ignores foreign positions,
  blocked by a foreign product order), dry-run writing no order and no committed fraction, a crash between the
  fill bookkeeping and the final status (RECONCILING repeated gives the same fraction).
- `tests_v5/test_r3_ledger.py`: the committed overlay fraction against an independent ledger kept by the test
  for full fill, partial fill, capped buy, rejection, NETTED opposing change (core exits while the overlay
  enters), and two attempts with a price move in between.
- `tests_v5/test_r3_e2e.py`:
  * End-to-end A: `decide` + `Executor` on FakeBroker over the whole `golden_high.csv.gz` history (one decision
    per row from `first_decision` on; history, `px`, `h`, `l` and the product return `r_T` used exactly as
    `v5/backtest.py` uses them; the FakeBroker product price follows `r_T`; large account 1,000,000; zero
    slippage): the same trade days as the golden and held weights within 1e-6 on every row. Also run
    `golden_high_rt.csv.gz`.
  * End-to-end B, parameters frozen in RF-PLAN-6.md rock R3: package high on `golden_high.csv.gz`, start equity
    $490.00, seeds 1 to 5, adverse fill move uniform 0 to 20 bps, cent rounding and the $1 minimum, per order
    10% partial fill (30 to 90%), 10% delayed visibility (2 lookups), 5% ambiguous submit (half accepted).
    For every seed: no duplicate accepted order; cash never negative; never short; product weight never above
    cap + 1 pp; the committed overlay fraction equals the test's own ledger on every row, with every date of
    `opposing_component_days` in `golden_meta.json` covered; held weight within 3 pp of the golden on at least
    99% of rows; final equity between $27,000 and $59,000.
  The whole suite must stay under about three minutes: no real sleeps, and you may speed up FakeBroker's
  internals (for example indexes instead of scans) as long as its behaviour and every existing test stay
  the same.

CONSTRAINTS
- Python 3.12, Windows and Linux. `v5/data.py` and `v5/executor.py` use the standard library only (tests may
  use `pandas` / `numpy` to read the golden files).
- Files you may create or change: `v5/data.py`, `v5/executor.py`, new files `tests_v5/test_r3_*.py`, and
  `tests_v5/fakebroker.py` (ADD capabilities or speed only; existing behaviour and every existing test must stay
  intact). Nothing else: never touch `tests_v5/golden/*`, `tests_v5/fixtures/*`, other R1 / R2 files and tests,
  `tests_v5/test_r1_saldirgan.py`, `tests_v5/test_r2_saldirgan.py`, any `RF*.md`, `.gitignore`, or any v4 file.
- No real sleeps in tests (inject `sleep_fn`); no network; no em dash characters; English identifiers.
- No owner-facing text in this rock (reason keys and status codes only). Do not commit.

NON-GOALS
Rails, notifications, health, ops commands, the runner loop, Docker (rocks R4, R5). The executor does not decide
WHEN it runs: it is given the window and a clock.

PROOF
Run `python -m pytest tests_v5 -q -p no:cacheprovider --basetemp=.pytest_tmp_r3 --ignore-glob="tests_v5/test_r4_*"`
and include its full output in your report (another builder is working on rock R4 in the same folder at the
same time; its files are `v5/rails.py`, `v5/notify.py`, `v5/health.py`, `v5/ops.py`, `tools/v5_durum.py`,
`tests_v5/test_r4_*.py`: never read, run or touch them).

OUTPUT
End with a report: files changed (one line each: path + what and why), the proof output, and any deviations
from the spec with reasons, plus every case where you chose "send no order" because the spec was silent.
