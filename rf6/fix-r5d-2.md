# R5d, second part: the remaining findings of the reviews

Same rules as the first part (no commit, no `.env`, tests in `tests_v5/test_r5d_duzeltme.py`, proof command at
the end). In this part you may also edit `tools/v5_tatbikat.py` and `ops/vps/v5_watchdog.sh`.

## Group G. State and tools

G1. A `runner.json` of the wrong shape is silently replaced by defaults. `Runner._load_state` treats anything
    that is not a JSON object as "no state", writes a new `started_at`, and every earlier session then counts
    as "before the engine existed": an owed summary is skipped, nothing is alerted and the heartbeat is healthy.
    Required: a missing file gives the defaults as today. A file that exists but is not a JSON object, or whose
    known fields have the wrong type, raises `StateCorrupt` and is never written over:
    `started_at` None or an ISO datetime string; `closed_session` None or an ISO date string;
    `closed_sessions` and `unknown_sessions` lists of ISO date strings; `cutoff_alerted` None or a string;
    `overlay_missing_streak` a non-negative int (not a bool). Missing keys in a valid older file are still
    filled in. (The tick's error accounting already alerts at once on `StateCorrupt`, and the heartbeat already
    carries STATE_CORRUPT.)
    Test: after D1's decision the file is replaced by `[]`; the next ticks send no summary for a wrong day, the
    owner gets the ERROR sentence, the heartbeat has STATE_CORRUPT, the file's bytes are unchanged, and nothing
    is submitted on D2 while it stays that way.

G2. A record may only be driven by the mode that wrote it. Today a live runner drives an unfinished record
    whose `mode` field says `paper` (a copied or migrated state folder) and sends a real order with a
    `v5-paper-...` id.
    Required: `Executor.run_chain` and `Executor.reconcile` raise `StateCorrupt(record.path)` before any broker
    call when `record.data.get("mode") != self.settings.mode`, or when an intent's `client_order_id` is not
    exactly `client_order_id(mode, session, purpose, attempt)` for that record and attempt.
    Test: a non-final paper record with a target in the state folder of a live runner with the flag on: no
    `submit_order` call during the whole window, the owner gets the ERROR sentence, the heartbeat has
    STATE_CORRUPT.

G3. The watchdog finds its containers by a name prefix, which only fits one way of naming them. Required: look
    a service up by its Compose label first
    (`docker ps --filter "label=com.docker.compose.service=<service>" --format '{{.Names}}' | head -n 1`), and
    only when that gives nothing by the name prefix as today. `<service>` is `trading-live` and
    `trading-paper-livecfg`. The existing watchdog tests use a fake `docker` that ignores `--filter`: extend the
    fake so that it answers the label query from a file of its own, and add a case where the container is
    found by label although its name has a project prefix.

G4. The drill sells a position of the same fund that it did not open. Its exits call `_chain(0.0)`, which
    means "sell everything", so shares that arrived in the account between the drill's entry and its exit are
    sold too. Required: each step that opens a position remembers the quantity its entry really bought (the sum
    of the entry record's filled quantities) and, right before the exit, compares it with what the account
    holds. Equal -> exit as today. Different -> sell nothing and fail the step with
    "the account changed during the drill: it holds <qty>, the drill bought <qty>; nothing was sold".
    This applies to `round_trip`, `restart` and the clean-up inside `duplicate_id`.
    Test: during `round_trip`, between entry and exit, one more share appears in the account: the step fails
    with that sentence and no sell order is sent.

G5. An unknown month or total change is written as "değişim yok", which states a computed zero. Required:
    `_period(label, None)` gives "<label>: bilgi yok." ("Bu ay: bilgi yok.", "Başlangıçtan beri: bilgi yok.");
    "değişim yok" stays for a computed change that rounds to 0,0.

G6. `clear-halt --new-peak <value>` trusts the typed number. Required: `_run_clear_halt` reads the account
    (`AlpacaBroker`, `read_equity`) and refuses (exit code 1, a one-line explanation, nothing written) unless
    the reading is trusted and `equity <= new_peak <= 3 * equity`; a new option `--force` skips that check.
    `cmd_clear_halt` itself keeps its signature and gains an optional `reading=None, force=False` pair for
    this. Test: a too-small and a too-large number are refused and leave `rails.json` unchanged; a value equal
    to the equity is accepted; `--force` accepts the odd one.

## Proof and report

`python -m pytest tests_v5 -q -p no:cacheprovider --basetemp=.pytest_tmp_codex`
Report as before: what changed, the proof's last line, every existing assertion you changed, anything you
think is wrong here.
