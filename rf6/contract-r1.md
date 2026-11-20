GOAL
Build rock R1 of RF-PLAN-6.md: the foundation of v5 (settings, state files, session clock, broker gateway, fake
broker). Done means the files below exist, follow the interfaces given here, and the proof command passes with
the wire-level, contract, state and clock tests described below. Nothing in this rock places a real order or
touches the network.

SPEC
Read RF-PLAN-6.md (sections DESIGN PRINCIPLES, RUNNER, EXECUTOR "Submitting", rock R1) and RF6-PROBE.md (all of
it: the measured broker behaviour and the eight points it fixes). Both are frozen and reviewed. The recorded
broker responses are in `tests_v5/fixtures/alpaca/*.json` (each file: `request`, `status`, `body`). Implement
exactly. If a detail is impossible as written but the intent is unambiguous, implement the closest faithful
version and report the deviation. If the impossibility is MATERIAL, stop, output `BLOCKED: <reason>` as your
report, and wait. Do not redesign. Rock R2 (`v5/packages.py`, `v5/strategy.py`, `v5/backtest.py`, its tests) is
already in the repo: do not change it.

Interfaces (fixed; later rocks import them):

1. `v5/config.py` (standard library only)
   - `@dataclass(frozen=True) class RailNumbers`: `alert_dd: float`, `breaker_day: float`, `floor_dd: float`.
     `RAILS_BY_PACKAGE = {"moderate": (0.31, 0.12, 0.50), "high": (0.37, 0.15, 0.60), "very_high": (0.47, 0.20, 0.70)}`.
   - `@dataclass(frozen=True) class Settings`: `mode` ("live" or "paper"), `api_key`, `api_secret`,
     `trading_base_url`, `data_base_url`, `state_dir` (pathlib.Path, the v5 folder), `live_trading_enabled` (bool),
     `package` (str), `rails` (RailNumbers), `ntfy_topic` (str, may be empty), `ntfy_base_url`.
     `Settings.orders_enabled` property: True in paper mode; in live mode equal to `live_trading_enabled`.
     `repr(Settings)` must not show the key or the secret.
   - `load_settings(env=None, app_dir=None) -> Settings`: `env` defaults to `os.environ`, `app_dir` to the parent
     directory of the `v5` package. Rules (same env names as v4's `config.py` lines 16-88, read that file):
     * `TRADING_MODE`: "live" or "paper" (default "paper"; anything else -> ConfigError).
     * `ALPACA_KEY_PREFIX`: "", "LIVE", "PAPER" or "PAPER2". Keys: LIVE -> `ALPACA_LIVE_API_KEY` /
       `ALPACA_LIVE_SECRET_KEY`; PAPER -> `ALPACA_PAPER_API_KEY` / `ALPACA_PAPER_SECRET_KEY`; PAPER2 ->
       `ALPACA_PAPER2_*` falling back to `ALPACA_PAPER_*`; "" -> by mode (live -> LIVE keys, paper -> PAPER keys),
       each falling back to `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`.
     * Fail closed with ConfigError: mode live with a PAPER / PAPER2 prefix; mode paper with the LIVE prefix; an
       empty key or secret.
     * `trading_base_url`: live `https://api.alpaca.markets`, paper `https://paper-api.alpaca.markets`.
       `data_base_url`: `https://data.alpaca.markets`.
     * State dir: root = `STATE_VOLUME_PATH` if set, else `app_dir`; then `state_live` or `state_paper` by mode;
       then `v5`. `load_settings` does not create it.
     * `LIVE_TRADING_ENABLED`: true only for the exact values "1", "true", "yes", "on" (case-insensitive); default false.
     * `STRATEGY_PACKAGE`: default "high"; must be a key of `v5.packages.PACKAGES`, else ConfigError.
     * Rails: `RAILS_BY_PACKAGE[package]`, each overridable by `V5_ALERT_DD`, `V5_BREAKER_DAY`, `V5_FLOOR_DD`
       (floats strictly between 0 and 1, and `alert_dd < floor_dd`; otherwise ConfigError).
     * `NTFY_TOPIC` (default ""), `NTFY_BASE_URL` (default `https://ntfy.sh`).
   - `parse_env_file(path) -> dict`: minimal `KEY=VALUE` parser (ignores blank lines and `#` comments, strips
     optional quotes); used by tools, never called implicitly.
   - `class ConfigError(ValueError)`.

2. `v5/state.py` (standard library only)
   - `atomic_write_json(path, obj)`: write to a temporary file in the same directory, flush, `os.fsync`,
     `os.replace`; creates parent directories; never leaves a partial target file.
   - `read_json(path, default=...)`: returns `default` when the file does not exist (if no default is given a
     missing file raises `FileNotFoundError`); a file that exists but cannot be read or parsed raises
     `StateCorrupt(path)` and is left untouched (never replaced, never deleted).
   - `ensure_state_dir(path)`: creates it and proves it is writable by writing and removing a probe file;
     failure raises `StateDirError` with the path in the message.
   - `class ProcessLock(path)`: OS advisory lock held for the life of the process (`fcntl.flock` on POSIX,
     `msvcrt.locking` on Windows), non-blocking. `acquire() -> bool`, `release()`, context manager. The file
     content (pid, start time) is diagnostic only: a stale file from a dead process must NOT block `acquire`.
   - `session_record_path(state_dir, session_date, dry_run=False)`: `decisions/YYYY-MM-DD.json` or
     `decisions_dryrun/YYYY-MM-DD.json`.

3. `v5/clock.py` (standard library only, `zoneinfo`)
   - `ET = ZoneInfo("America/New_York")`.
   - `@dataclass(frozen=True) class Session`: `date` (datetime.date), `open`, `close` (timezone-aware datetimes in UTC).
   - `parse_calendar(rows) -> list[Session]`: rows as in fixture `calendar_window` (`date`, `open` "09:30",
     `close` "16:00" or "13:00", ET wall times); sorted, de-duplicated by date; a malformed row raises ValueError.
   - `class SessionCalendar(sessions)`: `get(date) -> Session | None`; `previous(date, n) -> list[datetime.date]`
     (the last n session dates strictly before `date`, ascending; fewer if not available); `to_json()` /
     `SessionCalendar.from_json(obj)` round trip for the state cache; `covers(date) -> bool`.
   - `@dataclass(frozen=True) class Window`: `decide_from` (close - 10 min), `decide_until` (close - 4 min),
     `submit_until` (close - 3 min), `cancel_at` (close - 90 s), `reconcile_from` (close + 60 s),
     `reconcile_until` (close + 5 min). `window_for(session) -> Window`.
   - `clock_skew_seconds(broker_timestamp, local_now) -> float` (broker timestamp as in fixture `clock`, with
     nanoseconds and a UTC offset; must parse nine fractional digits).

4. `v5/broker.py` (standard library + `requests`)
   - Exceptions: `BrokerError`; `BrokerUnavailable(BrokerError)` (transient: timeout, connection error, 429, 5xx
     after the retries); `AmbiguousSubmit(BrokerUnavailable)` (a POST whose outcome is unknown);
     `BrokerRejected(BrokerError)` with `status`, `code` (int or None), `message`, `body`.
   - Frozen dataclasses (money parsed to float, quantities kept as the broker's strings):
     `Account(status, equity, last_equity, cash, buying_power, trading_blocked, account_blocked)`;
     `Position(symbol, qty, qty_available, market_value, current_price, asset_class)`;
     `Order(id, client_order_id, symbol, side, type, time_in_force, qty, notional, filled_qty, filled_avg_price,
     status, submitted_at, filled_at)` (`qty`, `notional` str or None; `filled_qty` str; `filled_avg_price`
     float or None; timestamps as timezone-aware datetimes or None);
     `BrokerClock(timestamp, is_open, next_open, next_close)`; `Bar(t, o, h, l, c, v)`; `Trade(t, price)`.
   - `TERMINAL_STATUSES = {"filled", "canceled", "expired", "rejected", "replaced", "done_for_day", "calculated"}`;
     `is_terminal(status) -> bool` (an unknown status is NOT terminal).
   - `class Broker(typing.Protocol)` with exactly these methods:
     `get_account()`, `get_positions()`, `get_open_orders()`,
     `list_orders(after, symbol=None) -> (list[Order], complete: bool)` (ONE call, `status=all`, `limit=500`,
     `direction=asc`, `nested=false`; `complete` is False when 500 rows came back; rows de-duplicated by id),
     `submit_order(symbol, side, client_order_id, notional=None, qty=None) -> Order` (market, time in force day;
     exactly one of `notional` / `qty`, given as str: notional with at most 2 decimals, qty with at most 9; a
     violation raises ValueError before any HTTP call),
     `cancel_order(order_id)` (204 is success; a 404 or 422 for an order that is already terminal is success),
     `get_order(order_id)`, `get_order_by_client_id(client_order_id) -> Order | None` (None exactly on 404 with
     code 40410000), `get_clock()`, `get_calendar(start, end) -> list[dict]`,
     `get_daily_bars(symbol, start, adjustment="all", feed="sip") -> list[Bar]` (follows `next_page_token`),
     `get_minute_bars(symbol, start, feed="iex") -> list[Bar]` (follows `next_page_token`),
     `get_latest_trade(symbol, feed="iex") -> Trade | None`.
   - `class AlpacaBroker(settings, transport=None, sleep=time.sleep)`: `transport(method, url, params, json_body,
     timeout) -> (status_code, text)` is injectable; the default uses `requests` with a 15 s timeout and the two
     auth headers. Rules: every data call sends an explicit `feed`; GET calls retry on transport exceptions, 429
     and 5xx with pauses 0.5, 1, 2, 4 s (through the injected `sleep`), then raise `BrokerUnavailable`; POST and
     DELETE are attempted exactly once; a POST that ends in a transport exception, 429 or 5xx raises
     `AmbiguousSubmit`; any other 4xx raises `BrokerRejected` with the broker's `code` and `message`; a 2xx body
     that cannot be parsed raises `BrokerError`. The key and secret never appear in an exception message or log.

5. `tests_v5/fakebroker.py`: `class FakeBroker` implementing `Broker` in memory, reproducing RF6-PROBE.md:
   - State: cash, positions (Decimal quantities), prices per symbol (settable), orders, a controllable clock and
     calendar, `last_equity`.
   - A market order is `pending_new` in the answer of `submit_order` and fills at the symbol's current price
     (plus an optional adverse move in bps) the next time any method is called, unless a scripted fault says
     otherwise. Notional buys fill `floor(notional / price, 9 decimals)` shares; equity = cash + sum(qty x price).
   - Rejections exactly as recorded: duplicate client id (422, code 40010001, "client_order_id must be unique");
     notional under 1.00 (422, 42210000, "notional amount must be >= 1.00"); notional with more than 2 decimals
     (422, 42210000); buy above the cash (403, 40310000, message starting "insufficient buying power"); sell
     above the available quantity (403, 40310000, message starting "insufficient qty available for order").
   - `get_order_by_client_id` returns None for an unknown id; `get_positions` omits flat symbols; quantities are
     strings with at most 9 decimals.
   - Scripted faults (a queue the test fills): `timeout_before_accept`, `timeout_after_accept` (both raise
     `AmbiguousSubmit`; the second one did create the order), `http_429`, `http_5xx` on GET (raise
     `BrokerUnavailable`), `invisible(n)` (a just-accepted order is invisible to the next n lookups), `partial(f)`
     (fills fraction f, then stays open until cancelled), `never_fill`, `reject(status, code, message)`.
   - It records every call (method, arguments) so tests can assert that no duplicate order was created.

Tests to write (no network, deterministic):
- `tests_v5/test_r1_wire.py`: for EVERY `AlpacaBroker` method, a stub transport that returns the matching
  fixture; assert the exact HTTP method, URL path, query parameters and JSON body that were sent, and the parsed
  result (for example: `buy_notional_20_TQQQ` -> Order with status "pending_new", notional "20", qty None;
  `get_by_client_order_id_unknown` -> None; `duplicate_client_order_id` -> BrokerRejected code 40010001;
  `reject_too_large` -> BrokerRejected code 40310000; `position_TQQQ_after_exit` handled by `get_positions`
  semantics; `daily_bars_QQQ_sip_all` -> 291 bars, last bar dated 2026-11-16; `calendar_window` -> rows;
  `clock` -> BrokerClock with `is_open` True and a parsed nanosecond timestamp; `list_orders_all_today` ->
  10 orders, complete True; a synthetic 500-row answer -> complete False). Retry rules: GET retried on 429 / 5xx /
  exception with the recorded pauses and then `BrokerUnavailable`; POST never retried and mapped to
  `AmbiguousSubmit`; the secret never appears in any exception text.
- `tests_v5/test_r1_contract.py`: one set of behavioural assertions run against FakeBroker and, where a fixture
  exists, against AlpacaBroker with the stub transport: duplicate id, unknown id, minimum notional, precision
  validation, insufficient buying power, sell above available, cancel of a terminal order, statuses.
  FakeBroker only: fill arithmetic (cash, quantity, equity), each scripted fault, call recording.
- `tests_v5/test_r1_config.py`: every rule of `load_settings` including each fail-closed case and `repr` hiding
  the secret; `parse_env_file`.
- `tests_v5/test_r1_state.py`: atomic write (a simulated crash between the temporary write and the replace leaves
  the old content), `read_json` on a missing / corrupt / valid file, `ensure_state_dir` on an unwritable path,
  `ProcessLock` exclusion tested with a real second process (subprocess) and release when that process dies.
- `tests_v5/test_r1_clock.py`: `parse_calendar` on the fixture (including an early close row you add as a
  literal), `previous`, JSON round trip, `window_for` on a normal and an early-close session across a daylight
  saving change, `clock_skew_seconds` on the fixture timestamp.

CONSTRAINTS
- Python 3.12 on Windows (development) and Linux (production): both must pass. Dependencies: standard library
  and `requests` only in `v5/`; tests may also use `pytest`.
- Do not create, modify or delete anything outside `v5/` and `tests_v5/`. Never touch `tests_v5/golden/*`,
  `tests_v5/fixtures/*`, the R2 files and tests, any `RF*.md`, `.gitignore`, or any v4 file.
- No network access in code paths exercised by tests. No em dash characters. English identifiers and comments.
- No real credentials anywhere; tests use dummy keys.
- Do not commit.

NON-GOALS
Decision data assembly, executor, rails, notifications, runner, health, ops, Docker (rocks R3 to R5). No order
logic beyond the single `submit_order` call.

PROOF
Run `python -m pytest tests_v5 -q -p no:cacheprovider --basetemp=.pytest_tmp` and include its full output in
your report.

OUTPUT
End with a report: files changed (one line each: path + what and why), the proof output, and any deviations
from the spec with reasons.
