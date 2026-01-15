#!/usr/bin/env bash
set -euo pipefail

if ! command -v pm2 >/dev/null 2>&1; then
  echo "pm2 not found. Run: npm install" >&2
  exit 1
fi

update_from_main() {
  local remote_ref="origin/main"
  echo "[start_all] checking for updates on ${remote_ref}"
  git fetch origin main

  local local_hash
  local remote_hash
  local_hash=$(git rev-parse HEAD)
  remote_hash=$(git rev-parse "${remote_ref}")

  if [ "${local_hash}" != "${remote_hash}" ]; then
    echo "[start_all] updating code from ${remote_ref}"
    git pull --ff-only origin main
  else
    echo "[start_all] already up to date"
  fi
}

update_from_main
npx pm2 delete all
#! bot,onboarding, dayly, weekly, monthly
npx pm2 start ecosystem.config.js --only onboarding,auto_update

echo "[start_all] enabling pm2 startup"
npx pm2 startup

echo "[start_all] saving process list"
npx pm2 save
