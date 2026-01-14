#!/usr/bin/env bash
set -euo pipefail

REPORT_TZ="${REPORT_TZ:-UTC}"
PYTHON_BIN="${PYTHON_BIN:-python}"
REPORT_SEND="${REPORT_SEND:-true}"

DATE="$(
  TZ="$REPORT_TZ" "$PYTHON_BIN" - <<'PY'
from datetime import date, timedelta
print((date.today() - timedelta(days=1)).isoformat())
PY
)"

ARGS=(--date "$DATE")
if [ "$REPORT_SEND" = "false" ]; then
  ARGS+=(--no-send)
fi

"$PYTHON_BIN" -m group_chat_telegram_ai.weekly_report "${ARGS[@]}"
