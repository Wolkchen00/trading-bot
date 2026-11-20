# RF-ISSUES-6.md , v5 cycle: deferred items and end-runs

> Rule 2 (No End Runs): anything that comes up mid-build lands here, not in the working diff.
> Impact: high / med / low. Status: OPEN unless stated.

## Deferred strategy modules (evidence exists, not in v5.0; each needs its own verification before use)

1. [med] Gold trend sleeve in the core's idle cash (tournament F6 "GOLD"): GLD above its SMA200 ->
   UGL sized `min(2, 15% / vol20(GLD)) / 2`, never cutting the core. Reported +3 to +5 pp CAGR in every long
   window; count +2 pp (gain concentrated in a few gold bull years; worse than the plain core in 2021, 2022,
   2026 YTD). Competes with the IBS overlay for free cash: needs a combined test and a priority rule first.
2. [med] Intraday noise-band momentum on bear sessions only (tournament F4-A): active only while QQQ is below
   its SMA200 (the core is in cash), TQQQ / SQQQ intraday, flat every night. In-sample +4.5 pp, count +2 pp;
   evidence moderate (t 2.3, 464 sessions, 61 after publication). Needs a minute loop, real-time bars and about
   30 paper round trips to confirm costs. It does not make gains regular (27% of its sessions are positive).
3. [low] BTC trend sleeve, 10% of capital, decided once per US close (tournament F5): helped 2015-2021, added
   nothing since the November 2021 peak; a second market with weekend gaps and fees taken in coin.
4. [low] 50/50 QQQ + SPY blend core (TQQQ + UPRO): 2 to 5 pp less CAGR since 2010 for a 1 to 10 pp shallower
   drawdown; the lower-concentration alternative if the owner wants less dependence on US tech.
5. [low] Parking idle cash in SGOV: worth 0.6 to 1.0 pp a year (1.4 to 2.4 pp in the last five years). Cheaper
   alternative with no code: the owner enrols the account in Alpaca's High-Yield Cash programme.

## Rejected by evidence (do not re-propose without new evidence)

6. Inverse-ETF short legs of any kind: 0 of 435 configurations beat the core; the sleeve alone never beat
   T-bills. If the owner insists on a bear module, the only harmless one was a fixed 10% SQQQ sleeve under a
   strict bear confirmation (expected 0 to +0.4% a year).
7. Intraday catastrophe stops on the product, drawdown-based de-risking, hysteresis bands of 3%, 2-3 day
   confirmation, slower volatility windows, target vol above 45%, walk-forward re-tuning of parameters.
8. RSI(2) with trend filter, trend-filtered IBS, larger overlay sizes (+2/3, fill), ORB, last-half-hour
   momentum, dual momentum, sector rotation, leveraged risk parity, overnight-only, turn-of-month.

## Platform and operations

9. [med] If equity ever reaches $2,000 the account gets Reg T margin and shorting: revisit only then.
10. [low] MOC / MOO orders are whole-share only and marked "contact sales": not used.
11. [low] Taxes: frequent TQQQ round trips create short-term gains and wash sales; Alpaca reports them (1099-B).
    Not modelled anywhere.
12. [med] v4 leftovers to clean after go-live: old state files in the volumes, the three v4 cron scripts on the
    VPS, the stale 115-check script, the paper account's old positions.

13. [med] Off-host dead-man monitor. The watchdog runs on the same VPS, so a total host outage (power, network,
    cron) is silent: no breaker, no decision, no alert. Options: a hosted heartbeat service, or a scheduled job
    outside the VPS that reads a heartbeat topic. Until then the owner's signal is a missing evening summary.
    (Codex round 2, finding 7.)
14. [low] Byte-identical diagnostic floats across independent implementations (freeze NumPy operations and
    reduction order). Not an acceptance requirement; parity is defined on discrete fields plus 1e-9.
15. [low] Catch-up decision after a missed session (removed in round 1 as a second unverified path). Evidence so
    far: a signal executed at the next open did as well as at the close on 2016-2026, a fresh signal was not tested.
