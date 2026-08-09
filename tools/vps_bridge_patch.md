# VPS alarm bridge DELIVERY-marker patch

Target (outside this repository): `/root/trading_protection_check.sh`

The bot now appends an alarm record with a unique `id`, POSTs it directly to
ntfy, and only after a successful POST appends `{"kind":"DELIVERY","ref":"..."}`.
The bridge must therefore pre-scan the complete JSONL for DELIVERY refs before
processing its current unread batch. A one-pass `while read` check is wrong:
the marker normally occurs later in the file than the alarm it covers.

## 1. Install the exact filter

Run this once on the VPS. It does not send, mutate, or truncate alarms:

```sh
install -m 0755 /dev/stdin /root/ntfy_undelivered.py <<'PY'
#!/usr/bin/env python3
import json
import sys

alarm_path = sys.argv[1]
delivered = set()
with open(alarm_path, "r", encoding="utf-8") as source:
    for line in source:
        try:
            record = json.loads(line)
        except (TypeError, ValueError):
            continue
        if record.get("kind") == "DELIVERY" and record.get("ref"):
            delivered.add(str(record["ref"]))

for line in sys.stdin:
    try:
        record = json.loads(line)
    except (TypeError, ValueError):
        # Preserve the bridge's existing malformed-line handling.
        sys.stdout.write(line)
        continue
    if record.get("kind") == "DELIVERY":
        continue
    alarm_id = record.get("id")
    if alarm_id and str(alarm_id) in delivered:
        continue
    # Legacy records have no id and remain eligible for the bridge.
    sys.stdout.write(line)
PY
```

## 2. Patch the unread-alarm input

In `/root/trading_protection_check.sh`, find the existing command that supplies
unread `alarms.jsonl` records to the bridge send loop. Add the filter as the
last stage of that input command, before `while read` consumes it.

If the script uses process substitution, apply this exact diff to that line
(keep its existing `UNREAD_ALARM_COMMAND` expression unchanged):

```diff
-done < <(UNREAD_ALARM_COMMAND)
+done < <(UNREAD_ALARM_COMMAND | python3 /root/ntfy_undelivered.py "$ALARMS_FILE")
```

If it uses a pipeline, the equivalent exact change is:

```diff
-UNREAD_ALARM_COMMAND | while IFS= read -r alarm_json; do
+UNREAD_ALARM_COMMAND | python3 /root/ntfy_undelivered.py "$ALARMS_FILE" | while IFS= read -r alarm_json; do
```

`$ALARMS_FILE` must be the same full path already tailed by the bridge. Do not
remove the bridge's current cursor and per-kind cooldown logic.

## 3. Verify before enabling cron

```sh
tmp_alarm_file="$(mktemp)"
printf '%s\n' \
  '{"ts":"2026-08-09T12:00:00","kind":"KORUMA","message":"sent","id":"a1"}' \
  '{"ts":"2026-08-09T12:01:00","kind":"KORUMA","message":"backstop","id":"a2"}' \
  '{"kind":"DELIVERY","ref":"a1"}' \
  '{"ts":"2026-08-09T12:02:00","kind":"KORUMA","message":"legacy"}' \
  > "$tmp_alarm_file"
python3 /root/ntfy_undelivered.py "$tmp_alarm_file" < "$tmp_alarm_file"
rm -f -- "$tmp_alarm_file"
```

Expected output contains `a2` and `legacy`, but neither `a1` nor the DELIVERY
record. This is an at-least-once, best-effort-dedup contract: a crash after the
bot's successful POST but before marker append can cause a rare duplicate; the
bridge must never skip an unmarked alarm.
