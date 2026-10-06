"""
Админка KAVKAZ-CAR (раздел 40 ТЗ).

Изолирована от обычного приложения: отдельная модель AdminUser, отдельная
сессия (admin_id), собственный rate limit на вход, и КАЖДОЕ значимое
действие пишется в admin_logs (раздел 92 ТЗ) — кто, что, над чем, когда.
"""
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func

from config import CAR_STATUSES, PLAN_ORDER, PLANS
from models import (
    AdminLog, Car, OwnerProfile, PaymentOrder, PromoCode, Report, Subscription, User, db,
)
from payments import mark_order_paid
from security import admin_required, hash_password
from services import activate_plan, generate_promo_code, purge_car
from validators import clean_text, to_int

admin_bp = Blueprint("admin_panel", __name__, url_prefix="/admin")


def _log_action(action: str, target_type: str | None = None, target_id: int | None = None):
    db.session.add(AdminLog(
        admin_id=g.current_admin.id, action=action[:80], target_type=target_type,
        target_id=target_id, ip=request.remote_addr,
    ))


@admin_bp.route("/")
@admin_required
def dashboard():
    from app import config_warnings
    week_ago = datetime.utcnow() - timedelta(days=7)
    now = datetime.utcnow()
    revenue = db.session.query(func.coalesce(func.sum(PaymentOrder.amount), 0)).filter(
        PaymentOrder.status == "paid", PaymentOrder.provider != "promo_code").scalar()
    stats = dict(
        users_count=User.query.count(),
        new_users=User.query.filter(User.created_at >= week_ago).count(),
        cars_count=Car.query.count(),
        published_count=Car.query.filter_by(status="published").count(),
        new_reports=Report.query.filter_by(status="new").count(),
        open_orders=PaymentOrder.query.filter(PaymentOrder.status.in_(("awaiting_manual", "awaiting_payment"))).count(),
        paid_subs=Subscription.query.filter(
            Subscription.status == "active", Subscription.plan != "free",
            db.or_(Subscription.expires_at.is_(None), Subscription.expires_at > now),
        ).count(),
        revenue=revenue,
    )
    recent_logs = AdminLog.query.order_by(AdminLog.created_at.desc()).limit(20).all()
    return render_template(
        "admin/dashboard.html", stats=stats, recent_logs=recent_logs, warnings=config_warnings(current_app),
    )


# ---------------------------------------------------------------------------
# Пользователи
# ---------------------------------------------------------------------------

@admin_bp.route("/users")
@admin_required
def users():
    q = clean_text(request.args.get("q"), 60)
    query = User.query
    if q:
        like = f"%{q.replace('%', '').replace('_', '')}%"
        query = query.filter(db.or_(User.phone.ilike(like), User.full_name.ilike(like)))
    all_users = query.order_by(User.created_at.desc()).limit(200).all()
    return render_template("admin/users.html", users=all_users, q=q, plan_order=PLAN_ORDER)


@admin_bp.route("/users/<int:user_id>/toggle-block", methods=["POST"])
@admin_required
def toggle_block_user(user_id):
    user = User.query.get_or_404(user_id)
    user.is_blocked = not user.is_blocked
    _log_action("block_user" if user.is_blocked else "unblock_user", "user", user.id)
    db.session.commit()
    flash("Статус пользователя обновлён.", "success")
    return redirect(url_for("admin_panel.users", q=request.form.get("q", "")))


@admin_bp.route("/users/<int:user_id>/toggle-phone-verified", methods=["POST"])
@admin_required
def toggle_phone_verified(user_id):
    user = User.query.get_or_404(user_id)
    profile = user.owner_profile
    if profile is None:
        profile = OwnerProfile(user_id=user.id, display_name=user.full_name or user.phone)
        db.session.add(profile)
    profile.phone_verified = not profile.phone_verified
    _log_action("phone_verified" if profile.phone_verified else "phone_unverified", "user", user.id)
    db.session.commit()
    flash("Отметка «телефон подтверждён» обновлена.", "success")
    return redirect(url_for("admin_panel.users", q=request.form.get("q", "")))


