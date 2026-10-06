"""
Слой безопасности KAVKAZ-CAR.

Собран в одном месте намеренно: пароли, CSRF, rate limiting, security
headers и проверка загружаемых файлов — это фундамент, а не украшение
(раздел 136 ТЗ: при сомнении выбирать безопасность, если это не ломает UX).
"""
import io
import secrets
import threading
import time
from collections import defaultdict
from functools import wraps

from flask import abort, current_app, g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from config import RATE_LIMITS
from validators import safe_next_url  # noqa: F401  (реэкспорт для удобства)

MAX_PASSWORD_LENGTH = 128  # защита от DoS: scrypt на мегабайтном «пароле» грузит CPU

# Пути, куда POST приходит от внешних систем (платёжный провайдер) и поэтому не
# может содержать CSRF-токен. Такие запросы защищены проверкой подписи.
CSRF_EXEMPT_PREFIXES = ("/payments/webhook/",)

# ---------------------------------------------------------------------------
# Пароли
# ---------------------------------------------------------------------------

def hash_password(raw_password: str) -> str:
    """werkzeug использует scrypt по умолчанию — современный алгоритм,
    отдельная библиотека вроде bcrypt не нужна (раздел 87 ТЗ: не плодить
    зависимости без причины)."""
    return generate_password_hash(raw_password)


def verify_password(password_hash: str, raw_password: str) -> bool:
    if len(raw_password or "") > MAX_PASSWORD_LENGTH:
        return False
    return check_password_hash(password_hash, raw_password)


_DUMMY_HASH = generate_password_hash("kavkaz-car-dummy-password")


def burn_password_check(raw_password: str) -> None:
    """Для несуществующего пользователя тратим столько же времени, сколько на
    настоящую проверку — иначе по скорости ответа можно перебирать, какие номера
    зарегистрированы."""
    check_password_hash(_DUMMY_HASH, (raw_password or "")[:MAX_PASSWORD_LENGTH])


# ---------------------------------------------------------------------------
# CSRF-защита
# ---------------------------------------------------------------------------

def get_csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_hex(32)
        session["_csrf_token"] = token
    return token


def csrf_protect():
    """Вызывается в before_request для всех изменяющих запросов."""
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return
    if request.path.startswith(CSRF_EXEMPT_PREFIXES):
        return  # вебхуки платёжной системы: защищены подписью, см. payments.py
    submitted = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    expected = session.get("_csrf_token")
    if not expected or not submitted or not secrets.compare_digest(submitted, expected):
        abort(400, description="Сессия устарела, обновите страницу и попробуйте снова.")


# ---------------------------------------------------------------------------
# Rate limiting (in-memory, для одного процесса).
#
# Для деплоя с несколькими worker-процессами/машинами лимиты стоит вынести
# в Redis — это явная точка расширения, но НЕ обязательная на старте
# (раздел 119 ТЗ: не тащить Redis без необходимости).
# ---------------------------------------------------------------------------

_attempts: dict[str, list[float]] = defaultdict(list)
_attempts_lock = threading.Lock()
_calls_since_purge = 0
_MAX_WINDOW = 3600


def client_ip() -> str:
    """Настоящий IP клиента. Берём request.remote_addr: за reverse proxy его
    корректно подставляет ProxyFix (см. app.py). Заголовок X-Forwarded-For
    напрямую НЕ читаем — клиент мог бы прислать любой и обходить лимиты."""
    return request.remote_addr or "unknown"


def _key(action: str, extra: str = "") -> str:
    return f"{action}:{extra or client_ip()}"


def _purge_stale(now: float) -> None:
    """Раз в N вызовов удаляем давно неактивные ключи (иначе память росла бы вечно)."""
    global _calls_since_purge
    _calls_since_purge += 1
    if _calls_since_purge < 500:
        return
    _calls_since_purge = 0
    for key in [k for k, hist in _attempts.items() if not hist or hist[-1] < now - _MAX_WINDOW]:
        _attempts.pop(key, None)


def is_rate_limited(action: str, extra: str = "") -> bool:
    """Записывает попытку и возвращает True, если лимит превышен."""
    limit, window = RATE_LIMITS.get(action, (30, 60))
    now = time.time()
    with _attempts_lock:
        _purge_stale(now)
        history = _attempts[_key(action, extra)]
        while history and history[0] < now - window:
            history.pop(0)
        if len(history) >= limit:
            return True
        history.append(now)
        return False


def is_throttled(action: str, extra: str = "") -> bool:
    """Только проверяет, не превышен ли лимит (попытку не записывает)."""
    limit, window = RATE_LIMITS.get(action, (30, 60))
    now = time.time()
    with _attempts_lock:
        history = _attempts.get(_key(action, extra), [])
        return len([t for t in history if t >= now - window]) >= limit


def record_failure(action: str, extra: str = "") -> None:
    """Записывает неудачную попытку (для лимитов «только по ошибкам»)."""
    now = time.time()
    with _attempts_lock:
        _purge_stale(now)
        _attempts[_key(action, extra)].append(now)


