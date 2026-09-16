"""
Аутентификация KAVKAZ-CAR.

Обычные пользователи (клиенты/владельцы — это один и тот же аккаунт,
роль не хранится отдельно) и администраторы используют РАЗНЫЕ таблицы,
разные сессионные ключи и разные формы входа (раздел 41 ТЗ: админка не
должна полагаться только на «if user.is_admin»).
"""
import re
from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from models import AdminUser, User, db
from security import (
    admin_required,
    hash_password,
    login_required,
    rate_limit,
    verify_password,
)

auth_bp = Blueprint("auth", __name__)

PHONE_RE = re.compile(r"^\+7\d{10}$")


def _normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith(("7", "8")):
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    else:
        return None
    phone = "+" + digits
    return phone if PHONE_RE.match(phone) else None


@auth_bp.route("/register", methods=["GET", "POST"])
@rate_limit("register")
def register():
    if request.method == "POST":
        phone = _normalize_phone(request.form.get("phone", ""))
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")
        full_name = request.form.get("full_name", "").strip()[:120]
        age = request.form.get("age", type=int)

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
        elif password != password_confirm:
            error = "Пароли не совпадают."
        elif User.query.filter_by(phone=phone).first():
            error = "Пользователь с таким номером уже зарегистрирован."

        if error:
            flash(error, "error")
            return render_template(
                "auth/register.html", phone=request.form.get("phone", ""),
                full_name=full_name, age=request.form.get("age", ""),
            ), 400

        user = User(phone=phone, password_hash=hash_password(password), full_name=full_name, age=age)
        db.session.add(user)
        db.session.commit()

        session.clear()
        session.permanent = True
        session["user_id"] = user.id
        flash("Добро пожаловать! Теперь вы можете разместить автомобиль.", "success")
        return redirect(request.args.get("next") or url_for("owner.dashboard"))

    return render_template("auth/register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
@rate_limit("login")
def login():
    if request.method == "POST":
        phone = _normalize_phone(request.form.get("phone", ""))
        password = request.form.get("password", "")
        user = User.query.filter_by(phone=phone).first() if phone else None

        if not user or not verify_password(user.password_hash, password):
            flash("Неверный номер телефона или пароль.", "error")
            return render_template("auth/login.html", phone=request.form.get("phone", "")), 400

        if user.is_blocked:
            flash("Этот аккаунт заблокирован. Свяжитесь с поддержкой.", "error")
            return render_template("auth/login.html"), 403

        session.clear()
        session.permanent = True
        session["user_id"] = user.id
        user.last_login_at = datetime.utcnow()
        db.session.commit()

        next_url = request.args.get("next")
        return redirect(next_url or url_for("owner.dashboard"))

    return render_template("auth/login.html")


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
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        admin = AdminUser.query.filter_by(username=username, is_active=True).first()

        if not admin or not verify_password(admin.password_hash, password):
            flash("Неверные учётные данные.", "error")
            return render_template("admin/login.html"), 400

        session.clear()
        session["admin_id"] = admin.id
        session["admin_last_activity"] = datetime.utcnow().isoformat()
        admin.last_login_at = datetime.utcnow()
        db.session.commit()
        return redirect(url_for("admin_panel.dashboard"))

    return render_template("admin/login.html")


@auth_bp.route("/admin/logout", methods=["POST"])
@admin_required
def admin_logout():
    session.clear()
    return redirect(url_for("auth.admin_login"))
