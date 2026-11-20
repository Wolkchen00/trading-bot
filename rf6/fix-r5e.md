# Fix round R5e: defects found by the six-reviewer sweep of the v5 engine (bug fixes, with tests)

You are the builder. I am the reviewer: I will read your whole diff, run the proof myself and run my own tests
written from this text. Work in this repository. Do not commit. Do not open `.env`. No network is needed.

## Background (read these parts only)

- `RF-PLAN-6.md`: Revisions 11 and 12 at the top, and APPENDIX A (every sentence the owner can receive).
- `rf6/fix-r5d.md` and `rf6/fix-r5d-2.md`: the round before this one (already built and committed).
- `rf6/contract-r5.md`: the "AS BUILT" notes at the end (how `v5/runner.py` works).
- The owner is not technical. Every message must be literally true.

## Rules

- Files you may edit: `v5/runner.py`, `v5/executor.py`, `v5/rails.py`, `v5/notify.py`, `v5/ops.py`,
  `v5/health.py`, `v5/broker.py`, `tools/*.py`, `tests_v5/fakebroker.py` (only additions an item names)
  and ONE new test file `tests_v5/test_r5e_duzeltme.py`.
- Do not touch `v5/strategy.py`, the golden files or the plan documents. An existing test may only be changed
  where it asserts the OLD behaviour of exactly one of the items below: change that assertion and name it in
  your report.
- If an item contradicts the plan in a way that matters, answer `BLOCKED:` with the reason. Do not guess.
- Keep the style of the surrounding code. No new dependencies.
- Each new test must fail on the code as it is now and pass after your change. Reuse the helpers of
  `tests_v5/test_r5_saldirgan.py` (`Sim`, `window`, `D1`, `D2`, `D3`) and of `tests_v5/test_r5b_inceleme.py`
  (`break_call`, `Death`, `not_sent`, `rows`) by importing them.
- Order of work, the most important first; run your new tests after each item, so that an interrupted
  session still leaves complete items behind:
  H1, J1, M1, H3, H4, I3, L1, I1, I2, I4, H2, K1, I9, I8, I5, I6, I7, I10, J2, J3, J4, K2, K3, L3, L2, L4,
  L6, H5, H6, I11, J5, L5, L7.
- If an item's scenario no longer fails on the code as it is, say so in the report and add only its test.
- The reviewers' own scripts for every item are in `C:/Users/ihsan/trading_lab_v5/wf_current/wf_review/`
  (folders `rails-time`, `truth`, `silence`, `books`, `state-ops`, `orders`; each script names its finding). They are a reference
  only: read them for the exact scenario, do not copy them into the repository and do not edit them.
- Proof at the end (about ten minutes; in a restricted sandbox the few tests that need a POSIX shell or a
  system temp folder may fail for that reason alone, say so):
  `python -m pytest tests_v5 -q -p no:cacheprovider --basetemp=.pytest_tmp_codex`

---------------------------------------------------------------------------------------------------------------

## Group H. The safety limits and time (reviewer "rails-time"; every item was reproduced on the code as it is)

### H1. One reading must never move the peak (CRITICAL)

Today `_after_close` stores the single on-time reading as the session's closing value and
`Rails.record_close` does `peak = max(peak, value)`. One absurd print in that one reading (the fund at 6.2 times
its price for one tick) raises the peak for good; on the next session the floor halts on TRUE readings and sells
everything. The floor needs two readings to halt; the peak must not be weaker than that.

Required design. A closing value is PROVISIONAL while only the engine's own reading says it. The peak moves
only when the broker's official close says it too (or alone, when the engine has no reading of its own).

1. `Rails.record_close(session_date, equity, raise_peak=True, current_equity=None)`.
   - `raise_peak=False`: the row is appended as today, with `"provisional": true`, and `peak` is not touched.
   - `raise_peak=True`: as today, but through the sanity bound of point 4 when `current_equity` is given.
   - The return value says what happened: `"stored"`, `"stored_peak_refused"` (the row was written, the bound
     refused the peak) or `None` (nothing written: an equal or newer row exists).
2. `Runner._after_close`: the on-time branch (`not late`) calls `record_close(day, close_equity,
   raise_peak=False)`. The late branch that stores the broker's `last_equity`, and `_backfill_close`, pass
   `raise_peak=True, current_equity=reading.equity`.
