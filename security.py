"""
Слой безопасности KAVKAZ-CAR.

Собран в одном месте намеренно: пароли, CSRF, rate limiting, security
headers и проверка загружаемых файлов — это фундамент, а не украшение
(раздел 136 ТЗ: при сомнении выбирать безопасность, если это не ломает UX).
"""
import io
import secrets
import time
from collections import defaultdict
from functools import wraps

from flask import abort, current_app, g, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from config import RATE_LIMITS

# ---------------------------------------------------------------------------
# Пароли
# ---------------------------------------------------------------------------

def hash_password(raw_password: str) -> str:
    """werkzeug использует scrypt по умолчанию — современный алгоритм,
    отдельная библиотека вроде bcrypt не нужна (раздел 87 ТЗ: не плодить
    зависимости без причины)."""
    return generate_password_hash(raw_password)


def verify_password(password_hash: str, raw_password: str) -> bool:
    return check_password_hash(password_hash, raw_password)


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
    if request.blueprint == "admin_panel" or request.path.startswith("/admin"):
        pass  # админка тоже проверяется — исключений нет
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


def _client_key(action: str) -> str:
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
    return f"{action}:{ip}"


def is_rate_limited(action: str) -> bool:
    limit, window = RATE_LIMITS.get(action, (30, 60))
    key = _client_key(action)
    now = time.time()
    history = _attempts[key]
    # чистим устаревшие попытки
    while history and history[0] < now - window:
        history.pop(0)
    if len(history) >= limit:
        return True
    history.append(now)
    return False


def rate_limited_response():
    from flask import jsonify, render_template
    if request.accept_mimetypes.best == "application/json" or request.is_json:
        return jsonify(error="Слишком много попыток. Подождите немного и повторите."), 429
    return render_template("errors/429.html"), 429


def rate_limit(action: str):
    """Декоратор: ограничивает частоту вызова роута по IP."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if is_rate_limited(action):
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
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    response.headers.setdefault("Content-Security-Policy", csp)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(self), camera=(), microphone=()")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if request.is_secure:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
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

    try:
        image = Image.open(file_storage.stream)
        image.verify()  # проверяет целостность, но закрывает поток для повторного чтения
        file_storage.stream.seek(0)
        image = Image.open(file_storage.stream)  # переоткрываем для реальной обработки
        image.load()
    except Exception as exc:  # намеренно широкий except: любое некорректное изображение
        raise InvalidImageError("Файл не является поддерживаемым изображением.") from exc

    if image.format not in ALLOWED_IMAGE_FORMATS:
        raise InvalidImageError("Поддерживаются только JPEG, PNG и WEBP.")

    if image.width * image.height > MAX_IMAGE_PIXELS:
        raise InvalidImageError("Слишком большое разрешение изображения.")

    return image


# ---------------------------------------------------------------------------
# Декораторы доступа
# ---------------------------------------------------------------------------

def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            from flask import redirect, url_for
            return redirect(url_for("auth.login", next=request.path))
        return view_func(*args, **kwargs)

    return wrapped


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("admin_id"):
            from flask import redirect, url_for
            return redirect(url_for("auth.admin_login"))
        return view_func(*args, **kwargs)

    return wrapped


def current_user():
    """Возвращает текущего пользователя из g (наполняется в app.py)."""
    return getattr(g, "current_user", None)