@admin_bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def reset_user_password(user_id):
    """Сброс пароля по просьбе пользователя (SMS-восстановления на сайте нет).
    Временный пароль показывается один раз; все старые сессии пользователя завершаются."""
    user = User.query.get_or_404(user_id)
    temp = secrets.token_urlsafe(9)
    user.password_hash = hash_password(temp)
    _log_action("reset_password", "user", user.id)
    db.session.commit()
    flash(f"Временный пароль для {user.phone}: {temp} — передайте владельцу, пусть сразу сменит его в профиле.", "success")
    return redirect(url_for("admin_panel.users", q=request.form.get("q", "")))


@admin_bp.route("/subscriptions/<int:user_id>/set-plan", methods=["POST"])
@admin_required
def set_plan(user_id):
    user = User.query.get_or_404(user_id)
    plan = request.form.get("plan")
    days = to_int(request.form.get("days"), minimum=1, maximum=3650)

    if plan not in PLAN_ORDER or days is None:
        flash("Укажите тариф и срок от 1 до 3650 дней.", "error")
        return redirect(url_for("admin_panel.users"))

    if plan == "free":
        current = user.active_subscription()
        if current:
            current.status = "cancelled"
        user.invalidate_plan_cache()
    else:
        activate_plan(user, plan, days, provider="manual_admin", admin_id=g.current_admin.id)
    _log_action(f"set_plan:{plan}:{days}d", "user", user.id)
    db.session.commit()
    flash(f"Тариф {plan.upper()} установлен.", "success")
    return redirect(url_for("admin_panel.users", q=request.form.get("q", "")))


# ---------------------------------------------------------------------------
# Автомобили и жалобы
# ---------------------------------------------------------------------------

@admin_bp.route("/cars")
@admin_required
def cars():
    status = request.args.get("status")
    query = Car.query
    if status in CAR_STATUSES:
        query = query.filter_by(status=status)
    all_cars = query.order_by(Car.created_at.desc()).limit(200).all()
    return render_template("admin/cars.html", cars=all_cars, status=status, statuses=CAR_STATUSES)


@admin_bp.route("/cars/<int:car_id>/status", methods=["POST"])
@admin_required
def set_car_status(car_id):
    car = Car.query.get_or_404(car_id)
    new_status = request.form.get("status")
    if new_status not in CAR_STATUSES:
        flash("Некорректный статус.", "error")
        return redirect(url_for("admin_panel.cars"))

    car.status = new_status
    _log_action(f"set_car_status:{new_status}", "car", car.id)
    db.session.commit()
    flash("Статус автомобиля обновлён.", "success")
    return redirect(url_for("admin_panel.cars", status=request.form.get("filter") or None))


@admin_bp.route("/cars/<int:car_id>/delete", methods=["POST"])
@admin_required
def delete_car(car_id):
    car = Car.query.get_or_404(car_id)
    title = purge_car(car)
    _log_action(f"delete_car:{title}", "car", car_id)
    db.session.commit()
    flash("Объявление удалено безвозвратно.", "success")
    return redirect(url_for("admin_panel.cars"))


@admin_bp.route("/reports")
@admin_required
def reports():
    status = request.args.get("status", "new")
    query = Report.query
    if status != "all":
        query = query.filter_by(status=status)
    all_reports = query.order_by(Report.created_at.desc()).limit(200).all()
    return render_template("admin/reports.html", reports=all_reports, status=status)


@admin_bp.route("/reports/<int:report_id>/resolve", methods=["POST"])
@admin_required
def resolve_report(report_id):
    report = Report.query.get_or_404(report_id)
    action = request.form.get("action")  # hide|block|restore|dismiss|delete
    if action not in ("hide", "block", "restore", "dismiss", "delete"):
        flash("Неизвестное действие.", "error")
        return redirect(url_for("admin_panel.reports"))

    if action == "delete" and report.car:
        car_id = report.car_id
        title = purge_car(report.car)  # вместе с самой жалобой
        _log_action(f"delete_car_via_report:{title}", "car", car_id)
        db.session.commit()
        flash("Объявление удалено, жалоба закрыта.", "success")
        return redirect(url_for("admin_panel.reports"))

    if action == "hide" and report.car:
        report.car.status = "paused"
    elif action == "block" and report.car:
        report.car.status = "blocked"
    elif action == "restore" and report.car:
        report.car.status = "published"

    report.status = "actioned" if action in ("hide", "block", "restore") else "dismissed"
    report.resolved_at = datetime.utcnow()
    report.resolved_by_admin_id = g.current_admin.id
    _log_action(f"resolve_report:{action}", "report", report.id)
    db.session.commit()
    flash("Жалоба обработана.", "success")
    return redirect(url_for("admin_panel.reports"))