3. New `Rails.confirm_close(session_date, official, named, current_equity)`; the runner calls it in
   `_watch_breaker`, after `_backfill_close` and before `check_breaker`, for `previous = the session before
   this one`, whenever the reading is trusted, `reading.last_equity` is positive and
   `last_equity_is_close_of(reading, previous) is not False` (`named` is `True` when that call returned `True`).
   Only a row of exactly that session that is still provisional is looked at:
   - the row and `official` agree within 2 % of `official`: the row's `equity` becomes `official`,
     `provisional` is removed and the peak is raised to `max(peak, official)` through the bound of point 4.
     Returns `"confirmed"` (or `"confirmed_peak_refused"`).
   - they differ and `named` is True (the broker itself says the number is that session's close): the row's
     `equity` becomes `official`, the old value is kept as `"corrected_from"`, `provisional` is removed, the
     peak is NOT raised by this session. Returns `"corrected"`.
   - they differ and `named` is False: nothing changes (the existing BASELINE_DOUBT path tells the owner).
     Returns `None`. No row, or a row that is not provisional: `None`.
4. The sanity bound, used by every raise of the peak from a closing value: a value may raise the peak only if
   `current_equity > (1.0 - settings.rails.alert_dd) * value`. A raise that would by itself put the account at
   the alert level (and all the more below the floor) cannot be a real day-to-day change. When the bound
   refuses, the peak stays as it is.
5. The runner's messages: on `"corrected"` send the new alert `CLOSE_CORRECTED` once (state key: that session's
   date) with the template "Dünkü özetteki hesap değeri yanlış okunmuş. Doğru kapanış değeri {equity} dolar."
   (add it to `TEMPLATES`; default priority; it is one more known sentence for
   `tests_v5/test_r5_saldirgan.py::assert_only_known_sentences` and the template pin in
   `tests_v5/test_r4_saldirgan.py`). On `"stored_peak_refused"` or `"confirmed_peak_refused"` send
   `DATA_DOUBT` (state key: today's date).
6. Accepted limit, write it as a comment at `confirm_close`: a provisional row older than the previous session
   is never confirmed and never raises the peak (the broker no longer reports that close); the floor is then
   measured from a slightly lower peak, never from a higher one.

Tests (in `tests_v5/test_r5e_duzeltme.py`):
- The reviewer's scenario: D1 normal, the engine buys one third; in the one after-close reading the fund prints
  at 6.2 times its price (`sim.fb.set_price(PRODUCT, "310")` at `w1.reconcile_from + 10 s`, back to "50" one
  minute later); D2 the account is worth 490 all day. Required: the peak is still 490.0 after D1, on D2 no
  order is sent, the floor state is "OK" at the end of D2.
- The same with the broker naming the date (`sim.fb.balance_asof = D1` on D2, `last_equity` 490): the row of
  D1 becomes 490.0, exactly one `CLOSE_CORRECTED` message, no `DATA_DOUBT`, the daily brake is not active.
- A normal rising day: D1 closes at 520 on time (provisional, peak unchanged that evening); on D2's first
  trusted reading with `last_equity` 520 the row is confirmed and the peak is 520.
- The bound: `record_close(day, 1359.0, raise_peak=True, current_equity=490.0)` writes the row and leaves the
  peak; `confirm_close` with an official value of 1359 against a provisional 490 and `named=False` changes
  nothing.
- Existing tests that expect the peak to rise on the evening of the same day now see it rise at the next
  session's first trusted reading: change those assertions and list them.

### H2. A doubt about the baseline is not a fall (HIGH)

`_watch_breaker` returns True for the causes BREACH and BASELINE_DOUBT alike, and everything behind it says
"Bugün sert düşüş olduğu için yeni alım yapmadım." even on a day the account rose.

Required: carry the cause.
- `reduce_only(decision, doubt=False)`: with `doubt=True` the added key is `DOUBT_REDUCE_ONLY` instead of
  `BREAKER_REDUCE_ONLY`; new sentence in `REASON_SENTENCES`:
  "Hesap değerini doğrulayamadığım için bugün yeni alım yapmadım."
- The runner asks `rails.breaker_info()` for the cause of today's entry: `doubt = cause != "BREACH"`.
- `Executor.run_chain(..., buys_blocked=...)` takes the hold code instead of a flag: `"BREAKER"` for a breach,
  `"EQUITY_DOUBT"` for a doubt, falsy for none; the two places that write `record.data["hold"] = "BREAKER"`
  write that code. (`CAUSE_SENTENCES["EQUITY_DOUBT"]` already reads "hesap değerini doğrulayamadım".)
Tests: a D2 with cause BASELINE_DOUBT (stored close of D1 differs from `last_equity` by 3 %, the broker names
no date) and a rule that wants a buy: the summary's reason line has the new sentence and not the "sert düşüş"
one; a chain under way that is stopped by the doubt reports the cause "hesap değerini doğrulayamadım". The
breach case keeps today's words.

### H3. A doubtful clock books no closing value (MEDIUM)

`_after_close` never looks at `self.clock_ok` or `self.market_open`. With a host clock 30 minutes ahead it
stores a mid-session value as the close, sends the day's summary early and marks the session closed.

Required, at the top of `_after_close` (after the two early returns; J1 below already stops every booking
in a tick whose clock was measured as wrong, this item covers the rest):
- `self.clock_ok is not True and self.market_open`: return False (nothing is booked or sent; the step is
  tried again on the next tick).
- `self.clock_ok is not True` and the market is not open: go on, but treat the closing value like the late
  case that has no broker number: `close_equity = None` (no row; the summary says "Hesap şu an").
Test: the reviewer's scenario (host clock 30 minutes ahead on D1 while the broker says open; the clock is set
right at true 15:40): no row for D1 and no summary before the real close; after the real close one summary
and a row equal to the true closing equity.

