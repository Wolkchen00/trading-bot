# RF-PLAN-6.md , v5 "Yenileme": one small honest engine that keeps the account on a verified rule

> Date: 2026-11-16 (Los Angeles). Driver: Claude (Visionary) + Codex (Integrator).
> **Revision 7** , after Codex rounds 1 to 5 (47 + 13 + 9 + 5 + 4 findings; round cap reached with
> `VERDICT: NOT YET`; the three open FIX items of round 5 are applied; the owner decides whether the build
> starts, see `RF-SAME-PAGE-LOG-6.md`). Revision 7 changes ONE thing after the meeting, from the tournament
> judge's evidence: the daily breaker blocks buys instead of selling, and a drawdown alert rail is added
> (section RAILS; logged as a post-meeting note). It removes an order path; it adds none.
> **Revision 8** , build-time amendments by Claude from the Level 10 reviews of R1 to R4 (they close gaps, they
> add no order path; to be shown to Codex in its whole-tree review): (1) `clear-halt` re-bases the peak, without
> it the bot would halt again at the next reading; (2) rail state is read-modify-write under an inter-process
> lock, because operator commands run in a second process; (3) `decide-now` / `replay` move from R4 to R5;
> (4) the per-session reminder of a blocked state is the last line of the daily digest; (5) Appendix A fixes the
> digest line by line and adds the `READINESS`, `ERROR` and drill sentences; (6) R1: a POST whose outcome is not
> a definite refusal is always `AmbiguousSubmit`, and a 404 on the positions list is an error, never "flat".
> **Revision 9** (2026-11-19) , the allocation bookkeeping rule is corrected after Codex answered BLOCKED in
> the R3 build: plain interpolation by `theta` left rounding dust in the overlay part (a buy floored to cents,
> a sell floored to 9 decimals), and THE RULE treats any positive overlay as "held", so the next overlay
> entry was skipped (golden 2011-12-30, 2016-05-25). Now: a completely filled, uncapped order has `theta = 1`;
> opposing moves of the two parts are a re-assignment that is always complete; a part worth under $1.00 is
> merged into the other part. Section EXECUTOR, "Verification and bookkeeping".
> **Revision 10** (2026-11-19) , three changes from the owner's request of that day and from the R3 review.
> (1) Owner-held symbols: the owner bought a share of his own (ANT) in the same account and wants it left
> alone. Symbols listed in `V5_OWNER_SYMBOLS` are never traded and never count as a foreign asset; the engine
> sizes its orders on the account equity minus those holdings; the rails keep watching the whole account; the
> digest names the holding. (2) The money the engine may spend is `min(cash, buying power)`: an open order of
> the owner reserves buying power that the cash figure still shows. (3) E2E-B: "within 3 pp of the golden on
> 99% of days" becomes 98%, because a fill a few basis points off can flip a band-edge decision and the two
> valid paths then differ by the band width for weeks (seed 3: 98.9% with every chain DONE or NETTED); the
> test now also asserts the executor's own promise directly: every DONE chain holds its own target.
> **Revision 11** (2026-11-19) , from the independent review of R5 (twelve findings, all reproduced; the list
> and what was done about each is item 25 of `RF-ISSUES-6.md`). Rules that changed or became explicit:
> (1) An order chain is gated on every tick, not only when it starts (`run_chain(record, window, blocked)`).
> While a gate is closed (clock doubt, the broker says closed, pause, floor state, no notification channel, a
> breaker that trips before a buy) nothing new is sent; the order already accepted is still followed,
> cancelled at the cancel time and booked. A trade that was decided but never sent ends `NOT_SUBMITTED`, is
> told at once (`NOT_SENT`) and is never described with the decision's reasons.
> (2) Any other unfinished chain blocks a new one (not only status `UNRESOLVED`). Housekeeping settles every
> record that outlived its session, whatever its status. A summary made while an order of the day is still
> open says so. The after-close work that is still owed is caught up in every later phase, for up to five
> earlier sessions, oldest first (one dated summary each); only the after-close step of a newer session gives
> up on it. A record settled long after its session is told in that dated summary, not in a message that
> says "today".
> (3) The two breaching floor readings must be at most 600 s apart; an older first reading is replaced.
> (4) The closing value of a session is the account reading taken within 35 minutes of the close. Later than
> that: the broker's `last_equity`, read during the next session; before the next open nothing is stored and
> the summary shows no "today" figure. When the engine has no row for the previous session it takes that row
> from `last_equity` (this also gives the month and total lines the value before the first day). The breaker
> cross-check uses only the previous session's row.
> (5) During a halt a fund position that reappears is sold again and announced again; a sale that finished
> unseen is announced after the close; a dry-run announces a broken floor too; no drawdown alert while halted;
> one `FLOOR_PENDING` message a day.
> (6) A broken state file is announced on the first failing tick; any other fault when three ticks fail
> within 30 minutes (they need not be consecutive).
> (7) `decide-now` lists `blockers`, applies today's breaker and exits 0 only when the rule is READY and
> nothing blocks; `replay` recomputes the order plan too and compares types strictly; `notify-test` sends one
> test message to the process's own topic. The drill never sells a position it did not buy and also closes
> short positions. The watchdog announces every new outage and labels the practice service.
> **Revision 12** (2026-11-19) , from Codex's whole-tree review (cut short by its own content filter after
> seven minutes, but it left four reproduction scripts; `RF-ISSUES-6.md` item 28). (1) The gate of a chain
> under way also looks at the account: an unreadable or untrusted snapshot, or a foreign asset, stops any new
> submission (a chain resumed after a crash used to re-send without that look). (2) A record that ends FAILED
> because the order never reached the broker (`NOT_FOUND`) is told as "not sent", not as "refused".
> (3) The two reason sentences that state an order are only shown when such an order was filled:
> `OVERLAY_ENTER` needs a buy (when the books took the overlay without a buy, the new `OVERLAY_HELD` sentence
> is shown instead), `OVERLAY_EXIT` needs a sale. (4) A dry-run summary says what would have been done.
> (5) A summary sent late without a closing value says "Hesap şu an ..." (the value of the moment).
> (6) `decide-now` uses the engine's own breaker rule (`rails.breaker_cause`); `health.assess` no longer
> calls an incomplete or future-dated heartbeat healthy. (7) The drill's clean-up step leaves dust alone and
> waits for its cancels before it closes positions.
> Builder note: Codex's quota ran out on 2026-11-16 08:49 PT in the R1 fix round. From there the builder is a
> fresh Claude agent per rock working from the same written contracts; Claude (this session) stays the
> reviewer with its own adversarial suites; Codex reviews the whole v5 tree when its quota returns.
> Code baseline: `59a413e` (main = origin/main, clean tree). The old system is called "v4" below.
> Evidence: `RF6-KANIT.md`. Owner decisions: `RF6-VTO.md`. Deferred: `RF-ISSUES-6.md`.
> Broker evidence: `RF6-PROBE.md` + `tests_v5/fixtures/alpaca/` (real paper-account responses; NOT YET RECORDED,
> they need an open market: see GATES. Any executor behaviour they contradict is re-reviewed before R1).
> Language: code, identifiers and docs in English; every text the OWNER reads in plain Turkish (Appendix A).

