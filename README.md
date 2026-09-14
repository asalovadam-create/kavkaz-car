# KAVKAZ-CAR

Премиальная платформа поиска и размещения автомобилей в аренду по Северному
Кавказу. KAVKAZ-CAR — агрегатор: владельцы и автопрокаты сами размещают
автомобили, клиент находит подходящий вариант и связывается с владельцем
напрямую (звонок, WhatsApp, Telegram) — без обязательной регистрации.

Список всех реализованных функций — в [`FEATURES.md`](FEATURES.md).
Чек-лист безопасности перед релизом — в [`SECURITY_CHECKLIST.md`](SECURITY_CHECKLIST.md).

---

## 1. Структура файлов

```
kavkaz-car/
├── app.py                        # фабрика приложения, middleware, CLI
├── config.py                     # тарифы, города, категории, лимиты — ЕДИНЫЙ источник
├── models.py                     # SQLAlchemy-модели (схема БД)
├── auth.py                       # регистрация/вход клиента и владельца + отдельный вход админа
├── cars.py                       # каталог, поиск, карточка авто, избранное, сравнение
├── owners.py                     # личный кабинет владельца, мастер добавления авто
├── admin.py                      # админ-панель
├── routes.py                     # главная, юр. страницы, health check, sitemap
├── security.py                   # пароли, CSRF, rate limiting, security headers, проверка фото
├── services.py                   # обработка фото, хранилище, поиск, тарифы, промокоды
├── requirements.txt
├── Procfile                      # для Render
├── Dockerfile                    # для VPS
├── .env.example
├── migrations_reference_schema.sql  # справочная SQL-схема (см. раздел 6)
├── migrations/                   # версии Alembic (создаются командой `flask db init/migrate`)
├── templates/                    # Jinja2-шаблоны (HTML)
├── static/
│   ├── css/style.css             # вся дизайн-система в одном файле (CSS-переменные)
│   ├── js/app.js                 # лёгкий JS без фреймворков
│   ├── icons/icons.svg           # собственный набор SVG-иконок
│   └── uploads/                  # локальное хранилище фото — ТОЛЬКО для разработки
├── FEATURES.md
└── SECURITY_CHECKLIST.md
```

Архитектура — **модульный монолит** (раздел 120 ТЗ): одно Flask-приложение,
логически разделённое на auth / cars / owners / admin / security / services,
вместо микросервисов или десятков мелких файлов.

---

## 2. Установка (локально)

```bash
git clone <ваш-репозиторий> kavkaz-car
cd kavkaz-car
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Откройте `.env` и задайте как минимум `SECRET_KEY` (любая длинная случайная
строка). Для быстрого локального теста без PostgreSQL можно оставить
`DATABASE_URL` пустым — тогда автоматически используется SQLite-файл
`dev.db` (см. `config.py`). **Для production это не подходит** — см. раздел 5.

---

## 3. Переменные окружения (`.env`)

| Переменная | Обязательна | Описание |
|---|---|---|
| `SECRET_KEY` | да | Ключ подписи сессий/CSRF. Генерируйте: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL` | да (production) | Строка подключения PostgreSQL, напр. `postgresql://user:pass@host:5432/db` |
| `FORCE_HTTPS` | нет | `1` в production — cookies только по HTTPS |
| `UPLOAD_PROVIDER` | нет | `local` (разработка) или `cloudinary` (production) |
| `CLOUDINARY_*` | если `UPLOAD_PROVIDER=cloudinary` | Данные из личного кабинета Cloudinary |
| `ADMIN_SECRET` | рекомендуется | Дополнительный секрет, который стоит использовать в вашей инфраструктуре для скрытия `/admin/login` (например, отдельным правилом на reverse proxy) |
| `SITE_URL` | нет | Используется в sitemap.xml и robots.txt |
| `SUPPORT_PHONE` | нет | Показывается в кабинете владельца при апгрейде тарифа |

Никогда не коммитьте `.env` — только `.env.example` с placeholder-значениями.

---

## 4. База данных и миграции

Основная СУБД — **PostgreSQL**. Для локальной разработки допустим SQLite
(автоматически, если `DATABASE_URL` не задан), но структура таблиц полностью
совместима с PostgreSQL без переделки архитектуры.

Миграции — через **Flask-Migrate (Alembic)**:

```bash
# один раз при создании проекта (папка migrations/ уже может быть создана)
flask --app app.py db init

# после любого изменения models.py
flask --app app.py db migrate -m "описание изменения"
flask --app app.py db upgrade
```

Файл `migrations_reference_schema.sql` в корне — это **справочный** снимок
схемы (для ревью, обучения или ручного разворачивания), а не основной
механизм миграций. Никогда не редактируйте боевую БД вручную в обход Alembic.

Заполнение справочников (города) и первый администратор:

```bash
flask --app app.py seed-db        # создаёт таблицы (если их ещё нет) и города
flask --app app.py create-admin   # интерактивно создаёт первого администратора
```

---

## 5. Запуск локально

```bash
flask --app app.py seed-db
flask --app app.py create-admin
flask --app app.py run --debug
```

Откройте `http://127.0.0.1:5000`. Админка — `http://127.0.0.1:5000/admin/login`.

---

## 6. Хранилище фотографий

`UPLOAD_PROVIDER=local` сохраняет файлы в `static/uploads/` — **это только для
разработки**. Диск на Render эфемерный: при пересборке контейнера файлы
теряются. Для production используйте `UPLOAD_PROVIDER=cloudinary` и заполните
`CLOUDINARY_*` переменные.

