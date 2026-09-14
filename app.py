"""
KAVKAZ-CAR — точка входа приложения.

Модульный монолит (раздел 120 ТЗ): одно Flask-приложение, но логически
разделённое на auth / cars / owners / admin / security / services.
"""
import logging
from datetime import datetime, timedelta

from flask import Flask, g, render_template, request, session

from config import Config
from models import AdminUser, User, db
from security import apply_security_headers, csrf_protect, get_csrf_token
from services import format_price


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

    db.init_app(app)

    from flask_migrate import Migrate
    Migrate(app, db)

    from auth import auth_bp
    from cars import cars_bp
    from owners import owner_bp
    from admin import admin_bp
    from routes import main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(cars_bp)
    app.register_blueprint(owner_bp)
    app.register_blueprint(admin_bp)

    _register_hooks(app)
    _register_error_handlers(app)
    _register_template_globals(app)
    _register_cli(app)
    _configure_logging(app)

    return app


def _register_hooks(app: Flask) -> None:
    @app.before_request
    def load_current_user():
        g.current_user = None
        user_id = session.get("user_id")
        if user_id:
            user = db.session.get(User, user_id)
            if user and not user.is_blocked:
                g.current_user = user
            else:
                session.pop("user_id", None)

    @app.before_request
    def enforce_admin_session_timeout():
        if not session.get("admin_id"):
            return
        last_activity = session.get("admin_last_activity")
        timeout = timedelta(seconds=app.config["ADMIN_SESSION_LIFETIME"])
        if last_activity:
            elapsed = datetime.utcnow() - datetime.fromisoformat(last_activity)
            if elapsed > timeout:
                session.clear()
                return
        session["admin_last_activity"] = datetime.utcnow().isoformat()

    @app.before_request
    def check_csrf():
        csrf_protect()

    @app.after_request
    def set_security_headers(response):
        return apply_security_headers(response)


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(400)
    def bad_request(err):
        return render_template("errors/400.html", message=getattr(err, "description", None)), 400

    @app.errorhandler(403)
    def forbidden(err):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(err):
        return render_template("errors/404.html"), 404

    @app.errorhandler(429)
    def too_many_requests(err):
        return render_template("errors/429.html"), 429

    @app.errorhandler(500)
    def server_error(err):
        # Пользователь не видит traceback/SECRET_KEY/SQL — подробности только в логах.
        app.logger.exception("Внутренняя ошибка сервера")
        return render_template("errors/500.html"), 500


def _register_template_globals(app: Flask) -> None:
    from config import PLANS
    from services import build_car_slug

    @app.context_processor
    def inject_globals():
        def car_detail_url(car):
            from flask import url_for
            return url_for("cars.car_detail", slug=build_car_slug(car))

        return dict(
            csrf_token=get_csrf_token,
            format_price=format_price,
            plans=PLANS,
            current_user=g.get("current_user"),
            car_detail_url=car_detail_url,
            now_ts=datetime.utcnow().timestamp(),
        )


def _register_cli(app: Flask) -> None:
    @app.cli.command("seed-db")
    def seed_db():
        """Создаёт таблицы и заполняет базовые справочники (города).
        Использование: flask --app app.py seed-db
        """
        from config import LAUNCH_CITIES

        db.create_all()
        for city_data in LAUNCH_CITIES:
            if not City_exists(city_data["slug"]):
                from models import City
                db.session.add(City(**city_data))
        db.session.commit()
        print("База данных готова, города загружены.")

    def City_exists(slug: str) -> bool:
        from models import City
        return City.query.filter_by(slug=slug).first() is not None

    @app.cli.command("create-admin")
    def create_admin():
        """Создаёт первого администратора интерактивно.
        Использование: flask --app app.py create-admin
        """
        import getpass
        from security import hash_password

        username = input("Логин администратора: ").strip()
        password = getpass.getpass("Пароль: ")
        if len(password) < 12:
            print("Пароль администратора должен быть не короче 12 символов.")
            return

        admin = AdminUser(username=username, password_hash=hash_password(password), is_super=True)
        db.session.add(admin)
        db.session.commit()
        print(f"Администратор {username} создан.")


def _configure_logging(app: Flask) -> None:
    if not app.debug:
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        app.logger.addHandler(handler)
        app.logger.setLevel(logging.INFO)


app = create_app()

if __name__ == "__main__":
    app.run(debug=app.config.get("DEBUG", False))
