#!/bin/sh
set -e

echo "==> Running database migrations..."
flask db upgrade || echo "==> Migration step failed or not needed, continuing..."

echo "==> Starting gunicorn..."
exec gunicorn app:app --bind 0.0.0.0:${PORT:-8000} --workers 3 --timeout 60
