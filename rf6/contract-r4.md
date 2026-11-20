GOAL
Build rock R4 of RF-PLAN-6.md: rails (`v5/rails.py`), owner notifications with a durable outbox
(`v5/notify.py`), health (`v5/health.py`), operator commands (`v5/ops.py`) and the owner status tool
(`tools/v5_durum.py`). Done means the proof command passes with the tests below. Nothing touches the network:
the ntfy post function and the broker are injected.
The rails are the only protection of a small real-money account and the notifications are the owner's only
window into the bot (the owner is not a programmer and reads Turkish push messages on a phone): exact rules and
exact texts matter more than elegance.

SPEC
Read RF-PLAN-6.md sections DESIGN PRINCIPLES, RAILS, NOTIFICATIONS AND HEALTH, APPENDIX A and rock R4, and
RF6-VTO.md (the owner's numbers: package high, alert 37%, breaker 15%, floor 60%; they arrive through
`Settings.rails`). They are frozen and reviewed. Rocks R1 and R2 are in the repo and reviewed: read
`v5/config.py`, `v5/state.py`, `v5/broker.py`, `v5/strategy.py` and `tests_v5/fakebroker.py` in full first and
use them; do not change them (impossible -> answer `BLOCKED: <reason>`). Where this contract is more exact than
the plan (digest lines, `clear_halt` re-basing the peak, the cause table), this contract wins. The two ops
commands `decide-now` and `replay` named in the plan are NOT part of this rock (they move to R5).

Interfaces (fixed; the runner in R5 imports them):