# ---------------------------------------------------------------------------
# Заявки на оплату тарифов
# ---------------------------------------------------------------------------

@admin_bp.route("/orders")
@admin_required
def orders():
    status = request.args.get("status", "open")
    query = PaymentOrder.query
    if status == "open":
        query = query.filter(PaymentOrder.status.in_(("created", "awaiting_manual", "awaiting_payment")))
    elif status != "all":
        query = query.filter_by(status=status)
    rows = query.order_by(PaymentOrder.created_at.desc()).limit(200).all()
    return render_template("admin/orders.html", orders=rows, status=status, plans=PLANS)


@admin_bp.route("/orders/<int:order_id>/confirm", methods=["POST"])
@admin_required
def confirm_order(order_id):
    """Оплата получена вне сайта (перевод, наличные) — включаем тариф. Безопасно
    нажать дважды: повторное нажатие ничего не изменит."""
    order = PaymentOrder.query.with_for_update().filter_by(id=order_id).first_or_404()
    if order.status == "cancelled":
        flash("Заказ отменён — сначала создайте новый.", "error")
    elif mark_order_paid(order, provider="manual_admin", admin_id=g.current_admin.id):
        _log_action(f"confirm_order:{order.public_id}", "order", order.id)
        db.session.commit()
        flash(f"Тариф {order.plan.upper()} включён для {order.user.phone}.", "success")
    else:
        flash("Этот заказ уже оплачен.", "success")
    return redirect(url_for("admin_panel.orders", status=request.form.get("filter") or "open"))


@admin_bp.route("/orders/<int:order_id>/cancel", methods=["POST"])
@admin_required
def cancel_order_admin(order_id):
    order = PaymentOrder.query.get_or_404(order_id)
    if order.is_open:
        order.status = "cancelled"
        _log_action(f"cancel_order:{order.public_id}", "order", order.id)
        db.session.commit()
        flash("Заказ отменён.", "success")
    return redirect(url_for("admin_panel.orders", status=request.form.get("filter") or "open"))


# ---------------------------------------------------------------------------
# Промокоды и журнал
# ---------------------------------------------------------------------------

@admin_bp.route("/promo-codes", methods=["GET", "POST"])
@admin_required
def promo_codes():
    if request.method == "POST":
        plan = request.form.get("plan")
        duration_days = to_int(request.form.get("duration_days"), minimum=1, maximum=3650)
        if plan not in PLAN_ORDER or plan == "free":
            flash("Выберите PRO или BUSINESS.", "error")
        elif duration_days is None:
            flash("Срок — от 1 до 3650 дней.", "error")
        else:
            code = generate_promo_code(plan)
            db.session.add(PromoCode(
                code=code, plan=plan, duration_days=duration_days,
                expires_at=datetime.utcnow() + timedelta(days=90),
                created_by_admin_id=g.current_admin.id,
            ))
            _log_action(f"create_promo:{code}", "promo_code")
            db.session.commit()
            flash(f"Промокод создан: {code}", "success")
        return redirect(url_for("admin_panel.promo_codes"))

    codes = PromoCode.query.order_by(PromoCode.created_at.desc()).limit(200).all()
    return render_template("admin/promo_codes.html", codes=codes, plans=PLAN_ORDER, now=datetime.utcnow())


@admin_bp.route("/logs")
@admin_required
def logs():
    all_logs = AdminLog.query.order_by(AdminLog.created_at.desc()).limit(300).all()
    return render_template("admin/logs.html", logs=all_logs)