Провайдер хранилища реализован как отдельный слой (`services.py`,
класс `ImageStorage`) — чтобы позже заменить Cloudinary на Selectel Object
Storage, S3-совместимое хранилище или свой VPS-storage, достаточно добавить
новый класс с методами `upload_bytes`/`delete` и подключить его в
`get_image_storage()`, не трогая остальной код.

---

## 7. Деплой на Render

1. Создайте Web Service из репозитория, окружение — Python 3.
2. Build command: `pip install -r requirements.txt`
3. Start command: `gunicorn app:app --bind 0.0.0.0:$PORT` (Procfile уже это описывает)
4. Добавьте PostgreSQL (Render Postgres) и скопируйте `DATABASE_URL` в переменные окружения сервиса.
5. Задайте `SECRET_KEY`, `FORCE_HTTPS=1`, `UPLOAD_PROVIDER=cloudinary` и остальные переменные из `.env.example`.
6. После первого деплоя выполните миграции и seed через Render Shell:
   ```bash
   flask --app app.py db upgrade
   flask --app app.py seed-db
   flask --app app.py create-admin
   ```
7. Health check для Render: `/health`.

---

## 8. Переезд на VPS

```
Git clone → Docker → .env → PostgreSQL → Nginx → SSL → миграции → Gunicorn
```

```bash
git clone <репозиторий> && cd kavkaz-car
cp .env.example .env   # заполнить реальными значениями
docker build -t kavkaz-car .
docker run -d --name kavkaz-car --env-file .env -p 8000:8000 kavkaz-car
```

Перед первым запуском на VPS накатите миграции (можно выполнить внутри
контейнера или из отдельного окружения с тем же `DATABASE_URL`):

```bash
flask --app app.py db upgrade
```

Настройте Nginx как reverse proxy перед Gunicorn (порт 8000) и выпустите
сертификат (например, Let's Encrypt/Certbot) для HTTPS. Приложение не
привязано к localhost и корректно работает за reverse proxy — во всех
абсолютных ссылках используется `SITE_URL` из конфигурации.

---

## 9. Backup

- Используйте автоматический ежедневный backup вашего PostgreSQL-провайдера
  (Render Postgres или managed PostgreSQL на VPS) с retention от 7 дней.
- Периодически (например, раз в квартал) проверяйте восстановление backup
  на тестовом окружении — backup, который никогда не проверялся, не
  гарантирует восстановление.
- Фотографии хранятся в object storage (Cloudinary/S3-совместимом) — уточните
  политику резервного копирования у выбранного провайдера отдельно от БД.

---

## 10. Безопасность

Полный чек-лист — [`SECURITY_CHECKLIST.md`](SECURITY_CHECKLIST.md). Кратко:
пароли хэшируются (werkzeug/scrypt), все изменяющие запросы защищены CSRF,
на чувствительные действия есть rate limiting, загружаемые фото проверяются
по реальному содержимому (не по расширению), настроены security headers,
админка полностью изолирована (отдельная модель, отдельная сессия, таймаут).

---

## 11. Как добавить новый функционал

Проект — модульный монолит, поэтому для большинства задач правки локальны:

- Новый публичный маршрут → `cars.py` (для контента, связанного с
  автомобилями) или `routes.py` (для общих страниц)
- Новая функция в кабинете владельца → `owners.py`
- Новая функция в админке → `admin.py`
- Новое поле автомобиля/пользователя → `models.py` + `flask db migrate`
- Новая бизнес-логика (расчёты, обработка) → `services.py`
- Новый security-механизм → `security.py`

## 12. Где менять тарифы

`config.py`, словарь `PLANS`. Все шаблоны и бизнес-логика читают лимиты
только оттуда — искать и менять цифры по всему коду не нужно.

## 13. Где менять города

`config.py`, список `LAUNCH_CITIES`. После изменения выполните
`flask --app app.py seed-db`, чтобы синхронизировать таблицу `cities`
(существующие города по `slug` не дублируются).

## 14. Где менять лимиты (rate limiting, лимиты фото и т.д.)

Частота действий — `config.py`, словарь `RATE_LIMITS`. Лимиты фото/авто по
тарифу — там же, `PLANS`. Пороговые значения «неактуальности» объявления —
`LISTING_STALE_AFTER_DAYS` / `LISTING_EXPIRE_AFTER_DAYS`.

## 15. Где менять дизайн

Все дизайн-токены — в начале `static/css/style.css` (`:root { ... }`):
цвета, радиусы, тени, длительности анимаций. Изменение одной переменной
меняет весь сайт. Иконки — `static/icons/icons.svg` и
`templates/partials/icon_sprite.html` (должны быть идентичны).

---

## 16. Не сломай старое

Прежде чем менять архитектуру или удалять код — проверьте, какие маршруты
и шаблоны от него зависят (`grep -rn "имя_функции"` по проекту). Не делайте
деструктивный рефакторинг без причины (раздел 101 ТЗ).

## 17. Тестирование перед релизом

Функциональность, безопасность и адаптивность — см. чек-листы в
`SECURITY_CHECKLIST.md`. Обязательно вручную пройдите: регистрация → вход →
добавление автомобиля через мастер → загрузка фото → появление в каталоге →
поиск/фильтры → звонок/WhatsApp/Telegram → жалоба → админка (блокировка,
промокод) — на iPhone Safari в первую очередь (раздел 56 ТЗ).

---

## Важно перед публичным запуском

- Тексты в `templates/legal/*` — типовые шаблоны. Обязательно проверьте их у
  квалифицированного юриста для вашей юрисдикции перед запуском.
- Замените дефолтный `SECRET_KEY`/`ADMIN_SECRET`, включите `FORCE_HTTPS`,
  переключите `UPLOAD_PROVIDER` на `cloudinary` (или иное постоянное
  хранилище) — все три пункта критичны и описаны выше.
