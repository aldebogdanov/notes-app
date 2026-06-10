#!/usr/bin/env bash
# End-to-end demo: a dated note really produces a Telegram message.
#
# Requirements: stack running with a bot token (TELEGRAM_BOT_TOKEN=... make up),
# jq, curl, and a Telegram account at hand to press Start.
set -euo pipefail

API=${API:-http://localhost:8000/api}

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing dependency: $1"; exit 1; }; }
need jq
need curl

echo "==> checking backend"
curl -sf "${API%/api}/healthz" >/dev/null || { echo "backend is not up — run: make up"; exit 1; }

echo "==> checking bot token inside the backend container"
if ! docker compose exec -T backend sh -c '[ -n "${TELEGRAM_BOT_TOKEN:-}" ]'; then
  echo "TELEGRAM_BOT_TOKEN is empty — restart with: TELEGRAM_BOT_TOKEN=... make up"
  exit 1
fi

echo "==> seeding demo user"
make seed >/dev/null

echo "==> logging in as demo"
TOKEN=$(curl -sf -X POST "$API/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'username=demo&password=demo1234' | jq -r .access_token)
AUTH="Authorization: Bearer $TOKEN"

echo "==> requesting a link code"
DEEP_LINK=$(curl -sf -X POST "$API/account/notifications/telegram/link" -H "$AUTH" | jq -r .deep_link)
echo
echo "    Open this link and press START in Telegram:"
echo "    $DEEP_LINK"
echo

verified=""
for _ in 1 2 3; do
  read -rp "    Press Enter here after pressing Start... "
  if curl -sf -X POST "$API/account/notifications/telegram/verify" -H "$AUTH" >/dev/null; then
    verified=yes
    break
  fi
  echo "    Not seen yet (did you press Start?). Retrying..."
done
[ -n "$verified" ] || { echo "FAIL: could not verify the Telegram link"; exit 1; }
echo "==> linked"

echo "==> enabling telegram notifications"
curl -sf -X PUT "$API/account/notifications/settings" -H "$AUTH" \
  -H 'Content-Type: application/json' \
  -d '{"channels":{"telegram":{"enabled":true}}}' >/dev/null

# The demo user's timezone defaults to UTC, so the note is dated in UTC.
TODAY_UTC=$(date -u +%F)
echo "==> creating a note dated $TODAY_UTC"
NOTE_ID=$(curl -sf -X POST "$API/notes" -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"title\":\"Reminder demo $(date -u +%H:%M:%S)\",\"content\":\"Sent by scripts/demo-reminder.sh\",\"note_date\":\"$TODAY_UTC\"}" \
  | jq -r .id)

echo "==> waiting for the scheduler (note id $NOTE_ID, timeout 90s)"
STATUS=pending
for _ in $(seq 1 30); do
  STATUS=$(curl -sf "$API/notes/$NOTE_ID" -H "$AUTH" \
    | jq -r '.notification_status.telegram // "pending"')
  case "$STATUS" in
    sent)
      echo "PASS: notification_status.telegram=sent — check your Telegram!"
      exit 0
      ;;
    failed)
      echo "FAIL: status=failed — see: docker compose logs backend"
      exit 1
      ;;
  esac
  sleep 3
done
echo "FAIL: timed out after 90s (last status: $STATUS)"
exit 1
