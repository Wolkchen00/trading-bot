# RF6-PROBE.md , broker evidence for v5 (the probe gate of RF-PLAN-6.md)

> Recorded by Claude on the PAPER account on 2026-11-16 at 11:10 ET (market open), with the v4 paper container
> stopped for the three minutes of the run. Script: session scratchpad `scripts/broker_probe.py` (kept in the
> lab backup). Raw responses: `tests_v5/fixtures/alpaca/*.json` (56 files; each holds `request`, `status`,
> `body`; account identifiers masked because the repository is public). The account ended exactly as it
> started (no probe position, no open order).
> Scope: paper endpoint for trading calls, live market-data keys for data calls. Live trading calls were not
> probed; the first live order is watched in R6 step 10.

## Trading API (https://paper-api.alpaca.markets, same paths on live)

| Behaviour | Measured | Fixture |
|---|---|---|
| Clock | `GET /v2/clock` -> `is_open`, `next_open`, `next_close`, `timestamp` as ISO strings with the ET offset | `clock` |
| Calendar | `GET /v2/calendar?start&end` -> rows `{date, open "09:30", close "16:00", session_open, session_close, settlement_date}`; ET wall times; one row per session | `calendar_window` |
| Account | strings for money fields (`equity`, `last_equity`, `cash`, `buying_power`), `status`, `trading_blocked`, `account_blocked`; no PDT fields | `account` |
| Positions | list of `{symbol, qty, qty_available, market_value, current_price, avg_entry_price, asset_class, side}`; quantities as strings with up to 9 decimals; `GET /v2/positions/{symbol}` -> 404 code 40410000 "position does not exist" when flat | `positions_before`, `position_TQQQ`, `position_TQQQ_after_exit` |
| Notional market DAY buy | `POST /v2/orders {symbol, notional "20.00", side buy, type market, time_in_force day, client_order_id}` -> 200, status `pending_new`, `qty` null, `notional` "20"; filled 5 ms later with `filled_qty` (9 decimals) and `filled_avg_price` | `buy_notional_20_TQQQ`, `buy_notional_20_TQQQ_final` |
| Visibility by client id | `GET /v2/orders:by_client_order_id?client_order_id=` found the order (already `filled`) on the first lookup, under one second after the POST returned | `get_by_client_order_id` |
| Unknown client id | 404 `{code 40410000, message "order not found for <id>"}` | `get_by_client_order_id_unknown` |
| Duplicate client id | 422 `{code 40010001, message "client_order_id must be unique"}` (also after the first order has filled) | `duplicate_client_order_id` |
| Minimum notional | $1.00 accepted; $0.50 -> 422 `{code 42210000, "notional amount must be >= 1.00"}` | `buy_notional_1_00`, `buy_notional_0_50` |
| Notional precision | 3 decimals -> 422 "notional value must be limited to 2 decimal places" | `reject_notional_3_decimals` |
| Fractional time in force | `gtc` -> 422 "fractional orders must be DAY orders" | `reject_gtc_fractional` |
| Insufficient buying power | 403 `{code 40310000, message "insufficient buying power", buying_power, cost_basis}` | `reject_too_large` |
| Quantity sell | 9-decimal quantity accepted and filled; a sell worth $0.50 by quantity is ACCEPTED and filled | `sell_qty_third_9dp`, `sell_qty_sub_dollar` |
| Notional sell | accepted and filled | `sell_notional_5` |
| Sell above the available quantity | 403 `{code 40310000, message "insufficient qty available for order (requested: x, available: y)", available, existing_qty, held_for_orders}` | `sell_qty_above_position`, `sell_full_qty_while_resting` |
| Dust exit | a $0.60 position sold by its exact quantity string: filled, position gone | `position_dust`, `sell_exact_position_qty` |
| Resting order | a resting limit sell reduces `qty_available`; a market buy while it rests was ACCEPTED (no wash-trade rejection in this case) | `resting_limit_sell`, `buy_while_resting_sell`, `position_with_resting_order` |
| Cancel | `DELETE /v2/orders/{id}` -> 204; the order then reads `canceled`; cancelling it again -> 204 | `cancel_by_id`, `canceled_order`, `cancel_already_canceled` |
| All-status listing | `GET /v2/orders?status=all&after=<ISO>&limit=500&direction=asc&nested=false&symbols=TQQQ` returned all 10 probe orders (`filled`, `canceled`). Paging with `limit=3` and `after=<last submitted_at>` returned 14 rows for 10 orders (overlaps: list timestamps are truncated to microseconds), the union was complete | `list_orders_all_today` |
| Order life cycle | `pending_new` in the POST answer, `filled` at the first poll (0.5 s); six consecutive orders | `*_final` |

## Market data API (https://data.alpaca.markets, free plan)