16. [low] Overlay as an intraday trade (buy at the next open, sell near that close): the verifier found the whole
    overlay edge in the next regular session and none overnight. Would remove the overlay's overnight gap risk.

## Mid-build end-runs (append below)

17. [process] 2026-11-16 08:49 PT: Codex's quota ran out in the R1 fix round ("try again at 1:12 PM"). The owner's
    request names Codex as the builder; waiting would have stalled the build for hours at every rock. Decision
    (Claude, technical): from the R1 fix round on, the builder of each rock is a fresh Claude agent that sees only
    the written contract and the repo; Claude (main session) stays the reviewer exactly as before (full read,
    own proof run, own adversarial suite written from the contract before reading the code). When Codex is
    available again it reviews the whole v5 tree read-only before anything is deployed. Nothing is pushed or
    deployed without that review and the owner's go.
18. [low] R1 review, accepted as built: nanosecond broker timestamps are truncated to microseconds; precision
    violations raise ValueError in FakeBroker (the recorded 422 can never be reached through the gateway).
19. [low] R1 review, not done: money fields of broker answers are not checked for finiteness in the gateway
    (the snapshot, the rails and the data layer check the numbers they use).
20. [med] Plan Revision 8 items were added by Claude during the build reviews and have not been through a Same
    Page round: `clear-halt` re-bases the peak; rail state under an inter-process lock; `decide-now` / `replay`
    in R5; the digest's last line as the per-session reminder; `MARKET_CLOSED` (the broker clock must say the
    market is open before a strategy order); the `ERROR`, `READINESS` and drill sentences. They are the first
    thing Codex is asked to attack in its whole-tree review.

21. [resolved, 2026-11-19] R3 build: Codex answered BLOCKED. Plain interpolation of the two parts by `theta`
    leaves rounding dust in the overlay (cent floor on buys, 9-decimal floor on sells); `decide` treats any
    overlay above 1e-12 (and any core above 0) as held, so the next overlay entry was skipped (golden
    2011-12-30 and 2016-05-25) and a core entry could be blocked. Resolved in plan Revision 9 and in
    `rf6/contract-r3.md` (`allocate`): complete uncapped fill -> theta 1; opposing moves are a complete
    re-assignment; a part under $1.00 is merged. THE RULE and the golden files are unchanged.
22. [owner request, 2026-11-19] The owner asked for one share of Anthropic (ANT, listed today) in the live
    account, managed by Claude, outside the bot's rule. Done: v4 live container stopped (it would have managed
    a foreign position by its own exit rules) and the three v4 cron lines commented out (backup
    `/root/crontab.backup.20261119` on the host); a GTC bracket order for one share rests at the broker (a buy with a
    take-profit and a stop leg; client id `owner-ant-20261119-1`; its price levels are kept in the operator's
    private notes, not in this public repository).
    Consequence for v5: with ANT in the account the snapshot reports a foreign position and the strategy
    holds (FOREIGN_ASSET). Before go-live either the share is sold, or v5 gets an explicit owner-held symbol
    list (excluded from the foreign check, its value subtracted from the equity used for sizing, shown in the
    digest). To be designed after R3 / R5 are reviewed; needs an Appendix A sentence.

23. [done, 2026-11-19] Item 22 is implemented as plan Revision 10 ("owner-held symbols"): `V5_OWNER_SYMBOLS`
    in `v5/config.py`, the snapshot's engine equity / `owner_positions` / `owner_orders` / `spendable` in
    `v5/executor.py`, the digest's third line in `v5/notify.py`, tests in `tests_v5/test_r3b_owner.py`.
    At deploy the live container needs `V5_OWNER_SYMBOLS=ANT` in its environment (harmless on paper).
24. [process, 2026-11-19] Codex's quota ran out again at about 14:45 PT (back at 19:08 PT) during its final
    verification of R3. As the owner instructed ("when the limit is full, take over"), Claude took over: it
    reviewed and finished R3, wrote R3b itself, and builds R5. Codex's whole-tree review is still owed.

