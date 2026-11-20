# RF6-VTO.md , v5 "Yenileme": vision and owner decisions

> Date: 2026-11-16. Owner: Ihsan. Visionary: Claude. Integrator: Codex.
> STATUS OF THE OWNER DECISIONS BELOW: **FINAL**, answered by the owner in the Grill on 2026-11-16 (about
> 08:05 PT). Active package: **high**. Rails: alert 37%, breaker 15%, floor 60% below the peak.

## Owner's request (2026-11-15, his words, summarised)

"The bot has been running for months with no profit. Renew the whole system from start to finish, working with
Codex. First look at the most starred projects on GitHub and use what is needed. Smartest methods, fully
automatic, daily and monthly gains, risk may go up for faster growth, use long and short if we can."

Standing rules from earlier sessions that still bind: the ~$490 is the only money (never suggest deposits);
protection limits are never loosened without the owner's explicit decision; live and paper stay separate.

## Core Focus (one sentence)

Replace the v4 agent-voting bot with a small engine that, once per trading day near the close, moves the account
to the holdings required by ONE verified rule, protected by rails derived from that rule's own history, unable
to freeze silently, and reporting to the owner in plain Turkish.

## What done looks like

1. The live account is managed every session by the v5 rule; the owner receives a short Turkish summary after
   every close (value, change, holdings, what was done and why).
2. A blocked or degraded bot cannot stay unnoticed for more than one session.
3. The same code runs on the paper account; its decisions match the live bot's.
4. The decision code is proven identical to the laboratory rule (golden parity) and the whole daily loop is
   proven on a simulated broker over 16 years of history.
5. v4 is retired from the image; its code is kept under `_legacy/v4/`.

## Non-goals (this cycle)

Stock picking, news and sentiment, options, real short selling, margin, inverse ETFs, crypto, the gold sleeve,
the intraday sleeve, a dashboard. See `RF-ISSUES-6.md` for what was deferred and why.

## Constraints and stack

- Alpaca live account: about $490, 1x buying power, no shorting, no options; fractional orders are simple DAY
  orders only. Free market data (IEX real time, SIP 15 minutes delayed).
- Python 3.12, plain HTTPS to Alpaca (requests), pandas, numpy. Docker on the existing VPS through Coolify, same
  service and volume names. ntfy for phone notifications.
- Codex builds; Claude reviews every line, runs every proof, writes adversarial tests.

## Goals (3 to 7)

G1. One pure decision function with golden parity against an independent reference.
G2. An idempotent executor with no resting orders and bounded retries.
G3. Rails that never fire in normal operation and never fail silently.
G4. Plain-Turkish reporting every session.
G5. Paper first, live in dry-run, then live with the owner's go.

## Owner decisions (the Grill)

| # | Decision | Claude's recommendation | Owner's answer |
|---|---|---|---|
| 1 | Risk package (the only parameter is the volatility target) | **High** (target 30%) | **High** ("Yuksek") |
| 2 | Catastrophe floor: how far below the peak before the bot sells everything and waits for the owner (with it: the drawdown alert and the daily breaker of the package table) | High: 60% below the peak | **60% below the peak** ("196 dolar"); alert 37% and breaker 15% come with the package |
| 3 | Short side | Cash in downtrends; no inverse ETFs (0 of 435 tested rules helped) | **Cash in downtrends** ("Nakitte beklesin") |
| 4 | Go-live path | Paper for a few sessions, live in dry-run in parallel, then live | **Straight to real money** ("Dogrudan gercek para"): no multi-day paper period. The technical checks of R6 stay (paper drill with real paper orders, a same-day live dry-run decision). Claude promised to ask once more right before the live flag is flipped |
| 5 | Notifications | Daily summary and alerts through ntfy on the phone | Not asked in the Grill; confirmed at deploy with a test message to the owner's phone (R6 step 4) |
| 6 | Total outage of the server (VPS): the bot, its breaker and its alarms all stop; leveraged holdings stay unmanaged and nobody is told. The owner's only signal is a missing evening summary. An off-host monitor is deferred (RF-ISSUES-6 item 13) | Accept for now; go-live is gated on this answer | **Accepted** ("Kabul ediyorum") |

## Package table (what the owner chooses from)

All three use the same rule (core + dip overlay); only `target_vol` differs. Source: tournament judge
(`RF6-KANIT.md` section 6.4), cross-checked by Claude's independent reference.

| Package | target_vol | Backtest 2010-2026, best estimate: CAGR / max drawdown | Planning return per year after haircuts | From $490 after 1 year: bad (5%) / typical (median) / good (95%) | Year ends below its start | Typical after 3 years | Drawdown worse than 50% within 3 years |
|---|---|---|---|---|---|---|---|
| moderate | 0.20 | 24.2% / -30% | 12.1% | $369 / $549 / $814 | 31% | $695 | 7% |
| high | 0.30 | 30.8% / -36% | 14.6% | $334 / $563 / $930 | 33% | $734 | 20% |
| very_high | 0.45 | 35.9% / -47% | 15.6% | $294 / $568 / $1,070 | 36% | $755 | 40% |

Haircuts: every decision one close late; Nasdaq-100 lowered so that QQQ compounds at 10.5% a year instead of
14.5%; the overlay counted at 41% of its measured size. About 6 months in 10 are positive in every package.
Regular daily or monthly gains cannot be guaranteed. From high to very_high the typical outcome gains $5 after
one year while the chance of losing more than half within three years doubles: very_high is not recommended.

Rails proposed per package (from the package's own history; the owner decides):

| Package | A: drawdown alert (no trading change) | B: daily breaker (no buys for the rest of the day) | F: catastrophe floor (sell everything, stop, ask the owner) |
|---|---|---|---|
| moderate | 31% below the peak | -12% versus the previous close | 50% below the peak (from $491: $245) |
| high | 37% | -15% | 60% (from $491: $196) |
| very_high | 47% | -20% | 70% (from $491: $147) |

The v4 rails (5% daily kill, floor 15% below the peak = $417) cannot be kept: at high a day worse than -5%
happens about 5 times a year and the account is more than 15% below its peak on 35% of days in normal operation.