### H4. A reading is stamped when it is taken (MEDIUM)

`tick()` reads the host time once and every `read_equity(self.broker, now)` stamps the reading with it. A slow
call earlier in the same tick makes two readings that are 20 s apart look 65 s apart: the floor's "at least 60
seconds apart" rule is then met by readings that are not.

Required:
- every `read_equity(self.broker, now)` in `v5/runner.py` becomes `read_equity(self.broker, self.now_fn())`;
- `_clock_step`: read the host time again after `get_clock()` returned; the clock is fine when the broker's
  timestamp lies between `before - 60 s` and `after + 60 s`;
- `_floor_flow` and `_floor_block` compute the seconds to the close from `self.now_fn()`.
Tests: the reviewer's scenario (the clock call of one tick takes 45 s through a wrapped `get_clock` that
advances `sim.clock`; an absurd low print lasts 30 s): no halt, no order. A clock call that takes 70 s with
both clocks in agreement leaves `clock_ok` True.

### H5. A lifted halt is honoured at once (LOW)

- `Rails.set_liquidating(True)` returns False and writes nothing unless `floor_state` is `"HALT"` (decided
  inside the lock); `set_liquidating(False)` works as today and returns True. `_floor_flow` and
  `_settle_floor` stop when it returns False: no order, no message.
- `_floor_done` sends `FLOOR_DONE` only while `floor_state` is `"HALT"`.
- A safety sale that is still working when the halt is lifted is cancelled at once: when the block code of a
  floor chain is `"HALT_CLEARED"`, the executor cancels the chain's open order in that call (the path the
  cancel time uses) and books what was filled.
Tests: (a) `clear-halt` lands between the engine's look at the rail and its order (wrap `Rails.liquidating` or
`_snapshot` to run `rails.clear_halt(...)` once): no order is sent, no `FLOOR_DONE`, the rail is OK and not
liquidating. (b) the sale is accepted and not filled (`never_fill`), then `clear-halt`: on the next tick the
order is cancelled (one cancel call) and never fills.

### H6. The daily brake never sells by itself (LOW)

`reduce_only` recomputes `core_trade` from the numbers; when the fund has drifted above the cap the rule's
"no trade" becomes a small sale. Required:
`core_trade = decision.core_trade and abs(core_new - decision.core_held) > 1e-12` and
`trade = decision.trade and (core_trade or abs(ovl_new - decision.ovl_held) > 1e-12)`.
Test: a READY decision with `trade=False`, `core_held=1.0227`, `core_new=0.99` stays `trade=False` and
`core_trade=False` through `reduce_only`; a decision whose rule wanted a reduction is unchanged; a decision
that wanted a buy becomes no trade with the reduce-only key.

---------------------------------------------------------------------------------------------------------------

## Group I. What the owner is told (reviewer "truth"; every item still reproduces on the code after R5d)

New or changed sentences go into `v5/notify.py`; list each of them in your report so that Appendix A of the
plan can be brought up to date by the reviewer. Tests that pin an old sentence change with it (name them).

### I1. "Everything sold" is only said about the fund (CRITICAL)

