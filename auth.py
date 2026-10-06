"""
Аутентификация KAVKAZ-CAR.

Обычные пользователи (клиенты/владельцы — это один и тот же аккаунт,
роль не хранится отдельно) и администраторы используют РАЗНЫЕ таблицы,
разные сессионные ключи и разные формы входа (раздел 41 ТЗ: админка не
должна полагаться только на «if user.is_admin»).
"""
import secrets
from datetime import datetime

from flask import (
    Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for,
)

from config import is_placeholder_admin_secret
from models import AdminUser, User, db
from security import (
    MAX_PASSWORD_LENGTH, admin_required, burn_password_check, clear_failures, hash_password,
    is_throttled, login_required, rate_limit, record_failure, verify_password,
)
from validators import clean_text, normalize_ru_phone, safe_next_url

auth_bp = Blueprint("auth", __name__)


def _start_user_session(user: User) -> None:
    """Новая сессия после входа/регистрации: старая очищается (защита от session fixation),
    а токен привязывает сессию к паролю пользователя."""
    session.clear()
    session.permanent = True
    session["user_id"] = user.id
    session["uv"] = user.session_token()


def _home_for(user: User) -> str:
    return url_for("owner.dashboard") if user.is_owner else url_for("cars.catalog")


@auth_bp.route("/register", methods=["GET", "POST"])
@rate_limit("register")
def register():
    if g.current_user and request.method == "GET":
        return redirect(_home_for(g.current_user))

    next_url = safe_next_url(request.values.get("next"))

    if request.method == "POST":
        phone_raw = request.form.get("phone", "")
        phone = normalize_ru_phone(phone_raw)
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")
        full_name = clean_text(request.form.get("full_name"), 120)
        age = request.form.get("age", type=int)
        role = request.form.get("role")
        is_owner = role == "owner"  # по умолчанию, если ничего не пришло — считаем клиентом

        error = None
        if not full_name:
            error = "Введите имя."
        elif not phone:
            error = "Введите корректный номер телефона."
        elif age is None or age < 18:
            error = "Регистрация на KAVKAZ-CAR доступна только с 18 лет."
        elif age > 100:
            error = "Проверьте указанный возраст."
        elif len(password) < 8:
            error = "Пароль должен быть не короче 8 символов."
        elif len(password) > MAX_PASSWORD_LENGTH:
            error = f"Пароль слишком длинный (максимум {MAX_PASSWORD_LENGTH} символов)."
        elif password != password_confirm:
            error = "Пароли не совпадают."
        elif User.query.filter_by(phone=phone).first():
            error = "Пользователь с таким номером уже зарегистрирован. Попробуйте войти."

        if error:
            flash(error, "error")
            return render_template(
                "auth/register.html", phone=phone_raw, full_name=full_name,
                age=request.form.get("age", ""), role=role, next_url=next_url,
            ), 400

        user = User(
            phone=phone, password_hash=hash_password(password), full_name=full_name,
            age=age, is_owner=is_owner,
        )
        db.session.add(user)
        db.session.commit()
        _start_user_session(user)

        if is_owner:
            flash("Добро пожаловать! Разместим ваш первый автомобиль.", "success")
            return redirect(url_for("owner.wizard_start"))

        flash("Добро пожаловать на KAVKAZ-CAR!", "success")
        return redirect(next_url or url_for("cars.catalog"))

    return render_template("auth/register.html", role=request.args.get("role"), next_url=next_url)


@auth_bp.route("/login", methods=["GET", "POST"])
@rate_limit("login")
def login():
    next_url = safe_next_url(request.values.get("next"))
    if g.current_user and request.method == "GET":
        return redirect(next_url or _home_for(g.current_user))

    if request.method == "POST":
        phone_raw = request.form.get("phone", "")
        phone = normalize_ru_phone(phone_raw)
        password = request.form.get("password", "")

        # Лимит неудачных попыток на конкретный номер (перебор пароля с разных IP).
        if phone and is_throttled("login_phone", phone):
            flash("Слишком много неудачных попыток. Подождите 15 минут или напишите в поддержку.", "error")
            return render_template("auth/login.html", phone=phone_raw, next_url=next_url), 429

        user = User.query.filter_by(phone=phone).first() if phone else None
        if user is None:
            burn_password_check(password)  # одинаковое время ответа для существующих и несуществующих номеров

        if not user or not verify_password(user.password_hash, password):
            if phone:
                record_failure("login_phone", phone)
            flash("Неверный номер телефона или пароль.", "error")
            return render_template("auth/login.html", phone=phone_raw, next_url=next_url), 400

        if user.is_blocked:
            flash("Этот аккаунт заблокирован. Свяжитесь с поддержкой.", "error")
            return render_template("auth/login.html", next_url=next_url), 403

        clear_failures("login_phone", phone)
        _start_user_session(user)
        user.last_login_at = datetime.utcnow()
        db.session.commit()
        return redirect(next_url or _home_for(user))

    return render_template("auth/login.html", next_url=next_url)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("Вы вышли из аккаунта.", "success")
    return redirect(url_for("main.home"))


# ---------------------------------------------------------------------------
# Отдельный вход для администраторов
# ---------------------------------------------------------------------------

@auth_bp.route("/admin/login", methods=["GET", "POST"])
@rate_limit("admin_login")
def admin_login():
    admin_secret = current_app.config.get("ADMIN_SECRET", "")
    needs_secret = not is_placeholder_admin_secret(admin_secret)

    if request.method == "POST":
        if is_throttled("admin_login_fail"):
            flash("Слишком много неудачных попыток. Подождите 15 минут.", "error")
            return render_template("admin/login.html", needs_secret=needs_secret), 429

        username = clean_text(request.form.get("username"), 60)
        password = request.form.get("password", "")
        secret_ok = True
        if needs_secret:
            submitted = request.form.get("secret", "")
            secret_ok = secrets.compare_digest(submitted.encode(), admin_secret.encode())

        admin = AdminUser.query.filter_by(username=username, is_active=True).first()
        if admin is None:
            burn_password_check(password)

        if not (admin and secret_ok and verify_password(admin.password_hash, password)):
            record_failure("admin_login_fail")
            flash("Неверные учётные данные.", "error")
            return render_template("admin/login.html", needs_secret=needs_secret), 400

        clear_failures("admin_login_fail")
        session.clear()
        session["admin_id"] = admin.id
        session["av"] = admin.session_token()
        session["admin_last_activity"] = datetime.utcnow().isoformat()
        admin.last_login_at = datetime.utcnow()
        db.session.commit()
        return redirect(url_for("admin_panel.dashboard"))

    return render_template("admin/login.html", needs_secret=needs_secret)


@auth_bp.route("/admin/logout", methods=["POST"])
@admin_required
def admin_logout():
    session.clear()
    return redirect(url_for("auth.admin_login"))
