# Fix round R5d: defects found by three reviews of the v5 engine (bug fixes, with tests)

You are the builder. I am the reviewer: I will read your whole diff, run the proof myself and run my own tests
written from this text. Work in this repository. Do not commit. Do not open `.env`. No network is needed.

## Background (read these parts only)

- `RF-PLAN-6.md`: Revisions 11 and 12 at the top, and APPENDIX A (every sentence the owner can receive).
- `rf6/contract-r5.md`: the "AS BUILT" notes at the end (how `v5/runner.py` works today).
- `RF-ISSUES-6.md` items 25 to 28 (what the earlier rounds fixed).
- The owner is not technical. Every message must be literally true.

## Rules

- Files you may edit: `v5/runner.py`, `v5/executor.py`, `v5/rails.py`, `v5/notify.py`, `v5/ops.py`,
  `v5/health.py`, `v5/broker.py`, `tools/v5_durum.py`, `tests_v5/fakebroker.py` (only the addition named in C2)
  and ONE new test file `tests_v5/test_r5d_duzeltme.py`.
- Do not touch `v5/strategy.py`, the golden files or the plan documents. An existing test may only be changed
  where it asserts the OLD behaviour of exactly one of the items below: change that assertion and name it in
  your report.
- If an item contradicts the plan in a way that matters, answer `BLOCKED:` with the reason. Do not guess.
- Keep the style of the surrounding code. No new dependencies.
- Each new test must fail on the code as it is now and pass after your change. Reuse the helpers of
  `tests_v5/test_r5_saldirgan.py` (`Sim`, `window`, `D1`, `D2`, `D3`) and of `tests_v5/test_r5b_inceleme.py`
  (`break_call`, `Death`, `not_sent`, `rows`) by importing them.
- Work through the groups in the order given (the most important first) and run your new tests after each
  group, so that an interrupted session still leaves complete groups behind.
- Proof at the end (about ten minutes; in a restricted sandbox the few tests that need a POSIX shell or a
  system temp folder may fail for that reason alone, say so):
  `python -m pytest tests_v5 -q -p no:cacheprovider --basetemp=.pytest_tmp_codex`

---------------------------------------------------------------------------------------------------------------

## Group A. Orders: the gate of a chain that is already under way

A1. An untrusted account reading must block. `Runner._continue_block` reads the account but only feeds the
    breaker with it. Required: keep the reading; after the two floor checks:
    `if not reading.ok: return (reading.problem if reading.problem in ACCOUNT_PROBLEMS else "EQUITY_DOUBT"), None`.
    Test: a chain under way (its first step ended without an order); on the next ticks the account's equity
    differs from cash plus positions by more than 2 % (wrap `get_account` and scale `equity` by 1.05). No
    `submit_order` call; the record ends NOT_SUBMITTED; the owner gets `not_sent("hesap değerini doğrulayamadım")`.

A2. The breaker blocks purchases at the moment an order is planned, whatever the chain decided earlier.
    Today `_continue_block` asks `_is_buy(record)`, which compares the target with what was held at decision
    time. A chain that started as a reduction can be re-planned as a BUY after a partial fill and a price fall
    (the fresh weight is then below the target), and that buy goes out while the breaker is active.
    Required:
    - `Executor.run_chain(record, window, blocked=None, buys_blocked=False)`; the flag lives for that call only
      (reset in the same `finally` as `_blocked`).
    - `Executor._start`: right after `plan = plan_order(...)`: when `buys_blocked` and `plan.side == "buy"`:
      `hold = "BREAKER"`, event `"HOLD:BREAKER"`, return the record. No intent is appended, nothing is sent.
    - `Executor._submit`: before the counter is incremented: when `buys_blocked` and `intent["side"] == "buy"`:
      the same hold, return.
    - `Runner`: delete `_is_buy`; `_continue_block` no longer returns `"BREAKER"`. It returns three values
      `(code, problem, breaker)` and the caller passes `buys_blocked=breaker`. The first `run_chain` call right
      after a fresh decision passes `buys_blocked=breaker` as well.
    - A sale is never blocked by the breaker.
    Tests: (i) a reduction chain whose first sell is partially filled (`partial("0.5")`), then the fund's price
    falls so far that the fresh weight is below the target and the breaker trips: no buy is ever submitted and
    the record ends PARTIAL and closed; (ii) the inverse: a chain that started as an increase and whose next
    action after a price jump is a sale still sells under an active breaker.