`FLOOR_DONE` says "Her şeyi sattım ve durdum" while the account still holds the owner's own share (ANT) or a
foreign position, and also when the account held no fund at all and nothing was sold.
- `FLOOR_DONE` becomes: "Hesap en yüksek değerinden yüzde {pct} düştü, şu an {equity} dolar. Elimdeki Nasdaq
  fonunun tamamını sattım ve durdum. Devam etmemi istersen Claude'a 'botu devam ettir' yaz."
- New `FLOOR_FLAT`, used instead when no floor order of this halt was filled: "Hesap en yüksek değerinden
  yüzde {pct} düştü, şu an {equity} dolar. Elimde Nasdaq fonu yoktu, bu yüzden satış yapmadan durdum. Devam
  etmemi istersen Claude'a 'botu devam ettir' yaz."
- The same two forms in the status line of later summaries.
Tests: account 150 cash + 250 fund + 100 ANT (`V5_OWNER_SYMBOLS=ANT`), peak 1300: the message is the new
`FLOOR_DONE`; cash + ANT without any fund at the halt: `FLOOR_FLAT`, no order.

### I2. A sale that has not started is not called started (CRITICAL as reported; a wording gap in the plan)

`FLOOR_PENDING` ("Satışa başladım ama henüz bitmedi ...") and `RAIL_LIQUIDATING` are sent when
`floor_order_allowed` refused and no sell order exists, and by a dry-run process that can never sell.
- New `FLOOR_WAITING`: "Hesap en yüksek değerinden yüzde {pct} düştü, şu an {equity} dolar. Güvenlik satışını
  henüz başlatamadım, çünkü {cause}. Şu an elimde yüzde {product_pct} Nasdaq fonu var. Denemeye devam
  ediyorum."
- `{cause}` by block reason: CLOCK_DOUBT "saat bilgisi tutarsız göründü"; CALENDAR_MISSING "bugünün borsa
  saatlerini öğrenemedim"; MARKET_CLOSED "borsa şu an kapalı"; TOO_LATE "kapanışa çok az kaldı"; UNRESOLVED
  "önceki emrimin akıbeti hala belli değil"; FOREIGN_PRODUCT_ORDER "hesapta benim vermediğim bir Nasdaq fonu
  emri var"; BROKER_DOWN "Alpaca'ya bağlanamadım"; SNAPSHOT_INVALID "hesap değerini doğrulayamadım"; a dry
  run: "gerçek emir gönderme kapalı".
- `FLOOR_PENDING` and `RAIL_LIQUIDATING` are only used when a floor intent of this halt has
  `submit_count > 0`; otherwise `FLOOR_WAITING` (alert, same once-a-day key as today, and status line).
Tests: the reviewer's three cases (broker clock 95 s off; a foreign open sell order in the fund; a live process
with the flag off): zero submits and the new sentence with the right cause; a sale that was sent and did not
fill keeps `FLOOR_PENDING`.

### I3. A day with a real order is never reported as a dry run (CRITICAL)

After `LIVE_TRADING_ENABLED` is removed and the container recreated on a day that already has a real order,
the dry process loads only dry records: the summary carries "DENEME, gerçek emir gönderilmedi." and says
"İşlem yapmadım".
- In `_after_close`, `_digest_context` and `_cutoff` a dry process also loads the non-dry `dec` and `flr`
  records of that session. When one of them has an intent with `submit_count > 0`: action, outcome, reasons
  and status are built from that record, the summary's prefix is the one of a real run (`""` for live), and
  `_cutoff` does not write MISSED for that day. An open real record is settled by housekeeping as today
  (`Executor.reconcile` must accept a real record in a dry process for that purpose only: it never submits).
- `_decision_step` of a dry process takes no dry decision for a day whose real strategy record exists.
Tests: flag on, the engine buys at 15:50 (DONE); a new Runner on the same state with the flag off at 15:51
and, second case, at 15:57: one summary without the dry-run label that names the purchase; no MISSED alert.

### I4. ERROR claims nothing about holdings and is told once a day (CRITICAL as reported, plus silence 8)

- `ERROR` becomes "Bot beklenmeyen bir sorunla karşılaştı. Claude'a 'trading botuna bak' yaz." (the sentence
  "Elimdekilere dokunmadım." is removed: nothing verifies it).
- Its state key carries the New York date: one push per day while a fault lasts (today: every four hours,
  day and night).
Tests: a state file breaks after the day's buy was booked: the alert text has no "dokunmadım"; a fault from
Friday 18:00 to Monday 09:00 ET gives one ERROR per calendar day.

