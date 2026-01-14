#!/usr/bin/env bash
set -euo pipefail

if ! command -v pm2 >/dev/null 2>&1; then
  echo "pm2 not found. Run: npm install" >&2
  exit 1
fi
npx pm2 delete all
#! bot,onboarding, dayly, weekly, monthly
npx pm2 start ecosystem.config.js --only onboarding

echo "[start_all] enabling pm2 startup"
npx pm2 startup

echo "[start_all] saving process list"
npx pm2 save
