# Переезд KAVKAZ-CAR на свой сервер (Selectel, Ubuntu 24.04)

Сервер: IP 161.104.35.72 · домен: kavkaz-car.ru
Все команды ниже копируйте целиком. Строки со значком `$` — это то, что вводить; сам `$` не вводить.

---

## Шаг 1. Залить файлы переезда в GitHub (на вашем компьютере, Git Bash)
1. Распакуйте `kavkaz-deploy.zip` в папку проекта с заменой файлов.
2. В Git Bash:
```
git add .
git commit -m "Деплой на свой сервер: docker, caddy, бэкапы"
git push
```

## Шаг 2. Домен → сервер (DNS)
В личном кабинете Selectel: **Доменные зоны** → создайте зону `kavkaz-car.ru` (если её ещё нет) и добавьте две записи типа **A**:

| Имя | Значение |
|---|---|
| `kavkaz-car.ru.` (или @) | `161.104.35.72` |
| `www` | `161.104.35.72` |

Если домен куплен у Selectel, серверы имён (NS) обычно уже выставлены на Selectel. Если нет — на странице домена в «Домены» укажите NS Selectel (они написаны в зоне). Обновление адреса занимает от 10 минут до пары часов.

Проверка с вашего компьютера (Git Bash): `ping kavkaz-car.ru` — должен показать 161.104.35.72.

## Шаг 3. Подключиться к серверу
В Git Bash:
```
ssh root@161.104.35.72
```
Пароль root — в панели Selectel (вкладка «Консоль»). Вставка в терминал — правая кнопка мыши. При вопросе про fingerprint напишите `yes`.

## Шаг 4. Скачать проект на сервер
```
apt-get update && apt-get install -y git
git clone https://github.com/ВАШ_ЛОГИН/ВАШ_РЕПОЗИТОРИЙ.git /opt/kavkaz-car
cd /opt/kavkaz-car
```
Если репозиторий приватный, GitHub спросит логин и пароль. Вместо пароля вводится **токен**: GitHub → Settings → Developer settings → Personal access tokens → Fine-grained → доступ только к этому репозиторию, право Contents: Read-only.

## Шаг 5. Настроить сервер (один раз)
```
bash deploy/setup-server.sh
```
Скрипт ставит Docker, включает файрвол (открыты только SSH, 80, 443), защиту от подбора пароля, swap 2 ГБ и автообновления безопасности. Идёт 3–5 минут.

## Шаг 6. Файл с настройками (.env)
```
cp deploy/env.example .env
openssl rand -hex 16
nano .env
```
Команда `openssl` выдаст случайную строку — вставьте её в `DB_PASSWORD=`.
Заполните остальное: `ACME_EMAIL` (ваша почта), `SECRET_KEY`, `ADMIN_PATH`, `ADMIN_SECRET`, `SUPPORT_PHONE`.
**Значения SECRET_KEY, ADMIN_PATH, ADMIN_SECRET скопируйте с Render** (Environment) — тогда секретный адрес админки и входы остаются прежними.
Сохранить в nano: `Ctrl+O`, Enter, выйти: `Ctrl+X`.

## Шаг 7. Перенести базу из Neon
1. В Neon откройте Connection string и **уберите `-pooler` из адреса хоста** (для выгрузки нужен прямой адрес).
2. На сервере (адрес вставьте в одинарных кавычках):
```
cd /opt/kavkaz-car
docker compose up -d db
docker run --rm postgres:17-alpine pg_dump --no-owner --no-acl --format=custom 'ВАШ_АДРЕС_NEON' > /opt/neon.dump
ls -lh /opt/neon.dump
docker compose exec -T db pg_restore -U kavkazcar -d kavkazcar --no-owner --no-acl < /opt/neon.dump
```
Если `pg_dump` пишет про разницу версий — замените `17` в `postgres:17-alpine` на версию из сообщения.
Если к Neon с сервера не подключиться (бывает) — скажите мне, дам обходной путь через ваш компьютер.

## Шаг 8. Запуск сайта
```
docker compose up -d --build
docker compose ps
docker compose logs --tail=50 web
```
Первая сборка занимает несколько минут. В `ps` у трёх сервисов (db, web, caddy) должно быть Up. Caddy сам получит HTTPS-сертификат, когда DNS уже указывает на сервер. Откройте https://kavkaz-car.ru

## Шаг 9. Перенести фото из Cloudinary
```
docker compose run --rm web python tools/migrate_photos.py --dry-run
docker compose run --rm web python tools/migrate_photos.py
```
Первая команда только покажет список, вторая переносит. В конце будет строка «Готово. Перенесено: …, ошибок: …». Скрипт безопасно запускать повторно.

## Шаг 10. Безопасность админки и бэкапы
```
docker compose exec web flask --app app.py enable-admin-2fa
(crontab -l 2>/dev/null; echo "30 3 * * * /opt/kavkaz-car/deploy/backup.sh >> /var/log/kavkaz-backup.log 2>&1") | crontab -
bash deploy/backup.sh
ls -lh /opt/backups
```
Бэкапы лежат в `/opt/backups` (14 дней). **Раз в неделю скачивайте их к себе на компьютер** (в Git Bash на вашем ПК: `scp root@161.104.35.72:/opt/backups/* ./backups/`) — если сломается сам сервер, бэкапы на нём пропадут вместе с ним.
Дополнительно включите автоматические снимки диска в панели Selectel.

## Шаг 11. Поиск
Зарегистрируйте сайт в **Яндекс Вебмастере** и **Google Search Console**, добавьте карту сайта `https://kavkaz-car.ru/sitemap.xml`.

---

## Обновление сайта в будущем
На компьютере, как всегда: заменили файлы → `git add . && git commit -m "..." && git push`.
На сервере:
```
ssh root@161.104.35.72
cd /opt/kavkaz-car && bash deploy/update.sh
```

## Если что-то не работает
- Сайт не открывается: `docker compose ps` и `docker compose logs --tail=100 web caddy`
- Нет HTTPS: проверьте, что `ping kavkaz-car.ru` показывает IP сервера, и откройте порты 80/443 в файрволе панели Selectel, если он там включён.
- Нехватка памяти: `free -h`; тогда в панели Selectel поднимите тариф до 2 vCPU / 4 ГБ (переустановка не нужна).
- Перезапуск: `docker compose restart`

## Переключение с Render
1. Не удаляйте Render, Neon и Cloudinary минимум неделю.
2. Данные, которые пользователи добавят на Render после выгрузки базы (шаг 7), на новый сервер не попадут. Поэтому делайте шаги 7–9 и переключайте DNS в один вечер, когда на сайте мало людей.
3. Когда новый сайт проверен, остановите (Suspend) сервис на Render.