### I5. A late summary shows the holdings as "now" (HIGH)

R5d made line 4 come from the session's record. Line 3 still shows the holdings of the moment under
"Elimde:" (after the next day's order, for example "yüzde 0 Nasdaq fonu" for a day that ended with 34 %).
Required: whenever the summary is produced late (`late` is passed into `_digest_context`), line 3 starts with
"Elimde şu an: ", whatever line 2 says.
Test: the reviewer's scenario (D1 sells part; the process is away until 15:50:40 of D2, which sells the
rest; the summary of D1 follows): line 3 starts with "Elimde şu an: ", line 4 names D1's own sale.

### I6. Yesterday's extra part is not called a purchase (HIGH)

`OVERLAY_EXIT` ("Dünkü ek alımı sattım.") and `OVERLAY_KEEP` ("... dünkü ek alımı bir gün daha tutuyorum.")
are printed on the days after an overlay that was created without any buy (a re-assignment, or a refused
order). New wording, true in both cases:
- `OVERLAY_EXIT`: "Dünkü düşüş için tuttuğum ek payı sattım."
- `OVERLAY_KEEP`: "Düşüş sürdü, ek payı bir gün daha tutuyorum."
Test: the reviewer's case (c) (D1 NETTED, D2 exits): no summary contains "ek alımı".

### I7. A partly filled order replaces the reasons (MEDIUM)

When the day's strategy record ends PARTIAL, line 5 still gives the decision's reasons as facts ("ana para
nakitte bekliyor" with 20 % still in the fund). New outcome `PARTIAL` in `OUTCOME_SENTENCES`:
"Karar verdim ama emrimin yalnız bir kısmı gerçekleşti." It replaces the reasons like the other outcomes.
Test: a trend break whose exit fills 40 % before the cancel time: line 5 is "Neden: " plus the new sentence.

### I8. The shares line states what the account holds (HIGH; also reviewer "books" 4)

When the engine's snapshot fails at summary time the fallback uses `reading.positions_value` for the fund:
the owner's ANT is counted as Nasdaq fund. A foreign position is shown as cash.
- The fallback computes the fund's share from the product position only and the owner's share from the
  owner symbols (one `broker.get_positions()`), and derives `flat` from the product quantity.
- The cash share is `cash / account equity`. When fund + owner + cash is below 99 % of the account, line 3
  names the rest: "..., yüzde {other_pct} benim almadığım varlık, yüzde {cash_pct} nakit."
Tests: ANT 21 %, fund 27 %, the open-orders call fails after the close: "yüzde 27 Nasdaq fonu, yüzde 21 senin
aldığın hisse (ANT), yüzde 52 nakit"; a foreign SPY position of 38 %: the line names it and cash is 62 %.

### I9. CAPPED is not "my order did not fill" (MEDIUM; reviewer "books" 1 and "truth" 9)

While the owner's own resting order reserves buying power the record ends CAPPED every day; the status line
then says "Emrimin tamamı gerçekleşmedi" although the order filled completely as sized, or although no order
was sent at all.
- `ORDER_PARTIAL` (alert and status line) only when an order of the day was sent and did not fill completely.
- CAPPED, every sent order filled completely: status line `CAPPED_FILLED`, "Hedefin tamamı için boş nakit
  yetmedi; alabildiğim kadarını aldım." No alert.
- CAPPED and no order was sent (the last plan has kind "none"): line 4 "İşlem yapmadım.", line 5 the new
  outcome `NO_CASH` "Karar verdim ama alım için boş nakit yoktu.", status line `CAPPED_IDLE` "Hedefin tamamı
  için boş nakit yok." No alert.
Tests: 490 cash, a resting owner buy order reserves 100, target 0.99: day 1 buys 388.05 and its status line
is `CAPPED_FILLED`; day 3 sends nothing and shows the `NO_CASH` outcome; no alert on any day.

### I10. A message delivered late says when it was written (MEDIUM; also silence 3)

Alerts say "bugün" and are retried for 48 hours: Monday's breaker message can arrive on Tuesday unchanged.
Required, in `Notifier.flush`: an alert (not a summary) whose first successful post happens more than 60
minutes after it was written is sent with the prefix "Gecikmiş mesaj ({date}): " in front of its text (after
the account label), `{date}` being the New York date it was written, as 16.11.2026.
Test: posts fail from Monday 09:30 ET until Tuesday 08:45 ET, Monday is a breaker day: the BREAKER text that
arrives on Tuesday starts (after the label) with "Gecikmiş mesaj (16.11.2026): ".

### I11. The status tool does not say "normal" unless health does (LOW; also silence 5)

`tools/v5_durum.py` prints "Bot çalışıyor." / "Durum: Her şey normal." for a blocked account, readiness
problems, an undelivered owner message and a missing topic. Required: `render()` calls `v5.health.assess`;
the two sentences are printed only for level 0 with a fresh heartbeat; otherwise the status line is
"Durum: Normal değil: " followed by the state and readiness codes, comma separated.
Tests: one per case of the reviewer's script (`readiness.problems = ["ACCOUNT_STATUS"]`; an owner message
undelivered for 26 hours; `ntfy_configured` false with an empty state list).