| Behaviour | Measured | Fixture |
|---|---|---|
| Latest trade, `feed=iex` | QQQ 6 s old, TQQQ 19 s old during the session; before the open it returned the last trade of the previous session (2.6 days old) | `latest_trade_QQQ_iex`, `latest_trade_TQQQ_iex` |
| Latest trade, `feed=delayed_sip` | works, 15 minutes old; `feed=sip` -> 403 "subscription does not permit querying recent SIP data" | `latest_trade_QQQ_delayed_sip`, `latest_trade_QQQ_sip` |
| Latest IEX minute bar | `{t, o, h, l, c, v, n, vw}`, `t` = bar start in UTC | `latest_bar_QQQ_iex` |
| Daily bars | `GET /v2/stocks/bars?symbols=QQQ&timeframe=1Day&start=&adjustment=all&feed=sip&limit=10000` -> `{bars: {QQQ: [{t, o, h, l, c, v, n, vw}]}, next_page_token}`; `t` = 05:00Z of the session date in November (midnight ET). 291 bars for 420 calendar days. DURING the session the last bar is TODAY's partial bar (present at 11:10 ET; absent before the open) | `daily_bars_QQQ_sip_all` |
| Adjusted versus raw | `adjustment=all` equals `adjustment=raw` from the last ex-dividend date on (2026-09-21); before it adjusted = raw x 0.99896. So today's raw price is on the same basis as the adjusted series of the same request | `daily_bars_QQQ_sip_raw` |
| IEX minute bars of the session | `feed=iex`, from the session open, no `end`: 100 bars at 100.8 minutes after the open, first bar at the open, last bar 1 to 2 minutes old; high 672.02 / low 663.075 against SIP 672.15 / 663.0701 | `minute_bars_QQQ_iex_noend` |
| SIP minute bars | with `end = now - 16 min`: 200; with no `end`: 200, silently clipped at now - 15 min | `minute_bars_QQQ_sip_cut16`, `minute_bars_QQQ_sip_noend` |

## What this fixes in the build contracts (no plan rule is contradicted)

1. Idempotent submit holds as planned: unknown client id = 404 code 40410000; duplicate = 422 code 40010001;
   a just-submitted order is visible by client id immediately. The 20 second ambiguity lookup stays as margin.
2. Sub-dollar rule, frozen for R3: a BUY under $1.00 is never sent (the broker rejects it). A desired net change
   under $1.00 in either direction is NETTED (no order). One exception: a full exit (target 0 with a position
   held) always sells the exact position quantity string, whatever it is worth (a $0.60 dust sell was accepted).
   CAPPED remains for a desired change of at least $1.00 that the cash cap cut short.
3. Rejection classes for R3: 403 code 40310000 with message starting "insufficient buying power" -> one re-sized
   attempt; 403 code 40310000 with message starting "insufficient qty available" -> refresh the position and
   re-size once (it means our quantity was stale or an order holds shares); every other 4xx -> FAILED.
4. `Broker.list_orders` for R1: ONE call with `status=all`, `after` = the session's open, `limit=500`,
   `direction=asc`, `nested=false`, `symbols` = the product. v5 sends a handful of orders per session, so the
   answer is complete iff it has fewer than 500 rows; exactly 500 rows means "incomplete" (UNRESOLVED stays).
   No paging by `after` (it returns overlapping rows); rows are de-duplicated by `id` anyway.
5. Data layer for R3: today's partial daily bar must be dropped by date (it is present during the session);
   the adjusted window is fetched in one request per decision; the IEX latest trade needs the freshness check
   (before the open it is days old); IEX minute bars give the session range with about 99% coverage.
6. The fake broker must not model a mandatory wash-trade rejection (not observed); v5 keeps no resting order
   in any case.
7. Order statuses seen: `pending_new`, `filled`, `canceled`. The executor treats `filled`, `canceled`,
   `expired`, `rejected`, `replaced`, `done_for_day`, `calculated` as terminal, and `new`, `accepted`,
   `pending_new`, `partially_filled`, `pending_cancel`, `pending_replace`, `accepted_for_bidding`, `held`,
   `stopped`, `suspended` as open; an unknown status counts as open (never as terminal).
8. Money fields are strings: parse with `float` for decisions; send quantities as the broker's own strings or
   formatted with 9 decimals, notionals with 2 decimals (rounded DOWN).

## Measured later: when `last_equity` turns over (2026-11-19, paper account, read-only)

At 20:16 ET, four hours after the close and after the end of extended hours, `last_equity` was 61784.59, which
is the portfolio history's value for 2026-11-18 (the session BEFORE the one that had just closed); the history
had no row for 2026-11-19 yet. So for at least four hours after a close `last_equity` still means the close of
the previous session. The engine relies on exactly this in two places: the day base of an on-time summary, and
the row it takes from the broker for a previous session it did not record itself. In the same hour the
account's `equity` moved from 61757.20 (19:15 ET) to 61773.20 (20:16 ET) with no trade: extended-hours prices
do move it, which is why a reading taken later than 35 minutes after the close is never stored as a closing
value. Not measured yet: the moment of the turnover itself (to be read before and after the next open).

## Measured on 2026-11-20 at 09:13 ET, before the open (both accounts, read-only)

- Practice account: `last_equity` 61729.43 and `balance_asof` 2026-11-19; the portfolio history's value for
  2026-11-19 is 61729.43. So between 20:16 ET of the 19th (when `last_equity` was still the close of the
  18th) and 09:13 ET of the 20th the field turned over to the close of the 19th, and `balance_asof` names
  that session. `equity` at the same moment was 61851.67: pre-market prices move it.
- Real account: `equity` 490.06, `last_equity` 490.06, `balance_asof` 2026-11-19, cash 490.06; buying power
  is lower than cash by exactly the amount the owner's resting buy order reserves.
- What follows for the engine: during a session and before its open, `last_equity` is the close of the
  session before it, and the broker says so itself through `balance_asof`. Since fix round R5d the baseline
  rules read that date instead of inferring it from the time of day, so the exact minute of the overnight
  turnover no longer matters to the code.
- After the open (09:38 ET of the 20th): `last_equity` is unchanged on both accounts (61729.43 and 490.06,
  `balance_asof` 2026-11-19) while `equity` moves with the market. Both engines had by then taken their row
  for 2026-11-19 from it (`equity_history`: 61729.43 on the practice account, 490.06 on the real one); the
  peaks did not move (both closes are below them) and no brake entry was written.
