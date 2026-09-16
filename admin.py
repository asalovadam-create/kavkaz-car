"""
Админка KAVKAZ-CAR (раздел 40 ТЗ).

Изолирована от обычного приложения: отдельная модель AdminUser, отдельная
сессия (admin_id), собственный rate limit на вход, и КАЖДОЕ значимое
действие пишется в admin_logs (раздел 92 ТЗ) — кто, что, над чем, когда.
"""
from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from config import PLAN_ORDER
from models import AdminLog, Car, PromoCode, Report, Subscription, User, db
from security import admin_required
from services import generate_promo_code, get_image_storage

admin_bp = Blueprint("admin_panel", __name__, url_prefix="/admin")


def _log_action(action: str, target_type: str | None = None, target_id: int | None = None):
    db.session.add(AdminLog(
        admin_id=session["admin_id"], action=action, target_type=target_type,
        target_id=target_id, ip=request.remote_addr,
    ))


@admin_bp.route("/")
@admin_required
def dashboard():
    stats = dict(
        users_count=User.query.count(),
        cars_count=Car.query.count(),
        published_count=Car.query.filter_by(status="published").count(),
        new_reports=Report.query.filter_by(status="new").count(),
    )
    recent_logs = AdminLog.query.order_by(AdminLog.created_at.desc()).limit(20).all()
    return render_template("admin/dashboard.html", stats=stats, recent_logs=recent_logs)


@admin_bp.route("/users")
@admin_required
def users():
    q = request.args.get("q", "").strip()
    query = User.query
    if q:
        query = query.filter(User.phone.ilike(f"%{q}%"))
    all_users = query.order_by(User.created_at.desc()).limit(200).all()
    return render_template("admin/users.html", users=all_users, q=q)


@admin_bp.route("/users/<int:user_id>/toggle-block", methods=["POST"])
@admin_required
def toggle_block_user(user_id):
    user = User.query.get_or_404(user_id)
    user.is_blocked = not user.is_blocked
    _log_action("toggle_block_user", "user", user.id)
    db.session.commit()
    flash("Статус пользователя обновлён.", "success")
    return redirect(url_for("admin_panel.users"))


@admin_bp.route("/cars")
@admin_required
def cars():
    status = request.args.get("status")
    query = Car.query
    if status:
        query = query.filter_by(status=status)
    all_cars = query.order_by(Car.created_at.desc()).limit(200).all()
    return render_template("admin/cars.html", cars=all_cars, status=status)


@admin_bp.route("/cars/<int:car_id>/status", methods=["POST"])
@admin_required
def set_car_status(car_id):
    car = Car.query.get_or_404(car_id)
    new_status = request.form.get("status")
    from config import CAR_STATUSES
    if new_status not in CAR_STATUSES:
        flash("Некорректный статус.", "error")
        return redirect(url_for("admin_panel.cars"))

    car.status = new_status
    _log_action(f"set_car_status:{new_status}", "car", car.id)
    db.session.commit()
    flash("Статус автомобиля обновлён.", "success")
    return redirect(url_for("admin_panel.cars"))


def _delete_car_completely(car: Car) -> str:
    """Полное удаление объявления вместе с фотографиями в хранилище.
    Возвращает название автомобиля для логов/сообщений (после удаления
    объекта из сессии обращаться к car.title() уже нельзя)."""
    title = car.title()
    storage = get_image_storage()
    for photo in car.photos:
        if photo.storage_public_id:
            storage.delete(photo.storage_public_id)
    db.session.delete(car)
    return title


@admin_bp.route("/cars/<int:car_id>/delete", methods=["POST"])
@admin_required
def delete_car(car_id):
    car = Car.query.get_or_404(car_id)
    title = _delete_car_completely(car)
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

    if action == "delete" and report.car:
        # Удаление автомобиля каскадно удалит и саму жалобу на уровне БД —
        # поэтому лог пишем ДО удаления и больше не трогаем объект report.
        title = _delete_car_completely(report.car)
        _log_action(f"delete_car_via_report:{title}", "car", report.car_id)
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
    report.resolved_by_admin_id = session["admin_id"]
    _log_action(f"resolve_report:{action}", "report", report.id)
    db.session.commit()
    flash("Жалоба обработана.", "success")
    return redirect(url_for("admin_panel.reports"))


@admin_bp.route("/subscriptions/<int:user_id>/set-plan", methods=["POST"])
@admin_required
def set_plan(user_id):
    user = User.query.get_or_404(user_id)
    plan = request.form.get("plan")
    days = request.form.get("days", 30, type=int)

    if plan not in PLAN_ORDER:
        flash("Некорректный тариф.", "error")
        return redirect(url_for("admin_panel.users"))

    current = user.active_subscription()
    if current:
        current.status = "cancelled"

    from datetime import timedelta
    expires_at = None if plan == "free" else datetime.utcnow() + timedelta(days=days)
    db.session.add(Subscription(
        owner_id=user.id, plan=plan, status="active", expires_at=expires_at,
        payment_provider="manual_admin", payment_status="paid",
        activated_by_admin_id=session["admin_id"],
    ))
    _log_action(f"set_plan:{plan}", "user", user.id)
    db.session.commit()
    flash(f"Тариф {plan.upper()} активирован для пользователя.", "success")
    return redirect(url_for("admin_panel.users"))


@admin_bp.route("/promo-codes", methods=["GET", "POST"])
@admin_required
def promo_codes():
    if request.method == "POST":
        plan = request.form.get("plan")
        duration_days = request.form.get("duration_days", 30, type=int)
        if plan not in PLAN_ORDER or plan == "free":
            flash("Выберите PRO или BUSINESS.", "error")
        else:
            code = generate_promo_code(plan)
            from datetime import timedelta
            db.session.add(PromoCode(
                code=code, plan=plan, duration_days=duration_days,
                expires_at=datetime.utcnow() + timedelta(days=90),
                created_by_admin_id=session["admin_id"],
            ))
            _log_action(f"create_promo:{code}", "promo_code")
            db.session.commit()
            flash(f"Промокод создан: {code}", "success")
        return redirect(url_for("admin_panel.promo_codes"))

    codes = PromoCode.query.order_by(PromoCode.created_at.desc()).limit(200).all()
    return render_template("admin/promo_codes.html", codes=codes, plans=PLAN_ORDER)


@admin_bp.route("/logs")
@admin_required
def logs():
    all_logs = AdminLog.query.order_by(AdminLog.created_at.desc()).limit(300).all()
    return render_template("admin/logs.html", logs=all_logs)