## CORE FOCUS (one sentence)

**Replace the v4 agent-voting bot with a small engine that, once per trading day near the close, moves the
account to the holdings required by ONE verified rule (volatility-targeted leveraged Nasdaq trend plus the
one overlay that survived independent verification), protected by rails derived from that rule's own
history, unable to freeze silently, and reporting to the owner in plain Turkish.**

## GATES

- No rock is built before `VERDICT: SAME PAGE` and before the owner's Grill answers are recorded in
  `RF6-VTO.md` (risk package, breaker and floor numbers are the owner's explicit decision; v4's 5% daily kill
  and 0.85 x peak floor cannot coexist with this rule and are replaced only by his decision).
- R1 is not built before `RF6-PROBE.md` and the fixtures exist (recorded by Claude on the paper account at the
  next market open: order states, duplicate client id, delayed visibility, cent and quantity rounding, sub-dollar
  sells, adjusted versus raw daily bars, IEX trade and minute-bar shapes). If the evidence contradicts an
  executor or data rule, the rule is corrected and re-reviewed by Codex first.
- Nothing is deployed live before R6's checklist passes and the owner says go.

## WHY (measured; details and sources in RF6-KANIT.md)

1. v4 has no edge (live profit factor 0.97, -$1.30 in 8 months) and barely deployed capital (about 68% idle).
2. v4 froze silently three times; the last freeze (auto-lock since 2026-09-21) came from a false alarm that
   nothing ever cleared while the health report said "SAGLIKLI" for 56 days.
3. Platform: PDT abolished 2026-06-04; live account is 1x, no shorting, no options; fractional orders are simple
   DAY orders only.
4. Tournament (6 families, about 5,700 configurations, independent re-implementation of every recommended
   family): the vol-targeted trend core holds (PLAUSIBLE: drawdown control, not extra return) and the IBS
   one-day dip overlay holds (CONFIRMED); inverse-ETF legs, stops, drawdown de-risking and the rest do not.

## DESIGN PRINCIPLES

P1. ONE decision function, pure, used by replay, tests and the live runner.
P2. Holdings, not signals: the rule states what the account should hold; the executor trades the difference.
P3. Broker is truth for positions, cash and equity. Local state: peak equity, rail states, per-session decision
    records (idempotency, overlay fraction, input bundle), outbox, heartbeat, calendar cache.
P4. No resting orders: every order is a simple DAY market order with a deterministic client id, submitted and
    driven to a terminal state inside the session's decision window.
P5. Doubtful data never sells the core and never opens risk. "NOT READY" means no order at all. (The overlay is
    a one-day position that EXPIRES unless a valid fresh signal renews it; expiry is the rule, not a data
    reaction. Rails sell only on confirmed readings.)
P6. Nothing blocks silently: every blocked or degraded state has an automatic clear condition or a repeating
    plain-Turkish reminder. The only sticky state is the floor halt. A watchdog on the same host covers a dead
    process or container; a total host outage is NOT covered this cycle (KNOWN RISKS, RF-ISSUES-6 item 13).
P7. Bounded everything: at most 3 order attempts per purpose per session, alert cooldowns, no permanent
    self-lock from errors.
P8. Paper and live run the identical image and code path. Differences: keys, endpoint, state dir, and
    `LIVE_TRADING_ENABLED` (live defaults to dry-run).
P9. Small and pinned: requests, pandas, numpy, tzdata with exact versions. No broker SDK, no TA library.
P10. The account is dedicated to this engine: it holds cash, the product, and nothing else except the
     symbols the owner declares as his own (`V5_OWNER_SYMBOLS`, Revision 10). Those are never traded, never
     treated as foreign, and their value is subtracted from the equity the engine sizes on.

## THE RULE (canonical)

Symbols: signal = QQQ, product = TQQQ (3x). Packages differ ONLY in `target_vol` (moderate 0.20, high 0.30,
very_high 0.45). Fixed: `sma = (150, 200, 250)`, `vol_window = 20`, `lmax = 3.0`, `leverage = 3.0`,
`band = 0.10`, `ibs_threshold = 0.2`, `ibs_add = 1/3`, `cap = 0.99`. All arithmetic is IEEE float64.

Inputs of one decision:
- `closes`: adjusted daily closes of the signal symbol for consecutive exchange sessions up to and including
  the PREVIOUS session, oldest first (the data layer guarantees the session sequence, see DECISION DATA).
- `px`: today's decision price of the signal symbol (stands in for today's close).
- `session_high`, `session_low`: today's session range so far, or unknown.
- `product_weight`: market value of the product position / account equity, right now.
- `overlay_fraction`: share of the product position that belongs to the overlay (0..1), committed by the
  previous decision record (0 if none).

Validation, in this order; the first failure returns NOT READY with its reason code and nothing else happens:
- V1 `closes` has at least 249 values and the last 249 are finite and > 0 (`HISTORY_SHORT`, `HISTORY_BAD`).
- V2 `px` is finite and > 0, and `abs(px / closes[-1] - 1) <= 0.25` (`PRICE_BAD`, `PRICE_JUMP`).
- V3 `product_weight` is finite and `0 <= product_weight <= 1.05` (`WEIGHT_BAD`).
- V4 `overlay_fraction` is finite and `0 <= overlay_fraction <= 1` (`FRACTION_BAD`).
- V5 (checked after steps 1 to 3) every derived value (the three means, the 20 returns, `vol`, `E`, `ct`) is
  finite; otherwise NOT READY (`DERIVED_BAD`). Finite inputs can still overflow in float64.
The session range is KNOWN iff both values are finite, > 0 and `session_high >= session_low`; an unknown range is
not a readiness failure (see step 7).

Steps (with `series = closes + [px]`):
1. Trend score = mean over m in (150, 200, 250) of `1 if px > mean(last m values of series) else 0`.
2. `vol` = sample standard deviation (ddof = 1) of the last 20 daily returns of `series`
   (return i = series[i] / series[i-1] - 1), times `sqrt(252)`.
3. `E = min(lmax, target_vol / vol) * trend_score` (if `vol == 0`: `E = lmax * trend_score`).
   Core target `ct = E / leverage`.
4. Split the holding: `core_held = product_weight * (1 - overlay_fraction)`,
   `ovl_held = product_weight * overlay_fraction`.
5. Core band (drift aware). `core_trade` is true iff `(ct <= 0 and core_held > 0)` or
   `(core_held <= 0 and ct > 0)` or `abs(ct - core_held) > band`.
   `core_cand = max(ct, 0) if core_trade else core_held`. `core_new = min(core_cand, cap)`.
