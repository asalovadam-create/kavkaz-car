"""
KAVKAZ-CAR — точка входа приложения.

Модульный монолит (раздел 120 ТЗ): одно Flask-приложение, но логически
разделённое на auth / cars / owners / payments / admin / security / services.
"""
import hmac
import logging
import os
from datetime import datetime, timedelta

from flask import Flask, abort, g, jsonify, render_template, request, session, url_for
from flask.sessions import SecureCookieSessionInterface
from werkzeug.middleware.proxy_fix import ProxyFix

from config import (
    Config, ORDER_STATUS_LABELS, PLANS, STATUS_LABELS, admin_prefix, is_placeholder_admin_secret,
    is_weak_secret,
)
from models import AdminUser, Car, Favorite, Notification, User, db
from security import (
    apply_security_headers, csrf_protect, get_csrf_token, wants_json,
)
from services import format_price
from validators import format_phone, ru_plural

class SplitSessionInterface(SecureCookieSessionInterface):
    """Админка и сайт используют РАЗНЫЕ cookie.

    Раньше у них была одна общая cookie «session»: вход в админку стирал клиентский вход
    (и наоборот), а форма в соседней вкладке получала «Сессия устарела». Теперь cookie
    админки называется иначе, действует только на секретном адресе админки и только
    для переходов с этого же сайта (SameSite=Strict)."""

    ADMIN_COOKIE = "kc_adm"

    def _is_admin_request(self, app) -> bool:
        prefix = app.config.get("ADMIN_PREFIX", "")
        return bool(prefix) and (request.path == prefix or request.path.startswith(prefix + "/"))

    def get_cookie_name(self, app):
        return self.ADMIN_COOKIE if self._is_admin_request(app) else super().get_cookie_name(app)

    def get_cookie_path(self, app):
        return app.config["ADMIN_PREFIX"] if self._is_admin_request(app) else super().get_cookie_path(app)

    def get_cookie_samesite(self, app):
        return "Strict" if self._is_admin_request(app) else super().get_cookie_samesite(app)


def config_warnings(app: Flask) -> list[str]:
    """Что в настройках небезопасно или не доделано. Показывается в админке."""
    warnings = []
    if app.config.get("WEAK_SECRET"):
        warnings.append(
            "SECRET_KEY слабый или остался из примера. Задайте длинный случайный ключ в переменных "
            "окружения (python -c \"import secrets; print(secrets.token_hex(32))\")."
        )
    if app.config.get("ENV") == "production" and app.config.get("UPLOAD_PROVIDER") == "local":
        warnings.append(
            "Фото хранятся на диске сервера (UPLOAD_PROVIDER=local) — на Render они пропадут при "
            "перезапуске. Подключите Cloudinary."
        )
    if not app.config.get("SESSION_COOKIE_SECURE"):
        warnings.append("FORCE_HTTPS выключен: cookie сессии могут передаваться без шифрования.")
    if is_placeholder_admin_secret(app.config.get("ADMIN_SECRET")):
        warnings.append("ADMIN_SECRET не задан или остался из примера — вход в админку защищён только паролем.")
    from services import get_settings
    saved = get_settings() if app.config.get("_BOOTSTRAPPED") else {}
    if not (saved.get("support_telegram") or saved.get("support_whatsapp") or saved.get("support_phone")
            or app.config.get("SUPPORT_TELEGRAM") or app.config.get("SUPPORT_WHATSAPP") or app.config.get("SUPPORT_PHONE")):
        warnings.append("Не указаны контакты поддержки — клиенты не смогут связаться при покупке тарифа. "
                        "Заполните их в админке: «Настройки сайта».")
    if not app.config.get("ADMIN_PATH"):
        warnings.append("Секретный адрес админки вычислен автоматически. Задайте свой в переменной ADMIN_PATH "
                        "(например /ctl-k8f3a9x2q7), чтобы его знали только вы.")
    if not app.config.get("ADMIN_ALLOWED_IPS"):
        warnings.append("Вход в админку не ограничен по IP (ADMIN_ALLOWED_IPS). Если у вас статичный IP — задайте его.")
    if app.config.get("PAYMENT_PROVIDER") == "manual":
        warnings.append("Онлайн-оплата не подключена: тарифы оплачиваются по заявке и подтверждаются вручную.")
    if app.config.get("PAYMENT_PROVIDER") == "rollypay" and not app.config.get("ROLLYPAY_SECRET"):
        warnings.append("Rolly Pay включён, но ROLLYPAY_SECRET пуст — вебхуки оплаты будут отклоняться.")
    return warnings


