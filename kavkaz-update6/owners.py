"""
Личный кабинет владельца KAVKAZ-CAR.

Раздел 30 ТЗ: интерфейс должен быть настолько простым, чтобы человек,
который плохо разбирается в сайтах, разместил автомобиль без посторонней
помощи. Поэтому добавление машины — пошаговый мастер (раздел 31 ТЗ),
а не одна большая форма.
"""
from datetime import datetime, timedelta

from flask import (
    Blueprint, current_app, flash, g, jsonify, redirect, render_template,
    request, session, url_for,
)

from config import BODY_TYPES, CAR_BRANDS, LISTING_STALE_AFTER_DAYS
from models import Car, CarPhoto, City, ContactClick, OwnerProfile, PromoCode, User, View, db
from security import InvalidImageError, login_required, owner_mode_required, rate_limit, validate_and_load_image
from services import (
    boosts_remaining, can_add_car, can_add_photo, optimize_image,
    plan_limits, redeem_promo_code, get_image_storage,
)

owner_bp = Blueprint("owner", __name__, url_prefix="/owner")

WIZARD_SESSION_KEY = "car_draft"
WIZARD_STEPS = ["basics", "photos", "terms", "location", "contact", "review"]


def _draft() -> dict:
    return session.setdefault(WIZARD_SESSION_KEY, {})


def _ensure_profile(user: User) -> OwnerProfile:
    if not user.owner_profile:
        default_name = user.full_name or f"Владелец {user.phone[-4:]}"
        profile = OwnerProfile(user_id=user.id, display_name=default_name)
        db.session.add(profile)
        db.session.commit()
    return user.owner_profile


@owner_bp.route("/dashboard")
@login_required
@owner_mode_required
def dashboard():
    user = g.current_user
    _ensure_profile(user)
    cars = Car.query.filter_by(owner_id=user.id).order_by(Car.created_at.desc()).all()
    car_ids = [c.id for c in cars]

    views_total = View.query.filter(View.car_id.in_(car_ids)).count() if car_ids else 0
    contacts_total = ContactClick.query.filter(ContactClick.car_id.in_(car_ids)).count() if car_ids else 0

    plan = user.current_plan()
    sub = user.active_subscription()

    return render_template(
        "owner/dashboard.html", cars=cars, views_total=views_total,
        contacts_total=contacts_total, plan=plan, plan_info=plan_limits(plan),
        subscription=sub, stale_after_days=LISTING_STALE_AFTER_DAYS,
    )


@owner_bp.route("/cars")
@login_required
@owner_mode_required
def car_list():
    cars = Car.query.filter_by(owner_id=g.current_user.id).order_by(Car.created_at.desc()).all()
    return render_template("owner/cars_list.html", cars=cars)


# ---------------------------------------------------------------------------
# Мастер добавления автомобиля (шаги 1-6, раздел 31 ТЗ)
# ---------------------------------------------------------------------------

@owner_bp.route("/cars/new")
@login_required
@owner_mode_required
def wizard_start():
    if not can_add_car(g.current_user):
        flash("Вы достигли лимита автомобилей на вашем тарифе.", "error")
        return redirect(url_for("owner.subscription"))
    session[WIZARD_SESSION_KEY] = {}
    return redirect(url_for("owner.wizard_step", step="basics"))


@owner_bp.route("/cars/new/<step>", methods=["GET", "POST"])
@login_required
@owner_mode_required
def wizard_step(step):
    if step not in WIZARD_STEPS:
        return redirect(url_for("owner.wizard_start"))

    draft = _draft()

    if request.method == "POST":
        _save_step(step, draft, request.form)
        session.modified = True

        if step == "review":
            return _publish_draft(draft)

        next_step = WIZARD_STEPS[WIZARD_STEPS.index(step) + 1]
        return redirect(url_for("owner.wizard_step", step=next_step))

    cities = City.query.filter_by(is_active=True).order_by(City.is_primary.desc(), City.name).all()
    return render_template(
        f"owner/wizard_{step}.html", draft=draft, step=step, steps=WIZARD_STEPS,
        brands=CAR_BRANDS, body_types=BODY_TYPES, cities=cities,
    )