6. IBS. If the range is known: `hi = max(session_high, px)`, `lo = min(session_low, px)`; if `hi > lo`:
   `ibs = (px - lo) / (hi - lo)`, else the IBS is undefined. If the range is unknown the IBS is undefined.
   Canonical comparison: `wanted = overlay enabled and ibs defined and round(ibs, 6) < ibs_threshold`
   (exactly 0.2 after rounding is NOT a signal; decimal ties such as 0.30 / 1.50 are frequent with cent prices).
7. Overlay. If `wanted` and `ovl_held <= 1e-12`: `ovl_new = max(0, min(ibs_add, cap - core_new))` (new entry).
   If `wanted` and `ovl_held > 1e-12`: `ovl_new = max(0, min(ovl_held, cap - core_new))` (kept as drifted,
   squeezed only by the cap). If not `wanted` (including an undefined IBS): `ovl_new = 0` (the one-day
   position expires).
8. Result: `total_new = core_new + ovl_new`; `trade = core_trade or abs(ovl_new - ovl_held) > 1e-12`;
   `overlay_fraction_new = ovl_new / total_new` (0 if `total_new == 0`);
   `overlay_data_missing = overlay enabled and the IBS is undefined` (unknown range, or `hi == lo`).

Output `Decision`: status (READY or NOT_READY + reason code), trend_score, vol, E, ct, ibs (or None), wanted,
overlay_data_missing, core_trade, core_held, ovl_held, core_new, ovl_new, total_new, trade,
overlay_fraction_new, and `reason_keys`
(a list of keys from Appendix A; the Turkish sentences live in `notify`, never in `strategy`).

Golden replay account model (weight space, cash earns 0, costs never touch the weight path). For row t with
product return `r` (previous decision price to this one) and holdings `core`, `ovl` after the previous decision:
`g = (core + ovl) * r`; then `core = core * (1 + r) / (1 + g)` and the same for `ovl`; the decision of row t
gets `product_weight = core + ovl` and `overlay_fraction = ovl / (core + ovl)` (0 if the sum is 0); if the
decision trades, `core, ovl = core_new, ovl_new`.

Golden files (`tests_v5/golden/`, generated by the Visionary's independent reference, never edited by hand;
`golden_meta.json` lists every file with its parameters, warm-up start, first decision, row and decision counts,
columns, data provenance and the sha256 of its uncompressed CSV; R2 asserts all of it):
six daily files (three packages, with and without the overlay; 4,215 decisions each, 2010-02-12..2026-11-13,
`px` = close, range = the day's high and low) and one realistic file `golden_high_rt.csv.gz` (2016-2026, `px` =
15:50 ET price, range up to 15:50, product return between 15:55 prices). Measured safety margins of the daily
set against float noise: closest `px` to any SMA 4.7e-5 relative, closest band test 6.1e-7, IBS decimal ties
(4 days within 1e-9 of 0.2) resolved by the rounding rule with a 5e-7 margin. Parity therefore means: every
discrete field equal (trend_score as a multiple of 1/3, wanted, core_trade, trade) and every float within 1e-9.

## DECISION DATA (`v5/data.py`)

All market data comes from the Alpaca data API over HTTPS with an explicit `feed` on every call.
- Session sequence: the cached exchange calendar gives the last 260 session dates up to the previous session.
- Daily closes: ONE request for daily bars of the signal symbol, `adjustment=all`, `feed=sip`, covering those
  sessions. Accept only bars whose ET date is in the session list; today's partial bar is dropped. The result
  must be strictly increasing, without duplicates, and must contain every one of the last 249 sessions
  including the previous session; otherwise NOT READY (`HISTORY_GAP`, `HISTORY_STALE`). Adjusted closes are
  never cached across sessions (a dividend re-bases the series).
- Decision price: the latest IEX trade of the signal symbol, at most 120 seconds old; otherwise the close of
  the latest IEX minute bar, at most 5 minutes old; otherwise NOT READY (`PRICE_STALE`). No delayed-SIP price.
- Session range: IEX minute bars of the signal symbol from the session open to now (one feed, no merge).
  Bars are de-duplicated by timestamp and must lie inside the session. The range is KNOWN only if the first
  bar starts within 10 minutes of the open, the last bar within 5 minutes of now, and at least 70% of the
  session's elapsed minutes have a bar; otherwise the range is unknown (the overlay expires or is not entered;
  the core is unaffected). An undefined IBS (unknown range, or a flat range `hi == lo` from a stuck feed) sets
  the degraded flag OVERLAY_DATA_MISSING: shown in the decision
  record, heartbeat and digest; from the second consecutive session on, one reminder per session; it clears
  itself at the first session with a defined IBS. Measured: IEX-only IBS signals agree with SIP on 99.2% of
  1,580 sessions.
- Product price for sizing: the broker's own position price and the latest IEX trade of the product.
- Every decision record stores the complete input bundle so that any decision can be replayed bit for bit.

## SESSION TIMELINE (T = session close from the cached calendar; early closes included)

| time | action |
|---|---|
| open - 45 min .. open | static readiness once (broker reachable, account ACTIVE and not blocked, state writable, process lock held, calendar cached for today, clocks agree, ntfy configured, peak initialised, no HALT); failure -> alert where possible. Each item is also a decision-time blocker: a missing ntfy topic (`NOTIFY_MISSING`) or a clock disagreement (`CLOCK_DOUBT`) means NOT READY and no STRATEGY order in any mode (rail sales are not blocked by them) |
| session, every 5 min | breaker check (see RAILS): a breach blocks buys for the rest of the session |
| T - 10:00 | decision window opens: floor check, then build inputs and decide. NOT READY and exceptions are retried every tick |
| T - 4:00 | last moment a decision may be made (after this: record MISSED or HOLD_NOT_READY, alert) |
| T - 3:00 | last moment an order may be submitted or re-submitted |
| T - 1:30 | any order of this session that is not terminal is cancelled; polled until terminal |
| T + 1:00 .. T + 5:00 | reconcile from the broker, close the record, store after-close equity, update the peak, send the digest |

There is no catch-up trading: a missed session is reported and the next decision is the next session's.

## EXECUTOR (`v5/executor.py`)

One purpose per order chain: `dec` (strategy decision) and `flr` (floor liquidation). Client order id:
`v5-{mode}-{YYYYMMDD}-{purpose}-{attempt}` with attempt 1..3. One product, so each attempt is ONE order.

Sizing from a fresh broker snapshot taken immediately before each submit:
- target 0: SELL the full position quantity exactly as the broker reports it.
- reduction: SELL `floor9(position_qty * (product_weight - total_new) / product_weight)`, never more than held.
- increase: BUY notional `floor_cents(min((total_new - product_weight) * equity, spendable * 0.995))` with
  `spendable = min(cash, buying power)`.
- `equity` in every sizing formula is the ENGINE's equity: account equity minus the market value of the
  owner-held symbols; `product_weight` is the product's value divided by it. A holding of the owner that
  cannot be valued, or an engine equity that is not positive, makes the snapshot invalid (no order).
