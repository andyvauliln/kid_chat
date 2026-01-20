#!/usr/bin/env bash
set -euo pipefail

if ! command -v pm2 >/dev/null 2>&1; then
  echo "pm2 not found. Run: npm install" >&2
  exit 1
fi

# pm2 prints "[PM2][WARN] No process found" when the list is empty.
# Avoid that noisy warning by only deleting when there are processes.
if [[ "$(npx pm2 jlist)" != "[]" ]]; then
  npx pm2 delete all
fi
#! bot,onboarding, dayly, weekly, monthly
npx pm2 start ecosystem.config.js --only onboarding

echo "[start_all] enabling pm2 startup"
# On macOS this often requires sudo; don't fail the whole script if not set up yet.
if ! npx pm2 startup; then
  echo "[start_all] pm2 startup not configured (requires sudo). Continuing." >&2
fi

echo "[start_all] saving process list"
npx pm2 save