def _save_step(step, draft, form):
    if step == "basics":
        draft["brand"] = form.get("brand", "").strip()[:80]
        draft["model"] = form.get("model", "").strip()[:80]
        draft["year"] = form.get("year", type=int)
        draft["body_type"] = form.get("body_type")
        draft["seats"] = form.get("seats", type=int)
    elif step == "terms":
        draft["price_per_day"] = form.get("price_per_day", type=int)
        draft["with_driver"] = form.get("with_driver") == "1"
        draft["for_wedding"] = form.get("for_wedding") == "1"
        draft["delivery_available"] = form.get("delivery_available") == "1"
        draft["transmission_auto"] = form.get("transmission_auto", "1") == "1"
        draft["min_rental_days"] = form.get("min_rental_days", 1, type=int)
        draft["min_driver_age"] = form.get("min_driver_age", 18, type=int)
        draft["description"] = form.get("description", "").strip()[:2000]
    elif step == "location":
        draft["city_slug"] = form.get("city_slug")
    elif step == "contact":
        draft["phone"] = form.get("phone", "").strip()[:20]
        draft["telegram"] = form.get("telegram", "").strip()[:120]
        draft["whatsapp"] = form.get("whatsapp", "").strip()[:20]
        draft["contact_consent"] = form.get("contact_consent") == "1"


@owner_bp.route("/cars/new/photos/upload", methods=["POST"])
@login_required
@owner_mode_required
@rate_limit("upload")
def wizard_upload_photo():
    """Фото при создании грузятся сразу, но привязываются к draft-списку
    в сессии, а к самой машине — только на шаге публикации."""
    draft = _draft()
    photos = draft.setdefault("photo_temp_urls", [])

    plan = g.current_user.current_plan()
    if len(photos) >= plan_limits(plan)["max_photos_per_car"]:
        return jsonify(error="Достигнут лимит фотографий для вашего тарифа."), 400

    file = request.files.get("photo")
    if not file:
        return jsonify(error="Файл не найден."), 400

    try:
        image = validate_and_load_image(file)
    except InvalidImageError as exc:
        return jsonify(error=str(exc)), 400

    main_bytes, thumb_bytes = optimize_image(image)
    storage = get_image_storage()
    url, public_id = storage.upload_bytes(main_bytes, "car.webp")
    thumb_url, _ = storage.upload_bytes(thumb_bytes, "car_thumb.webp")

    photos.append({"url": url, "thumbnail_url": thumb_url, "storage_public_id": public_id})
    session.modified = True
    return jsonify(url=url, thumbnail_url=thumb_url, count=len(photos))


def _publish_draft(draft):
    city = City.query.filter_by(slug=draft.get("city_slug")).first()
    if not city or not draft.get("brand") or not draft.get("phone") or not draft.get("contact_consent"):
        flash("Заполните все обязательные поля мастера перед публикацией.", "error")
        return redirect(url_for("owner.wizard_step", step="basics"))

    car = Car(
        owner_id=g.current_user.id,
        city_id=city.id,
        brand=draft.get("brand", "")[:80],
        model=draft.get("model", "")[:80],
        year=draft.get("year"),
        body_type=draft.get("body_type"),
        seats=draft.get("seats"),
        transmission_auto=bool(draft.get("transmission_auto", True)),
        price_per_day=draft.get("price_per_day"),
        min_rental_days=draft.get("min_rental_days") or 1,
        min_driver_age=draft.get("min_driver_age") or 18,
        with_driver=bool(draft.get("with_driver")),
        for_wedding=bool(draft.get("for_wedding")),
        delivery_available=bool(draft.get("delivery_available")),
        description=draft.get("description", "")[:2000],
        phone=draft.get("phone", "")[:20],
        telegram=draft.get("telegram") or None,
        whatsapp=draft.get("whatsapp") or None,
        contact_consent=True,
        status="published",
    )
    db.session.add(car)
    db.session.flush()

    for position, photo in enumerate(draft.get("photo_temp_urls", [])):
        db.session.add(CarPhoto(
            car_id=car.id, url=photo["url"], thumbnail_url=photo["thumbnail_url"],
            storage_public_id=photo["storage_public_id"], position=position,
        ))

    db.session.commit()
    session.pop(WIZARD_SESSION_KEY, None)
    flash("Автомобиль опубликован. Теперь его могут найти пользователи KAVKAZ-CAR.", "success")
    return redirect(url_for("owner.car_list"))