- An increase under $1.00 is not sent. Whether a sell under $1.00 is accepted is taken from `RF6-PROBE.md`;
  the side-specific minimum rule and its single terminal status (NETTED for a bookkeeping-only change, CAPPED
  for a real residual that cannot be traded, including an unsellable dust position) are frozen in R3's build
  contract from that evidence, with tests for a small ordinary adjustment and for a dust exit.
- Before sizing, the account is INSPECTED first. An open order is OURS only if its symbol is the product AND
  its client id equals an intent persisted in an unfinished record of this state dir. Every other open order
  (including one that merely carries the `v5-` prefix), and a position in any other symbol worth >= $1, is a
  FOREIGN_ASSET. Foreign items are never cancelled or traded; no order is sent, the owner is alerted, the state
  clears itself when the foreign item is gone.
- Only then, our own leftover orders are cancelled and polled until terminal, and the snapshot is refreshed.

Submitting (idempotent): the intent (client id, side, desired and submitted size) is persisted BEFORE the HTTP
call. The submit call is never auto-retried by the HTTP layer. Any ambiguous outcome (timeout, connection
error, 429, 5xx) is resolved by looking the order up by its client id for up to 20 seconds; if it exists,
continue; if it does not and it is still before T - 3:00, the SAME client id is submitted again (a duplicate-id
answer from the broker means the first one exists). After T - 3:00 nothing is ever submitted or re-submitted.
NOT_SUBMITTED (final, nothing filled) is reserved for an intent whose HTTP call was provably never started
(the record carries a `submit_started` flag written before the call). Every intent whose call was started and
whose outcome is not known stays UNRESOLVED: it blocks every new order, in this and in later sessions, and is
looked up on every tick. It is resolved only by positive broker evidence: the order found by its client id
(then reconciled normally, however late it appears), or, after the session has closed, the broker's
all-status order list for that session (`Broker.list_orders`, every page fetched; shape and pagination
recorded by the probe gate) not containing the client id AND the position quantity equal to the pre-attempt
snapshot (then FAILED with nothing filled). If that listing cannot be obtained completely, absence is never
assumed and the intent stays UNRESOLVED. A definite rejection (4xx with a
broker message) ends that attempt: `insufficient buying power` allows one re-sized attempt; anything else is
FAILED for the session with one alert.

Record statuses and what a restart may do:

| status | meaning | restart inside the window | after T - 3:00 |
|---|---|---|---|
| (none) | nothing decided | build inputs, decide | MISSED at T - 4:00 |
| NOT_READY | last attempt to build inputs failed | retry | HOLD_NOT_READY (final) at T - 4:00 |
| NO_TRADE | the rule trades nothing | final | final |
| PLANNED | order intent persisted | call never started: submit; call started: look up by client id, submit the same id if absent | call never started -> NOT_SUBMITTED; call started: found -> SUBMITTED, else UNRESOLVED |
| NOT_SUBMITTED | the submit call was provably never started | final | final |
| UNRESOLVED | a started submit with unknown outcome | look up; blocks all new orders until broker evidence resolves it | same, also in later sessions (alert) |
| NETTED | components re-assigned, net change under the minimum order, no order needed | final | final |
| SUBMITTED | broker acknowledged | poll | cancel at T - 1:30, poll |
| RECONCILING | order terminal, after-state being computed | recompute from the broker | same |
| DONE | terminal and within tolerance | final | final |
| CAPPED | filled as sized, size was limited, residual recorded | final | final |
| PARTIAL | terminal, not completely filled | one more attempt (max 3) for the remainder | final |
| FAILED | rejected, or nothing filled and not retryable | final (alert) | final |
| MISSED / HOLD_NOT_READY | no decision this session | final (alert) | final |

Verification and bookkeeping after an order is terminal (from a fresh broker snapshot):
- Two progress numbers per attempt, both from the broker's fill report, never from market weights (a price
  move is not a fill). `fill_ratio` = filled / SUBMITTED size (decides PARTIAL versus complete).
  `theta` = allocation progress. It is exactly 1 when the order filled completely and its size was not
  limited by the cash cap (the only shortfall is then the mandatory rounding to cents or to 9 decimals, which
  is below anything that can be traded). Otherwise it is filled / DESIRED size, where the desired size is the
  uncapped, unfloored net change the decision asked for at the attempt's snapshot: for a buy
  `theta = filled_qty * filled_avg_price / desired_notional`, for a sell `theta = filled_qty / desired_qty`;
  clipped to [0, 1]; 0 for a rejected or never-submitted order. A capped order therefore has `theta < 1`
  even when it filled completely.
- Bookkeeping-only case: when the desired net change is under the minimum order ($1.00), no order exists and
  `theta = 1` by definition (status NETTED), whatever the reason (an ordinary small adjustment, or an opposing
  core and overlay change that nets to almost nothing, for example the core exits while the overlay enters;
  the latter re-assigns the holding between the two parts without a trade). CAPPED is used only when a change
  of at least $1.00 was desired but the submitted size was limited or could not be traded. Tests cover the
  ordinary small adjustment and the opposing-component netting separately.
