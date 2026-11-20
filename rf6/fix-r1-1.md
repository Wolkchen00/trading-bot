FIX ROUND 1 for rock R1 (review by Claude). Your build passed its own proof when I ran it (113 passed). I read
every file and ran my own adversarial suite, now in the repo as `tests_v5/test_r1_saldirgan.py`. It finds the
problems below. Fix them all in this round. Do not edit `tests_v5/test_r1_saldirgan.py` or
`tests_v5/test_r2_saldirgan.py`; if you believe one of my assertions is wrong, leave the test alone and say so
in the report with your reason. Update your own R1 tests where a rule below changes behaviour.

Items F1 to F3 are safety defects (they can make the bot believe something false about an order or the account).

F1. Submit outcome classification (`AlpacaBroker.submit_order` / `_request` for POST).
    Once the transport has been called, a POST may end in exactly two ways besides success:
    - `BrokerRejected`: HTTP 4xx other than 408, 425 and 429 (a definite refusal);
    - `AmbiguousSubmit`: everything else: ANY exception raised by the transport (catch `Exception`, not a list
      of types: today a `requests` ChunkedEncodingError, a generic RequestException, an OSError or any unknown
      error escapes raw), HTTP 408 / 425 / 429 / 5xx, any status outside 200-299 and 400-499 (1xx, 3xx), and a
      2xx answer whose body is not a parsable order object (invalid JSON, empty, null, a list, an object without
      the required fields: the order may exist, so this is NOT a plain `BrokerError`).
    Still exactly one transport call and no sleep. The message names the HTTP status or the exception CLASS name
    only (never the exception text). `ValueError` stays reserved for validation before any HTTP call.

F2. Only `BrokerError` subclasses may leave the gateway.
    - GET: any `Exception` raised by the transport is retried on the same schedule (0.5, 1, 2, 4 s) and ends in
      `BrokerUnavailable`. DELETE: any transport exception ends in `BrokerUnavailable` after the single attempt.
    - A 2xx answer of the wrong shape raises `BrokerError` in EVERY method: null, a list where an object is
      expected, an object where a list is expected, rows that are not objects, missing keys. No AttributeError,
      TypeError or KeyError may escape (today `get_order` on a `null` or list body raises AttributeError).
      Unchanged meaning: an object without the optional key is "nothing there" (`get_latest_trade` -> None when
      there is no `trade`; no bars entry for the symbol -> []).

F3. `get_positions`: remove the "404 with code 40410000 -> []" mapping. A flat account is HTTP 200 with `[]`.
    A 404 on the positions LIST is an error and must raise, otherwise an error can read as "we hold nothing"
    and the bot would buy again. (The 404 in RF6-PROBE.md belongs to the single-symbol endpoint, which the
    gateway does not use.)

F4. Paging bound: `_bars` follows at most 50 pages per call; a 51st page token raises `BrokerError`.

F5. One shared validator `validate_order_request(symbol, side, client_order_id, notional, qty)` in
    `v5/broker.py`, called first by `AlpacaBroker.submit_order` and by `FakeBroker.submit_order`, raising
    ValueError before any HTTP call / before any order is created:
    - `symbol`: str, `re.fullmatch(r"[A-Za-z0-9./-]{1,20}")`;
    - `side`: "buy" or "sell";
    - `client_order_id`: str, `re.fullmatch(r"[A-Za-z0-9._-]{1,128}")` (an empty or missing id would make the
      broker invent one and the order would be lost to our lookups);
    - exactly one of `notional` / `qty`; the value must be a `str` matching `[0-9]+(\.[0-9]{1,2})?` (notional) or
      `[0-9]+(\.[0-9]{1,9})?` (qty) by `re.fullmatch` with ASCII digits only, and be greater than zero. So
      "1e3", "+5", " 5", "5.", ".5", "5_0", a float, a non-ASCII digit are all ValueError.
    Keep `_validate_size` only if you still need it internally.

F6. `Order.filled_qty`: a null or missing `filled_qty` becomes "0" (today a JSON null becomes the text "None").

F7. Default transport: pass `allow_redirects=False` to `requests.request` (a redirected POST would be replayed
    as a GET by `requests`).

F8. `v5/config.py`, notification settings:
    - `NTFY_TOPIC` is stripped; whitespace only -> ""; a non-empty topic must match `[A-Za-z0-9_-]{1,64}`, else
      ConfigError.
    - `NTFY_BASE_URL` is stripped; empty -> the default; it must be `http://` or `https://` followed by a
      non-empty host, else ConfigError; a trailing "/" is removed.
    - `repr(Settings)` must not show the topic (it is a bearer secret: whoever knows it can read and forge the
      owner's messages). Show `ntfy_configured=True/False` instead.

F9. `v5/state.py`:
    - `atomic_write_json` writes with `allow_nan=False` (a NaN or infinity raises ValueError, the old file stays,
      no temporary file is left behind);
    - `read_json` treats the constants `NaN`, `Infinity`, `-Infinity` in a file as `StateCorrupt`;
    - after `os.replace`, on POSIX fsync the parent directory (best effort: ignore OSError), so the rename
      itself survives a power loss.
    - `ProcessLock.__enter__` raises `LockBusy` (new `class LockBusy(RuntimeError)` in `v5/state.py`) when the
      lock cannot be acquired. The body of a `with` block must never run unlocked.

F10. `tests_v5/conftest.py`: every hook must be a no-op unless `os.name == "nt"` (today `pytest_sessionfinish`
    touches private pytest attributes on Linux too, where the image runs the suite), and each access to a
    private pytest attribute must go through `getattr(..., None)` so another pytest version cannot break the
    session. Remove `collect_ignore`. Never create scratch directories inside `tests_v5/` (I deleted
    `.pytest_work2`, `fullwork_usable`, `r1tempwork`, `r1work_usable2`); use `.pytest_tmp/` and clean up.

Accepted as delivered (no action): precision violations raising ValueError in FakeBroker; nanosecond
timestamps truncated to microseconds; `covers()` meaning "inside the known range"; `cancel_order` proving the
terminal state with a follow-up read.

CONSTRAINTS (unchanged): only files under `v5/` and `tests_v5/`; never golden, fixtures, R2 files, `RF*.md`,
`.gitignore`, v4 files; no network; no em dash characters; do not commit.

PROOF
`python -m pytest tests_v5 -q -p no:cacheprovider --basetemp=.pytest_tmp` must pass completely, including
`tests_v5/test_r1_saldirgan.py`. Include the full output.

OUTPUT
Report: files changed (one line each), the proof output, and for each of F1..F10 one line saying what you did
(or why not).
