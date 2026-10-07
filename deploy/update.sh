#!/usr/bin/env bash
# Обновление сайта после git push. На сервере:  bash deploy/update.sh
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
docker compose up -d --build
docker image prune -f >/dev/null
docker compose ps
