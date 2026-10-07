#!/bin/sh
set -e

echo "==> Running database migrations..."
flask db upgrade || echo "==> Migration step failed or not needed, continuing..."

echo "==> Starting gunicorn..."
# WEB_WORKERS: на сервере с 1 ядром достаточно 2 процессов (по умолчанию 2)
exec gunicorn app:app \
  --bind 0.0.0.0:${PORT:-8000} \
  --workers ${WEB_WORKERS:-2} \
  --timeout 60 \
  --worker-tmp-dir /dev/shm \
  --access-logfile - \
  --error-logfile -
