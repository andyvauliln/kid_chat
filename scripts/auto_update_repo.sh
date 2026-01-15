#!/usr/bin/env bash
set -euo pipefail

REMOTE_REF="origin/main"
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"

if [ "${CURRENT_BRANCH}" != "main" ]; then
  echo "[auto_update] current branch is ${CURRENT_BRANCH}; skipping"
  exit 0
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "[auto_update] working tree not clean; skipping"
  exit 0
fi

echo "[auto_update] checking for updates on ${REMOTE_REF}"
git fetch origin main

LOCAL_HASH="$(git rev-parse HEAD)"
REMOTE_HASH="$(git rev-parse "${REMOTE_REF}")"

if [ "${LOCAL_HASH}" != "${REMOTE_HASH}" ]; then
  echo "[auto_update] updating code from ${REMOTE_REF}"
  git pull --ff-only origin main
  npx pm2 restart all
else
  echo "[auto_update] already up to date"
fi
