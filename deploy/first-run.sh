#!/usr/bin/env bash
# Первый запуск: база из Neon -> сервер, запуск сайта, фото из Cloudinary -> сервер, администратор.
# На сервере, из папки проекта:  bash deploy/first-run.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -s .env ]; then echo "Нет файла .env в $(pwd). Сначала положите его на сервер (см. DEPLOY.md, шаг 5)."; exit 1; fi
if ! grep -q '^DB_PASSWORD=.\+' .env; then echo "В .env пустой DB_PASSWORD."; exit 1; fi

echo "==> 1/5 Запускаю базу данных"
docker compose up -d db
until docker compose exec -T db pg_isready -U kavkazcar -d kavkazcar >/dev/null 2>&1; do sleep 2; done

echo
echo "==> 2/5 Перенос базы из Neon"
echo "Вставьте адрес базы из Render (DATABASE_URL) и нажмите Enter."
echo "(Ввод не отображается. Если переносить нечего — просто нажмите Enter.)"
read -r -s -p "DATABASE_URL: " NEON_URL
echo
if [ -n "$NEON_URL" ]; then
  NEON_URL="${NEON_URL//-pooler/}"      # для выгрузки нужен прямой адрес, без -pooler
  NEON_URL="${NEON_URL#\"}"; NEON_URL="${NEON_URL%\"}"
  docker run --rm postgres:17-alpine pg_dump --no-owner --no-acl --format=custom "$NEON_URL" > /opt/neon.dump
  ls -lh /opt/neon.dump
  set +e
  docker compose exec -T db pg_restore -U kavkazcar -d kavkazcar --no-owner --no-acl < /opt/neon.dump
  RESTORE_CODE=$?
  set -e
  if [ "$RESTORE_CODE" -ne 0 ]; then echo "(pg_restore сообщил о предупреждениях — для пустой базы это обычно не страшно; ниже проверим сайт)"; fi
else
  echo "Пропускаю: сайт стартует с пустой базой."
fi

echo
echo "==> 3/5 Сборка и запуск сайта (несколько минут)"
docker compose up -d --build

echo
echo "==> 4/5 Перенос фото из Cloudinary"
docker compose run --rm web python tools/migrate_photos.py

echo
echo "==> 5/5 Администратор сайта"
docker compose run --rm web python tools/set_admin.py

echo
docker compose ps
ADMIN_PATH=$(grep '^ADMIN_PATH=' .env | cut -d= -f2)
DOMAIN=$(grep '^DOMAIN=' .env | cut -d= -f2)
echo
echo "Готово. Сайт:   https://${DOMAIN}"
echo "Админка:        https://${DOMAIN}/${ADMIN_PATH#/}/login"