1. `v5/rails.py`
   - `@dataclass(frozen=True) class EquityReading`: `time`, `equity`, `last_equity`, `cash`, `positions_value`,
     `ok` (bool), `problem` (None or one of `ACCOUNT_STATUS`, `BLOCKED`, `EQUITY_BAD`, `INCONSISTENT`,
     `BROKER_DOWN`). `read_equity(broker, now) -> EquityReading`: one `get_account` and one `get_positions`;
     `ok` iff the account status is "ACTIVE", neither `trading_blocked` nor `account_blocked`, equity is a
     finite number > 0, cash is finite, and `abs(equity - (cash + sum of position market values)) <=
     0.02 * equity`. The first failing check names the problem, in this order: status (`ACCOUNT_STATUS`),
     blocked flags (`BLOCKED`), equity or cash not finite or equity <= 0 (`EQUITY_BAD`), consistency
     (`INCONSISTENT`). It never raises on broker trouble (`BrokerError` -> a reading with `ok` False and
     `BROKER_DOWN`; numeric fields may then be None).
   - `class PeakNotInitialised(RuntimeError)`.
   - `class Rails(settings, state_dir, now_fn, sleep_fn=time.sleep)`: all state in ONE file `rails.json` inside
     `state_dir`, through `v5.state` (atomic; a corrupt file raises `StateCorrupt`, it is never replaced).
     Another process (an operator command) may change the file at any moment, so NO method may keep state in
     memory between calls: every read method reads the file; every changing method is read-modify-write while
     holding an inter-process lock on `rails.lock` (`v5.state.ProcessLock`, retried every 0.05 s through
     `sleep_fn` for at most 5 s, then `v5.state.LockBusy` is raised). Methods:
     * `peak() -> float | None`.
     * `init_peak(value, source, force=False)`: value must be a finite number > 0; a second call raises
       ValueError unless `force=True`; stores value, source and time.
     * `record_close(session_date, equity)`: appends `{session, equity}` to `equity_history` (one entry per
       session; a second call for the same session changes nothing) and, when a peak is initialised, raises
       it to `max(peak, equity)`. Equity must be finite and > 0 (ValueError otherwise). The peak never falls
       except through `clear_halt` / `init_peak(force=True)`.
     * `check_breaker(reading, session_date) -> bool`: a reading with `ok` False changes nothing. Otherwise
       baseline = `reading.last_equity`. If the baseline is not a finite number > 0, or a stored closing
       equity exists for the latest recorded session before `session_date` and differs from the baseline by
       more than 2% of the baseline: BREAKER_TODAY is set for `session_date` with cause `BASELINE_DOUBT`.
       Else if `equity <= (1 - breaker_day) * baseline`: BREAKER_TODAY is set with cause `BREACH`.
       Returns True iff BREAKER_TODAY is set for `session_date` (so it clears by itself: another date -> False).
       `breaker_info() -> dict | None` returns `{session, cause, equity, baseline, time}` of the stored state.
     * `check_alert(reading) -> float | None`: without a peak or with `ok` False -> None. With drawdown
       `dd = 1 - equity / peak`: returns `dd` exactly once per episode when `dd >= alert_dd`; the episode ends
       (re-arms) at the first passing reading with `dd < alert_dd - 0.05`.
     * `check_floor(reading) -> str`: returns `"OK"`, `"CONFIRMING"` or `"HALT"`. No peak -> raises
       `PeakNotInitialised`. State HALT -> "HALT" whatever the reading. A reading with `ok` False changes
       nothing and returns the current state. A passing reading breaches when `equity <= (1 - floor_dd) *
       peak`: from OK -> persisted CONFIRMING with the reading's time and equity; from CONFIRMING, when the
       reading is at least 60 seconds after the stored one -> persisted HALT (with `liquidating = True`, the
       equity and the time, in the same write), otherwise still CONFIRMING (stored time unchanged). A passing
       reading that does not breach while CONFIRMING -> back to OK.
     * `floor_state() -> str`; `liquidating() -> bool`; `set_liquidating(flag)` (the runner clears it once the
       account is verified flat).
     * `clear_halt(by, note, new_peak)`: only valid in state HALT or CONFIRMING (else ValueError). Sets the
       floor state to OK, `liquidating` False, the peak to `new_peak` (a finite number > 0; the operator
       passes the current account value: without re-basing the next reading would breach again at once), ends
       the alert episode, and appends `{time, by, note, old_peak, new_peak}` to `peak_events`.
     * `pause(by, note)`, `resume(by, note)`, `paused() -> bool` (each appends `{time, action, by, note}` to
       `pause_events`). Dates are stored as ISO strings (`equity_history` entries are
       `{"session": "2026-11-16", "equity": 512.3}`).
     * `state() -> dict`: the whole document (for health, the digest and `ops status`).
   - `reduce_only(decision) -> Decision` (pure, uses `dataclasses.replace` on `v5.strategy.Decision`): for a
     READY decision `core_new = min(core_new, core_held)`, `ovl_new = min(ovl_new, ovl_held)`, then
     `total_new = core_new + ovl_new`, `core_trade = abs(core_new - core_held) > 1e-12`,
     `trade = core_trade or abs(ovl_new - ovl_held) > 1e-12`, `overlay_fraction_new = ovl_new / total_new`
     (0.0 when `total_new` is 0); `reason_keys` kept, with `"BREAKER_REDUCE_ONLY"` appended when anything
     changed. A decision that is not READY is returned unchanged.
   - `floor_order_allowed(clock_ok, calendar_ok, market_open, seconds_to_close, unresolved,
     foreign_product_order) -> (bool, reason)`: False with the FIRST matching reason in this order:
     `CLOCK_DOUBT` (not `clock_ok`), `CALENDAR_MISSING` (not `calendar_ok`), `MARKET_CLOSED` (not
     `market_open`), `TOO_LATE` (`seconds_to_close < 120`), `UNRESOLVED`, `FOREIGN_PRODUCT_ORDER`; otherwise
     `(True, None)`. Nothing else blocks a floor sale.

