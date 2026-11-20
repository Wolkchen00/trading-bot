# Fix round R5f: three small items from the review of the R5d -> R5e upgrade

You are the builder. I am the reviewer: I will read your whole diff, run the proof myself and run my own tests
written from this text. Work in this repository. Do not commit. Do not open `.env`. No network is needed.

Background: R5e (`rf6/fix-r5e.md`, `rf6/fix-r5e-2.md`, `rf6/fix-r5e-3.md`) is built, committed and deployed.
Before the deploy four independent reviewers examined the upgrade on a copy of the real state. They found no
blocker and a few small things; three of them are code (`RF-ISSUES-6.md` item 33). The owner is not technical
and reads every message on a phone: every sentence must be literally true whatever else happens that day.

## Rules

- Files you may edit: `v5/notify.py`, `v5/runner.py`, `v5/health.py`, and ONE new test file
  `tests_v5/test_r5f_duzeltme.py`. Existing tests may be changed only where they pin the OLD text of T1 or T2
  (`tests_v5/test_r4_notify.py`, `tests_v5/test_r5e_duzeltme.py`, and any other place you find); name every
  changed assertion in your report.
- Do not edit the reviewer's suites (`tests_v5/*_saldirgan.py`): they already pin the NEW sentences of T1 and
  T2, so three of their tests fail on the code as it is and must pass after your change.
- Do not touch `v5/strategy.py`, the golden files or the plan documents (`RF-*.md`, `rf6/*.md`).
- Keep the style of the surrounding code. No new dependencies. One test per item at least, each failing on the
  code as it is and passing after your change. Reuse the helpers of `tests_v5/test_r5_saldirgan.py`
  (`Sim`, `window`, `D1`, `D2`, `D3`) by importing them.
- If an item contradicts the plan in a way that matters, answer `BLOCKED:` with the reason. Do not guess.
- Proof at the end (about ten minutes; in a restricted sandbox the few tests that need a POSIX shell or a
  system temp folder may fail for that reason alone, say so):
  `python -m pytest tests_v5 -q -p no:cacheprovider --basetemp=.pytest_tmp_codex`

## T1. The doubt alert speaks about the safety sale, not about every sale

`DATA_DOUBT` reads "Hesap değeri tutarsız göründü. Yanlış alarm olmasın diye satış yapmadım, izlemeye devam
ediyorum." The engine sends it when an account reading does not add up, when a peak is refused, and when the
daily brake holds for a doubtful baseline. On such a day the rule's own sale (for example the extra share of a
dip day that expires) still goes through, and the evening summary then says "Elimdeki fonun tamamını sattım"
or "Dünkü düşüş için tuttuğum ek payı sattım": two messages of one day that contradict each other.

Required: the template becomes exactly
"Hesap değeri tutarsız göründü. Yanlış alarm olmasın diye güvenlik satışı yapmadım, izlemeye devam ediyorum."
Nothing else changes (same kind, same keys, same priority).

Test: `Sim`; D1 ordinary; D2 with `sim.ibs[D2] = 0.0` (the extra share is bought); on D3, at the tick
`session.open + 2 hours` only, `get_account` reports an equity 8 percent above cash plus positions (wrap
`sim.fb.get_account` like `test_i9_capped_fills_and_idle_days_are_told_truthfully` does). Expect: exactly one
alert, equal to the new sentence; the day's order is a sell; the summary of D3 contains "Dünkü düşüş için
tuttuğum ek payı sattım."; no text sent on D3 contains "diye satış yapmadım".

## T2. A corrected close names the summary it corrects

`CLOSE_CORRECTED` reads "Dünkü özetteki hesap değeri yanlış okunmuş. Doğru kapanış değeri {equity} dolar."
It is sent in the first brake step of the NEXT session. After a weekend or a holiday the summary it speaks
about is not yesterday's (Friday's close is corrected on Monday).