---------------------------------------------------------------------------------------------------------------

## Group J. Silence (reviewer "silence")

### J1. A tick whose clock disagrees with the broker's does no bookkeeping (CRITICAL)

One tick with the host clock ahead (a server whose clock is wrong at boot until it is stepped back) closes
sessions that have not happened: MISSED, a dated summary, `closed_sessions` and `started_at` move forward, and
the real sessions then pass without a decision or a summary. In that same tick `_clock_step` has already
measured the disagreement.
- `_clock_step` remembers how the doubt arose: `self.clock_skewed = True` when the broker's timestamp was
  read and lies outside the allowed range (see H4), `False` when the clocks agree or the broker clock could
  not be read. While `self.clock_ok is not True` the broker clock is asked again on every tick.
- `_session_step`: while `self.clock_skewed` is True nothing is booked in that tick: no `started_at`, no
  `_cutoff`, no `_after_close`, no `_catch_up`, no `_no_calendar_cutoff`, no decision. The heartbeat still
  shows CLOCK_DOUBT. With an unreadable broker clock today's behaviour stays (and H3 applies).
- `_load_state` / the first write of `started_at`: a `started_at` later than the present moment is never
  written.
- `Notifier.alert`: a cooldown row dated in the future does not silence a later alert.
Tests: Monday 12:00 ET, one tick reads 19:00 while the broker clock says 12:00, the next tick is right again:
no record, no summary, `closed_sessions` unchanged, and the engine decides at 15:50 as usual. The first tick
ever reads three days ahead: `started_at` is not in the future afterwards and the three days are traded.

### J2. An engine that cannot reach the owner is announced by the watchdog (CRITICAL as reported; hardening)

With no topic in the container, or every post failing, nothing reaches the phone and the watchdog (which only
reads the heartbeat age) stays quiet; after 48 hours the queue is dropped and health reads "Healthy.".
- `python -m v5.health --json` gets a top-level field `"owner_unreachable": true|false`: true when
  `ntfy_configured` is false, when the oldest undelivered owner message is older than 120 minutes, or when a
  message expired undelivered and no post has succeeded since.
- `ops/vps/v5_watchdog.sh`: for a running container with a fresh heartbeat it also looks for
  `"owner_unreachable": true` and then pushes through its own topic file, with the same four-hour stamp
  logic under a separate stamp name, the text "Trading botu sana mesaj gönderemiyor. Claude'a 'trading
  botuna bak' yaz." (with "DENEME HESABI. " in front for the practice service).
- Extend the fake docker / fake curl tests of `tests_v5/test_r5b_inceleme.py::watchdog` for it.
No change to what the engine trades in that state (a plan question, not part of this round).

### J3. A message is never posted twice by one process (MEDIUM)

When the "delivered" mark cannot be written (disk full, read-only volume) the same message is posted again
on every tick: 31 copies in ten minutes. Required: `Outbox`/`Notifier.flush` keep the ids posted successfully
by this process in memory and skip them in later flushes; the bookkeeping of each message runs in its own
`try`, so one failed mark neither posts again nor aborts the rest of the flush.
Test: appends to `outbox.jsonl` fail while posts answer 200: one post in ten minutes of ticks.

### J4. The heartbeat names a day that cannot be decided (MEDIUM; also reviewer "books" 0)

- `_states` adds `CALENDAR_MISSING` while the phase is `no_calendar`, and the hold code of today's record
  while its status is NOT_READY or HOLD_NOT_READY (any code, not only FOREIGN_ASSET).
