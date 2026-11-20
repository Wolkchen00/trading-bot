GOAL
Build rock R5 of RF-PLAN-6.md: the runner loop (`v5/runner.py`), the two remaining operator commands
(`decide-now`, `replay` in `v5/ops.py`), packaging (`Dockerfile`, `docker-compose.yml`, `.dockerignore`), the
watchdog script (`ops/vps/v5_watchdog.sh`) and the paper drill tool (`tools/v5_tatbikat.py`). Done means the
proof command passes with the accelerated-clock scenarios below and the packaging files are exactly as
specified. Nothing in the tests touches the network or sleeps for real.
The runner decides WHEN things happen and WHICH rule has precedence. Its two ways to fail are the ones that
killed the previous bot: doing nothing without telling anyone, and acting on a state it did not verify. When
this contract is silent, choose "send no order and tell the owner".

SPEC
Read RF-PLAN-6.md completely (it is short) with special care for SESSION TIMELINE, RUNNER, RAILS (precedence,
what blocks what), NOTIFICATIONS AND HEALTH, APPENDIX A and rocks R5 and R6; they are frozen and reviewed.
Rocks R1 to R4 are in the repo and reviewed: read `v5/*.py` and `tests_v5/fakebroker.py` in full first and use
their public functions; do not change them (exceptions under CONSTRAINTS). A MATERIAL impossibility ->
answer `BLOCKED: <reason>`; do not redesign. `requirements-v5.lock.txt` exists (pinned by the reviewer): do not
edit it.

