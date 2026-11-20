# Fix round R5c: five defects in the v5 engine (ordinary bug fixes, with tests)

You are the builder. I am the reviewer: I will read your whole diff and run the proof myself.
Work in this repository (`v5/` is the engine, `tests_v5/` its tests). Do not commit. Do not read `.env`.
No network is needed.

## Background you need (read these parts only)

- `RF-PLAN-6.md`: Revision 11 at the top, and APPENDIX A (every sentence the owner can receive is fixed there).
- `rf6/contract-r5.md`: the "AS BUILT" notes at the end describe how `v5/runner.py` works today.
- The owner is not technical. Every message must be literally true: it may never say that something was
  bought or sold on a day without such an order.

## Rules for this round

- Files you may edit: `v5/runner.py`, `v5/notify.py`, `v5/rails.py`, `v5/ops.py`, `v5/health.py`, and ONE new
  test file `tests_v5/test_r5c_duzeltme.py`.
- Do not touch `v5/strategy.py`, `v5/executor.py`, the golden files, the plan documents or other test files.
  Exception: if an existing test asserts the OLD behaviour of exactly one of the items below, change that one
  assertion and name it in your report.
- If an item contradicts the plan in a way that matters, answer `BLOCKED:` with the reason. Do not guess.
- Keep the style of the surrounding code (short functions, comments that say WHY, no new dependencies).
- Each new test must fail on the code as it is now and pass after your change. Reuse the helpers of
  `tests_v5/test_r5_saldirgan.py` (`Sim`, `window`, `D1`, `D2`, `D3` ...) by importing them.
- Proof (must pass, about ten minutes):
  `python -m pytest tests_v5 -q -p no:cacheprovider --basetemp=.pytest_tmp_codex`

## D1. A chain that resumes after a crash can send its order although the account now holds a foreign asset

Today: `Runner._continue_block` (the gate for a strategy chain that is already under way) checks the rails,
the clock, the market, the notification channel, the pause and the breaker, but it does not look at the account.
The new-decision path does (snapshot, then `FOREIGN_ASSET`). `Executor._resume` re-submits a PLANNED intent whose
`submit_count > 0` without `_inspect`, so the order goes out.

Scenario: a record gets its target; the first `run_chain` dies inside `broker.submit_order` (the record is
PLANNED, `submit_started` true, `submit_count` 1, the broker has no order). A position in another symbol appears
(SPY, 1 share at 100). A new `Runner` ticks through the rest of the decision window.
Now: one `submit_order` call, record CAPPED. Required: no `submit_order` call at all.

Required change, in `_continue_block`, after the NOTIFY_MISSING check and before PAUSED:
- take `self._snapshot()`; `SnapshotInvalid` -> code `SNAPSHOT_INVALID`; `BrokerError` -> code `BROKER_DOWN`;
- `snapshot.foreign` -> code `FOREIGN_ASSET`, and send the `FOREIGN_ASSET` alert exactly as the new-decision
  path does (`state_key` = the symbol, `symbol` = the symbol, via `_item_symbol`).
The chain's own open order is not foreign (`_snapshot` already passes the executor's own client ids).

## D2. A record that ends FAILED because the order never reached the broker is reported as "refused by Alpaca"

When `Executor.reconcile` finds no order under the client id and the position unchanged, the record becomes
FAILED with `status_detail == "NOT_FOUND"`. The owner then reads "Alpaca emrimi kabul etmedi ..." (alert and
digest), which is not what happened.

Required:
- `CAUSE_SENTENCES["NOT_FOUND"] = "emrim Alpaca'ya ulaşmadı"`.
- `Runner._after_chain`: FAILED with detail `NOT_FOUND` on a strategy record sends `NOT_SENT` (cause from
  `_not_sent_cause`, which must map `NOT_FOUND` to that sentence) instead of `ORDER_FAILED`. This one is sent
  also when `late` is true (it is news, and the sentence without "bugün" would need a new template: keep it).
- `_digest_context`: such a record has `outcome = "NOT_SENT"` (not `REFUSED`).
- `_status_text`: such a record gives the `NOT_SENT` sentence (same cause), not `ORDER_FAILED`.

## D3. The daily summary can say an extra buy was made, or yesterday's extra buy was sold, without such an order

`_digest_context` passes the decision's `reason_keys` unchanged. Two of the sentences state an order:
`OVERLAY_ENTER` ("... yarın için küçük bir ek alım yaptım.") and `OVERLAY_EXIT` ("Dünkü ek alımı sattım.").
When the core and the overlay move against each other, the record can end NETTED (no order at all) or with a
net order in the other direction, and the books simply re-assign what is held.

Required, in `_digest_context`, once `bought` and `sold` are known and only when `reason_keys` is a list:
- `OVERLAY_ENTER` stays only when `bought > 0`. Otherwise: if the record's books show that the overlay was
  entered without a buy (`record.data["overlay_fraction_committed"] > 1e-12` while
  `record.data["before"]["overlay_fraction_committed"] <= 1e-12`; a missing `before` counts as not entered),
  replace it in place by the new key `OVERLAY_HELD`; if not, remove it.