2. `v5/notify.py`
   - Number formats (Turkish, `decimal.Decimal` with ROUND_HALF_UP on `repr` of the float):
     `fmt_money(1234.5) == "1.234,50"`, `fmt_money(512.3) == "512,30"`, `fmt_money(0.004) == "0,00"`
     (always the absolute value); `fmt_pct(0.0123) == "1,2"`, `fmt_pct(-0.0123) == "1,2"`,
     `fmt_pct(0.37) == "37,0"` (one decimal of the absolute percentage); `fmt_weight(0.094) == "9"` (whole
     percent, half up).
   - `REASON_SENTENCES`: the reason keys of Appendix A -> their sentences (exact strings), plus
     `"BREAKER_REDUCE_ONLY": "Bugün sert düşüş olduğu için yeni alım yapmadım."`.
   - `TEMPLATES`: every alert / reminder kind of Appendix A with its exact text (`DRAWDOWN_ALERT`, `BREAKER`,
     `FLOOR_DONE`, `FLOOR_PENDING`, `RAIL_LIQUIDATING`, `HOLD_NOT_READY`, `MISSED`, `ORDER_FAILED`,
     `ORDER_PARTIAL`, `UNRESOLVED`, `FOREIGN_ASSET`, `DATA_DOUBT`, `PAUSED`, `NOT_STARTED`, `WATCHDOG`), plus
     `READINESS`: "Bugün borsa açılmadan önce bir sorun gördüm: {cause}. Düzelmezse bugün işlem yapmayacağım."
     `ERROR`: "Bot beklenmeyen bir sorunla karşılaştı. Elimdekilere dokunmadım. Claude'a 'trading botuna bak' yaz."
     and `TEST`: "Deneme bildirimi: bu mesajı görüyorsan bildirimler çalışıyor."
     `build_alert` receives RAW values and formats them itself: `pct` (a fraction such as 0.371) with
     `fmt_pct`, `equity` (dollars) with `fmt_money`, `product_pct` (a weight 0..1) with `fmt_weight`;
     `symbol` and `cause` are inserted as given.
   - `CAUSE_SENTENCES` and `cause_for(code) -> str` (the `{cause}` phrase of `HOLD_NOT_READY` and `READINESS`):
     `PRICE_STALE`, `PRICE_BAD`, `PRICE_JUMP`, `DATA_ERROR` -> "fiyat bilgisine ulaşamadım";
     `HISTORY_SHORT`, `HISTORY_BAD`, `HISTORY_GAP`, `HISTORY_STALE`, `DERIVED_BAD` -> "geçmiş fiyatlar eksik geldi";
     `BROKER_DOWN` -> "Alpaca'ya bağlanamadım";
     `CALENDAR_MISSING`, `MARKET_CLOSED` -> "bugünün borsa saatlerini öğrenemedim";
     `CLOCK_DOUBT` -> "saat bilgisi tutarsız göründü";
     `STATE_CORRUPT`, `WEIGHT_BAD`, `FRACTION_BAD` -> "kayıt dosyam bozuk";
     `EQUITY_DOUBT`, `SNAPSHOT_INVALID`, `RAIL_CONFIRMING` -> "hesap değerini doğrulayamadım";
     `NOTIFY_MISSING` -> "bildirim kanalı tanımlı değil"; `PEAK_MISSING` -> "başlangıç değeri kayıtlı değil";
     `ACCOUNT_STATUS`, `BLOCKED` -> "Alpaca hesabı işleme kapalı görünüyor";
     any other code -> "beklenmeyen bir sorun çıktı".
   - `build_digest(ctx) -> str`: seven lines joined by "\n". `ctx` keys: `date` (datetime.date), `prefix`
     ("" | "paper" | "dry_run"), `equity`, `day_usd`, `day_pct` (signed floats; None when unknown),
     `product_weight` (0..1), `action` ("none" | "buy" | "sell" | "sell_all"), `action_usd`, `reason_keys`
     (list of keys, or None when no decision was made), `month_pct`, `total_pct` (signed fractions or None),
     `status_text` (None, or a ready sentence).
     line 1: prefix text + "Gün sonu özeti ({date})" with the date as 16.11.2026; prefix text is "" /
       "DENEME HESABI. " / "DENEME, gerçek emir gönderilmedi. ";
     line 2: "Hesap: {equity} dolar. " + "Bugün yüzde {p} arttı ({usd} dolar)." (day_usd > 0) |
       "Bugün yüzde {p} azaldı ({usd} dolar)." (day_usd < 0) | "Bugün değişmedi." (when `fmt_money(day_usd)`
       is "0,00" or the day change is None);
     line 3: "Elimde: yüzde {product_pct} Nasdaq fonu, yüzde {cash_pct} nakit." (whole percents; cash_pct =
       100 - product_pct);
     line 4: "Bugün ne yaptım: " + "İşlem yapmadım." | "{usd} dolarlık Nasdaq fonu aldım." |
       "{usd} dolarlık Nasdaq fonu sattım." | "Elimdeki fonun tamamını sattım.";
     line 5: "Neden: " + the sentences of `reason_keys` joined by one space (unknown keys are skipped; an
       empty result or `reason_keys` None -> "Bugün karar veremedim.");
     line 6: "Bu ay: yüzde {p} artıda." | "Bu ay: yüzde {p} ekside." | "Bu ay: değişim yok." then one space
       and "Başlangıçtan beri: yüzde {p} artıda." | "... ekside." | "Başlangıçtan beri: değişim yok."
       ("değişim yok" when `fmt_pct` gives "0,0" or the value is None);
     line 7: "Durum: Her şey normal." or "Durum: " + `status_text`.
     A missing key raises KeyError; an unknown `action` or `prefix` raises ValueError.
   - `build_alert(kind, **fields) -> str` from `TEMPLATES`; an unknown kind or a missing field raises KeyError
     (never a half-filled text); extra fields are ignored.
   - `class Outbox(state_dir)`: append-only `outbox.jsonl` in `state_dir`: one JSON object per line, flushed
     and fsynced per append; a message line `{id, time, kind, state_key, dedupe_key, text, priority}` and
     later event lines `{id, event: "attempt" | "delivered", time}`. `add(kind, text, now, state_key="",
     dedupe_key=None, priority=4) -> id` (a `dedupe_key` already present, delivered or not, returns the
     existing id and appends nothing); `pending(now) -> list` (undelivered, younger than 48 hours, and due:
     the n-th retry waits 1, 2, 4, 8, 16, 32, 60, 60 ... minutes after the previous attempt; a message with
     no attempt is due at once); `mark_attempt(id, now)`; `mark_delivered(id, now)`;
     `oldest_undelivered_minutes(now) -> float | None` (over messages younger than 48 hours);
     `last(kind) -> dict | None`; `corrupt_lines` (count). Corrupt lines are skipped, never fatal; the file is
     never rewritten.
   - `class Notifier(settings, outbox, post_fn, now_fn)`: `post_fn(url, data: bytes, headers: dict, timeout)
     -> int` (HTTP status); the module's `default_post` uses `requests.post` with a 10 s timeout.
     `configured` = the topic is not empty. Message kinds are the template names; the digest's kind is
     `DIGEST`. `alert(kind, state_key="", **fields) -> id | None`: builds the
     text; if a message with the same `(kind, state_key)` was added less than 4 hours ago it returns None
     and adds nothing; else adds it and calls `flush()`. `digest(session_date, ctx) -> id`: one per session
     (dedupe key `DIGEST:<date>:<prefix>`), priority 3, no cooldown. `reminder(kind, session_date, state_key="",
     **fields)`: one per (kind, state_key, session) (dedupe key), no cooldown. `flush() -> int`: tries every
     pending message that is due, oldest first; returns the number delivered. Delivery = one POST to
     `{ntfy_base_url}/{topic}` with the UTF-8 text as body and ASCII-only headers `Title: Trading bot`,
     `Priority` (the message priority: digest 3, alerts and reminders 4, `FLOOR_DONE`, `FLOOR_PENDING`,
     `RAIL_LIQUIDATING` and `NOT_STARTED` 5) and `Tags`. A status 200..299 marks it delivered; any other
     status or any exception from `post_fn` is recorded as an attempt and never propagates. Without a topic
     nothing is posted and messages stay pending. The URL (it contains the topic) is never logged or put in an
     exception message. No message is ever posted without first being in the outbox.