1. `v5/runner.py`
   - `class Runner(settings, broker, now_fn, sleep_fn, post_fn)`: builds `Rails`, `Outbox`, `Notifier`,
     `Executor` (`v5.packages.get_package(settings.package)`) on `settings.state_dir`. `tick() -> None`
     performs ONE loop iteration and never raises (not even `StateCorrupt` or `OSError`).
     `run_forever(tick_seconds=20, max_ticks=None)`: `tick()`, then `sleep_fn(tick_seconds)`.
   - `main()`: logging to stdout (one line per event, never a secret, never the ntfy topic or URL);
     `load_settings()` (a `ConfigError` -> message on stderr, exit code 2); `ensure_state_dir` (failure -> exit
     2); `ProcessLock(state_dir / "process.lock")` (busy -> exit 3); then `AlpacaBroker`, `Runner`,
     `run_forever()`. If building the Runner or the loop itself dies with an unexpected exception, `main` adds
     a `NOT_STARTED` alert through a fresh `Outbox` / `Notifier` (default post function), tries one flush and
     exits 1.
   - Own persisted state: `calendar.json` (`{"fetched_at": <ISO>, "sessions": SessionCalendar.to_json()}`) and
     `runner.json` (`closed_session`: the last session whose after-close work is complete; `cutoff_alerted`:
     the session whose cutoff alert was sent; `overlay_missing_streak`: int). Everything else is derived from
     the executor's records, the rails and the clock, so a restart at any moment continues correctly.
   - A dry-run process (`not settings.orders_enabled`) uses dry-run records everywhere
     (`SessionRecord.new(..., dry_run=True)` / `load(..., dry_run=True)`).
   - `tick()`, in this order. Each numbered section is guarded on its own: an exception in one section is
     logged with its traceback and does not prevent the later sections; the heartbeat is always written.
     (1) HEARTBEAT (first thing, and again at the end with the final phase and states), keys as in the R4
         contract (`v5.health.write_heartbeat`).
     (2) CALENDAR: load the cache; fetch (`broker.get_calendar(today - 400 days, today + 16 days)`,
         `parse_calendar`) when there is no cache, when it is older than 24 hours, or when it does not cover
         today; not more often than once per 5 minutes; a failed fetch keeps the old cache.
     (3) BROKER CLOCK: `broker.get_clock()` when it was never read, every 5 minutes, and on every tick inside
         the decision window. `clock_ok` = the call succeeded and the broker time differs from `now_fn()` by
         at most 60 seconds; `market_open` = the broker says the market is open. A failed call means
         `clock_ok = False`, `market_open = False`.
     (4) HOUSEKEEPING: `notifier.flush()`. Every record with status UNRESOLVED (any session, strategy or
         floor): if its session is today and the close has not passed, `executor.run_chain(record, window)`;
         otherwise `executor.reconcile(record, <its session from the calendar>)`.
     (5) SESSION by time. `today` = the ET date of `now`. Calendar does not cover today -> phase `no_calendar`
         (see cutoff rule). `calendar.get(today)` is None -> phase `closed`. Otherwise with `w =
         window_for(session)`:
         * before `open - 45 min`: phase `pre`.
         * `[open - 45 min, open)`: phase `readiness`. Once per process and session: broker reachable and
           account usable (`read_equity`), state dir writable, calendar covers today, `clock_ok`, notifier
           configured, peak initialised, rails file readable. The problems (codes) go into the heartbeat
           `readiness`; for the FIRST problem ONE alert `READINESS` with `cause_for(code)` (state key =
           session date + code). Codes: `BROKER_DOWN` or the reading's problem, `STATE_CORRUPT`,
           `CALENDAR_MISSING`, `CLOCK_DOUBT`, `NOTIFY_MISSING`, `PEAK_MISSING`.
         * `[open, decide_from)`: phase `session`. Every 5 minutes: `read_equity` -> a reading that is not ok
           with problem `EQUITY_BAD` or `INCONSISTENT`: alert `DATA_DOUBT` (state key = session date; a broker
           outage or a closed account is NOT a data doubt and sends nothing here); an ok reading:
           `rails.check_breaker(reading,
           session.date)`; when it becomes true for the first time this session: alert `BREAKER` with
           `pct = 1 - equity / baseline` (cause `BREACH`) or `DATA_DOUBT` (cause `BASELINE_DOUBT`), state key =
           session date.
         * `[decide_from, close)`: phase `decision`: the DECISION STEP below.
         * `[close, reconcile_from)`: phase `closing`: nothing.
         * from `reconcile_from`: phase `after_close`: the AFTER-CLOSE STEP below.
         In phases `pre`, `closed`, `readiness` and `no_calendar`, the after-close step of the most recent
         session that already closed is also run if `closed_session` shows it is still missing (a process
         that was down at the close), until that step's give-up time.
     (6) ERRORS: when any section raised, a counter of consecutive failing ticks grows; at 3 the alert `ERROR`
         is sent (cooldown as usual) and the heartbeat carries the state `TICK_ERROR` (or `STATE_CORRUPT` when
         the exception was `StateCorrupt`); a clean tick resets the counter.
   - DECISION STEP (strategy record `dec`, floor record `flr`, both of today's session):
     a. An existing floor record that is not final: `executor.run_chain(flr, w)`, then the floor report
        (rule g); stop. An existing strategy record with a target that is not final:
        `executor.run_chain(dec, w)`; stop. A final strategy record: stop.
     b. `now > w.decide_until` (nothing decided in time): the CUTOFF RULE; stop.
     c. `reading = read_equity(broker, now)`. No peak (`rails.peak() is None`): not ready `PEAK_MISSING`; stop.
        Reading ok: `floor = rails.check_floor(reading)`; reading not ok: `floor = rails.floor_state()`, and
        when that is "OK": problem `BROKER_DOWN` -> not ready `BROKER_DOWN`; `ACCOUNT_STATUS` / `BLOCKED` ->
        not ready with that code; `EQUITY_BAD` / `INCONSISTENT` -> alert `DATA_DOUBT` (state key = session
        date) and not ready `EQUITY_DOUBT`; stop.
     d. `floor == "HALT"`: the FLOOR FLOW (rule g); stop. `floor == "CONFIRMING"`: not ready
        `RAIL_CONFIRMING`; stop (the next tick takes the next reading).
     e. Breaker: as in phase `session` (same alerts); remember whether it is active.
     f. In this order, the first that applies ends the step for this tick:
        not `clock_ok` -> not ready `CLOCK_DOUBT`; not `market_open` -> not ready `MARKET_CLOSED` (a DAY order
        sent into a closed market would rest until the next open); notifier not configured -> not ready
        `NOTIFY_MISSING`;
        `executor.has_unresolved()` -> not ready `UNRESOLVED` (alert `UNRESOLVED`, state key = session date);
        `take_snapshot(broker, params, executor.own_client_ids())`: `SnapshotInvalid` -> not ready
        `SNAPSHOT_INVALID`, `BrokerError` -> not ready `BROKER_DOWN`; `snapshot.foreign` not empty -> not ready
        `FOREIGN_ASSET` with the first item as detail (alert `FOREIGN_ASSET` with `symbol` = the ticker symbol
        of that item only, for example "SPY"; state key = that symbol); `rails.paused()` -> not ready `PAUSED`;
        `build_inputs(...)` returns `InputsNotReady` -> not ready with its code;
        `decide(inputs.closes, inputs.px, inputs.session_high, inputs.session_low, snapshot.product_weight,
        executor.load_overlay_fraction(), params)` is not READY -> not ready with its `reason_code`.
        Otherwise: breaker active -> `decision = reduce_only(decision)`; `decision.trade` False ->
        `executor.finalize(dec, NO_TRADE, inputs=inputs, decision=decision)`; else
        `executor.attach_decision(dec, inputs, decision)` and `executor.run_chain(dec, w)`.
        "not ready CODE" = `executor.mark_not_ready(dec, CODE, detail)` on today's strategy record (created
        when missing); the step is simply tried again on the next tick.
     g. FLOOR FLOW (floor state HALT): take a snapshot (failure: try again next tick). Product quantity zero:
        `rails.set_liquidating(False)` when it was set, alert `FLOOR_DONE` (`pct` = drawdown from the peak,
        `equity`; state key "halt"), and today's strategy record, when it is not final and has no target, is
        finalised `HOLD_NOT_READY` with detail `FLOOR_HALT` (no further alert); stop.
        Product held: `floor_order_allowed(clock_ok, True, market_open, seconds to the close,
        executor.has_unresolved(), bool(snapshot.foreign_product_orders))`; refused -> alert `FLOOR_PENDING`
        (`pct`, `equity`, `product_pct` = product weight; state key = the reason); stop. Allowed: today's floor
        record (load, or new + `attach_floor`), `executor.run_chain(flr, w)`; then a fresh snapshot: flat ->
        `set_liquidating(False)` and `FLOOR_DONE`; still held -> `FLOOR_PENDING` (state key "selling").
        No strategy order is ever placed while the floor state is HALT or CONFIRMING.
     h. CUTOFF RULE (`now > w.decide_until`, strategy record missing or without a target and not final):
        status NEW or no record -> `finalize(MISSED)` and alert `MISSED`; status NOT_READY with code C ->
        `finalize(HOLD_NOT_READY, C)` and, unless C is `PAUSED`, `FOREIGN_ASSET`, `UNRESOLVED` or `FLOOR_HALT`
        (already reported, or the owner's own choice), alert `HOLD_NOT_READY` with `cause_for(C)`.
        The same rule applies at the after-close step for a process that was down during the window, and to a
        weekday without calendar coverage once the ET time passes 15:56 (`HOLD_NOT_READY` with
        `CALENDAR_MISSING`, one alert per day). `cutoff_alerted` guarantees one cutoff alert per session.
   - AFTER-CLOSE STEP (once per session; `closed_session` marks it done):
     1. Every record of the session that is not final: `executor.reconcile(record, session)`; the cutoff rule
        for a strategy record that is missing or still without a target.
     2. `reading = read_equity(broker, now)`. Not ok: try again on the next tick; give up (mark the session
        closed, log, no digest) at the next session's `open - 45 min`.
     3. `rails.record_close(session.date, reading.equity)`; `dd = rails.check_alert(reading)` not None ->
        alert `DRAWDOWN_ALERT` (`pct = dd`, `equity`).
     4. `overlay_missing_streak`: +1 when the session's decision has `overlay_data_missing`, else 0.
     5. The digest: `notifier.digest(session.date, ctx)` with
        `prefix` "paper" (paper mode) / "dry_run" (live without `orders_enabled`) / "";
        `equity` = the reading; `day_usd` = equity minus the stored closing equity of the previous recorded
        session (the broker's `last_equity` when there is none); `day_pct` = `day_usd` / that base;
        `product_weight` from a snapshot (`positions_value / equity` of the reading when it fails);
        `action` / `action_usd` from the fills in the session's records (bought dollars = sum of `filled_qty *
        filled_avg_price` of buys; sold likewise; sold and now flat -> "sell_all"; nothing filled -> "none");
        `reason_keys` = the stored decision's reason keys, or None when there is no decision;
        `month_pct` = equity / base - 1 with base = the last stored closing equity before the first day of
        this month (the first stored one when there is none); `total_pct` likewise with the first stored
        closing equity; both None without any base;
        `status_text` = None, or `build_alert(...)` of the FIRST that applies: floor HALT and still
        liquidating -> `RAIL_LIQUIDATING`; floor HALT -> `FLOOR_DONE`; an UNRESOLVED record -> `UNRESOLVED`;
        today's hold was a foreign item -> `FOREIGN_ASSET`; a record of today FAILED -> `ORDER_FAILED`;
        PARTIAL, or CAPPED with `abs(residual) > 0.03` -> `ORDER_PARTIAL`; paused -> `PAUSED`;
        HOLD_NOT_READY -> `HOLD_NOT_READY` with its cause; MISSED -> `MISSED`.
     6. `closed_session = session.date`.
     There are no separate reminder messages: while a blocked state lasts, the digest's last line is the
     reminder of the day.
   - Heartbeat `states` (names): `FLOOR_HALT`, `RAIL_LIQUIDATING`, `RAIL_CONFIRMING`, `UNRESOLVED`,
     `FOREIGN_ASSET`, `PAUSED`, `BREAKER_TODAY`, `OVERLAY_DATA_MISSING` (streak >= 2), `CLOCK_DOUBT`,
     `MARKET_CLOSED`, `NOTIFY_MISSING`, `PEAK_MISSING`, `DIGEST_PENDING`, `TICK_ERROR`, `STATE_CORRUPT`,
     `DRY_RUN`.

2. `v5/ops.py` (add two commands; keep the existing ones and their tests intact)
   - `decide-now [--out <file>]`: read-only. Settings, `AlpacaBroker`, the calendar (cache or fetch), today's
     session (none, or outside `[open, close)`: prints why and exits 1), `build_inputs`, `take_snapshot`,
     `decide` with the committed overlay fraction, `plan_order`. Prints (and writes to `--out`) ONE JSON
     document: `{time, mode, package, inputs (the bundle or the not-ready code), snapshot (numbers), 
     overlay_fraction, decision (all fields), plan (kind, side, qty, notional)}`. It never calls
     `submit_order` or `cancel_order` and writes nothing into the state dir.
   - `replay <file>`: reads such a document, recomputes `decide` from its inputs, snapshot weight, fraction and
     package, prints the recomputed decision and exits 0 when it equals the stored one field by field, else 1.
   - Importable, dependency-injected forms (tests run them on FakeBroker):
     `cmd_decide_now(settings, broker, now, out_path=None) -> (exit_code, document)` and
     `cmd_replay(document) -> (exit_code, recomputed_decision_dict)`.

3. `Dockerfile` (replace the v4 file): `FROM python:3.12-slim`; `ENV PYTHONUNBUFFERED=1
   PYTHONDONTWRITEBYTECODE=1`; `WORKDIR /app`; copy `requirements-v5.lock.txt` and `pip install --no-cache-dir
   -r` it; then copy ONLY `v5/`, `tools/v5_durum.py`, `tools/v5_tatbikat.py`, `tests_v5/`, `ops/`; create
   `/app/logs`; no `HEALTHCHECK` (a blocked state must not restart the container);
   `CMD ["python", "-u", "-m", "v5.runner"]`. No model download, no `COPY . .`, no `.env`.

4. `docker-compose.yml` (replace): two services, `trading-live` and `trading-paper-livecfg`, both with
   `build: .`, `image: trading-bot:latest`, `restart: unless-stopped`, `env_file: .env`, json-file logging
   (max-size 10m, max-file 3). Environment: live `TRADING_MODE=live`, `ALPACA_KEY_PREFIX=LIVE`; paper
   `TRADING_MODE=paper`, `ALPACA_KEY_PREFIX=PAPER`. Volumes: `state-live:/app/state_live` and
   `./logs/live:/app/logs` for live, `state-paper:/app/state_paper` and `./logs/livecfg:/app/logs` for paper.
   Named volumes `state-live`, `state-paper`. No `depends_on`, no `container_name`, no `version:` key, no
   third service.

5. `.dockerignore` (replace): `*` first, then `!` lines for exactly what the Dockerfile copies
   (`requirements-v5.lock.txt`, `v5/`, `tools/v5_durum.py`, `tools/v5_tatbikat.py`, `tests_v5/`, `ops/`), then
   ignore `**/__pycache__` and `**/.pytest_tmp*`.

6. `ops/vps/v5_watchdog.sh` (POSIX sh, LF line endings, run by cron every 10 minutes on the VPS host): for
   each of the two service name prefixes `trading-live-` and `trading-paper-livecfg-`: find the running
   container whose name starts with it (`docker ps --format '{{.Names}}'`), run `docker exec <c> python -m
   v5.health --json` and read `heartbeat_age_seconds`. If no such container runs, or the command fails, or the
   age is missing or above 300: POST the `WATCHDOG` sentence of Appendix A to `https://ntfy.sh/<topic>` (topic
   read from the file `/root/.v5_watchdog_topic`; header `Title: Trading bot`), at most once per 4 hours per
   service (timestamp file `/var/tmp/v5_watchdog_<service>`). It never restarts, stops or changes a container
   and never prints the topic.

7. `tools/v5_tatbikat.py` (paper drill, run by the operator during deployment; real PAPER orders): refuses to
   run unless `settings.mode == "paper"`. It always uses a temporary state dir of its own (never the bot's)
   and synthetic session dates in the years 1990 to 2009 derived from the current time, so its client ids can
   never collide with the bot's. Steps selected by flags, each printing `PASS` or `FAIL` with evidence, exit
   code 0 only if every selected step passed: `--inventory` (account, positions, open orders),
   `--flatten-foreign --yes` (cancel every open order and sell every position that is not the product),
   `--round-trip` (through `Executor`: target weight 0.05 -> DONE or CAPPED, then target 0 -> flat),
   `--duplicate-id` (the same client id twice -> code 40010001), `--restart` (persist an intent with
   `submit_started` False, build a fresh Executor on the same drill state dir, expect exactly one order at
   the broker), `--notify` (the `TEST` sentence through the outbox; reports the HTTP status).
   Importable form: `run_steps(settings, broker, steps, now_fn, sleep_fn, post_fn, confirm=False) ->
   dict` mapping each step name ("inventory", "flatten_foreign", "round_trip", "duplicate_id", "restart",
   "notify") to `(passed: bool, evidence: str)`; it raises ValueError for a settings object that is not in
   paper mode before touching the broker, and `flatten_foreign` does nothing without `confirm=True`.

Tests to write (`tests_v5/test_r5_*.py`; FakeBroker, injected clock, fake post function; a helper module
`tests_v5/simworld.py` may keep the fake market in step with the fake clock: calendar, 300 sessions of daily
bars, minute bars up to "now", a fresh latest trade, the broker clock):
accelerated-clock scenarios, each asserting the final record status, the kinds and exact texts of the messages
sent (Appendix A / R4 templates), the heartbeat phase and states, that no order was sent when none may be, and
the automatic recovery in the next session where it applies:
(1) normal session with a trade (record DONE, one digest, no other message); (2) normal session without a
trade (NO_TRADE, digest still sent); (3) early-close session (the decision happens 10 minutes before 13:00 ET);
(4) a process restart (a new Runner on the same state dir and broker) at many moments of the decision window
and at every record status: never a second order, the record ends final; (5) the window missed (process
started after `decide_until`: MISSED, alert, next session normal); (6) calendar unavailable and no cache
(`CALENDAR_MISSING`, one alert, recovery when it returns); (7) broker down for a whole session
(`HOLD_NOT_READY` with the broker cause, one alert, late or no digest, next session normal); (8) market data
failing until the cutoff (`HOLD_NOT_READY`) and, separately, recovering before the cutoff (decision made);
(9) an order still open at `cancel_at` (cancelled; PARTIAL or FAILED; the digest's status line says so);
(10) a corrupt state file at start-up (no order, the file untouched, `STATE_CORRUPT` in the heartbeat, the
`ERROR` alert); (11) a foreign position appears and disappears (`FOREIGN_ASSET` alert, no order, normal next
session); (12) breaker day (`BREAKER` alert once, an overlay entry and a core increase suppressed, the rule's
own exit still executed, normal next session); (13) floor: first breaching reading -> no strategy order,
second -> HALT, the floor chain sells everything, `FLOOR_DONE`, no trading on the following sessions until
`clear_halt`; a floor during `CLOCK_DOUBT` sends no order and reports `FLOOR_PENDING`; (14) no ntfy topic: no
strategy order, health degraded, while a confirmed floor still liquidates; (15) dry-run live: no order method
called over three sessions, digests carry the dry-run prefix, dry-run records only; (16) two months (44
sessions) with a paused bot: exactly one digest per session whose last line is the `PAUSED` sentence, no other
message, positions untouched; (17) an UNRESOLVED record: no order of any kind in later sessions, it is
resolved by the after-close evidence, then trading resumes; (18) `tick()` never raises: with every broker
method failing, with a corrupt `rails.json`, with an unwritable state dir.
Also `tests_v5/test_r5_packaging.py` (the three packaging files and the watchdog script as text: every rule
above, for example no `COPY . .`, no `depends_on`, exact service and volume names, the CMD, LF endings in the
shell script) and `tests_v5/test_r5_ops_tools.py` (`decide-now` on FakeBroker proving that no order method is
called and nothing is written to the state dir; `replay` equal / different; each drill step on FakeBroker;
the drill refuses live mode).

CONSTRAINTS
- Python 3.12, Windows and Linux; standard library plus `requests`.
- Owner-facing strings: only the exact strings of RF-PLAN-6.md Appendix A and of `v5.notify` (use
  `build_alert`, `build_digest`, `cause_for`; never write an owner-facing sentence in this rock).
- Files you may create or replace: `v5/runner.py`, `Dockerfile`, `docker-compose.yml`, `.dockerignore`,
  `ops/vps/v5_watchdog.sh`, `tools/v5_tatbikat.py`, `tests_v5/simworld.py`, new `tests_v5/test_r5_*.py`.
  Files you may extend without changing existing behaviour or tests: `v5/ops.py` (the two commands),
  `tests_v5/fakebroker.py` (new capabilities only). Nothing else: no v4 file is deleted or edited in this rock
  (the reviewer moves v4 afterwards), no `RF*.md`, no golden or fixture file, no earlier test, no
  `tests_v5/test_*_saldirgan.py`.
- SAFETY: the repo holds a `.env` with real brokerage keys. Never read it, never print environment variables,
  never run anything that contacts a network service, never run `v5.runner`, `v5.ops decide-now` or
  `tools/v5_tatbikat.py` for real: everything is exercised through FakeBroker and injected functions.
- No network, no real sleep in tests, no em dash characters, English identifiers. Do not commit.

NON-GOALS
Deploying, editing the VPS, moving v4 code, writing the runbook: the reviewer does these in R6.

PROOF
Run `python -m pytest tests_v5 -q -p no:cacheprovider --basetemp=.pytest_tmp` and include its full output in
your report.

OUTPUT
End with a report: files changed (one line each: path + what and why), the proof output, any deviations from
the spec with reasons, and every case where you chose "send no order and tell the owner" because the spec was
silent.

AS BUILT (2026-11-19, builder: Claude after Codex's quota ran out; reviewer suite: tests_v5/test_r5_saldirgan.py)
Deliberate differences from the text above, decided while building and pinned by tests:
1. `runner.json` also holds `started_at` (the first tick ever). A session that closed before it is not ours:
   no MISSED message and no digest for it. The after-close work of the last closed session is caught up in the
   phases pre / closed / readiness; in readiness a failing account reading gives up (no digest, logged).
2. `FLOOR_DONE` is sent once, when the account is first seen flat after a halt (the `liquidating` flag flips);
   afterwards the digest's last line carries it every day.
3. Owner-held symbols (plan Revision 10): every snapshot the runner takes passes `settings.owner_symbols`; the
   digest context carries `owner_weight` / `owner_label`; `product_weight` in the digest is a share of the
   whole account.
4. After a chain step the runner sends at once: `UNRESOLVED` (status UNRESOLVED) and `ORDER_FAILED` (FAILED).
5. On a weekday without calendar coverage, after 15:56 ET, the date gets a record HOLD_NOT_READY
   (CALENDAR_MISSING) and one message.
6. A foreign item's ticker is the second field of its text (`position:SPY:...`, `order:SPY:...`).
7. `Executor.unresolved_records()` exists for the housekeeping step; record scans re-read only changed files.
8. Drill dates: a date is used only after the broker confirmed it never saw that order id.

AS BUILT, second part (2026-11-19, after the independent review; plan Revision 11; tests: tests_v5/test_r5b_inceleme.py)
9. `run_chain(record, window, blocked=None)`: the runner computes the gate on every tick and passes its code.
   For a strategy chain under way: STATE_CORRUPT, FLOOR_HALT, RAIL_CONFIRMING, CLOCK_DOUBT, MARKET_CLOSED,
   NOTIFY_MISSING, PAUSED, BREAKER (the breaker is measured again; it only blocks a buy). For a floor chain
   under way: HALT_CLEARED, then `floor_order_allowed`. A chain blocked until the submit cutoff ends
   NOT_SUBMITTED with the code as its detail.
10. `_after_chain` also sends `NOT_SENT` (cause from the record's detail; empty or CUTOFF_BEFORE_SUBMIT reads
    "süre yetmedi"). ORDER_FAILED names the fund's share of the whole account.
11. `Executor.open_records()`: every non-final record with a target (never a dry-run record). Housekeeping
    reconciles each of them once its session's reconcile time has come, and reports a status change. A new
    strategy decision is refused (`UNRESOLVED`) while any other chain is open. Before its session's reconcile
    time a record is driven only by the decision step, so a record of real orders is never driven by a
    process whose flag is off.
12. After the close: the summary waits until the reconcile deadline for an open order of the day; then it is
    sent with action "unknown", the outcome sentence and the UNRESOLVED status. Late (more than 35 minutes
    after the close): the separate cutoff message is replaced by the dated summary. Catch-up runs in the
    phases closed, pre, readiness, session and decision without giving up (this replaces item 1's "readiness
    gives up"), and once more at the start of the next after-close step, where it gives up. It covers up to
    five earlier sessions, oldest first; a session still pending holds the later ones back. `_after_chain(...,
    late=True)` (housekeeping settling an old record) does not send `NOT_SENT`.
13. `_backfill_close`: the previous session's row from `last_equity` when missing; called by the breaker
    watch and by the on-time after-close step. `check_breaker(reading, day, previous_session)`.
14. Floor: `_floor_done` / `_floor_pending` (one key a day); `set_liquidating(True)` whenever the fund is held
    in HALT; `_settle_floor` after the close; a failed snapshot while a sale is owed marks the day BROKER_DOWN
    or SNAPSHOT_INVALID (so the cutoff speaks); no `check_alert` in HALT; the dry-run condition is gone.
15. Error accounting: failure times of the last 30 minutes; ERROR at once for StateCorrupt, else at three.
16. `_no_calendar_cutoff` skips a day whose usual cutoff (15:56 ET) passed before `started_at`.
17. Heartbeat: `MARKET_CLOSED` while the broker calls the market closed inside session hours (the clock is
    then read on every tick); `UNRESOLVED` also for any record left open after its reconcile deadline.
18. Digest context: `outcome` (NOT_SENT, REFUSED, UNKNOWN, FLOOR) replaces the reasons; `action` may be
    "unknown"; `day_usd` is None when the base is not known.

AS BUILT, third part (2026-11-19, fix round R5c; builder Codex from `rf6/fix-r5c.md` and its second part, reviewer
Claude with `tests_v5/test_r5c_saldirgan.py`; plan Revision 12)
19. `_continue_block` takes the engine snapshot after the NOTIFY_MISSING check: SNAPSHOT_INVALID, BROKER_DOWN,
    FOREIGN_ASSET (with its alert); any other fault there is ENGINE_FAULT and is re-raised after the chain
    was driven.
20. FAILED with detail NOT_FOUND on a strategy record: `NOT_SENT` (alert, digest outcome, status line), also
    when the record is settled late.
21. Digest context: `as_of_now`; dry-run actions `would_buy` / `would_sell` / `would_sell_all` from the DRY_RUN
    record's last intent (floor record first); `OVERLAY_ENTER` / `OVERLAY_EXIT` filtered by what was filled.
22. `rails.breaker_cause(reading, stored_close, breaker_day)` with the sentinel `NO_ROW` is the one breaker
    rule; `Rails.check_breaker` and `ops._blockers` both use it.

AS BUILT, fourth part (2026-11-19, fix round R5d; builder Codex for groups A to F from `rf6/fix-r5d.md`, Claude
for G1 to G6 from `rf6/fix-r5d-2.md`; reviewer suite `tests_v5/test_r5d_saldirgan.py`; plan Revision 13)
23. `Executor.run_chain(record, window, blocked=None, buys_blocked=False)`: `_check_provenance` first (mode
    and client ids, else `StateCorrupt`); a buy planned or re-planned while `buys_blocked` is held with
    `hold = "BREAKER"`; `_resume` inspects the account before it re-sends a PLANNED intent.
24. `_continue_block` returns `(code, problem, breaker)` and blocks on a reading that is not ok;
    `_floor_block` takes the snapshot and the foreign product orders into account.
25. `EquityReading.last_equity_asof` (from the account's `balance_asof`); `last_equity_is_close_of`;
    `breaker_cause(..., previous_session)` can answer `BASELINE_PENDING`; `check_breaker(..., decisive)`.
    `record_close` only appends a session later than every stored row.
26. Runner state: `closed_sessions` (the newest fifteen; `closed_session` is their maximum),
    `unknown_sessions`; `_after_close` and `_catch_up` return whether the session is done; `_correction_step`
    sends the one correction summary; `LATE_CLOSE` is five minutes.
27. `_failure_kind(record)`: REFUSED (a broker status code in the detail), NOT_SENT (NOT_FOUND, VALIDATION),
    else UNFILLED; `_after_chain(record, session, late)` sends no alert for a strategy record when `late`.
28. `_load_state` raises `StateCorrupt` for a file that is not an object of the known field types; the file
    is left as it is.
29. `ops`: `_blockers` adds `OUTSIDE_DECISION_WINDOW` and `ALREADY_DECIDED`; `clear_halt_refusal(reading,
    new_peak)` and `clear-halt --force`. `tools/v5_tatbikat.py`: `_exit(bought)` sells only when the account
    holds exactly what the step bought. `ops/vps/v5_watchdog.sh`: lookup by the label
    `com.docker.compose.service`, the name prefix is the fallback.

AS BUILT, fifth part (2026-11-20, fix round R5e; builder Codex from `rf6/fix-r5e.md` and `rf6/fix-r5e-2.md`,
Claude for `rf6/fix-r5e-3.md`; reviewer suite `tests_v5/test_r5e_saldirgan.py`; plan Revision 14)
30. `Rails.record_close(session, equity, raise_peak=True, current_equity=None)`: with `raise_peak=False` the
    row is `provisional` and the peak is untouched; answers "stored", "stored_peak_refused" or None.
    `Rails.confirm_close(session, official, named, current_equity)`: "confirmed", "confirmed_peak_refused",
    "corrected" or None. The bound: no raise when `current_equity <= (1 - alert_dd) * value`.
    `_after_close` stores its on-time reading with `raise_peak=False`; `_watch_breaker` confirms the previous
    session's row before `check_breaker`.
31. `tick()`: after the clock step, `effective = min(now, self._broker_now)` while `clock_skewed`;
    housekeeping and the session step get `effective`. `_clock_step` asks on every tick while in doubt and
    accepts a broker timestamp between `before - 60 s` and `after + 60 s`. `_catch_up` returns at once while
    skewed. Every `read_equity` is stamped with `self.now_fn()`.
32. `_continue_block` measures the floor (`check_floor`) and returns the hold code of the brake ("BREAKER" or
    "EQUITY_DOUBT"); `run_chain(..., buys_blocked=<code>)`; `reduce_only(decision, doubt)`.
33. `_decision_step`: the fresh path is `_fresh_decision`; `StateCorrupt` from it marks the day STATE_CORRUPT
    (unless it is a halt day) and is raised again. `_session_step` with a corrupt `runner.json`: READINESS
    only before the open, the floor part in the decision window, then the exception.
34. Floor messages: `_floor_done` (FLOOR_DONE or FLOOR_FLAT, key `halt:<New York date>`, only while HALT),
    `_floor_pending(day, drawdown, equity, held, reason, holding_known)` (FLOOR_PENDING after a submit, else
    FLOOR_WAITING or FLOOR_WAITING_BLIND with a cause from `FLOOR_WAIT_CAUSES`), `_floor_filled` counts floor
    records since the halt's date. `Rails.set_liquidating(True)` answers False unless the rail is HALT;
    `_poll` cancels at once when the block is HALT_CLEARED.
35. Reports: `_reported_records(day)` gives a dry process the real records of a day that has a submitted
    order; `_cutoff` and the dry decision skip such a day; the summary's prefix follows the records.
    `_digest_context(..., late)`: `holdings_now`, `other_weight`, `cash_weight`; outcomes PARTIAL and NO_CASH;
    a `BrokerError` from its last fallback makes `_after_close` return False.
36. `Notifier.alert(kind, state_key, real)`: with a key once per key (a future-dated row does not count),
    without a key the four-hour cooldown; `flush` remembers what it posted and prefixes late alerts;
    `Outbox.owner_unreachable(now)`; `v5.health --json` prints `owner_unreachable`.
37. `Executor`: `_records(skip_corrupt)`, `_commit_last` in `_final` and `_close`, `_no_order` commits before
    the final status, `_terminal` decides DONE or CAPPED from the order, `_resume` closes a refused record.
38. `ops`: `ENGINE_FAULT` blocker, the floor plan in `decide-now`, `init-peak --no-check`.
    `tools/v5_durum.py`: status from `health.assess`. `tools/v5_tatbikat.py`: `too_close()` and
    `ORDER_STEPS`; the clean-up skips the engine's own orders. `ops/vps/v5_watchdog.sh`: `DOCKER_DEAD`,
    `owner_unreachable`, the `/dev/shm` stamp.