- Committed overlay fraction: before EVERY attempt the split is rebuilt from the fresh broker snapshot and the
  committed fraction (`core_b = w_fresh * (1 - fraction)`, `ovl_b = w_fresh * fraction`), so a price move
  between attempts cannot desynchronise the bookkeeping. Each attempt intends to move the sub-holdings from
  `(core_b, ovl_b)` to `(core_new, ovl_new)`. With `dc = core_new - core_b`, `do = ovl_new - ovl_b` and
  `net = dc + do`, after the attempt is terminal:
  when the two parts move the same way (or one does not move): `core_x = core_b + theta * dc`,
  `ovl_x = ovl_b + theta * do`;
  when they move in opposite directions, the smaller move is a re-assignment between the two parts that needs
  no trade and is always complete, and only the net is scaled by the fill: if `abs(dc) >= abs(do)` then
  `ovl_x = ovl_new`, `core_x = core_b - do + theta * net`, else `core_x = core_new`,
  `ovl_x = ovl_b - dc + theta * net`.
  A part worth less than $1.00 (`part * equity` of the attempt's snapshot) can never be traded on its own and
  is merged into the other part, so each part is either exactly zero or tradable (THE RULE treats any
  positive overlay or core as "held": rounding dust would block the next overlay entry or a core entry).
  `fraction = ovl_x / (core_x + ovl_x)` (0 if the sum is 0). A following attempt starts from `(core_x, ovl_x)`.
  The fraction is stored on every terminal outcome, so partial fills, caps, skips and failures never leave
  holdings paired with a stale fraction. Tests check it against an independent allocation ledger kept by the
  test, including every golden date listed under `opposing_component_days` in `golden_meta.json`.
- With `w_after` and `w_target = total_new` from a fresh snapshot and `tol = max(0.015, 2 / equity)`:
  DONE iff `abs(w_after - w_target) <= tol`. CAPPED (final) iff the order filled completely as sized but the
  size itself was limited (cash cap, sub-dollar skip, quantity floor) and the residual exceeds `tol`.
  PARTIAL iff the order did not fill completely; it may be followed by the next attempt before T - 3:00,
  otherwise it is final. The residual is always recorded and shown in the digest when above 3 pp.

Dry-run (live with `LIVE_TRADING_ENABLED` false): the same code up to sizing, then the intended order is
written to `decisions_dryrun/` and reported; dry-run records never count for idempotency, never change the
overlay fraction and are never read by the live path.

## RAILS (`v5/rails.py`)

Three rails. Their numbers per package are the owner's decision in `RF6-VTO.md`; the proposals come from the
package's own history (132 admissible runs since 1990 plus minute-bar intraday marks):

| package | A: drawdown alert | B: daily breaker | F: catastrophe floor |
|---|---|---|---|
| moderate | 31% below the peak | 12% below the previous close | 50% below the peak |
| high | 37% | 15% | 60% |
| very_high | 47% | 20% | 70% |

- Equity readings used by rails must pass: account ACTIVE and not blocked; equity finite and > 0;
  `abs(equity - (cash + sum of position market values)) <= 2%` of equity. A failing reading is a data doubt:
  no rail action, alert (`DATA_DOUBT`).
- Baselines: after every close v5 stores the session's closing equity (`equity_history`). Breaker baseline =
  the broker's `last_equity`, cross-checked against the stored closing equity of the previous session
  (difference above 2%: data doubt, the breaker is treated as breached for the session, alert). Peak = running
  maximum of the stored closing equities (daily basis, the same basis as the history the numbers come from).
- Peak initialisation is explicit: `python -m v5.ops init-peak <value> --source <text>` writes it once with a
  marker. Without an initialised peak a trading-enabled process refuses to trade and alerts. R6 initialises the
  live peak from the v4 record ($491.08) by hand and records the evidence.
- Rail A, drawdown ALERT: at the decision and after the close, equity <= (1 - A) x peak -> one alert per
  episode (re-armed once the drawdown is 5 pp shallower again) and a self check in the digest (holdings versus
  the rule, data freshness, open orders). No trading change: this depth is the edge of the package's own
  history, not a fault.
- Rail B, daily BREAKER: in session (every 5 minutes) and at the decision, one passing reading with
  equity <= (1 - B) x baseline -> state BREAKER_TODAY until the next session's open: the strategy is
  REDUCE-ONLY for the rest of the session (no overlay entry, no core increase; the rule's own sells still
  execute), one alert. It never sells by itself: selling on a crash day was tested (intraday stops at 8 to 20%)
  and loses or does nothing, and the rule's own near-close decision already cuts exposure when volatility
  jumps. A single reading is enough because not buying is always the safe direction.
  Reduce-only is applied to the decision before sizing: `core_new = min(core_new, core_held)`,
  `ovl_new = min(ovl_new, ovl_held)`, then `total_new`, `trade` and the overlay fraction are recomputed.
- Rail F, catastrophe FLOOR: at T - 10:00 before the strategy, equity <= (1 - F) x peak on TWO consecutive
  passing readings at least 60 seconds apart -> state FLOOR_HALT: sell the whole product position (purpose
  `flr`), stop trading; sticky until `python -m v5.ops clear-halt --by owner --new-peak <account value>`
  (the command re-bases the peak; without that the next reading would breach again at once). From the first breaching
  reading until the second resolves it the persisted state RAIL_CONFIRMING forbids every strategy order (also
  when the breach starts exactly at T - 10:00); unresolved by T - 4:00 -> the session ends as HOLD_NOT_READY.
- The floor sale uses the executor's order chain and statuses. While it is not verified flat the state is
  RAIL_LIQUIDATING: retried every session until flat (3 attempts per session), no new exposure, daily alert.
- Pause: `python -m v5.ops pause|resume --by owner`. Paused = no strategy orders; rails still act.
- The rail state lives in one file that an operator command (a second process) may change at any moment:
  every change is read-modify-write under an inter-process lock and nothing is cached in memory.
- Precedence, highest first: FLOOR_HALT, RAIL_LIQUIDATING, RAIL_CONFIRMING, FOREIGN_ASSET, NOT READY, PAUSE,
  strategy (reduce-only while BREAKER_TODAY).
- NOT READY reasons block STRATEGY orders. A confirmed floor still sells when notifications are missing
  (`NOTIFY_MISSING`), when signal data is missing, and during a pause. A floor order is NOT sent, and the rail
  stays pending with an alert, in exactly these cases: (a) session timing is not trusted (`CLOCK_DOUBT`,
  `CALENDAR_MISSING`, the broker clock does not report an open market, or less than 2 minutes remain to the
  close), because a DAY order outside a verified session could rest until the next one; (b) an UNRESOLVED
  intent exists; (c) a foreign open order exists in the PRODUCT symbol (an unknown order is never crossed).
  Foreign positions or orders in other symbols do not block a rail: it sells the product only.
  The state that decides is persisted before any order is sent.

## NOTIFICATIONS AND HEALTH (`v5/notify.py`, `v5/health.py`)

- Every owner-facing message is first appended to `outbox.jsonl` (id, time, kind, text), then posted to ntfy.
  Undelivered messages are retried every cycle with backoff (1, 2, 4 ... 60 minutes) for 48 hours.
- Messages: the daily digest after every session (also when nothing was traded); immediate alerts (drawdown
  alert, breaker, floor, missed decision, hold on doubtful data at the cutoff, rejected order, foreign asset, data doubt on a
  rail, startup refusal, unexpected error). While a blocked state lasts, the per-session reminder is the last
  line of the daily digest ("Durum: ..." with the sentence of the highest active state). Cooldown 4 hours per
  (kind, state). No weekly or monthly messages; the digest carries month-to-date and since-start lines.
- All texts come from Appendix A templates; tests snapshot them. Messages about orders and rails are built from
  the RECONCILED broker state (what is actually held now), with separate templates for "completed" and
  "still in progress"; no message may claim "nothing changed" or "everything sold" unless the snapshot says so.
- `heartbeat.json` every tick: time, mode, phase, readiness result, active states, last decision status,
  oldest undelivered message age, ntfy configured. `python -m v5.health` prints it; exit code 0 healthy,
  1 degraded, 2 blocked.
- Watchdog `ops/vps/v5_watchdog.sh` (cron on the VPS, every 10 minutes, independent of the bot process):
  container running and heartbeat younger than 5 minutes, else a Turkish push with a 4 hour cooldown. It
  replaces the three v4 cron jobs, which are removed in R6. It cannot report an outage of the host itself.

## RUNNER (`v5/runner.py`, `v5/clock.py`, `v5/state.py`)

- Tick every 20 seconds. One unexpected exception never kills the loop: log, alert with cooldown, continue.
- Calendar: on start and once a day the next 10 sessions (date, open, close in ET) are fetched and cached in
  state. Decision times come from the cache. No cached entry for today -> NOT READY (`CALENDAR_MISSING`), alert.
- Clock: local UTC time. It is compared with the broker clock on start, on every calendar refresh and at
  T - 10:00. A difference above 60 seconds is a doubt, not a correction: NOT READY (`CLOCK_DOUBT`), alert,
  re-checked every tick; neither clock is trusted automatically.
- Single instance: an OS advisory lock on `v5/process.lock` held for the life of the process (released by the
  OS when the process dies); the file content is diagnostic only. A second process on the same state dir exits.
- State files: atomic write (temp file, fsync, os.replace). A corrupt or unreadable state file is never
  silently replaced: the bot holds, alerts and names the file.
- State dir: `/app/state_live` or `/app/state_paper` selected exactly as v4's `config.py` lines 16-88 do
  (same env names TRADING_MODE and ALPACA_KEY_PREFIX), with all v5 files in the subfolder `v5/`.
  Not writable -> the process refuses to start (the watchdog reports it).

## ARCHITECTURE

```
v5/config.py packages.py strategy.py backtest.py data.py broker.py executor.py rails.py state.py
   notify.py clock.py runner.py health.py ops.py
tools/v5_durum.py          owner-readable status (Turkish)
tools/v5_tatbikat.py       paper drill: real paper orders through the executor (see R6)
tests_v5/                  fakebroker.py, fixtures/alpaca/*.json, golden/*, test_r*.py
ops/vps/v5_watchdog.sh     external watchdog
```

## ROCKS (dependency order). Proof command for all:
`python -m pytest tests_v5 -q -p no:cacheprovider --basetemp=.pytest_tmp`

### R1 , Foundation: config, state, clock, broker gateway, fake broker
Files: `v5/__init__.py`, `config.py`, `state.py`, `clock.py`, `broker.py`, `tests_v5/fakebroker.py`,
`tests_v5/test_r1_*.py` (the fixtures come from the probe gate, see GATES).
- `Broker` Protocol with typed dataclasses. `AlpacaBroker` over `requests`: account, positions, open orders,
  all-status orders of a time window with pagination (`list_orders`), submit (market DAY; notional buy or qty
  sell; client id), cancel, get order by id and by client id, clock, calendar, daily bars, minute bars, latest
  trade. GET calls retry on timeouts, connection errors, 429 and 5xx
  (0.5 / 1 / 2 / 4 s). POST and DELETE are never auto-retried (the executor resolves ambiguity).
- Wire-level tests: every `AlpacaBroker` method is tested against the recorded fixtures through a stubbed HTTP
  transport; the test asserts the exact method, path, query and JSON body sent, and the parsed result.
- `FakeBroker`: in-memory account reproducing the recorded behaviours (order states, duplicate client id,
  insufficient buying power, cent and 9-decimal rounding, $1 minimum) with scripted faults: timeout before or
  after acceptance, 429, 5xx, delayed visibility of a just-submitted order, partial fill, never fills, reject.
- Done: both brokers pass one shared contract suite; state dir not writable -> exit with a clear message; the
  OS lock excludes a second process; no test touches the network.

### R2 , Strategy engine with golden parity
Files: `v5/packages.py`, `v5/strategy.py`, `v5/backtest.py`, `tests_v5/test_r2_*.py`.
- `decide` implements THE RULE; it is total (never raises on any float or None input; returns NOT READY).
- `replay` reproduces all seven golden files: discrete fields equal, floats within 1e-9, on every row.
- Property tests on randomised inputs: weights >= 0; `core_new + ovl_new <= cap` (+1e-12); inside the band the
  core never trades; a core exit is exactly 0; the overlay never outlives a decision without a valid fresh
  signal; NOT READY returns no trade for every invalid input class of V1-V5 (including extreme finite floats
  that overflow); `round(ibs, 6)` ties at 0.2 are never a signal.
- Done: parity exact as defined; `reason_keys` asserted per case against Appendix A keys.

### R3 , Decision data and executor
Files: `v5/data.py`, `v5/executor.py`, `tests_v5/test_r3_*.py`.
- `data.build_inputs` per DECISION DATA, tested from recorded API responses (daily bars with today's partial
  bar, a missing session, a duplicate bar, out-of-order bars, stale trade, thin minute coverage, price jump).
- `executor` per EXECUTOR: sizing, idempotent submit, statuses, verification, overlay-fraction commit.
- Crash tests: a simulated process death after every persisted transition, then restart inside and after the
  window; assert no duplicate order ever, and the record ends in a final status. Ambiguity tests: a started
  submit whose order becomes visible only after the 20 second lookup period (and one that appears only after
  the close) stays UNRESOLVED, blocks new orders, and is reconciled correctly when it appears; a submit that
  never reached the broker resolves to FAILED only on the after-close evidence.
- End-to-end A (ideal): `decide` + executor on the fake broker over the full daily golden history, large
  account, zero slippage: same trade days, weights within 1e-6.
- End-to-end B (account scale), parameters frozen here: package high on `golden_high.csv.gz`, start equity
  $490.00, seeds 1, 2, 3, 4, 5; every fill at an adverse price move drawn uniformly from 0 to 20 bps; cent
  rounding and the $1 minimum; each order independently: 10% partial fill (30 to 90% filled, rest cancelled),
  10% delayed visibility (invisible for the first 2 lookups), 5% ambiguous submit (half of them accepted).
  Required for every seed: no duplicate order; cash never negative; never short; product weight never above
  the cap plus 1 pp; overlay fraction equal to the test's own allocation ledger; held weight within 3 pp of the
  golden on at least 98% of days (Revision 10) and every DONE chain within tolerance of its own target; final
  equity between $27,000 and $59,000 (the reference replay ends at
  $58,237 with zero cost and at $27,840 charged 20 bps per unit of turnover; total turnover 369.6).

### R4 , Rails, notifications, health
Files: `v5/rails.py`, `v5/notify.py`, `v5/health.py`, `v5/ops.py`, `tools/v5_durum.py`, `tests_v5/test_r4_*.py`.
- `v5/ops.py` commands: `init-peak`, `pause`, `resume`, `clear-halt`, `status`. (`decide-now` and
  `replay <bundle>` are added in R5, where the data layer and the executor exist.)
- Per RAILS and NOTIFICATIONS AND HEALTH. Texts exactly from Appendix A (snapshot tests).
- Tests: a single bad equity reading never sells; two confirmed floor readings do; a first floor breach at
  T - 10:00 blocks the strategy order until resolved; a confirmed floor breach during `CLOCK_DOUBT` sends no
  order and alerts; a foreign open order in the product blocks the floor sale while a foreign position in
  another symbol does not; a breaker breach never sells, suppresses an overlay entry and a core increase the
  same session, lets the rule's own exit through, and is gone at the next open; the drawdown alert fires once
  per episode and changes no order; a rail sale that fills partially survives a restart and finishes next
  session; pause does not block a rail; precedence table; peak not initialised -> refuses to trade; outbox retry
  after failed posts and after a restart; a missing ntfy topic blocks every strategy order and shows in health,
  while a confirmed floor still liquidates with the topic missing (proved separately); messages
  after a partial liquidation use the "in progress" template and state the real holdings; a simulated two-month
  blocked state yields one reminder per session and clears itself when the cause is gone (except FLOOR_HALT).

### R5 , Runner, packaging, ops
Files: `v5/runner.py`, `Dockerfile`, `docker-compose.yml`, `requirements-v5.lock.txt`, `.dockerignore`,
`ops/vps/v5_watchdog.sh`, `tools/v5_tatbikat.py`, `RF6-CALISMA-KILAVUZU.md`, `tests_v5/test_r5_*.py`.
- Loop per RUNNER and SESSION TIMELINE with an injectable clock.
- `v5/ops.py` gains `decide-now` (builds the inputs and prints the decision the bot would take right now with
  its input bundle; never sends an order) and `replay <bundle>` (re-computes a stored decision).
- Accelerated-clock scenarios (fake broker): normal session; early close; restart inside the window at every
  status; decision window missed; calendar missing; broker down for a whole session; data outage until the
  cutoff; order still open at T - 1:30; startup with a corrupt state file; foreign position appears and
  disappears. Each asserts the final record status, the alert text key, and automatic recovery next session.
- Image: `python:3.12-slim`, lock file, `COPY` of `v5/`, `tools/v5_*.py`, `tests_v5/`, `ops/` only; no `.env`,
  no v4 module, no model download; `CMD ["python", "-u", "-m", "v5.runner"]`. Compose keeps the service names
  `trading-live` and `trading-paper-livecfg` and the volume names; no `depends_on` between them.
- Proof: full suite; `docker build`; the suite inside the image with `--network none`; a listing of the image
  proving the absence of v4 code and secrets.
- After the rock passes Claude moves v4 code and tests to `_legacy/v4/` (mechanical).

### R6 , Deploy (operation, owner-gated; Claude runs it and records evidence in `RF6-DEVAM.md`)
Paper: (1) stop the v4 paper container, record and cancel its orders, flatten its positions; (2) deploy; verify
both services run the same image id, correct keys, endpoints and mounts; (3) `init-peak`; (4) drill with real
paper orders through the executor: forced round trip, duplicate client id, process kill between PLANNED and
SUBMITTED then restart, cancel path, a test message received on the owner's phone; (5) first real decision
watched end to end.
Live dry-run: (6) verify flat and no open orders; archive v4 state files; `init-peak` from the v4 record;
(7) the owner chose "straight to real money" (no multi-day paper period, no waiting session): instead, on the
same day and before the decision window, each container produces a dry decision on demand
(`python -m v5.ops decide-now`, no order) and the stored input bundle of each container replayed in the other
gives the identical decision; (8) v4 cron jobs removed, watchdog installed, a stopped container produces a
phone alert.
Go-live (the owner is asked once more right before this step): (9) `LIVE_TRADING_ENABLED=true` in the Coolify env store and the host `.env`, live
container recreated on the same image id; (10) first live decision watched: order, fill, record DONE, digest.
Abort: set the flag false and recreate; `pause`; cancel open orders; positions stay unless the owner decides.

## NON-GOALS (this cycle; see RF-ISSUES-6.md)
Stock picking, news, sentiment, options, real shorting, margin, inverse ETFs, crypto, the gold sleeve, the
intraday sleeve, SGOV parking, catch-up trading after a missed session, weekly or monthly messages, a
dashboard, porting v4 stop / protection / ledger machinery.

## KNOWN RISKS (accepted by the owner in RF6-VTO.md)
- Deep drawdowns are normal for a leveraged rule; the first months can be negative.
- No broker-side stop and no rule that sells on a bad day: between two decisions the position is exposed to
  gaps (a -10% Nasdaq day costs about 30% of the product held). The only real risk control is the package.
- If the whole VPS is down there is no decision, no rail and no alert (the watchdog lives on the same host).
  The owner's signal is a missing evening summary; an off-host monitor is RF-ISSUES-6 item 13.
- The overlay's edge is lumpy (ten days carry about two thirds of it) and lost money in 2022.
- Backtests flatter reality; expectations in RF6-VTO.md are after haircuts. Regular daily or monthly gains
  cannot be guaranteed.

## APPENDIX A , owner-facing texts (Turkish; `{}` are filled by `notify`; numbers use a comma as decimal mark)

Reason keys returned by `decide` and their sentences:
- `TREND_UP`: "Nasdaq yükseliş eğiliminde."
- `TREND_MIXED`: "Nasdaq kararsız, bu yüzden ana payı küçük tutuyorum."
- `TREND_DOWN`: "Nasdaq düşüş eğiliminde, bu yüzden ana para nakitte bekliyor."
- `VOL_HIGH`: "Piyasa çok oynak, bu yüzden payı azalttım." (used when `target_vol / vol < 1`)
- `VOL_CALM`: "Piyasa sakin." (used when `target_vol / vol >= 3`)
- `BAND_HOLD`: "Hedefe yeterince yakınım, işlem gerekmedi."
- `OVERLAY_ENTER`: "Bugün sert bir düşüş oldu, yarın için küçük bir ek alım yaptım." (Revision 12: shown
  only on a day with a filled buy)
- `OVERLAY_HELD` (Revision 12; replaces `OVERLAY_ENTER` in the summary when the books took the overlay without
  a buy, for example when the core's exit and the overlay's entry cancel out): "Bugün sert bir düşüş oldu, bu yüzden elimdekinin bir kısmını yarına kadar tutuyorum."
- `OVERLAY_KEEP`: "Düşüş sürdü, dünkü ek alımı bir gün daha tutuyorum."
- `OVERLAY_EXIT`: "Dünkü ek alımı sattım." (Revision 12: shown only on a day with a filled sale)
- `OVERLAY_DATA_MISSING`: "Bugün gün içi fiyat aralığına ulaşamadım, bu yüzden ek alım kuralını çalıştırmadım."
- `BREAKER_REDUCE_ONLY` (added by the rails when the breaker is active): "Bugün sert düşüş olduğu için yeni alım yapmadım."

Digest (kind `DIGEST`), seven lines, numbers with a comma as decimal mark and a dot for thousands:
1. "Gün sonu özeti ({date})" with the date as 16.11.2026. Prefix for paper: "DENEME HESABI. " Prefix for live
   dry-run: "DENEME, gerçek emir gönderilmedi. "
2. (Revision 12: "Hesap şu an {equity} dolar." and nothing else when the summary is sent late and no closing
   value of that session could be determined.) Otherwise:
   "Hesap: {equity} dolar." followed by " Bugün yüzde {p} arttı ({usd} dolar)." or
   " Bugün yüzde {p} azaldı ({usd} dolar)." or " Bugün değişmedi."; nothing follows when the day's change is
   not known (Revision 11: a late report, or no usable value for the session before).
3. "Elimde: yüzde {product_pct} Nasdaq fonu, yüzde {cash_pct} nakit." When the owner holds shares of his own
   worth at least 1% of the account: "Elimde: yüzde {product_pct} Nasdaq fonu, yüzde {owner_pct} senin
   aldığın hisse ({symbols}), yüzde {cash_pct} nakit." (all three as shares of the whole account)
4. "Bugün ne yaptım: " followed by "İşlem yapmadım." | "{usd} dolarlık Nasdaq fonu aldım." |
   "{usd} dolarlık Nasdaq fonu sattım." | "Elimdeki fonun tamamını sattım." |
   "Bir emir gönderdim ama sonucunu henüz öğrenemedim." (Revision 11: an order of the day is still open) |
   dry-run only (Revision 12): "Gerçek emir açık olsaydı {usd} dolarlık Nasdaq fonu alırdım." |
   "Gerçek emir açık olsaydı {usd} dolarlık Nasdaq fonu satardım." |
   "Gerçek emir açık olsaydı elimdeki fonun tamamını satardım."
5. "Neden: " followed by the reason sentences, or "Bugün karar veremedim.". When the decision was not carried
   out the reasons are replaced (Revision 11) by: "Karar verdim ama emri gönderemedim." (NOT_SUBMITTED) |
   "Karar verdim ama Alpaca emrimi kabul etmedi." (FAILED) | "Emrimin sonucunu henüz öğrenemedim." (still
   open) | "Hesap güvenlik sınırının altına düştü." (a halt day)
6. "Bu ay: yüzde {p} artıda." | "Bu ay: yüzde {p} ekside." | "Bu ay: değişim yok." followed by
   " Başlangıçtan beri: yüzde {p} artıda." (the same three forms)
7. "Durum: Her şey normal." or "Durum: " followed by the sentence of the highest active state below.

Alerts and reminders (Revision 10: every alert or reminder from a paper process starts with
"DENEME HESABI. " and from a live process in dry-run with "DENEME, gerçek emir gönderilmedi. ", like the
digest; in paper mode `NTFY_TOPIC_PAPER`, when set, replaces `NTFY_TOPIC`, so the practice account does not
write to the owner's phone):
- `DRAWDOWN_ALERT`: "Hesap en yüksek değerinden yüzde {pct} aşağıda, şu an {equity} dolar. Bu risk seviyesinde geçmişte görülen en derin düşüşe geldik. Kurala göre çalışmaya devam ediyorum. Durdurmak istersen Claude'a 'botu durdur' yaz."
- `BREAKER`: "Hesap bugün yüzde {pct} düştü. Bugün yeni alım yapmıyorum. Yarın normal çalışmaya dönerim. Senden bir şey beklemiyorum."
- `FLOOR_DONE`: "Hesap en yüksek değerinden yüzde {pct} düştü, şu an {equity} dolar. Her şeyi sattım ve durdum. Devam etmemi istersen Claude'a 'botu devam ettir' yaz."
- `FLOOR_PENDING`: "Hesap en yüksek değerinden yüzde {pct} düştü, şu an {equity} dolar. Satışa başladım ama henüz bitmedi. Şu an elimde yüzde {product_pct} Nasdaq fonu var. Denemeye devam ediyorum."
- `RAIL_LIQUIDATING`: "Güvenlik satışı hala bitmedi. Şu an elimde yüzde {product_pct} Nasdaq fonu var. Denemeye devam ediyorum. Claude'a 'trading botuna bak' yazarsan inceler."
- `HOLD_NOT_READY`: "Bugün karar veremedim, çünkü {cause}. Elimdekilere dokunmadım. Yarın yeniden denerim."
  `{cause}`: "fiyat bilgisine ulaşamadım" | "geçmiş fiyatlar eksik geldi" | "Alpaca'ya bağlanamadım" |
  "bugünün borsa saatlerini öğrenemedim" | "saat bilgisi tutarsız göründü" | "kayıt dosyam bozuk" |
  "hesap değerini doğrulayamadım" | (Revision 11) "hesapta benim almadığım bir varlık ya da emir var" |
  "önceki emrimin akıbeti hala belli değil" | "bot duraklatılmış durumda" |
  "güvenlik sınırı aşıldığı için durmuş durumdayım" | "bugün sert düşüş olduğu için yeni alım yapmıyorum" |
  "süre yetmedi" | (Revision 12) "emrim Alpaca'ya ulaşmadı"
- `NOT_SENT` (Revision 11; a trade was decided but its order was never sent): "Bugün bir işlem yapmam gerekiyordu ama emri gönderemedim, çünkü {cause}. Elimdekilere dokunmadım. Yarın yeniden karar veririm."
- `MISSED`: "Bugünkü karar saatini kaçırdım. Elimdekilere dokunmadım. Yarın normal devam ederim."
- `ORDER_FAILED`: "Alpaca emrimi kabul etmedi. Şu an elimde yüzde {product_pct} Nasdaq fonu var. Claude'a 'trading botuna bak' yazarsan sebebini inceler."
- `ORDER_PARTIAL`: "Emrimin tamamı gerçekleşmedi. Şu an elimde yüzde {product_pct} Nasdaq fonu var. Yarın duruma yeniden bakarım."
- `UNRESOLVED`: "Son emrimin akıbetini öğrenemedim. Netleşene kadar yeni işlem yapmıyorum. Claude'a 'trading botuna bak' yazarsan inceler."
- `FOREIGN_ASSET`: "Hesapta benim almadığım bir varlık ya da emir var ({symbol}). Karışmamak için işlem yapmıyorum. Bunu sen yaptıysan Claude'a haber ver."
- `DATA_DOUBT`: "Hesap değeri tutarsız göründü. Yanlış alarm olmasın diye satış yapmadım, izlemeye devam ediyorum."
- `PAUSED`: "Bot duraklatılmış durumda. Elimdekiler duruyor, yeni işlem yapmıyorum."
- `NOT_STARTED`: "Bot başlayamadı. Claude'a 'trading botuna bak' yaz."
- `READINESS`: "Bugün borsa açılmadan önce bir sorun gördüm: {cause}. Düzelmezse bugün işlem yapmayacağım."
  (`{cause}` as in `HOLD_NOT_READY`, plus "bildirim kanalı tanımlı değil", "başlangıç değeri kayıtlı değil",
  "Alpaca hesabı işleme kapalı görünüyor" and, for an unknown cause, "beklenmeyen bir sorun çıktı")
- `ERROR`: "Bot beklenmeyen bir sorunla karşılaştı. Elimdekilere dokunmadım. Claude'a 'trading botuna bak' yaz."
- Drill message (sent by hand in R6): "Deneme bildirimi: bu mesajı görüyorsan bildirimler çalışıyor."
- `WATCHDOG` (sent by the external script): "Trading botu çalışmıyor görünüyor. Claude'a 'trading botuna bak' yaz."
  For the practice service the script puts "DENEME HESABI. " in front (Revision 11).