A3. A floor chain under way must look at the account too. `Runner._floor_block` passes `False, False` for the
    two account conditions of `floor_order_allowed`, and `Executor._resume` re-submits a PLANNED intent with
    `submit_count > 0` without looking at the account. A floor intent that died inside the submit call is
    re-sent although an unknown order in the fund appeared meanwhile.
    Required:
    - `_floor_block(now, session)`: take `self._snapshot()` exactly like `_continue_block` does
      (`SnapshotInvalid` -> `"SNAPSHOT_INVALID"`, `BrokerError` -> `"BROKER_DOWN"`, any other exception ->
      `"STATE_CORRUPT"` or `"ENGINE_FAULT"` together with the exception) and pass
      `self._other_open(session.date, "flr")` and `bool(snapshot.foreign_product_orders)` to
      `floor_order_allowed`.
    - `Executor._resume`, status PLANNED with `submit_count > 0`: when the lookup finds nothing and the submit
      time has not passed, call `self._inspect(record)` first; `None` -> return the record (it then holds
      `FOREIGN_ASSET`), otherwise `_submit` as today.
    Tests: (i) with the runner: floor halt, the floor intent dies inside `submit_order` (record PLANNED, counted
    once, no order at the broker), an order in the fund with a foreign client id appears, a new `Runner` ticks
    to the close: no `submit_order` call, the owner gets the FLOOR_PENDING sentence; (ii) with the executor
    alone (no runner gate): a strategy record in the same state and a foreign position: `run_chain` sends
    nothing and the record holds `FOREIGN_ASSET`.

## Group B. Three untrue or missing statements

B1. During a halt the summary can say "Her şeyi sattım ve durdum" while the fund is held. `_settle_floor`
    returns without looking when `liquidating` is false, and `_status_text` treats "HALT and not liquidating"
    as "everything is sold".
    Required: `_settle_floor(reading)` looks at the account whenever the floor state is HALT: fund held ->
    `set_liquidating(True)`; flat and `liquidating` -> `_floor_done(...)`. `_status_text` receives the fact
    `flat` that `_digest_context` already computes (from its snapshot, or from the reading in the fallback) and
    returns the FLOOR_DONE sentence only when `flat`; HALT and not flat -> the RAIL_LIQUIDATING sentence.
    Test: floor sale done and announced (flat, `liquidating` false); later the same day, before the close, the
    fund is held again (put a position into the fake broker); after the close: line 7 is the RAIL_LIQUIDATING
    sentence, line 3 shows the holding, `liquidating` is true.

B2. `tools/v5_durum.py` tells the owner "Bot çalışıyor." for a heartbeat whose time is in the future (it clamps
    the age to "0 dakika önce"). Required: fresh only when `-60 <= age <= 300`; for an age below -60 the first
    line is "Bot çalışmıyor görünüyor." and the second line "Kalp atışı kaydının saati ileride görünüyor.".
    Test: `render` with a heartbeat one day in the future, one 30 seconds in the future (fresh) and a normal one.

B3. A dry-run day drops the reason of the purchase it would have made (`OVERLAY_ENTER` needs a filled buy and a
    dry-run never fills), and shows the wrong amount.
    Required:
    - `REASON_SENTENCES["OVERLAY_WOULD"] = "Bugün sert bir düşüş oldu; gerçek emir açık olsaydı yarın için küçük
      bir ek alım yapardım."` (one line in the code).
    - `_digest_context`: when the strategy record's status is DRY_RUN, `OVERLAY_ENTER` is replaced in place by
      `OVERLAY_WOULD` (not removed, not replaced by `OVERLAY_HELD`).
    - `would_buy` shows the order that would have been sent: `float(intent["notional"])` for a buy (fall back to
      `desired_usd` when there is no notional); `would_sell` keeps `desired_usd`.
    Test: `Sim(mode="live")` with a dip day: line 5 contains the OVERLAY_WOULD sentence; with little cash
    (so that the plan is capped) line 4 shows the planned notional, not the desired amount.

## Group C. `last_equity` is "the close of session X" only when the broker says so

The engine assumes that from the first tick of a session the broker's `last_equity` is the close of the session
before. Measured so far: four hours after a close it has NOT turned over yet; when it does is unknown. The
account answer carries the date of that value (`balance_asof`, for example "2026-11-13" on Monday the 16th).

C1. `v5/broker.py`: `Account` gets a last field `balance_asof: date | None = None`. `_account` fills it from
    `row.get("balance_asof")` when that is a string of the form YYYY-MM-DD; anything else (missing, empty, not a
    date) gives `None`, never an error.
C2. `tests_v5/fakebroker.py`: an attribute `self.balance_asof = None` that `get_account` returns in that field.
    Nothing else changes there. (`None` means "the broker does not say": the engine then behaves as today.)
C3. `v5/rails.py`: `EquityReading` gets a last field `last_equity_asof: date | None = None` (seven positional
    arguments keep working), filled by `read_equity`. Add
    `def last_equity_is_close_of(reading, session_date) -> bool | None`: `True` when the dates are equal,
    `False` when the broker names another date, `None` when it names none.