# ---------------------------------------------------------------------------
# Управление опубликованными автомобилями
# ---------------------------------------------------------------------------

def _owned_car_or_404(public_id):
    car = Car.query.filter_by(public_id=public_id).first_or_404()
    if car.owner_id != g.current_user.id:
        from flask import abort
        abort(403)
    return car


@owner_bp.route("/cars/<public_id>/edit", methods=["GET", "POST"])
@login_required
@owner_mode_required
def edit_car(public_id):
    car = _owned_car_or_404(public_id)
    cities = City.query.filter_by(is_active=True).order_by(City.name).all()

    if request.method == "POST":
        car.price_per_day = request.form.get("price_per_day", type=int)
        car.description = request.form.get("description", "").strip()[:2000]
        car.with_driver = request.form.get("with_driver") == "1"
        car.for_wedding = request.form.get("for_wedding") == "1"
        car.delivery_available = request.form.get("delivery_available") == "1"
        min_age = request.form.get("min_driver_age", type=int)
        if min_age and 18 <= min_age <= 60:
            car.min_driver_age = min_age
        car.status = request.form.get("status") if request.form.get("status") in (
            "published", "paused",
        ) else car.status
        db.session.commit()
        flash("Изменения сохранены.", "success")
        return redirect(url_for("owner.car_list"))

    return render_template("owner/car_form.html", car=car, cities=cities)


@owner_bp.route("/cars/<public_id>/delete", methods=["POST"])
@login_required
@owner_mode_required
def delete_car(public_id):
    car = _owned_car_or_404(public_id)
    storage = get_image_storage()
    for photo in car.photos:
        if photo.storage_public_id:
            storage.delete(photo.storage_public_id)
    db.session.delete(car)
    db.session.commit()
    flash("Автомобиль удалён.", "success")
    return redirect(url_for("owner.car_list"))


@owner_bp.route("/cars/<public_id>/confirm-actual", methods=["POST"])
@login_required
@owner_mode_required
def confirm_actual(public_id):
    car = _owned_car_or_404(public_id)
    car.last_confirmed_at = datetime.utcnow()
    db.session.commit()
    return jsonify(ok=True, message="Актуально сегодня")


@owner_bp.route("/cars/<public_id>/boost", methods=["POST"])
@login_required
@owner_mode_required
def boost_car(public_id):
    car = _owned_car_or_404(public_id)
    if boosts_remaining(g.current_user) <= 0:
        flash("В этом месяце поднятия уже закончились.", "error")
        return redirect(url_for("owner.car_list"))

    car.is_boosted_until = datetime.utcnow() + timedelta(hours=48)
    sub = g.current_user.active_subscription()
    if sub:
        sub.boosts_used_this_period += 1
    db.session.commit()
    flash("Автомобиль поднят в выдаче на 48 часов.", "success")
    return redirect(url_for("owner.car_list"))


@owner_bp.route("/cars/<public_id>/photos/<int:photo_id>/delete", methods=["POST"])
@login_required
@owner_mode_required
def delete_photo(public_id, photo_id):
    car = _owned_car_or_404(public_id)
    photo = CarPhoto.query.filter_by(id=photo_id, car_id=car.id).first_or_404()
    storage = get_image_storage()
    if photo.storage_public_id:
        storage.delete(photo.storage_public_id)
    db.session.delete(photo)
    db.session.commit()
    return jsonify(ok=True)


