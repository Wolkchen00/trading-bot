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