C4. Breaker. `breaker_cause(reading, stored_close, breaker_day, previous_session=None)` gets a fourth result:
    - Let `known = last_equity_is_close_of(reading, previous_session)` (treat `previous_session is None` as
      `None`).
    - `known is False` (the broker's value is about another day): with a usable stored close, the stored close
      is the baseline and there is no cross-check; with `NO_ROW`, return `"BASELINE_PENDING"`.
    - Otherwise exactly today's logic (the broker's value is the baseline, cross-checked against a stored close).
    `Rails.check_breaker(reading, session_date, previous_session=None, decisive=False)`: `"BASELINE_PENDING"`
    stores nothing and returns `False` when `decisive` is false; when `decisive` is true it is treated as
    `"BASELINE_DOUBT"`. `Runner._watch_breaker(reading, session, decisive=False)`: the 5-minute breaker step
    passes `False`; the decision step and `_continue_block` pass `True`. `ops._blockers` treats
    `"BASELINE_PENDING"` like the engine at decision time (breaker true).
C5. `Runner._backfill_close(reading, session)`: only when
    `last_equity_is_close_of(reading, previous) is not False`.
C6. `Runner._after_close`:
    - the day base of an on-time summary: the stored row of the previous session when there is one; otherwise
      `reading.last_equity` when `last_equity_is_close_of(reading, previous) is not False`; otherwise unknown
      (`None`);
    - a late summary takes `reading.last_equity` as that session's closing value when
      `last_equity_is_close_of(reading, day) is True`, or (today's assumption, kept for a broker that names no
      date) when it is `None` and the next session is open; otherwise there is no closing value
      (`as_of_now`).
C7. `LATE_CLOSE` becomes five minutes: a reading taken later than ten minutes after the close is not stored as
    a closing value (extended-hours prices move the account).
    Tests: (i) broker dated for the session before (`balance_asof` = that date): behaviour as today; (ii) at the
    first breaker tick of D2 the broker still names the session before D1 (it has not turned over) and the
    engine has its own row for D1: no row is added or changed, the breaker uses the engine's own row, no
    DATA_DOUBT; (iii) the same but the engine has no row for D1 (it was away at that close): nothing is
    stored, no doubt is raised in the session phase; once the broker names D1 the row is taken; if it still
    names the older date at the decision tick, the breaker is on (reduce-only) with cause BASELINE_DOUBT;
    (iv) a late summary for D1 made during D2's session takes `last_equity` only when the broker names D1;
    (v) an after-close step that runs 12 minutes after the close stores no row and its summary starts line 2
    with "Hesap şu an".

## Group D. No session without its summary: per-session bookkeeping instead of one high-water mark

Today `runner.json` has one marker (`closed_session`) and the catch-up gives up on a failed account reading
when it runs in the after-close phase; a session whose cutoff message was withheld (`quiet`) can then end with
no message at all.

D1. `runner.json` gets `closed_sessions`: the ISO dates whose after-close work is finished (or that are not the
    engine's), sorted, at most the last 15. `closed_session` stays and is always the largest entry (other
    readers use it). `_load_state`: a file without the list gets `[closed_session]` when that is set, else `[]`.
D2. `Runner._after_close(now, session, state) -> bool` (the `give_up` parameter is gone): returns `True` when
    the session is finished (already, or by this call) and `False` while it is still owed.
    - finished already: `day.isoformat() in state["closed_sessions"]`;
    - a session that closed before `started_at`: marked finished without a report;
    - an account that cannot be read: `False` (it never gives up here);
    - otherwise as today, and the session is marked finished at the end.
D3. `Runner._catch_up(now, today, state) -> bool`: every one of the last `CATCH_UP_SESSIONS` sessions before
    today gets `_after_close`, oldest first, each on its own (one that is still owed does not hold the others
    back); the result is `True` when none is owed. Before that: when the session just outside the window
    (`previous(today, CATCH_UP_SESSIONS + 1)[0]`, if there are that many) is not finished and closed after
    `started_at`, log one error line ("no summary could be made for <date>") and mark it finished.
D4. `_session_step`: catch-up runs in the phases closed, pre, readiness, session and decision as today. In the
    after-close phase: catch-up first, then today's own `_after_close`, and today's step runs whether or not an
    older session is still owed. `self._digest_pending` is `True` exactly when one of this tick's calls
    returned `False`.