- `overlay.json`: `_decision_step` reads the fraction before `decide` inside
  `try: ... except StateCorrupt: self._not_ready(day, "STATE_CORRUPT"); raise` (as for `rails.peak()`);
  `_states` and `_readiness_step` read it too, so health shows STATE_CORRUPT all day and the owner hears
  before the open.
- A process that starts after 09:30 ET runs the readiness check once.
Tests: `overlay.json` holds `{"fraction": 1.2}` before D2: the pre-open READINESS message names "kayıt dosyam
bozuk", the day ends HOLD_NOT_READY with that cause (not MISSED), the heartbeat shows STATE_CORRUPT from the
first tick. `trading_blocked` with a process started after the open: the heartbeat is not healthy.

### J5. The watchdog bounds every docker call (LOW)

`$LIMIT docker ps ...` for both listings; a listing that fails or times out gives
`PROBLEM="docker does not answer"`. Test: a fake docker whose `ps` sleeps: the script ends and pushes once.

---------------------------------------------------------------------------------------------------------------

## Group K. The books (reviewer "books")

### K1. The outcome of a filled order comes from the order (MEDIUM)

`_terminal` books DONE or CAPPED from an account reading taken afterwards. When that reading is of a later
moment (a record settled the next day, or a position list one read behind the order status) a completely
filled, uncapped order is booked CAPPED and reported as "not completely filled".
- For `order.status == "filled"`: `outcome = DONE if not intent["capped"] or abs(residual) <= tolerance else
  CAPPED`.
- When `_terminal` is reached through `reconcile` (`allow_retry` False) the record stores `residual = None`
  (the weight of a later moment says nothing about that order) and the outcome follows from `capped` alone.
- On time, when the residual of an uncapped filled order is above the tolerance, the snapshot is read once
  more before the residual is stored.
Tests: the reviewer's two cases (the process dies before it reads the fill and the fund moves 20 % overnight;
the position list is one read behind): record DONE, no "Emrimin tamamı gerçekleşmedi".

### K2. The books are written before the final status (LOW)