@owner_bp.route("/cars/<public_id>/photos/upload", methods=["POST"])
@login_required
@owner_mode_required
@rate_limit("upload")
def upload_photo(public_id):
    car = _owned_car_or_404(public_id)
    if not can_add_photo(car, len(car.photos)):
        return jsonify(error="Достигнут лимит фотографий для вашего тарифа."), 400

    file = request.files.get("photo")
    if not file:
        return jsonify(error="Файл не найден."), 400
    try:
        image = validate_and_load_image(file)
    except InvalidImageError as exc:
        return jsonify(error=str(exc)), 400

    main_bytes, thumb_bytes = optimize_image(image)
    storage = get_image_storage()
    url, public_id_storage = storage.upload_bytes(main_bytes, "car.webp")
    thumb_url, _ = storage.upload_bytes(thumb_bytes, "car_thumb.webp")

    photo = CarPhoto(
        car_id=car.id, url=url, thumbnail_url=thumb_url,
        storage_public_id=public_id_storage, position=len(car.photos),
    )
    db.session.add(photo)
    db.session.commit()
    return jsonify(url=url, thumbnail_url=thumb_url, photo_id=photo.id)


# ---------------------------------------------------------------------------
# Профиль владельца / компании
# ---------------------------------------------------------------------------

@owner_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    owner_profile = _ensure_profile(g.current_user)
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()[:120]
        if full_name:
            g.current_user.full_name = full_name
            owner_profile.display_name = full_name  # держим синхронно — это одно и то же имя
        owner_profile.description = request.form.get("description", "").strip()[:1500]
        owner_profile.work_hours = request.form.get("work_hours", "").strip()[:200]
        if plan_limits(g.current_user.current_plan())["business_page"]:
            owner_profile.company_name = request.form.get("company_name", "").strip()[:150]
        db.session.commit()
        flash("Профиль обновлён.", "success")
    return render_template(
        "owner/profile.html", profile=owner_profile, plan=g.current_user.current_plan(),
        plan_info=plan_limits(g.current_user.current_plan()),
    )


@owner_bp.route("/profile/toggle-owner", methods=["POST"])
@login_required
def toggle_owner_mode():
    # Принимаем ЯВНОЕ желаемое состояние, а не «переверни текущее».
    # Так повторная отправка формы (двойной клик, кнопка «назад», медленная
    # сеть) даёт тот же результат, а не откатывает изменение обратно.
    enable = request.form.get("enable") == "1"
    g.current_user.is_owner = enable
    db.session.commit()
    if enable:
        flash("Режим владельца включён — теперь можно размещать автомобили.", "success")
    else:
        flash("Режим владельца выключен.", "success")
    return redirect(url_for("owner.profile"))


# ---------------------------------------------------------------------------
# Подписка и промокоды
# ---------------------------------------------------------------------------

@owner_bp.route("/subscription")
@login_required
def subscription():
    plan = g.current_user.current_plan()
    sub = g.current_user.active_subscription()
    return render_template(
        "owner/subscription.html", plan=plan, plan_info=plan_limits(plan), subscription=sub,
        cars_used=len(g.current_user.cars), boosts_left=boosts_remaining(g.current_user),
    )


@owner_bp.route("/subscription/redeem", methods=["POST"])
@login_required
@rate_limit("promo_redeem")
def redeem_promo():
    code_value = request.form.get("code", "").strip().upper()
    promo = PromoCode.query.filter_by(code=code_value).first()

    if not promo or not promo.is_valid():
        flash("Промокод недействителен или уже использован.", "error")
        return redirect(url_for("owner.subscription"))

    redeem_promo_code(promo, g.current_user)
    db.session.commit()
    flash(f"Тариф {promo.plan.upper()} активирован!", "success")
    return redirect(url_for("owner.subscription"))