Required:
- The template becomes exactly
  "Gün sonu özetindeki hesap değeri yanlış okunmuş ({date}). Doğru kapanış değeri {equity} dolar."
  where `{date}` is the session whose row was corrected, written like the first line of the summary
  (`%d.%m.%Y`, for example "20.11.2026").
- `build_alert` formats a `date` field that way when the template has one (the value is a `datetime.date`).
  A call without the field must fail (a `KeyError`), as for every other field: never a half-filled sentence.
- The one call site in `v5/runner.py` (`_watch_breaker`) passes the previous session's date. The alert key
  (`state_key=previous.isoformat()`) does not change.
- No template may begin with a placeholder (the reviewer's helper recognises a sentence by its fixed
  beginning); the text above respects that.

Test: `Sim` with the clock set to Friday 2026-11-20 before the open; on Friday the closing reading is absurd
for one tick (price 310 at `reconcile_from + 10 s`, back to 50 at `+ 70 s`, like `print_once` in
`tests_v5/test_r5e_duzeltme.py`); `sim.fb.balance_asof = <Friday>`; run Monday 2026-11-23. Expect exactly
one alert "Gün sonu özetindeki hesap değeri yanlış okunmuş (20.11.2026). Doğru kapanış değeri <the true
close> dolar." and no text containing "Dünkü özetteki".

## T3. The health tool answers even when it cannot read the message file

`v5/health.py` `main()` computes `owner_unreachable` with `Outbox(settings.state_dir).owner_unreachable(now)`
outside any guard. If that call raises (a permission or I/O error on `outbox.jsonl`), the tool dies with a
traceback and prints no JSON; the host watchdog then finds no heartbeat age and tells the owner "Trading botu
çalışmıyor görünüyor" although the heartbeat is fresh.

Required: when computing `owner_unreachable` raises any `Exception`:
- `owner_unreachable` is `True` (it cannot be shown that the owner can be reached),
- the level is at least 1,
- the line "The owner message file could not be read." is added, and "Healthy." is not among the lines,
- everything else is printed as usual (JSON with `heartbeat_age_seconds`, `level`, `lines`, `heartbeat`; the
  plain output with its first word and the lines), and the exit code is the level.
Nothing changes when the call does not raise.

Test: a fresh, healthy heartbeat written with `health.write_heartbeat` (time = now), `load_settings`
monkeypatched to the simulator's settings, `Outbox.owner_unreachable` monkeypatched to raise
`PermissionError`: `main(["--json"])` returns at least 1 and prints JSON with a numeric
`heartbeat_age_seconds` below 300, `owner_unreachable` true and the new line; without the fault the same
heartbeat gives exit 0 and `["Healthy."]`.

## T4. The doubt alert is not sent while the engine is halted (added after the build of T1 to T3)

Found by the independent reader of this round. With the floor state `HALT` the safety sale has been made or is
under way. `DATA_DOUBT` says "güvenlik satışı yapmadım": in that state it is false and contradicts
`FLOOR_DONE`, `FLOOR_PENDING` and `RAIL_LIQUIDATING` (the old sentence had the same flaw: "satış yapmadım").
What the owner needs to know in a halt is said by the floor messages and by the status line of the summary.

Required: every place in `v5/runner.py` that sends `DATA_DOUBT` goes through ONE helper, and the helper sends
nothing while `self.rails.floor_state() == "HALT"`. Nothing else changes: the brake's bookkeeping, the
readiness problems and the summary are as before; with the floor `OK` or `CONFIRMING` the sentence is sent as
before.

Tests: (1) the account is halted on D1 and everything is sold; on D2, at the tick `session.open + 2 hours`
only, the account reading does not add up: no text with "Hesap değeri tutarsız göründü" is ever sent, and the
summary of D2 still names the halt in its status line. (2) The control: the same odd reading on an account that
is not halted sends the doubt sentence once.

## Report

What you changed per item, every existing assertion you changed, and the last line of the proof command.