25. [done, 2026-11-19] Independent review of R5 (a fresh Claude agent, read-only, working from the code at
    `368e1f1`, with its own harness and two random explorers). Twelve findings, all reproduced with scripts,
    all fixed the same day (plan Revision 11; tests `tests_v5/test_r5b_inceleme.py`, 43 tests, each failing on
    the old code; the reviewer's scripts were run before and after, kept in the lab folder under
    `rf6/review_r5/`):
    1. CRITICAL: a decided trade that was never sent ended NOT_SUBMITTED silently and the digest claimed the
       action. -> `NOT_SENT` alert, outcome sentence instead of the decision's reasons.
    2. CRITICAL: gates were applied only before the first step of a chain. -> gated on every tick
       (`run_chain(..., blocked)`), also on the first tick of a restarted process and for floor chains.
    3. CRITICAL: a floor confirmation never expired; one reading on a later day could liquidate. -> 600 s.
    4. HIGH: non-final records were abandoned after the close or the next open (stale overlay books, missing
       digest). -> housekeeping settles every open record; catch-up after the open; honest "unknown" digest.
    5. HIGH: a late after-close step stored a non-closing equity; a missing row became a false data doubt.
       -> closing value rules of Revision 11 (4).
    6. MEDIUM: a re-sale during a halt was silent. 7. MEDIUM: floor messages wrong or missing in dry-run,
       with the broker down, and for a sale finished while the process was down. -> Revision 11 (5).
    8. MEDIUM: `decide-now` did not show what would block the engine; `replay` ignored the plan. -> fixed.
    9. MEDIUM (paper only): the drill could sell the whole fund position and ignored shorts. -> fixed.
    10. MEDIUM/LOW: watchdog cooldown survived a recovery; silent without a topic file; no timeout. -> fixed.
    11. LOW: month/total base missed day one; a suppressed extra buy was reported as made; ORDER_FAILED used
        the engine-basis share; the same FLOOR_PENDING text twice a day. -> fixed.
    12. LOW: intermittent faults never reached three in a row; flag off with a real order out raised on every
        tick; a first start after the close reported the day; no MARKET_CLOSED in the heartbeat. -> fixed.
    Lesson for the process: R5 and its suite came from the same hand; the suite passed 712 tests and still
    missed all twelve. A second reader with its own harness is not optional for the runner.
26. [known limits, accepted for now] (a) The floor is evaluated in the decision step only until the day's
    strategy record is final: a breach in the last minutes after the decision acts on the next session.
    (b) Catch-up reports at most the last five sessions before today (one dated summary each): after a longer
    outage the older sessions get no record and no digest (the watchdog covers the outage itself). An account
    that cannot be read from one close until after the next close loses that day's summary. (c) A late report before
    the next open shows the account value of that moment, without a "today" figure. (d) A floor sale waits
    while an order record of an earlier session is still unsettled (the owner gets FLOOR_PENDING); it starts
    as soon as housekeeping has settled that record.

27. [done, 2026-11-19] The reviewer's random explorer (`fuzz2.py`, kept with the review scripts) was run on the
    same seeds before and after: 35 invariant violations before (orders through closed gates, "normal" digests
    over abnormal records, orphan records, silent days), 4 after the fix round, all of one kind: a session whose
    close was missed stayed unreported when the process came back after the next close, or when one account
    reading failed during the catch-up. Fixed by the catch-up rule of Revision 11 (2) (tests at the end of
    `tests_v5/test_r5b_inceleme.py`); the same seeds and one new range are clean.

28. [done, 2026-11-19] Codex's whole-tree review started when its quota returned (19:10 PT) and was stopped by
    its own content filter after seven minutes (the request was worded as an attack exercise). It had read the
    plan and most of the tree and left four reproduction scripts, which the reviewer ran. All four were real:
    (1) a chain resumed after a crash re-sent its order although a foreign asset had appeared; (2) a NETTED
    re-assignment was described as an extra buy; (3) a summary sent late showed the value of the moment under
    an earlier date; (4) `decide-now` ignored the breaker's baseline cross-check, and the health check called
    an incomplete or future-dated heartbeat healthy. While tracing them the reviewer added: a FAILED record
    whose order never reached the broker was reported as refused, and the drill's clean-up step failed on the
    real practice account (dust, cancels still pending). Fix round R5c: Codex built from `rf6/fix-r5c.md`
    (two parts), Claude reviewed with its own suite. Plan Revision 12.
    Still owed: a complete Codex review with a request that is worded as a correctness review.
