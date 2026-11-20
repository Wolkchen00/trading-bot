# Fix round R5e, third part: five small items from the reviewer's second pass

The reviewer read your second run, ran the whole suite on Windows (908 passed), ran its own suite
(`tests_v5/test_r5e_saldirgan.py`, 25 tests, do not edit it) and re-ran the six reviewers' scripts on your
build. All 33 items and R1 to R9 are accepted. Five small things are left. Same rules as before: do not
commit, do not open `.env`, no network; one test per item in `tests_v5/test_r5e_duzeltme.py`.

## S1. Line 3 of the summary never adds up to more than 100

`build_digest` rounds the fund, the owner's share and the foreign part separately; 81.6 + 16.6 + 1.8 becomes
82 + 17 + 2 = 101 with "yüzde 0 nakit". Required: when the rounded parts that are named add up to more than
100, the largest of them is reduced by the excess, so that the numbers in the line always add up to exactly
100 and none is negative. The reviewer's test `test_the_shares_in_line_three_always_add_up_to_a_hundred`
fails today on exactly this and must pass.

## S2. A positions call that fails while the summary is built is a failed reading, not a failed tick

Since R7 `_digest_context` lets a `BrokerError` out when even the plain positions call fails. `_after_close`
and `_correction_step` do not catch it, so the whole session step fails (and three such ticks send ERROR).
Required: `_after_close` returns False and `_correction_step` goes on to the next session when
`_digest_context` raises `BrokerError`; nothing is marked as reported, and the next tick tries again.
Test: after the close the open-orders call and the positions call both fail on one tick and answer on the
next: no ERROR alert, exactly one summary.

## S3. The watchdog asks a docker that does not answer only once

With a docker daemon that hangs, each of the two services runs two listings of 60 seconds each: four
minutes before anybody is told. Required: after the first listing that fails or times out, no further docker
call is made in that run; both services get `PROBLEM="docker does not answer"`. Test: a fake docker whose
`ps` sleeps 100 seconds: the script ends within 75 seconds and has pushed.

## S4. A damaged old record on an ordinary day is not "I missed the decision time"

L1's last point is not reached for one case: with a torn record of an earlier, finished session the day
ends MISSED and the owner reads "Bugünkü karar saatini kaçırdım. Elimdekilere dokunmadım. Yarın normal devam
ederim." (the reviewers' script `state-ops/s02b_corrupt_file_says_missed.py`, third case). Required: a
`StateCorrupt` raised anywhere in the fresh-decision path of `_decision_step` marks the day
`_not_ready(day, "STATE_CORRUPT")` before it is raised again, so the cutoff tells the owner
"Bugün karar veremedim, çünkü kayıt dosyam bozuk. ...". Test: that scenario; no "karar saatini kaçırdım".

## S5. The drill keeps away from the engine's decision window (practice account only)

The drill's exit runs a chain to weight zero, which sells the whole fund position. If the engine buys between
the drill's check ("the account holds what I bought") and that chain, the drill sells the engine's purchase
as well (the reviewers' script `state-ops/s03_drill_vs_running_bot.py`, case 1). Required: the steps that
send orders (`flatten_foreign`, `round_trip`, `duplicate_id`, `restart`) refuse to run from 15 minutes before
today's decision window until today's close, with the evidence text "too close to the engine's decision
window; run the drill earlier in the session". Use the session of the broker's calendar for today and
`v5.clock.window_for`; on a day without a session the steps run as before. Test: the drill at
`decide_from - 10 minutes` refuses and sends nothing; at `decide_from - 20 minutes` it runs.

## Report

What you changed per item, every existing assertion you changed, and the last line of
`python -m pytest tests_v5 -q -p no:cacheprovider --basetemp=.pytest_tmp_codex`.
