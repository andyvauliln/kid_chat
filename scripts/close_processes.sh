#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Killing Telegram polling processes in: ${PROJECT_DIR}"
echo

print_matches() {
  local title="$1"
  echo "${title}:"
  local any=0
  for p in "${PATTERNS[@]}"; do
    while IFS= read -r pid; do
      [[ -z "$pid" ]] && continue
      any=1
      ps -p "$pid" -o pid= -o command= || true
    done < <(pgrep -f "$p" || true)
  done
  if [[ "$any" -eq 0 ]]; then
    echo "(none)"
  fi
}

PATTERNS=(
  # The actual python processes (often don't include project path)
  "python.*test_handle_message\\.py"
  "Python.*test_handle_message\\.py"
  "python.*bot\\.py"
  "Python.*bot\\.py"
  "python.*group_chat_telegram_ai\\.bot"
  "Python.*group_chat_telegram_ai\\.bot"

  # The Cursor/terminal wrapper processes (often include project path + cd ...)
  "${PROJECT_DIR}.*test_handle_message\\.py"
  "${PROJECT_DIR}.*src/group_chat_telegram_ai/bot\\.py"
  "${PROJECT_DIR}.*group_chat_telegram_ai\\.bot"
)

print_matches "Before"

echo
echo "Killing..."
for p in "${PATTERNS[@]}"; do
  pkill -f "$p" || true
done

sleep 1

echo
echo "Force-killing leftovers..."
for p in "${PATTERNS[@]}"; do
  pkill -9 -f "$p" || true
done

sleep 1

echo
print_matches "After"

alive=0
for p in "${PATTERNS[@]}"; do
  if pgrep -f "$p" >/dev/null 2>&1; then
    alive=1
  fi
done

echo
if [[ "$alive" -eq 1 ]]; then
  echo "Some processes are still alive. Kill them manually (see list above)."
  exit 2
fi

echo "OK: no matching polling processes running."