def _asset_version() -> str:
    """Версия статики = время последнего изменения css/js. Меняется при обновлении
    файлов, поэтому пользователи не застревают на старом дизайне из-за кеша."""
    base = os.path.join(os.path.dirname(__file__), "static")
    newest = 0
    for rel in ("css/style.css", "js/app.js"):
        try:
            newest = max(newest, int(os.path.getmtime(os.path.join(base, rel))))
        except OSError:
            pass
    return str(newest or 1)


def create_app(config_object: type = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object)

    if not app.config.get("SECRET_KEY"):
        if app.config.get("DEBUG"):
            app.config["SECRET_KEY"] = "dev-only-insecure-key-change-me"
        else:
            raise RuntimeError(
                "SECRET_KEY не задан. Установите переменную окружения SECRET_KEY перед запуском."
            )
    app.config["WEAK_SECRET"] = is_weak_secret(app.config["SECRET_KEY"])

    # Приложение стоит за прокси Render/Nginx: без ProxyFix request.remote_addr —
    # адрес прокси (лимиты, просмотры и журнал админа были бы бессмысленны), а
    # request.is_secure — всегда False (HSTS и https-ссылки не работали бы).
    proxies = app.config.get("TRUSTED_PROXIES", 0)
    if proxies:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxies, x_proto=proxies, x_host=0, x_prefix=0)

    app.config["ADMIN_PREFIX"] = admin_prefix(app.config["SECRET_KEY"], app.config.get("ADMIN_PATH", ""))
    app.session_interface = SplitSessionInterface()

    db.init_app(app)

    from flask_migrate import Migrate
    Migrate(app, db)

    from admin import admin_bp
    from analytics import analytics_bp
    from auth import auth_bp
    from cars import cars_bp
    from chat import chat_bp
    from owners import owner_bp
    from payments import payments_bp
    from routes import main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(cars_bp)
    app.register_blueprint(owner_bp)
    app.register_blueprint(payments_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(analytics_bp)
    prefix = app.config["ADMIN_PREFIX"]
    app.register_blueprint(admin_bp, url_prefix=prefix)
    # Вход/выход админа живут на секретном адресе. Имена endpoint сохранены (auth.admin_login),
    # поэтому url_for(...) в коде и шаблонах не изменился.
    import auth as auth_module
    app.add_url_rule(f"{prefix}/login", endpoint="auth.admin_login", view_func=auth_module.admin_login,
                     methods=["GET", "POST"])
    app.add_url_rule(f"{prefix}/logout", endpoint="auth.admin_logout", view_func=auth_module.admin_logout,
                     methods=["POST"])

    app.config["ASSET_VERSION"] = _asset_version()

    _register_hooks(app)
    _register_error_handlers(app)
    _register_template_globals(app)
    _register_cli(app)
    _configure_logging(app)

    if app.config.get("AUTO_BOOTSTRAP", True):
        from bootstrap import ensure_schema
        ensure_schema(app)
        app.config["_BOOTSTRAPPED"] = True

    if not app.debug:
        app.logger.warning("Адрес админки: %s%s/login (покажите его только тем, кому доверяете)",
                           app.config["SITE_URL"], prefix)
        with app.app_context():
            for warning in config_warnings(app):
                if "SECRET_KEY" in warning:
                    app.logger.critical(warning)
                else:
                    app.logger.warning(warning)

    return app


_last_sweep = {"at": 0.0}


def _register_hooks(app: Flask) -> None:
    @app.before_request
    def restrict_admin_area():
        """Если задан список разрешённых IP, остальным админка отвечает обычной 404 —
        словно такой страницы нет."""
        allowed = app.config.get("ADMIN_ALLOWED_IPS")
        prefix = app.config["ADMIN_PREFIX"]
        if allowed and (request.path == prefix or request.path.startswith(prefix + "/")):
            if (request.remote_addr or "") not in allowed:
                abort(404)

    @app.before_request
    def sweep_expired():
        """Раз в минуту закрывает истёкшие подписки и приостанавливает лишние объявления."""
        import time
        if request.endpoint == "static" or time.time() - _last_sweep["at"] < 60:
            return
        _last_sweep["at"] = time.time()
        try:
            from services import sweep_expired_subscriptions
            sweep_expired_subscriptions()
        except Exception:  # проверка не должна ронять страницу пользователя
            db.session.rollback()
            app.logger.exception("Не удалось проверить истёкшие подписки")

    @app.before_request
    def load_current_user():
        g.current_user = None
        user_id = session.get("user_id")
        if not user_id:
            return
        user = db.session.get(User, user_id)
        token = session.get("uv", "")
        if user and not user.is_blocked and hmac.compare_digest(str(token), user.session_token()):
            g.current_user = user
        else:
            session.pop("user_id", None)
            session.pop("uv", None)

    @app.before_request
    def load_current_admin():
        g.current_admin = None
        admin_id = session.get("admin_id")
        if not admin_id:
            return
        last_activity = session.get("admin_last_activity")
        timeout = timedelta(seconds=app.config["ADMIN_SESSION_LIFETIME"])
        expired = False
        if last_activity:
            try:
                expired = datetime.utcnow() - datetime.fromisoformat(last_activity) > timeout
            except ValueError:
                expired = True
        admin = None if expired else db.session.get(AdminUser, admin_id)
        token = session.get("av", "")
        if admin and admin.is_active and hmac.compare_digest(str(token), admin.session_token()):
            g.current_admin = admin
            session["admin_last_activity"] = datetime.utcnow().isoformat()
        else:
            for key in ("admin_id", "admin_last_activity", "av"):
                session.pop(key, None)

    @app.before_request
    def check_csrf():
        return csrf_protect()

    @app.after_request
    def set_security_headers(response):
        return apply_security_headers(response)

    @app.after_request
    def record_visit(response):
        from analytics import track_visit
        return track_visit(response)


def _register_error_handlers(app: Flask) -> None:
    def respond(template: str, status: int, message: str, **extra):
        if wants_json():
            return jsonify(error=message), status
        return render_template(template, message=message, **extra), status

    @app.errorhandler(400)
    def bad_request(err):
        message = getattr(err, "description", None) or "Некорректный запрос."
        return respond("errors/400.html", 400, message)

    @app.errorhandler(403)
    def forbidden(err):
        return respond("errors/403.html", 403, "Нет доступа.")

    @app.errorhandler(404)
    def not_found(err):
        return respond("errors/404.html", 404, "Страница не найдена.")

    @app.errorhandler(405)
    def method_not_allowed(err):
        return respond("errors/404.html", 405, "Метод не поддерживается.")

    @app.errorhandler(413)
    def too_large(err):
        return respond("errors/400.html", 413, "Файл слишком большой (максимум 12 МБ).")

    @app.errorhandler(429)
    def too_many_requests(err):
        return respond("errors/429.html", 429, "Слишком много попыток. Подождите немного.")

    @app.errorhandler(500)
    def server_error(err):
        # Пользователь не видит traceback/SECRET_KEY/SQL — подробности только в логах.
        db.session.rollback()
        app.logger.exception("Внутренняя ошибка сервера")
        return respond("errors/500.html", 500, "Внутренняя ошибка. Мы уже в курсе — попробуйте позже.")


def _register_template_globals(app: Flask) -> None:
    from services import build_car_slug

    @app.template_filter("phone_fmt")
    def phone_fmt(value):
        return format_phone(value)

    @app.template_filter("ru_date")
    def ru_date(value):
        return value.strftime("%d.%m.%Y") if value else "—"

    @app.template_filter("ru_datetime")
    def ru_datetime(value):
        return value.strftime("%d.%m.%Y %H:%M") if value else "—"

    @app.context_processor
    def inject_globals():
        def car_detail_url(car):
            return url_for("cars.car_detail", slug=build_car_slug(car))

        def static_url(filename):
            return url_for("static", filename=filename, v=app.config["ASSET_VERSION"])

        user = g.get("current_user")
        fav_ids = set()
        if user is not None:
            try:
                rows = (
                    db.session.query(Car.public_id)
                    .join(Favorite, Favorite.car_id == Car.id)
                    .filter(Favorite.user_id == user.id)
                    .all()
                )
                fav_ids = {public_id for (public_id,) in rows}
            except Exception:  # страница ошибки не должна падать из-за избранного
                db.session.rollback()

        unread = 0
        if user is not None:
            try:
                unread = Notification.query.filter_by(user_id=user.id, is_read=False).count()
            except Exception:
                db.session.rollback()

        from services import get_settings
        saved = get_settings()
        cfg = app.config

        def contact(key, env_key):
            return (saved.get(key) or cfg.get(env_key) or "").strip()

        return dict(
            csrf_token=get_csrf_token,
            format_price=format_price,
            plans=PLANS,
            current_user=user,
            car_detail_url=car_detail_url,
            static_url=static_url,
            now_ts=datetime.utcnow().timestamp(),
            current_year=datetime.utcnow().year,
            site_url=cfg["SITE_URL"],
            support_phone=contact("support_phone", "SUPPORT_PHONE"),
            support_telegram=contact("support_telegram", "SUPPORT_TELEGRAM"),
            support_whatsapp=contact("support_whatsapp", "SUPPORT_WHATSAPP"),
            founder_name=saved.get("founder_name", "").strip(),
            ru_plural=ru_plural,
            status_labels=STATUS_LABELS,
            order_status_labels=ORDER_STATUS_LABELS,
            fav_ids=fav_ids,
            unread_notifications=unread,
            has_logo_image=os.path.exists(os.path.join(app.static_folder, "img", "logo.png")),
        )


def _register_cli(app: Flask) -> None:
    @app.cli.command("seed-db")
    def seed_db():
        """Создаёт недостающие таблицы/колонки и города.
        Использование: flask --app app.py seed-db
        """
        from bootstrap import ensure_schema

        ensure_schema(app)
        print("База данных готова, города загружены.")

    @app.cli.command("create-admin")
    def create_admin():
        """Создаёт администратора интерактивно.
        Использование: flask --app app.py create-admin
        """
        import getpass
        from security import hash_password

        username = input("Логин администратора: ").strip()
        if not username:
            print("Логин не может быть пустым.")
            return
        if AdminUser.query.filter_by(username=username).first():
            print("Администратор с таким логином уже есть. Для смены пароля: flask --app app.py reset-admin-password")
            return
        password = getpass.getpass("Пароль (минимум 12 символов): ")
        if len(password) < 12:
            print("Пароль администратора должен быть не короче 12 символов.")
            return
        admin = AdminUser(username=username, password_hash=hash_password(password), is_super=True)
        db.session.add(admin)
        db.session.commit()
        print(f"Администратор {username} создан.")

    @app.cli.command("reset-admin-password")
    def reset_admin_password():
        """Меняет пароль администратора (все его сессии при этом завершаются).
        Использование: flask --app app.py reset-admin-password
        """
        import getpass
        from security import hash_password

        username = input("Логин администратора: ").strip()
        admin = AdminUser.query.filter_by(username=username).first()
        if not admin:
            print("Такого администратора нет.")
            return
        password = getpass.getpass("Новый пароль (минимум 12 символов): ")
        if len(password) < 12:
            print("Пароль должен быть не короче 12 символов.")
            return
        admin.password_hash = hash_password(password)
        db.session.commit()
        print("Пароль обновлён.")

    @app.cli.command("enable-admin-2fa")
    def enable_admin_2fa():
        """Включает двухфакторный вход для администратора (приложение-аутентификатор).
        Использование: flask --app app.py enable-admin-2fa
        """
        import totp

        username = input("Логин администратора: ").strip()
        admin = AdminUser.query.filter_by(username=username).first()
        if not admin:
            print("Такого администратора нет.")
            return
        secret = totp.generate_secret()
        print("\nОткройте Google Authenticator / Authy → «Добавить» → «Ввести ключ настройки» и введите:")
        print(f"   Название: KAVKAZ-CAR   Ключ: {secret}   Тип: по времени")
        print(f"\n(или ссылка для приложений, понимающих otpauth: {totp.otpauth_uri(secret, username)})")
        code = input("\nВведите 6-значный код из приложения для подтверждения: ").strip()
        if not totp.verify(secret, code):
            print("Код не подошёл — 2FA НЕ включена. Проверьте время на телефоне и повторите.")
            return
        admin.totp_secret = secret
        db.session.commit()
        print("Готово: теперь при входе в админку потребуется код из приложения.")

    @app.cli.command("disable-admin-2fa")
    def disable_admin_2fa():
        """Выключает 2FA у администратора (если потерян телефон).
        Использование: flask --app app.py disable-admin-2fa
        """
        username = input("Логин администратора: ").strip()
        admin = AdminUser.query.filter_by(username=username).first()
        if not admin:
            print("Такого администратора нет.")
            return
        admin.totp_secret = None
        db.session.commit()
        print("2FA выключена.")


def _configure_logging(app: Flask) -> None:
    # У Flask уже есть обработчик логов; свой не добавляем (иначе каждая запись дублируется).
    if not app.debug:
        app.logger.setLevel(logging.INFO)


app = create_app()

if __name__ == "__main__":
    app.run(debug=app.config.get("DEBUG", False))