29. [done, 2026-11-19] Second pass of the independent reviewer on the code after R5c (nine findings; nothing
    that sends a wrong order or loses a session in a normal run, including day one as deployed) and Codex's
    complete whole-tree review, worded as a correctness review (ten findings and three plan problems; its
    scripts are kept with the operator's review files). Together: a chain under way ignored an untrusted
    account reading; a re-planned buy could pass the daily brake; the meaning of `last_equity` was inferred
    from the time of day although the broker names the date (`balance_asof`); one failed reading during a
    catch-up closed a missed session without its summary; FAILED was reported as "refused" whatever happened;
    a record settled a day late sent messages that said "today"; an unknown outcome was never corrected; a
    dry-run dip day lost its reason; `decide-now` said READY outside the window; a state file of the wrong
    shape was silently replaced by defaults; a record of another mode would have been driven; the watchdog
    depended on a container name; the drill could sell shares it had not bought; `clear-halt` took any
    number as the new peak. Fix round R5d: groups A to F built by Codex from `rf6/fix-r5d.md` until its quota
    ran out during the proof, groups G1 to G6 (`rf6/fix-r5d-2.md`) and the older pinned tests by Claude.
    Plan Revision 13. Proof: 855 passed inside the image on the server with the network off. On Windows the
    whole run showed 23 failures, all in two test doubles (settings without a mode, a fake docker without
    the label filter); with the doubles completed the affected files pass.
30. [process, 2026-11-19] Codex's quota ran out twice more that evening: at 21:00 PT, four minutes into a
    run (the owner renewed it), and at 22:18 PT during the R5d proof (back at 02:46 PT on the 20th).
    The owner's rule stands: Codex writes, Claude reviews, and Claude takes over when the quota is gone.
31. [done, 2026-11-20; see item 32] Six-reviewer sweep of the code after R5c (orders, silence, truth, books, rails and
    time, state and operator tools; each reviewer with its own scripts on a frozen copy). First report, rails
    and time, seven findings, each re-run on the code after R5d:
    (a) CRITICAL, open: one unconfirmed reading after the close raises the peak for good; on the next session
        the floor then halts on true readings and sells everything. The floor needs two readings, the peak
        takes one.
    (b) fixed by R5d: one failed reading on the first tick after an outage closed the missed sessions without
        any message.
    (c) HIGH, open in part: a doubt about the baseline is told as "sharp fall today" (the after-hours path
        that produced the doubt is closed by `LATE_CLOSE`; the wording is still wrong on the other paths).
    (d) MEDIUM, open: with a host clock the engine itself calls doubtful, the after-close step still books a
        closing value and sends the summary early.
    (e) MEDIUM, open: the floor's "two readings at least 60 seconds apart" is measured between tick starts,
        not between the readings; one slow tick lets a halt confirm on readings 20 seconds apart.
    (f) LOW, open: a safety sale can still go out, or keep working, after the owner lifted the halt.
    (g) LOW, open: on a brake day `reduce_only` can turn the rule's "no trade" into a small sale.
    Fix round R5e is written (`rf6/fix-r5e.md`, group H). None of this can spend real money today: the live
    process runs dry. All of it is closed before the owner is asked about the live flag.
    Complete tally (2026-11-20 00:40 PT; every script re-run on the code after R5d, the copy and the outputs
    are in the operator's review folder): 56 findings in six reports.
    - truth, 15: closed by R5d: an accepted order called refused, an unknown outcome never corrected, the
      dry-run amount; open: I1 to I11 of the contract (the safety-sale sentences, a real order reported as a
      dry run after the flag is switched off, ERROR's "nothing touched", holdings of another day, the extra
      part called a purchase, CAPPED called "not filled", late delivery without a date, the status tool).
    - silence, 10: closed by R5d: one failed reading, an unknown order resolving in silence; open: J1 to J5
      (one tick with the host clock ahead closes sessions that have not happened; an engine that cannot
      reach the owner; the same message posted on every tick; a heartbeat that reads healthy on a day that
      cannot be decided; an unbounded docker call).
    - books, 7: closed by R5d: the dry-run amount; open: K1 to K3 (a filled order booked CAPPED from a later
      reading; the final record written before the books; a refused order sent again after a restart).
    - state and operator tools, 9: open: L1 to L7 (a damaged file the safety sale does not need stops it;
      `decide-now` and `init-peak` gaps; a halt day summarised as "nothing touched"; the drill's clean-up; a
      rail message lost in a death between two writes; the watchdog's stamp).
    - orders, 8: closed by R5d: a chain buying on an unverified reading (the skeptic could not refute it; it
      was the hole group A1 closed), the repeated floor submit, two wordings; open: M1 (a chain under way
      remembers the floor state instead of measuring it: a pending buy can go out under the floor).
    - The five skeptics: the top findings of rails-time, orders and state-ops stand; those of truth and
      silence reproduce but are behaviour the plan specifies, and are taken as a wording item (I2) and a
      hardening item (J2).
    - Accepted limit, not in the round: money the owner adds or takes out is read as gain or loss (and a
      withdrawal of 15 % trips the daily brake). The owner's rule is that no money moves in or out; if that
      changes, an operator command that records a transfer comes first.
    The contract has 33 items in six groups (H, I, J, K, L, M) with an order of work.
32. [done, 2026-11-20] Fix round R5e. Codex built it from `rf6/fix-r5e.md` in two runs (02:54 to 03:19 PT,
    when its quota ran out with the code mostly written and 45 older tests failing; 07:53 to 08:26 PT from the
    reviewer's note `rf6/fix-r5e-2.md`). The reviewer read both diffs line by line.
    What the review of the first run found (all fixed in the second run):
    - R1, HIGH: the contract's own wording for J1 ("a tick whose clocks disagree books nothing") made a
      disagreement that LASTS completely silent: no floor check, no cutoff message, no summary. Six older
      tests caught it. The rule is now "run on the earlier of the two clocks".
    - R2, HIGH: alerts with a state key had become once-per-key for ever, and two keys never changed: the
      completion of a second safety sale and a foreign asset on a later day would never have been told.
    - R3 to R7: a sale that waits no longer states a holding that was not read; "of this halt" counts floor
      records since the halt only; after `clear-halt` the status line does not say "durdum"; READINESS only
      before the open; the practice watchdog text; the status tool's first line; line 3 sums.
    After the second run: 908 passed on Windows; every older assertion that was changed was read and is one
    of the round's intended changes. The reviewer's own suite (`tests_v5/test_r5e_saldirgan.py`, 29 tests,
    written from the contract) found one more defect (line 3 could add up to 101), and re-running the six
    reviewers' scripts on the build found three (a damaged old record on an ordinary day still read as a
    missed decision; four minutes before a hanging docker is reported; the drill's exit could sell what the
    engine bought in between). Those five items (`rf6/fix-r5e-3.md`, S1 to S5) were written by the reviewer:
    Codex's quota was gone again (back at 12:53 PT).
    Scripts of the sweep that still show their effect, and why they stay: the cash-flow case (accepted
    limit); a refusal that was never written down is sent once more after a restart (the engine cannot know
    it); a summary can arrive twice when the process dies between the post and its "delivered" mark; after
    `clear-halt` inside the decision window the day's summary names the safety sale and not the purchase that
    followed it (line 3 shows the holding); three scripts that build the health JSON or read the queue's
    stored text by hand and so cannot see the new field or the late-delivery prefix.
    Final proof on Windows: 937 passed (855 before the round; 47 new cases in the builder's file, 29 in the
    reviewer's, the rest in older files). One older assertion pinned a line 3 that added up to 101 and was
    changed with S1.
    Plan Revision 14.
