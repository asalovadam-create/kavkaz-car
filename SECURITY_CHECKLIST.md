# Security checklist — перед релизом KAVKAZ-CAR

Отметьте каждый пункт перед тем, как открыть сайт для реальных пользователей.
Это не формальность — при сомнении между «быстрее» и «безопаснее» выбирайте
безопаснее, если это не ломает UX.

## Секреты и конфигурация

- [ ] `SECRET_KEY` — случайная строка ≥ 64 символов, задана только через `.env`/secret manager
- [ ] `.env` НЕ закоммичен в Git (проверить `git log --all -- .env`)
- [ ] `DATABASE_URL` указывает на PostgreSQL (не SQLite) в production
- [ ] `FORCE_HTTPS=1` в production
- [ ] `FLASK_DEBUG=0` в production — Flask debugger никогда не должен быть доступен снаружи

## Аутентификация и сессии

- [ ] Пароли хранятся только как хэш (werkzeug `generate_password_hash`, scrypt) — проверить, что нигде нет `password` в открытом виде
- [ ] Cookies: `HttpOnly`, `Secure`, `SameSite=Lax` — проверено в `config.py`
- [ ] Сессия очищается при logout (`session.clear()`)
- [ ] Админка использует отдельную модель `AdminUser` и отдельный ключ сессии `admin_id`
- [ ] Сессия админки автоматически завершается по таймауту (30 минут неактивности)
- [ ] Первый администратор создан через `flask create-admin`, а не через seed с дефолтным паролем

## Rate limiting

- [ ] Login, регистрация, admin-login, report, upload, redeem-promo — все под `@rate_limit`
- [ ] Лимиты не блокируют обычных пользователей при типичном использовании (проверить вручную)
- [ ] Для деплоя с несколькими процессами/машинами — заменить in-memory лимитер на Redis-backed (см. `security.py`, `_attempts`)

## CSRF / инъекции / IDOR

- [ ] Все POST/PUT/PATCH/DELETE формы содержат `csrf_token`
- [ ] Все запросы к БД идут через SQLAlchemy ORM — нет конкатенации/f-строк в SQL
- [ ] Каждый доступ к чужому ресурсу владельца проверяется на backend (`_owned_car_or_404` в `owners.py`) — нельзя открыть чужой автомобиль, изменив ID в URL
- [ ] Публичные URL используют непредсказуемый `public_id`, а не последовательный числовой ID

## Загрузка файлов

- [ ] Расширение файла игнорируется — тип проверяется через реальное содержимое (Pillow `Image.open().verify()`)
- [ ] Ограничен размер файла (`MAX_PHOTO_SOURCE_MB`) и разрешение (`MAX_IMAGE_PIXELS`)
- [ ] EXIF и лишние метаданные удаляются при обработке (`services.optimize_image`)
- [ ] Загруженные файлы никогда не исполняются как код (хранятся как статические webp/через object storage)
- [ ] В production `UPLOAD_PROVIDER=cloudinary` (или другой object storage) — не `local` (диск Render эфемерный)

## Заголовки и транспорт

- [ ] `Content-Security-Policy`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, `X-Frame-Options` — проверить через браузерные DevTools или `curl -I`
- [ ] `Strict-Transport-Security` присутствует при HTTPS
- [ ] Продакшн доступен только по HTTPS (проверить редирект HTTP → HTTPS на уровне reverse proxy/Render)

## Ошибки и логи

- [ ] Пользователь никогда не видит traceback, SQL, `SECRET_KEY`, пути на диске
- [ ] В логах нет паролей, токенов, номеров карт
- [ ] 500-ошибки логируются на сервере (`app.logger.exception`)

## Прочее

- [ ] Промокоды одноразовые, проверяются на срок действия (`PromoCode.is_valid`)
- [ ] Админ-действия (блокировки, смена тарифа, обработка жалоб) пишутся в `admin_logs`
- [ ] Массовое присвоение полей исключено — формы явно перечисляют разрешённые поля (нигде нет `Model(**request.form)`)
- [ ] Резервное копирование БД настроено на уровне провайдера PostgreSQL (Render Postgres / managed backups) с ежедневным расписанием и проверенным восстановлением
- [ ] Юридические тексты (`templates/legal/*`) проверены юристом для целевой юрисдикции — сейчас это шаблоны