3. `v5/health.py`
   - `write_heartbeat(state_dir, payload)` (atomic, file `heartbeat.json`) and `read_heartbeat(state_dir) ->
     dict | None` (None when missing; a corrupt file counts as missing here).
     Payload keys written by the runner: `time` (ISO, UTC), `mode`, `phase`, `session`, `readiness`
     (`{ok, problems}`), `states` (list of active state names), `last_decision` (`{session, status}`),
     `outbox_oldest_minutes`, `ntfy_configured`, `orders_enabled`, `package`.
   - `assess(heartbeat, now) -> (level, lines)`: level 2 (blocked) if the heartbeat is missing, unreadable
     (not a dict, or without a parsable `time`) or older than 5 minutes (300 seconds old is still fresh), or
     a state in {`FLOOR_HALT`, `RAIL_LIQUIDATING`, `UNRESOLVED`, `NOT_STARTED`} is active;
     level 1 (degraded) for any other active state, readiness problems, `ntfy_configured` False, or an
     undelivered message older than 60 minutes; else 0. `lines` are short English lines for the operator.
   - `python -m v5.health [--json]`: locates the state dir with `v5.config.load_settings()`, prints the
     assessment and exits with the level. `--json` prints one JSON object with `level`,
     `heartbeat_age_seconds` (null when missing), `lines`, `heartbeat`. If settings cannot be loaded it
     prints the error class name and exits 2.

