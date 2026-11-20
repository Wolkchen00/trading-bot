#!/bin/sh
# v5 watchdog. Cron on the host, every 10 minutes, independent of the bot process:
#   */10 * * * * sh /root/v5_watchdog.sh
# For each of the two services it asks the running container for its heartbeat age. No container,
# no answer, or a heartbeat older than five minutes: one push to the owner, at most once per four hours
# per service and per outage (a service that recovered is announced again the next time it fails).
# It only watches: it never starts, ends or changes a container.

TOPIC_FILE=/root/.v5_watchdog_topic
MESSAGE="Trading botu çalışmıyor görünüyor. Claude'a 'trading botuna bak' yaz."
COOLDOWN=14400
LOG=/var/log/v5_watchdog.log

note() {
  printf '%s %s\n' "$(date -u '+%F %T')" "$1" >> "$LOG"
}

TOPIC=""
if [ -r "$TOPIC_FILE" ]; then
  TOPIC=$(tr -d ' \n\r' < "$TOPIC_FILE")
fi
NOW=$(date +%s)
# A container that hangs must not hang the watchdog with it.
LIMIT=""
if command -v timeout >/dev/null 2>&1; then
  LIMIT="timeout 60"
fi

for PREFIX in trading-live- trading-paper-livecfg-; do
  SERVICE=${PREFIX%-}
  STAMP=/var/tmp/v5_watchdog_${SERVICE}
  PROBLEM=""
  NAME=$(docker ps --format '{{.Names}}' | grep "^${PREFIX}" | head -n 1)
  if [ -z "$NAME" ]; then
    PROBLEM="no running container"
  else
    AGE=$($LIMIT docker exec "$NAME" python -m v5.health --json 2>/dev/null \
      | sed -n 's/.*"heartbeat_age_seconds": *\([0-9][0-9]*\).*/\1/p' | head -n 1)
    if [ -z "$AGE" ]; then
      PROBLEM="no heartbeat"
    elif [ "$AGE" -gt 300 ]; then
      PROBLEM="heartbeat is ${AGE} seconds old"
    fi
  fi
  if [ -z "$PROBLEM" ]; then
    rm -f "$STAMP"                      # healthy again: the next outage is a new one and is announced
    continue
  fi
  if [ -z "$TOPIC" ]; then
    note "${SERVICE}: ${PROBLEM} (no topic file, nobody can be told)"
    continue
  fi

  LAST=0
  if [ -r "$STAMP" ]; then
    LAST=$(cat "$STAMP" 2>/dev/null)
  fi
  case "$LAST" in
    ''|*[!0-9]*) LAST=0 ;;
  esac
  if [ $((NOW - LAST)) -ge "$COOLDOWN" ]; then
    TEXT="$MESSAGE"
    if [ "$SERVICE" = "trading-paper-livecfg" ]; then
      TEXT="DENEME HESABI. ${MESSAGE}"  # the practice account never reads like news about real money
    fi
    if curl -fsS -m 20 -H "Title: Trading bot" -H "Priority: 5" -H "Tags: warning" \
        -d "$TEXT" "https://ntfy.sh/${TOPIC}" >/dev/null 2>&1; then
      printf '%s\n' "$NOW" > "$STAMP"
      RESULT="owner told"
    else
      RESULT="push failed"
    fi
    note "${SERVICE}: ${PROBLEM} (${RESULT})"
  fi
done
exit 0
