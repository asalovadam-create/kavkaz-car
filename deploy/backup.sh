#!/usr/bin/env bash
# Ночной бэкап базы и фото. Хранит последние 14 дней в /opt/backups.
# Подключение к cron:  (crontab -l 2>/dev/null; echo "30 3 * * * /opt/kavkaz-car/deploy/backup.sh >> /var/log/kavkaz-backup.log 2>&1") | crontab -
set -euo pipefail
cd "$(dirname "$0")/.."
DIR=/opt/backups
STAMP=$(date +%F_%H%M)
mkdir -p "$DIR"

docker compose exec -T db pg_dump -U kavkazcar -d kavkazcar --no-owner | gzip > "$DIR/db-$STAMP.sql.gz"
docker run --rm -v kavkazcar_uploads:/data:ro -v "$DIR":/backup alpine \
  tar czf "/backup/uploads-$STAMP.tgz" -C /data .

# удаляем бэкапы старше 14 дней
find "$DIR" -type f \( -name 'db-*.sql.gz' -o -name 'uploads-*.tgz' \) -mtime +14 -delete
echo "$(date -Is) backup ok: $STAMP"