4. `v5/ops.py`: `python -m v5.ops <command>`: `init-peak <value> --source <text> [--force]`,
   `pause --by <name> --note <text>`, `resume --by <name> --note <text>`,
   `clear-halt --by <name> --note <text> --new-peak <value>`, `status` (prints `Rails.state()` and the health
   assessment as one JSON document on stdout, nothing else, no credentials). `main` returns / exits 0 on
   success and non-zero when a command is refused (for example a second `init-peak`, `clear-halt` with
   nothing to clear). Each state-changing command prints what it changed and appends `{time, command,
   arguments, result}` to `ops_log.jsonl` in the state dir. The command functions are importable
   (`cmd_init_peak(state_dir, settings, ...)` and so on) and take their dependencies as arguments, so tests
   never touch the network or the real environment; `main(argv=None)` wires them to `load_settings()`.
   Structure `main` so that R5 can register two more commands without rewriting it (for example a dict of
   command name -> (add_arguments, run)).

5. `tools/v5_durum.py`: prints the owner's status in plain Turkish from the state dir only (no broker call):
   line 1 "Bot çalışıyor." (heartbeat younger than 5 minutes) or "Bot çalışmıyor görünüyor.";
   line 2 "Son kalp atışı: {n} dakika önce." (whole minutes, rounded down) or "Kalp atışı kaydı yok.";
   line 3 "Durum: Her şey normal." or "Durum: " + the active state names joined by ", ", or
   "Durum: bilinmiyor." when there is no heartbeat;
   then "Son günlük özet:" followed on the next lines by the last digest text (`Outbox.last("DIGEST")`), or
   the single line "Henüz günlük özet yok.".
   `render(state_dir, now) -> str` (lines joined by "\n") is importable and tested; `main()` uses
   `load_settings()`.

