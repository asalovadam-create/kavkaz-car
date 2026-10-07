# Переезд KAVKAZ-CAR на свой сервер (Selectel, Ubuntu 24.04)

Сервер: 161.104.35.72 · домен: kavkaz-car.ru
Строки в рамках копируйте целиком. Вставка в терминал — правая кнопка мыши.

## Шаг 1. Файлы переезда → GitHub (Git Bash на вашем компьютере)
Распакуйте `kavkaz-deploy.zip` в папку проекта с заменой файлов, затем:
```
git add .
git commit -m "Деплой на свой сервер"
git push
```

## Шаг 2. Домен → сервер (DNS)
Selectel → **Доменные зоны** → создайте зону `kavkaz-car.ru` (если нет) и добавьте две записи **A**:
`@` (сам домен) → `161.104.35.72` и `www` → `161.104.35.72`.
Проверка (Git Bash): `ping kavkaz-car.ru` должен показать 161.104.35.72 (обновление — от 10 минут до пары часов).

## Шаг 3. Подключиться к серверу и подготовить его
```
ssh root@161.104.35.72
```
Пароль root — в панели Selectel (вкладка «Консоль»). На вопрос про fingerprint — `yes`. Дальше уже на сервере:
```
apt-get update && apt-get install -y git
git clone https://github.com/asalovadam-create/kavkaz-car.git /opt/kavkaz-car
cd /opt/kavkaz-car
bash deploy/setup-server.sh
```
(Если репозиторий приватный, вместо пароля вводится токен GitHub с правом Contents: Read-only.)
Скрипт ставит Docker, файрвол, защиту от подбора пароля, swap и автообновления безопасности (3–5 минут).

## Шаг 4. Положить файл настроек на сервер
Скачайте `kavkaz-server-env.txt` (я его приготовил). Откройте **второе окно Git Bash** на вашем компьютере и выполните (путь поправьте, если файл лежит не в «Загрузках»):
```
scp ~/Downloads/kavkaz-server-env.txt root@161.104.35.72:/opt/kavkaz-car/.env
```
Этот файл содержит пароли. Не отправляйте его никому и не кладите в папку проекта.

## Шаг 5. Один запуск вместо многих команд
В окне, где вы подключены к серверу:
```
cd /opt/kavkaz-car
bash deploy/first-run.sh
```
Скрипт по порядку:
1. запустит базу;
2. спросит `DATABASE_URL` с Render (вставьте, ввод не виден) и перенесёт базу из Neon (приставку `-pooler` скрипт уберёт сам; если переносить нечего — Enter);
3. соберёт и запустит сайт;
4. перенесёт фото из Cloudinary на диск сервера;
5. спросит логин и пароль администратора (пароль — минимум 12 символов).

В конце он покажет адрес сайта и адрес входа в админку.

## Шаг 6. После запуска
1. Откройте https://kavkaz-car.ru и админку. При входе нужны: логин, пароль и **ADMIN_SECRET** (он в файле настроек).
2. Включите двухфакторный вход в админку:
```
cd /opt/kavkaz-car
docker compose exec web flask --app app.py enable-admin-2fa
```
3. Включите ночные бэкапы:
```
(crontab -l 2>/dev/null; echo "30 3 * * * /opt/kavkaz-car/deploy/backup.sh >> /var/log/kavkaz-backup.log 2>&1") | crontab -
bash deploy/backup.sh
ls -lh /opt/backups
```
Раз в неделю скачивайте бэкапы к себе (Git Bash на вашем ПК): `scp root@161.104.35.72:/opt/backups/* ./backups/`
И включите автоматические снимки диска в панели Selectel.
4. Зарегистрируйте сайт в **Яндекс Вебмастере** и **Google Search Console**, добавьте `https://kavkaz-car.ru/sitemap.xml`.

## Обновление сайта в будущем
На компьютере: заменили файлы → `git add . && git commit -m "..." && git push`.
На сервере: `ssh root@161.104.35.72`, затем `cd /opt/kavkaz-car && bash deploy/update.sh`

## Если что-то не работает
- Состояние: `docker compose ps`, логи: `docker compose logs --tail=100 web caddy`
- Нет HTTPS: `ping kavkaz-car.ru` должен показывать IP сервера (DNS), порты 80 и 443 открыты (setup-server.sh это делает).
- Смена пароля администратора: `docker compose run --rm web python tools/set_admin.py`
- Нехватка памяти: `free -h`, затем поднять тариф в Selectel до 2 vCPU / 4 ГБ (без переустановки).
- Перезапуск: `docker compose restart`

## Переключение с Render
Делайте шаг 5 и переключение DNS за один вечер, когда на сайте мало людей: что пользователи добавят на Render после выгрузки базы, на новый сервер не попадёт. Render, Neon и Cloudinary держите минимум неделю, потом остановите.