D5. `Rails.record_close(session, equity)`: a row is only added when its session is later than every stored
    row (the history stays ordered, one row per session); otherwise nothing changes.
    Tests: (i) the engine is away over D1's close and comes back on the evening of D2 while the first account
    read fails: later ticks still produce D1's dated summary, and D2's own summary is not delayed by it;
    (ii) D1 was MISSED (its cutoff message withheld), the account is unreadable for some ticks around D2's
    close: D1 still gets its summary; (iii) history rows stay ordered and unique in both; (iv) a `runner.json`
    written by the current code (only `closed_session`) is read correctly.

## Group E. Outcomes and wording

E1. A FAILED record is three different things. Add `Runner._failure_kind(record)`:
    `"REFUSED"` when `status_detail` matches `^\d{3}:` (a broker rejection, for example "422:42210000");
    `"NOT_SENT"` when it is `"NOT_FOUND"` or `"VALIDATION"`; otherwise `"UNFILLED"` (the order was accepted,
    nothing was filled, it was cancelled or expired).
    - new template `ORDER_UNFILLED`: "Emrim gerçekleşmedi ve iptal oldu. Şu an elimde yüzde {product_pct} Nasdaq
      fonu var. Yarın yeniden karar veririm."
    - new outcome `UNFILLED`: "Karar verdim ama emrim gerçekleşmedi."
    - `_after_chain`, the digest outcome and `_status_text` use the kind: REFUSED -> `ORDER_FAILED` / `REFUSED`;
      NOT_SENT -> `NOT_SENT` (cause from the detail; `VALIDATION` has no sentence of its own and gets the
      default cause); UNFILLED -> `ORDER_UNFILLED` / `UNFILLED`. For a floor record only REFUSED sends an
      alert (as today); the other two are covered by the floor's own messages.
E2. A record settled long after its session never sends a message that says "bugün": `_after_chain(...,
    late=True)` sends no `NOT_SENT` at all (also not for `NOT_FOUND`: this reverses the choice of round R5c).
    The dated summary of that session, or its correction (E3), says it.
E3. After a summary that had to say "Bir emir gönderdim ama sonucunu henüz öğrenemedim", the outcome is told.
    - `runner.json` gets `unknown_sessions` (list of ISO dates): a session is added when its summary is built
      while one of its records is still open.
    - When housekeeping settles a record (its status becomes final) whose session is in `unknown_sessions`,
      that session's summary is sent once more: built like a late summary (`as_of_now=True`) with the new
      context key `correction=True`; then the session is removed from the list.
    - `build_digest`: with `ctx.get("correction")` line 1 is "Gün sonu özeti ({date}), düzeltme" (after the
      label, as today). `Notifier.digest`: the dedupe key gets the suffix ":fix" for a correction.
E4. A late summary (`as_of_now`) shows what is held now: line 3 then starts "Elimde şu an: yüzde ..." (both
    forms of the line). And "everything sold" is decided from the record, not from the account of the moment:
    the action is `sell_all` exactly when the last intent that has a fill has `kind == "sell_all"` and its
    record's status is DONE. The test on `flat` is removed from the action altogether: with fills but no such
    intent the action is `sell` when `sold > 0` and `sold >= bought`, `buy` when `bought > 0`, else `none`
    (and `unknown` / the dry-run actions as today).
E5. Two reason sentences state an action although they describe a state (APPENDIX A will be revised to match):
    `VOL_HIGH` becomes "Piyasa çok oynak, bu yüzden payı küçük tutuyorum." and `BAND_HOLD` becomes
    "Ana pay hedefe yeterince yakın.".
E6. `Executor.attach_decision` sets `status_detail` to `""`: a NOT_READY code from an earlier tick of the same
    day must not be reported later as the cause of a NOT_SENT.
E7. The dry-run settle loop at the top of `_after_close` passes `late=late` to `_after_chain`.
    Tests for E1 to E7: one each, on the Sim where a flow is needed (for E1/UNFILLED: `never_fill`, the order is
    cancelled at the cancel time; for E3: order-status calls fail around the close and recover in the evening:
    exactly one correction summary, with the real purchase in line 4).

## Group F. `decide-now`

F1. `ops._blockers` adds `"OUTSIDE_DECISION_WINDOW"` unless `window.decide_from <= now <= window.decide_until`
    and `"ALREADY_DECIDED"` when today's record of the mode the engine would use (dry-run or not) exists and is
    final or has a target. The exit code is 0 only with an empty list; the document still carries the decision
    and the plan in every case.
    Tests: inside the window with nothing else wrong: 0 and `[]`; two hours before the window: 1 and exactly
    `["OUTSIDE_DECISION_WINDOW"]`; after the engine decided today: `"ALREADY_DECIDED"` is in the list.

---------------------------------------------------------------------------------------------------------------

## Report (your final message)

- What you changed, group by group, in a few lines each (say which groups are complete).
- The proof command's last line.
- Every existing assertion you changed, with the reason.
- Anything in this text you think is wrong or incomplete.