`_no_order` saves the final record before `overlay.json`; a death between the two writes leaves the committed
fraction stale for good and the next day's rule sells core money as "yesterday's extra part". Required: in
`_no_order` call `self._commit(record, fraction)` (not for a dry run) before the record gets its final status,
the same order `_terminal` uses. In the same way `_close` and `_final` write the committed fraction again
whenever the last intent carries a `fraction_after`, so no final record exists without its books. Test: a NETTED re-assignment day with a death at every write point (the
suite's death sweep pattern in `tests_v5/test_r3_saldirgan.py`): the committed fraction after a restart is
the reference value at every point.

### K3. A refused order is not sent again after a restart (LOW)

After a definite refusal (a 4xx that is not the one allowed buying-power re-size `403:40310000`), a death
before FAILED is saved lets the restarted process start another attempt. Required: in `_resume`, for a PARTIAL
record whose last intent carries such a refusal in `intent["error"]`, call `self._close(record,
intent["error"])` instead of `self._start`. Test: refusal 422, death at each write point up to the FAILED
save: one submit call in total, final status FAILED.

---------------------------------------------------------------------------------------------------------------

## Group L. Saved state and operator tools (reviewer "state-ops"; scripts in the folder `state-ops`)

Its finding 0 is I3 above (add to I3: an alert raised for a real record, for example by the `_after_chain`
call in housekeeping, never carries the dry-run label). Its finding 7 is K2 and K3.

### L1. The safety sale does not depend on files it does not need (HIGH)

With the account below the floor, a damaged `overlay.json`, a damaged finished record of an earlier session or
a damaged `runner.json` stops the safety sale, and the owner is told that the decision time was missed.
- `Executor._start`: for purpose `flr` use fraction 0.0 when `load_overlay_fraction` raises `StateCorrupt`
  (the sale ends at zero anyway).
- The floor's snapshot skips finished records it cannot read (orders of theirs that are still open then
  count as foreign product orders, the documented rule); `_floor_flow` catches `StateCorrupt` from
  `_snapshot` and still tells the owner (I2's sentence with the cause "kayıt dosyam bozuk").
- `_session_step`: when `_load_state` raises `StateCorrupt`, the floor part still runs in the decision
  window (trusted reading, `check_floor`, on HALT `_floor_flow`); no strategy decision is taken; the
  exception is raised again afterwards, so ERROR and STATE_CORRUPT are reported as today.
- `_cutoff`: a day whose ticks failed with corrupt state ends HOLD_NOT_READY with the code STATE_CORRUPT
  ("kayıt dosyam bozuk"), not MISSED.
Tests: account 61.5 % below its peak with the fund held and, one at a time, `overlay.json` truncated, an old
finished record truncated, `runner.json` truncated: the floor order is sent and fills in all three; the
owner's messages name the sale and the corrupt file, never "karar saatini kaçırdım".

### L2. `decide-now` also names an engine that cannot act (MEDIUM)

`_blockers` adds `ENGINE_FAULT` when the heartbeat is missing, older than 300 seconds, or carries
STATE_CORRUPT or TICK_ERROR. When the reading breaches the floor (state CONFIRMING or HALT, or the reading is
at or below the floor level) the document's plan is the floor action (sell all of the fund), not the
strategy plan. Tests: one per case.

### L3. `init-peak` checks the number like `clear-halt` does (MEDIUM)

R5d made `clear-halt` refuse a peak outside the range from the account value to three times that value.
`init-peak` gets the same check against a trusted account reading (the command-line entry builds the broker
as `_run_clear_halt` does); `--no-check` skips it; both commands print the floor level the new peak implies
("floor level: 196.43"). Test: `init-peak 4910.8` on an account worth 490 is refused and changes nothing.

### L4. A halt day is never summarised as "nothing touched" (LOW)

After `clear-halt` between the cutoff and the summary, the status line of a day with a safety sale says
"Bugün karar veremedim ... Elimdekilere dokunmadım". Required in `_status_text`: a day that has a floor
record with a filled order, or a strategy record held with the code FLOOR_HALT, never yields the
HOLD_NOT_READY sentence; the status is built from the floor record (I1's sentence) or is "Her şey normal.".
Test: halt and sale at 15:51, `clear-halt` at 15:58, summary at 16:01: no "dokunmadım" anywhere.

### L5. The drill's clean-up leaves the engine's own orders alone (LOW, practice account only)

`--flatten-foreign` cancels every open order. It must skip open orders whose client id is the engine's own
(`v5-paper-` followed by a session date from 2010 on) and say so in its evidence line.
Test: an engine order is working while the clean-up runs: it is not cancelled.

### L6. A rail's message survives a death between the state write and the queue (LOW)

The breaker and the drawdown alert are sent once, at the moment the rail changes; a process that dies after
the `rails.json` write and before the outbox append never sends them.
- Breaker: `_watch_breaker` calls the notifier on every tick while the breaker of today is active (the
  per-day dedupe key already makes it one message).
- Drawdown: `rails.json` gets `alert_told`, set only after the outbox append; the alert is sent while
  `alert_active and not alert_told`.
Tests: a death between the two writes for each rail; after the restart the message is sent exactly once.

### L7. The watchdog's cooldown holds without `/var/tmp` (LOW)

When the stamp cannot be written the four-hour cooldown never starts (a push every ten minutes). Required:
check the write; on failure use a stamp under `/dev/shm` and write one log line. Extend the watchdog test.

---------------------------------------------------------------------------------------------------------------

## Group M. Orders (reviewer "orders"; scripts in the folder `orders`)

Of its eight findings four are closed by R5d (an unverified reading, the repeated floor submit, the two
"refused" wordings); its findings 3, 6 and 7 are I3, K3 and K2 above. One is new:

### M1. A chain under way measures the floor, it does not remember it (HIGH)

`_continue_block` asks `self.rails.floor_state()`: the state as the last decision tick left it. A purchase
that is still to be sent (the first step ended without an order, a restart, a lost answer) goes out although
the account has meanwhile fallen under the floor, because nothing measures it again.
Required, inside the existing `try` of `_continue_block`:
`floor = self.rails.check_floor(reading) if reading.ok else self.rails.floor_state()`.
A first breaching reading then answers RAIL_CONFIRMING and the confirmed one FLOOR_HALT; the chain sends
nothing new and ends with a cause sentence that already exists.
Test: the reviewer's scenario (peak 1300, account 539 at the decision, the first step ends without an order,
the fund falls 8 % before the next tick, account 514.50): no buy is sent; the strategy record ends
NOT_SUBMITTED with the cause RAIL_CONFIRMING or FLOOR_HALT.

---------------------------------------------------------------------------------------------------------------

## Report (your final message)

- What you changed, item by item, in a few lines each (say which items are complete).
- The proof command's last line.
- Every existing assertion you changed, with the reason.
- Anything in this text you think is wrong or incomplete.