def clear_failures(action: str, extra: str = "") -> None:
    with _attempts_lock:
        _attempts.pop(_key(action, extra), None)


def wants_json() -> bool:
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.is_json
        or request.accept_mimetypes.best == "application/json"
    )


def rate_limited_response():
    from flask import render_template
    if wants_json():
        return jsonify(error="Слишком много попыток. Подождите немного и повторите."), 429
    return render_template("errors/429.html"), 429


def rate_limit(action: str, methods: tuple[str, ...] = ("POST",)):
    """Декоратор: ограничивает частоту вызова роута по IP.

    По умолчанию считаются только POST — раньше лимит съедали и обычные
    открытия страницы входа/регистрации (5 обновлений -> «Слишком много попыток»)."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if request.method in methods and is_rate_limited(action):
                return rate_limited_response()
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

def apply_security_headers(response):
    csp = (
        "default-src 'self'; "
        "img-src 'self' data: https:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    response.headers.setdefault("Content-Security-Policy", csp)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(self), camera=(), microphone=(), payment=()")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if request.is_secure:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    # Страницы для вошедших пользователей и админки не кешируем вовсе — иначе
    # после выхода кнопка «Назад» показала бы чужие данные. Публичные страницы
    # разрешаем показывать из истории браузера (кнопка «Назад» в каталоге не
    # теряет место, где вы остановились), но всегда с проверкой актуальности.
    if response.mimetype == "text/html":
        private_view = bool(g.get("current_user") or g.get("current_admin"))
        response.headers["Cache-Control"] = "no-store" if private_view else "private, no-cache"
    return response


# ---------------------------------------------------------------------------
# Проверка загружаемых изображений — не доверяем расширению файла
# (раздел 14 ТЗ: MIME, сигнатура, реальное содержимое).
# ---------------------------------------------------------------------------

ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_IMAGE_PIXELS = 24_000_000  # защита от «image bomb»


class InvalidImageError(ValueError):
    pass


def validate_and_load_image(file_storage):
    """Открывает файл через Pillow и проверяет, что это действительно
    поддерживаемое изображение разумного размера. Возвращает объект Image.

    Расширение имени файла игнорируется полностью — проверяется реальный
    формат по содержимому (magic bytes), как того требует ТЗ.
    """
    from PIL import Image

    file_storage.stream.seek(0, io.SEEK_END)
    size_bytes = file_storage.stream.tell()
    file_storage.stream.seek(0)

    max_bytes = current_app.config.get("MAX_PHOTO_SOURCE_MB", 10) * 1024 * 1024
    if size_bytes <= 0 or size_bytes > max_bytes:
        raise InvalidImageError("Файл слишком большой или пустой.")

    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS  # Pillow сам откажется открывать гигантские файлы
    try:
        probe = Image.open(file_storage.stream)
        # Формат и размер смотрим ДО полной загрузки пикселей в память.
        if probe.format not in ALLOWED_IMAGE_FORMATS:
            raise InvalidImageError("Поддерживаются только JPEG, PNG и WEBP.")
        if probe.width * probe.height > MAX_IMAGE_PIXELS:
            raise InvalidImageError("Слишком большое разрешение изображения.")
        probe.verify()  # проверяет целостность, но закрывает поток для повторного чтения
        file_storage.stream.seek(0)
        image = Image.open(file_storage.stream)  # переоткрываем для реальной обработки
        image.load()
    except InvalidImageError:
        raise
    except Exception as exc:  # намеренно широкий except: любое некорректное изображение
        raise InvalidImageError("Файл не является поддерживаемым изображением.") from exc

    return image


# ---------------------------------------------------------------------------
# Декораторы доступа
# ---------------------------------------------------------------------------

def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not g.get("current_user"):
            from flask import redirect, url_for
            if wants_json():
                return jsonify(error="Войдите в аккаунт.", login_url=url_for("auth.login")), 401
            target = request.full_path.rstrip("?") if request.method == "GET" else request.path
            return redirect(url_for("auth.login", next=target))
        return view_func(*args, **kwargs)

    return wrapped


def owner_mode_required(view_func):
    """Доступ к размещению/управлению автомобилями — только владельцам.
    Клиента ведём на страницу «Стать владельцем», а не в профиль с непонятным
    переключателем. Применяется ПОСЛЕ @login_required."""

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not g.current_user or not g.current_user.is_owner:
            from flask import redirect, url_for
            if wants_json():
                return jsonify(error="Доступно только владельцам."), 403
            return redirect(url_for("owner.become"))
        return view_func(*args, **kwargs)

    return wrapped


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not g.get("current_admin"):
            from flask import redirect, url_for
            return redirect(url_for("auth.admin_login"))
        return view_func(*args, **kwargs)

    return wrapped


def current_user():
    """Возвращает текущего пользователя из g (наполняется в app.py)."""
    return getattr(g, "current_user", None)