Tests to write (injected clock, FakeBroker, a fake post function; no network, no real sleep):
- `tests_v5/test_r4_rails.py`: every rule above. In particular: a single bad or inconsistent reading never
  changes a state; two passing breaching readings 60 s apart -> HALT, 59 s apart -> still CONFIRMING; a recovery
  between them -> OK; HALT survives a new `Rails` object and only `clear_halt` ends it; after `clear_halt` with
  the current equity as the new peak the same equity no longer breaches; breaker: a breach sets BREAKER_TODAY,
  it is gone for the next session date, baseline doubt counts as breached, a bad baseline counts as breached;
  `reduce_only` on an overlay entry, a core increase, a core exit (passes through), a kept overlay, a mixed
  case (core exits while the overlay wants to enter -> everything sold) and a NOT_READY decision; alert once per
  episode with re-arming; the peak only rises; `init_peak` twice raises; no peak -> `PeakNotInitialised`;
  `floor_order_allowed` truth table and reason order; pause does not affect `check_floor`; a change made
  through a SECOND `Rails` object (another process) between two calls of the first is never lost and never
  overwritten (pause set by B survives A's `record_close`; HALT written by A is seen by B at once); a held
  `rails.lock` makes a changing call wait and then raise `LockBusy` (injected sleep, no real waiting); a
  corrupt `rails.json` raises `StateCorrupt` and is left untouched.
- `tests_v5/test_r4_notify.py`: snapshot tests of EVERY template and of the digest in its variants (up, down,
  flat day; bought, sold, sold all, no trade; no decision; paper prefix, dry-run prefix; an active state;
  month / total up, down, flat, unknown) against literal expected strings; number formats incl. rounding
  edges; outbox durability (a new Outbox on the same dir sees pending messages), backoff schedule, 48 hour
  expiry, dedupe keys, corrupt line tolerance, the file only ever grows; a failing post (bad status, exception)
  leaves the message pending and a later `flush` delivers it; cooldown per (kind, state_key); no topic ->
  nothing posted, `configured` False; headers are ASCII and the body is UTF-8; the topic never appears in a
  raised message; a simulated two-month blocked state (44 sessions) produces exactly one reminder per session
  and stops the session the cause is gone.
- `tests_v5/test_r4_health_ops.py`: `assess` levels for each condition; heartbeat round trip; `--json` output
  keys; each ops command on a temp state dir (init-peak, pause / resume, clear-halt with the new peak,
  status) and the `ops_log.jsonl` entries; `tools/v5_durum.py` `render` for a fresh, a stale and a missing
  heartbeat, with and without a digest (literal expected strings).

CONSTRAINTS
- Python 3.12, Windows and Linux. Standard library plus `requests` (only inside `default_post`).
- Every owner-facing string is exactly a string from RF-PLAN-6.md Appendix A or from this contract: do not
  rephrase, translate, add or remove characters (Turkish letters included). No technical words in them.
- Files you may create or change: `v5/rails.py`, `v5/notify.py`, `v5/health.py`, `v5/ops.py`,
  `tools/v5_durum.py`, new files `tests_v5/test_r4_*.py`. Nothing else. Never touch earlier rocks' files and
  tests, `tests_v5/fakebroker.py` (if a test needs an account that is not ACTIVE or an inconsistent equity,
  wrap or subclass FakeBroker inside your own test file), golden, fixtures, any `RF*.md`, `.gitignore`, or any
  v4 file.
- No network, no real sleeps, no em dash characters, English identifiers. Do not commit.

NON-GOALS
The runner loop that calls all of this (R5), `decide-now` / `replay`, Docker, the watchdog script, the
executor's order chain (R3).

PROOF
Run `python -m pytest tests_v5 -q -p no:cacheprovider --basetemp=.pytest_tmp_r4 --ignore-glob="tests_v5/test_r3_*"`
and include its full output in your report (another builder is working on rock R3 in the same folder at the
same time; its files are `v5/data.py`, `v5/executor.py`, `tests_v5/test_r3_*.py` and it may be editing
`tests_v5/fakebroker.py`: never read-depend on, run or touch its files; if a failure comes from a file of
theirs changing under you, wait a minute and run again).

OUTPUT
End with a report: files changed (one line each: path + what and why), the proof output, and any deviations
from the spec with reasons.
