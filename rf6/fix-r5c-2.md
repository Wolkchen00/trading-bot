# R5c, second part: review notes on your change, and one more defect in the paper drill tool

Same rules as before (no commit, no `.env`, proof command at the end). In this part you may also edit
`tools/v5_tatbikat.py`. Keep adding tests to `tests_v5/test_r5c_duzeltme.py`.

## Review notes (I read the whole diff; the design is right, these are the gaps)

N1. `Runner._continue_block`: the new snapshot block sits outside the fault guard at the top of the function.
    `self._snapshot()` can raise something other than `SnapshotInvalid` / `BrokerError` (for example
    `StateCorrupt` from the executor's record scan). Then `_continue_block` raises, `run_chain` is not called
    in that tick, and an order that was already accepted is not followed. Required: any other exception from
    the snapshot returns `("STATE_CORRUPT" if it is a StateCorrupt else "ENGINE_FAULT", exc)`, exactly like the
    guard at the top (the caller drives the chain with that code and re-raises afterwards).
    Test: a chain with an accepted, unfilled order (`sim.fb.script("never_fill")`); from the second tick of the
    window `runner._snapshot` raises `RuntimeError`; assert exactly one `submit_order` call in the whole day,
    the order ends `canceled` (so the chain was still followed), and the record is final.

N2. `_digest_context`, dry-run branch: `dry_record.data.get("intents", [])[-1]` raises `IndexError` on a
    DRY_RUN record without an intent, and the day then gets no summary at all. Such a record should not exist,
    but a summary must never depend on that. Required: no intent -> action `none`.
    Test: write a DRY_RUN record by hand (status DRY_RUN, closed, `intents == []`) for a `Sim(mode="live")` day
    and run the after-close ticks: one digest, line 4 "Bugün ne yaptım: İşlem yapmadım.".

N3. `_digest_context`, `entered`: the contract says a missing `before` counts as NOT entered. Your code treats a
    missing `before` as fraction 0.0, which makes `entered` true when the committed fraction is positive.
    Required: `entered` is false unless `before` is a dict.

N4. `v5/ops.py`: `math` is no longer used after your change. Remove the import if nothing else needs it.

## D6. The drill's clean-up step fails on a real practice account (seen at the deployment of 2026-11-19)

`tools/v5_tatbikat.py::step_flatten_foreign` clears what an older bot left in the PAPER account before the
drill. The practice account holds a position with two open sell orders on it, plus a crypto remainder of
0.000000001.

(a) A position worth less than one dollar (`abs(market_value) < 1.0`, the same dust rule as `take_snapshot`
    in `v5/executor.py`) must be left alone: no order for it, and it does not count as "still held". Today the
    step sends an order for the dust, the broker refuses it, and the whole step fails.
(b) The step cancels the open orders and at once tries to close the positions. Shares that are reserved by an
    order still being cancelled cannot be sold, so the closing order is refused. Required: after the cancel
    calls, poll `get_open_orders()` (owner symbols left out) up to 30 times, `self.sleep_fn(1)` between polls,
    until none is left. If some are left: return `(False, "open orders did not cancel: <symbols>")` and close
    nothing.

Tests: (a) a long position SPY 2 at 600 plus XRPUSD 0.000000001 at 0.5 -> the step passes, SPY is sold, no order
is sent for XRPUSD and the dust is still in the account; (b) with `cancel_order` replaced by a function that
does nothing and one open order present -> the step returns False with that message and sends no closing
order; with the real `cancel_order` it passes.

## Proof and report

`python -m pytest tests_v5 -q -p no:cacheprovider --basetemp=.pytest_tmp_codex`
Report as before: what changed, the proof's last line, any existing assertion you changed, anything you think
is wrong in these notes.