- `OVERLAY_EXIT` stays only when `sold > 0`; otherwise remove it.
- New entry: `REASON_SENTENCES["OVERLAY_HELD"] = "Bugün sert bir düşüş oldu, bu yüzden elimdekinin bir kısmını
  yarına kadar tutuyorum."` (one line in the code).

A dry-run day must say what WOULD have been done (today it only says "İşlem yapmadım."):
- `build_digest` accepts three more actions:
  `would_buy` -> "Gerçek emir açık olsaydı {usd} dolarlık Nasdaq fonu alırdım."
  `would_sell` -> "Gerçek emir açık olsaydı {usd} dolarlık Nasdaq fonu satardım."
  `would_sell_all` -> "Gerçek emir açık olsaydı elimdeki fonun tamamını satardım."
- `_digest_context`: when the floor record or (else) the strategy record has status `DRY_RUN`, the action comes
  from that record's last intent: `kind` "buy" -> `would_buy`, "sell" -> `would_sell`, "sell_all" ->
  `would_sell_all` (amount: the intent's `desired_usd`), `kind` "none" -> `none`.

## D4. A summary sent late for an earlier session shows the account value of the moment under that session's date

`_after_close`, when it runs late and cannot determine a closing value (`close_equity is None`), shows
`reading.equity` in line 2 of a digest that carries the earlier session's date: "Hesap: 450,00 dolar."

Required: in that case the digest context carries `as_of_now = True`, and `build_digest` then writes line 2 as
"Hesap şu an {equity} dolar." (never a day sentence, whatever `day_usd` is). In every other case line 2 is
unchanged.

## D5. Two read-only checks disagree with the engine

(a) `decide-now` reports `breaker: false` where the engine would be in reduce-only mode because of the baseline
cross-check. Add to `v5/rails.py` a pure function `breaker_cause(reading, stored_close, breaker_day)` that
returns `"BASELINE_DOUBT"`, `"BREACH"` or `None`, plus a module-level sentinel `NO_ROW` meaning "there is no
stored close to compare with". It must contain exactly the logic `Rails.check_breaker` has today, and
`check_breaker` must use it (no behaviour change there). `ops._blockers` gets the calendar as an argument and
sets `breaker` true when the stored breaker is for today OR (`reading.ok` and `breaker_cause(reading, <stored
close of the previous session or NO_ROW>, settings.rails.breaker_day)` is not None).

(b) `health.assess`: a heartbeat whose `states` is missing or not a list, or whose `readiness` is missing or not
a dict, is at least level 1 with the line "Heartbeat is incomplete."; a heartbeat time more than 60 seconds in
the future is level 2 with the line "Heartbeat time is in the future (N seconds)." (N an integer).

## Tests to write (`tests_v5/test_r5c_duzeltme.py`)

1. D1: the scenario above, driven with `Sim` and a real `Runner` for the second process. Assert: no
   `submit_order` call after the foreign position appears; the owner got the FOREIGN_ASSET sentence; after the
   close the record is final, and the digest's lines 4 and 5 are "Bugün ne yaptım: İşlem yapmadım." and
   "Neden: Karar verdim ama emri gönderemedim." (this also covers D2).
2. D2: a record settled as FAILED / NOT_FOUND: alert text and digest status are the `NOT_SENT` sentence with
   "emrim Alpaca'ya ulaşmadı"; a record FAILED for a real refusal (`reject(422, 42210000, "refused")`) still
   says "Alpaca emrimi kabul etmedi".
3. D3: (i) a NETTED re-assignment (target core 0, overlay about equal to what is held, built with the executor
   as the scenario needs) -> the digest has no "ek alım yaptım", has the `OVERLAY_HELD` sentence, and says
   "İşlem yapmadım."; (ii) an ordinary dip day with a real buy still says "ek alım yaptım"; (iii) the day after,
   the real sale still says "Dünkü ek alımı sattım."; (iv) dry-run (`Sim(tmp_path, mode="live")`): a buy day's
   line 4 is "Bugün ne yaptım: Gerçek emir açık olsaydı {usd} dolarlık Nasdaq fonu alırdım." with the planned
   amount, and the text has no "ek alım yaptım"; (v) `build_digest` unit checks for the three new actions and
   the new reason sentence.
4. D4: the account cannot be read from D1's close until after D2's close; then D1's digest line 2 is
   "Hesap şu an {value} dolar." and D2's line 2 has its normal form.
5. D5: (a) with a stored close of the previous session that differs from the broker's `last_equity` by more
   than 2 %, `cmd_decide_now` returns `breaker` true and a reduce-only decision, like `Rails.check_breaker`;
   `breaker_cause` unit checks for its three results and for `NO_ROW`; (b) `assess` for an incomplete heartbeat
   (level >= 1) and for a heartbeat one day in the future (level 2).

## Report (your final message)

- What you changed, file by file, in a few lines each.
- The proof command's last line.
- Every existing assertion you changed, with the reason.
- Anything in this contract you think is wrong or incomplete.
