# Fix round R5e, second part: the reviewer's findings on your R5e build, and the proof you could not finish

Your quota ran out at 03:19 before the older tests were brought up to date and before the full proof. The
reviewer read your whole diff and ran the whole suite on it: 850 passed, 45 failed. Nothing was committed and
nothing of yours was reverted: the working tree is exactly as you left it.

Same rules as `rf6/fix-r5e.md`. Work through the items below in the order given, then the tests, then the proof.
Do not commit. Do not open `.env`. No network.

---------------------------------------------------------------------------------------------------------------

## R1. J1 again: in a tick whose clocks disagree, the engine runs on the EARLIER of the two clocks (HIGH)

What you built (the early `return` at the top of `_session_step`) follows the contract's wording, and the
wording was wrong: with a clock disagreement that LASTS (the suite's own `sim.fb.skew = 95.0` cases) the engine
now does nothing at all. No floor check (so no HALT and no message when the account is under the floor), no
cutoff message, no evening summary. Six older tests fail for exactly this reason and they are right:
`test_each_doubt_blocks_the_order_and_is_named[skew]`, `test_floor_waits_while_the_clock_is_in_doubt`,
`test_a_chain_under_way_sends_nothing_once_a_gate_closes[skew]` and `[restart_skew]`,
`test_a_safety_sale_under_way_is_gated_again_too[skew]`,
`test_a_pending_safety_sale_with_the_broker_down_is_not_silent`.

Required instead:
- `_clock_step` keeps the broker timestamp it read in this tick (`self._broker_now`; `None` when the broker
  clock could not be read).
- `tick()`: after `_clock_step`, `effective = now`; when `self.clock_skewed` and `self._broker_now` is not
  None, `effective = min(now, self._broker_now)`. `_housekeeping_step` and `_session_step` are called with
  `effective`. Nothing is booked before BOTH clocks say it has happened. `_beat`, `_calendar_step`,
  `_clock_step` and `_account_for` keep the host time.
- The early `return` in `_session_step` goes away. Everything a skewed tick did before R5e happens again, on
  the earlier clock: the decision is refused with CLOCK_DOUBT, the cutoff tells the owner, the summary is
  sent, the floor is measured and a sale that may not start is told with `FLOOR_WAITING` and the cause
  "saat bilgisi tutarsız göründü".
- `started_at` is written from `effective`, so it is never later than either clock.
- Keep `clock_skewed`, the every-tick clock check while in doubt, the future cooldown row rule, and your two
  J1 tests (they must still pass: one tick with the host hours ahead books nothing, because the broker's
  clock is the earlier one).
The six older tests above must pass again; change in them only the sentences that an item of the contract
changed (I1, I2), and name each change.

## R2. An alert key that never changes is now sent only once in the engine's life (HIGH)

You gave every alert with a state key a dedupe key (`ALERT:{kind}:{state_key}`). That makes "once per key"
true, which L6 needs. But two keys never change:
- `FLOOR_DONE` / `FLOOR_FLAT` use `"halt"`: the completion of a second safety sale (a later halt, or a fund
  that came back during the same halt, see `test_a_fund_that_comes_back_during_a_halt_is_sold_and_the_owner_
  hears_it`) would never be told. Use `f"halt:{New York date of the moment}"`.
- `FOREIGN_ASSET` uses the symbol: a foreign asset is then told once for ever. Use `f"{day}:{symbol}"` (the
  session date) in both places.
`test_cooldown_per_kind_and_state` pins the old four-hour rule: change it to the rule as it now is (with a
state key: once per key; without one: the four-hour cooldown) and name it in the report.

## R3. `FLOOR_WAITING` never states a holding that was not read (MEDIUM)

In the exception path of `_floor_flow` (no snapshot) you call `_floor_pending(..., 0.0, code)`: the message
then says "Şu an elimde yüzde 0 Nasdaq fonu var." although nothing was read. New template
`FLOOR_WAITING_BLIND` (priority 5): "Hesap en yüksek değerinden yüzde {pct} düştü, şu an {equity} dolar.
Güvenlik satışını henüz başlatamadım, çünkü {cause}. Denemeye devam ediyorum." Use it whenever the holding
is not known. Test: the open-orders call fails during a halt: the message has no "Şu an elimde".

## R4. "Of this halt" (MEDIUM)

`_floor_filled()` without a record reads every floor record there ever was. After an earlier halt with a
sale, a later halt on an account without the fund would say `FLOOR_DONE` instead of `FLOOR_FLAT`. Count only
floor records whose session date is on or after the New York date of `rails.state()["halt_time"]`.
Test: an old filled floor record from an earlier halt, a new halt with no fund held: `FLOOR_FLAT`.

## R5. After `clear-halt` the status line does not say "durdum" (MEDIUM)

`_status_text`: the branch for a day whose floor record has a fill while the rail is no longer HALT returns
`FLOOR_DONE`, whose text says the engine stopped and asks the owner to restart it. The halt was lifted: return
None there (lines 4 and 5 of the summary tell the sale). Adjust your L4 test.

## R6. READINESS is only said before the open (MEDIUM)

Its sentence starts "Bugün borsa açılmadan önce".
- The `StateCorrupt` branch you added to `_session_step` sends no READINESS alert at or after the session's
  open (ERROR is sent at once and the heartbeat says STATE_CORRUPT).
- The late-start readiness check (J4) runs only while `session.open <= now < session.close`; it fills the
  heartbeat's readiness as you built it, but `_readiness_step` sends its alert only when `now < session.open`.
`test_d1_d4_one_owed_session_does_not_delay_the_newer_summary` fails today because the late-start check runs
after the close and uses up the one failed account read the test injects; with this change it should pass
unchanged. If it does not, say why before touching it.

## R7. Smaller items

- Watchdog: for the practice service the text is rebuilt from `MESSAGE`, which overwrites the "cannot reach
  you" text. Use `TEXT="DENEME HESABI. ${TEXT}"`. Test: practice service with `"owner_unreachable": true`:
  the push says "DENEME HESABI. Trading botu sana mesaj gönderemiyor."
- `tools/v5_durum.py` (this corrects the contract's I11): the first line depends on the heartbeat's freshness
  only ("Bot çalışıyor." for a fresh heartbeat; a dry run is running). The status line is "Durum: Her şey
  normal." only for level 0; otherwise "Durum: " plus the codes, comma separated, without the words "Normal
  değil" (a dry run is a state, not a fault).
- Summary line 3 adds up to 100: the cash figure is `100 - product - owner - other`, with `other` only
  counted when it is named (at least 1). `test_digest_names_the_owners_share` and
  `test_the_owners_own_share_is_left_alone_and_named` must pass unchanged.
- `_digest_context`: when even the plain positions call fails, do not invent "0 % fund, flat". Let the
  `BrokerError` out; `_after_close` and `_correction_step` treat it like a failed reading (the summary is
  tried again on the next tick).
- K3: a buying-power refusal may be re-sized once. In `_resume`, a PARTIAL record that already holds two
  intents with the error `403:40310000` is closed instead of started again.
- No test may reach the network: `init-peak` now builds a real broker unless `--no-check` is given. Older
  tests that call it through `run_ops` pass `--no-check` (for example
  `test_ops_commands_change_the_shared_state_and_are_logged`); your L3 test injects the fake broker.
- `_floor_pending`: when no reason is passed and the engine is not a dry run, take the cause from the floor
  record's `hold` before falling back to SNAPSHOT_INVALID.

## R8. Tests still missing

I5 (a late summary's line 3 starts with "Elimde şu an: "), I6 (no summary contains "ek alımı" after a
re-assigned overlay) and I9 (the three CAPPED cases with a resting owner order) have code but no test of their
own. Add them.

## R9. The 45 older tests that fail on your build

Run the whole suite and go through every failure. For each one decide, and say in the report which it was:
(a) it asserts a sentence or behaviour that exactly one item of the contract changed on purpose: change that
    assertion only, and name the item;
(b) anything else is a defect of the build: fix the code, not the test.
What the reviewer saw (after R1 to R7 most of the second kind should be gone):
- sentences: ERROR without "Elimdekilere dokunmadım." (I4), FLOOR_DONE (I1), FLOOR_PENDING where no order
  was sent (I2), OVERLAY_EXIT / OVERLAY_KEEP (I6), the template, priority and reason tables, the literal
  summaries;
- `decide-now`: the tests have no heartbeat, so `ENGINE_FAULT` is in the list (L2): let those tests run one
  engine tick first (or write a heartbeat), do not weaken the rule;
- the health JSON has one more field (J2); the status tool's lines (R7);
- the cooldown rule (R2).

---------------------------------------------------------------------------------------------------------------

## Report (your final message)

- Per item of `rf6/fix-r5e.md` (H1 to M1): complete or not, in one line each.
- Per item R1 to R9 of this text: what you changed.
- Every existing assertion you changed, with the item that justifies it.
- Every new sentence the owner can receive, exactly as written in `v5/notify.py`.
- The proof command's last line.
- Anything in the two texts you think is wrong.
