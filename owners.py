"""
Личный кабинет владельца KAVKAZ-CAR.

Раздел 30 ТЗ: интерфейс должен быть настолько простым, чтобы человек,
который плохо разбирается в сайтах, разместил автомобиль без посторонней
помощи. Поэтому добавление машины — пошаговый мастер (раздел 31 ТЗ),
а не одна большая форма.
"""
import json
from datetime import datetime, timedelta

from flask import (
    Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for,
)
from sqlalchemy import func

from config import BODY_TYPES, CAR_BRANDS, PLANS
from models import (
    BoostLog, Car, CarDraft, CarPhoto, City, ContactClick, OwnerProfile, PaymentOrder,
    PromoCode, View, db,
)
from security import (
    InvalidImageError, MAX_PASSWORD_LENGTH, hash_password, is_throttled, login_required,
    owner_mode_required, rate_limit, record_failure, validate_and_load_image, verify_password,
)
from services import (
    boosts_remaining, can_add_car, can_add_photo, get_image_storage, higher_plan_active,
    optimize_image, plan_limits, purge_car, redeem_promo_code,
)
from validators import (
    clean_text, normalize_contact_phone, normalize_telegram, to_int,
)

owner_bp = Blueprint("owner", __name__, url_prefix="/owner")

WIZARD_STEPS = ["basics", "photos", "terms", "location", "contact", "review"]
WIZARD_TITLES = {
    "basics": "Автомобиль", "photos": "Фото", "terms": "Условия",
    "location": "Город", "contact": "Контакты", "review": "Публикация",
}
MIN_PRICE, MAX_PRICE = 500, 1_000_000
DRIVER_AGES = (18, 21, 23, 25)
BODY_TYPE_KEYS = {key for key, _ in BODY_TYPES}
CHECKBOXES_BY_GROUP = {
    "terms": ("transmission_auto", "with_driver", "for_wedding", "delivery_available"),
    "contact": ("contact_consent",),
}


# ---------------------------------------------------------------------------
# Общие помощники
# ---------------------------------------------------------------------------

def _ensure_profile(user) -> OwnerProfile:
    if not user.owner_profile:
        default_name = user.full_name or f"Владелец {user.phone[-4:]}"
        profile = OwnerProfile(user_id=user.id, display_name=default_name)
        db.session.add(profile)
        db.session.commit()
    return user.owner_profile


def _owned_car_or_404(public_id) -> Car:
    car = Car.query.filter_by(public_id=public_id).first_or_404()
    if car.owner_id != g.current_user.id:
        abort(404)  # не подтверждаем существование чужого объявления
    return car


def _active_cities():
    return City.query.filter_by(is_active=True).order_by(City.is_primary.desc(), City.name).all()


def _save_image(image):
    main_bytes, thumb_bytes = optimize_image(image)
    storage = get_image_storage()
    url, public_id = storage.upload_bytes(main_bytes, "car.webp")
    thumb_url, _ = storage.upload_bytes(thumb_bytes, "car_thumb.webp")
    return url, thumb_url, public_id


# ---------------------------------------------------------------------------
# Проверка полей объявления (одна и та же для мастера и для редактирования)
# ---------------------------------------------------------------------------

def parse_car_fields(group: str, form) -> tuple[dict, list[str]]:
    """Возвращает (значения, ошибки). Серверная проверка обязательна: HTML-атрибуты
    min/max/required обходятся одним запросом curl."""
    data, errors = {}, []
    year_max = datetime.utcnow().year + 1

    if group == "basics":
        data["brand"] = clean_text(form.get("brand"), 80)
        data["model"] = clean_text(form.get("model"), 80)
        if not data["brand"]:
            errors.append("Укажите марку автомобиля.")
        if not data["model"]:
            errors.append("Укажите модель автомобиля.")
        raw_year = (form.get("year") or "").strip()
        data["year"] = to_int(raw_year, minimum=1980, maximum=year_max) if raw_year else None
        if raw_year and data["year"] is None:
            errors.append(f"Год выпуска — от 1980 до {year_max}.")
        body = form.get("body_type")
        data["body_type"] = body if body in BODY_TYPE_KEYS else None
        raw_seats = (form.get("seats") or "").strip()
        data["seats"] = to_int(raw_seats, minimum=1, maximum=20) if raw_seats else None
        if raw_seats and data["seats"] is None:
            errors.append("Количество мест — от 1 до 20.")

    elif group == "terms":
        raw_price = (form.get("price_per_day") or "").strip()
        data["price_per_day"] = to_int(raw_price, minimum=MIN_PRICE, maximum=MAX_PRICE) if raw_price else None
        if raw_price and data["price_per_day"] is None:
            errors.append(f"Цена — от {MIN_PRICE} до {MAX_PRICE:,} ₽ за сутки. Оставьте поле пустым для «цена по запросу».".replace(",", " "))
        data["min_rental_days"] = to_int(form.get("min_rental_days") or 1, minimum=1, maximum=365) or 1
        age = to_int(form.get("min_driver_age") or 18)
        data["min_driver_age"] = age if age in DRIVER_AGES else 18
        data["description"] = clean_text(form.get("description"), 2000)
        for flag in ("transmission_auto", "with_driver", "for_wedding", "delivery_available"):
            # Неотмеченный чекбокс в запросе отсутствует — это «нет», а не «как раньше».
            data[flag] = form.get(flag) == "1"

    elif group == "location":
        slug = form.get("city_slug") or ""
        if City.query.filter_by(slug=slug, is_active=True).first():
            data["city_slug"] = slug
        else:
            errors.append("Выберите город из списка.")

    elif group == "contact":
        phone = normalize_contact_phone(form.get("phone"))
        data["phone"] = phone
        if not phone:
            errors.append("Введите корректный номер телефона, например +7 900 123-45-67.")
        raw_wa = (form.get("whatsapp") or "").strip()
        data["whatsapp"] = normalize_contact_phone(raw_wa) if raw_wa else None
        if raw_wa and not data["whatsapp"]:
            errors.append("Номер WhatsApp указан неверно.")
        raw_tg = (form.get("telegram") or "").strip()
        data["telegram"] = normalize_telegram(raw_tg) if raw_tg else None
        if raw_tg and not data["telegram"]:
            errors.append("Telegram укажите в виде @username (от 5 символов, латиница, цифры, _).")
        data["contact_consent"] = form.get("contact_consent") == "1"
        if not data["contact_consent"]:
            errors.append("Подтвердите, что разрешаете показывать контакты.")
    return data, errors


# ---------------------------------------------------------------------------
# Кабинет
# ---------------------------------------------------------------------------

@owner_bp.route("/become", methods=["GET", "POST"])
@login_required
def become():
    """Клиент решил сдавать авто: одно понятное действие вместо «переключателя»."""
    user = g.current_user
    if user.is_owner:
        return redirect(url_for("owner.dashboard"))
    if request.method == "POST":
        user.is_owner = True
        db.session.commit()
        _ensure_profile(user)
        flash("Готово! Теперь можно разместить первый автомобиль.", "success")
        return redirect(url_for("owner.wizard_start"))
    return render_template("owner/become.html")


def _stats_for(car_ids):
    """Просмотры и обращения по каждой машине (всего и за 7 дней) + каналы."""
    empty = {"views": 0, "views7": 0, "contacts": 0, "contacts7": 0, "channels": {}}
    stats = {cid: {**empty, "channels": {}} for cid in car_ids}
    if not car_ids:
        return stats
    since = datetime.utcnow() - timedelta(days=7)

    for car_id, n in db.session.query(View.car_id, func.count(View.id)).filter(View.car_id.in_(car_ids)).group_by(View.car_id):
        stats[car_id]["views"] = n
    for car_id, n in db.session.query(View.car_id, func.count(View.id)).filter(
        View.car_id.in_(car_ids), View.created_at >= since
    ).group_by(View.car_id):
        stats[car_id]["views7"] = n
    for car_id, channel, n in db.session.query(
        ContactClick.car_id, ContactClick.channel, func.count(ContactClick.id)
    ).filter(ContactClick.car_id.in_(car_ids)).group_by(ContactClick.car_id, ContactClick.channel):
        stats[car_id]["contacts"] += n
        stats[car_id]["channels"][channel] = n
    for car_id, n in db.session.query(ContactClick.car_id, func.count(ContactClick.id)).filter(
        ContactClick.car_id.in_(car_ids), ContactClick.created_at >= since
    ).group_by(ContactClick.car_id):
        stats[car_id]["contacts7"] = n
    return stats


@owner_bp.route("/dashboard")
@login_required
@owner_mode_required
def dashboard():
    user = g.current_user
    _ensure_profile(user)
    cars = Car.query.filter_by(owner_id=user.id).order_by(Car.created_at.desc()).all()
    stats = _stats_for([c.id for c in cars])
    plan = user.current_plan()
    sub = user.active_subscription()
    days_left = None
    if sub and sub.expires_at:
        days_left = max(0, (sub.expires_at - datetime.utcnow()).days)

    return render_template(
        "owner/dashboard.html", cars=cars, stats=stats,
        views_total=sum(s["views"] for s in stats.values()),
        contacts_total=sum(s["contacts"] for s in stats.values()),
        plan=plan, plan_info=plan_limits(plan), subscription=sub, days_left=days_left,
        boosts_left=boosts_remaining(user), can_add=can_add_car(user),
        extended_stats=plan_limits(plan)["stats"] == "extended",
    )


@owner_bp.route("/cars")
@login_required
@owner_mode_required
def car_list():
    return redirect(url_for("owner.dashboard"))


# ---------------------------------------------------------------------------
# Мастер добавления автомобиля (шаги 1-6, раздел 31 ТЗ). Черновик хранится в БД.
# ---------------------------------------------------------------------------

def _draft_row() -> CarDraft:
    row = CarDraft.query.filter_by(user_id=g.current_user.id).first()
    if row is None:
        row = CarDraft(user_id=g.current_user.id, data="{}")
        db.session.add(row)
        db.session.flush()
    return row


def _draft_data(row: CarDraft) -> dict:
    try:
        return json.loads(row.data or "{}")
    except ValueError:
        return {}


def _store_draft(row: CarDraft, data: dict) -> None:
    row.data = json.dumps(data, ensure_ascii=False)
    row.updated_at = datetime.utcnow()


def _delete_stored_photos(photos) -> None:
    storage = get_image_storage()
    for photo in photos:
        if photo.get("storage_public_id"):
            try:
                storage.delete(photo["storage_public_id"])
            except Exception:
                pass


@owner_bp.route("/cars/new")
@login_required
@owner_mode_required
def wizard_start():
    if not can_add_car(g.current_user):
        flash("Вы достигли лимита автомобилей на вашем тарифе. Выберите тариф побольше, чтобы добавить ещё.", "error")
        return redirect(url_for("payments.pricing"))
    row = _draft_row()
    _delete_stored_photos(_draft_data(row).get("photos", []))  # старый черновик не оставляет мусор в хранилище
    _store_draft(row, {"transmission_auto": True, "phone": g.current_user.phone})
    db.session.commit()
    return redirect(url_for("owner.wizard_step", step="basics"))


def _check_all(data: dict) -> tuple[str | None, list[str]]:
    """Проверяет черновик целиком. Возвращает (первый_шаг_с_ошибкой, ошибки)."""
    for group, step in (("basics", "basics"), ("terms", "terms"), ("location", "location"), ("contact", "contact")):
        form = {k: ("1" if v is True else "" if v in (None, False) else str(v)) for k, v in data.items()}
        _, errors = parse_car_fields(group, form)
        if errors:
            return step, errors
    if not data.get("photos"):
        return "photos", ["Добавьте хотя бы одно фото — без него объявление не опубликуется."]
    return None, []


@owner_bp.route("/cars/new/<step>", methods=["GET", "POST"])
@login_required
@owner_mode_required
def wizard_step(step):
    if step not in WIZARD_STEPS:
        return redirect(url_for("owner.wizard_start"))

    row = _draft_row()
    data = _draft_data(row)
    if not data:  # пользователь открыл ссылку шага без старта мастера
        data = {"transmission_auto": True, "phone": g.current_user.phone}

    if request.method == "POST":
        if step == "review":
            return _publish_draft(row, data)
        if step == "photos":
            _store_draft(row, data)
            db.session.commit()
            return redirect(url_for("owner.wizard_step", step=WIZARD_STEPS[WIZARD_STEPS.index(step) + 1]))

        values, errors = parse_car_fields(step, request.form)
        if errors:
            for message in errors:
                flash(message, "error")
            # Показываем то, что человек ввёл, а не стираем форму.
            shown = {k: v for k, v in request.form.items() if k != "csrf_token"}
            for flag in CHECKBOXES_BY_GROUP.get(step, ()):
                shown[flag] = request.form.get(flag) == "1"
            data.update(shown)
            return _render_step(step, data), 400
        data.update(values)
        _store_draft(row, data)
        db.session.commit()
        return redirect(url_for("owner.wizard_step", step=WIZARD_STEPS[WIZARD_STEPS.index(step) + 1]))

    return _render_step(step, data)


def _render_step(step: str, data: dict):
    context = dict(
        draft=data, step=step, steps=WIZARD_STEPS, step_titles=WIZARD_TITLES, brands=CAR_BRANDS,
        body_types=BODY_TYPES, cities=_active_cities(),
        max_photos=plan_limits(g.current_user.current_plan())["max_photos_per_car"],
    )
    if step == "review":
        city = City.query.filter_by(slug=data.get("city_slug")).first()
        problem_step, problems = _check_all(data)
        context.update(city=city, problems=problems, problem_step=problem_step)
    return render_template(f"owner/wizard_{step}.html", **context)


@owner_bp.route("/cars/new/photos/upload", methods=["POST"])
@login_required
@owner_mode_required
@rate_limit("upload")
def wizard_upload_photo():
    row = _draft_row()
    data = _draft_data(row)
    photos = data.setdefault("photos", [])

    if len(photos) >= plan_limits(g.current_user.current_plan())["max_photos_per_car"]:
        return jsonify(error="Достигнут лимит фотографий для вашего тарифа."), 400
    file = request.files.get("photo")
    if not file:
        return jsonify(error="Файл не найден."), 400
    try:
        image = validate_and_load_image(file)
    except InvalidImageError as exc:
        return jsonify(error=str(exc)), 400

    url, thumb_url, public_id = _save_image(image)
    photos.append({"url": url, "thumbnail_url": thumb_url, "storage_public_id": public_id})
    _store_draft(row, data)
    db.session.commit()
    return jsonify(url=url, thumbnail_url=thumb_url, token=public_id, count=len(photos))


@owner_bp.route("/cars/new/photos/delete", methods=["POST"])
@login_required
@owner_mode_required
def wizard_delete_photo():
    row = _draft_row()
    data = _draft_data(row)
    token = request.form.get("token", "")
    removed = [p for p in data.get("photos", []) if p.get("storage_public_id") == token]
    data["photos"] = [p for p in data.get("photos", []) if p.get("storage_public_id") != token]
    _delete_stored_photos(removed)
    _store_draft(row, data)
    db.session.commit()
    return jsonify(ok=True, count=len(data["photos"]))


@owner_bp.route("/cars/new/photos/primary", methods=["POST"])
@login_required
@owner_mode_required
def wizard_primary_photo():
    row = _draft_row()
    data = _draft_data(row)
    token = request.form.get("token", "")
    photos = data.get("photos", [])
    chosen = [p for p in photos if p.get("storage_public_id") == token]
    if chosen:
        data["photos"] = chosen + [p for p in photos if p is not chosen[0]]
        _store_draft(row, data)
        db.session.commit()
    return jsonify(ok=True)


def _publish_draft(row: CarDraft, data: dict):
    step, problems = _check_all(data)
    if step:
        for message in problems:
            flash(message, "error")
        return redirect(url_for("owner.wizard_step", step=step))
    if not can_add_car(g.current_user):
        # Лимит проверяем ещё раз именно в момент публикации: иначе его можно
        # обойти, открыв мастер в нескольких вкладках.
        flash("Вы достигли лимита автомобилей на вашем тарифе.", "error")
        return redirect(url_for("payments.pricing"))

    city = City.query.filter_by(slug=data["city_slug"], is_active=True).first()
    car = Car(
        owner_id=g.current_user.id, city_id=city.id,
        brand=data["brand"], model=data["model"], year=data.get("year"),
        body_type=data.get("body_type"), seats=data.get("seats"),
        transmission_auto=bool(data.get("transmission_auto", True)),
        price_per_day=data.get("price_per_day"),
        min_rental_days=data.get("min_rental_days") or 1,
        min_driver_age=data.get("min_driver_age") or 18,
        with_driver=bool(data.get("with_driver")), for_wedding=bool(data.get("for_wedding")),
        delivery_available=bool(data.get("delivery_available")),
        description=data.get("description") or None,
        phone=data["phone"], telegram=data.get("telegram"), whatsapp=data.get("whatsapp"),
        contact_consent=True, status="published",
    )
    db.session.add(car)
    db.session.flush()
    for position, photo in enumerate(data.get("photos", [])):
        db.session.add(CarPhoto(
            car_id=car.id, url=photo["url"], thumbnail_url=photo["thumbnail_url"],
            storage_public_id=photo["storage_public_id"], position=position,
        ))
    db.session.delete(row)
    db.session.commit()
    flash("Автомобиль опубликован — теперь его видят клиенты. Поделитесь ссылкой, чтобы получить первые звонки.", "success")
    return redirect(url_for("owner.dashboard", published=car.public_id))


# ---------------------------------------------------------------------------
# Управление опубликованными автомобилями
# ---------------------------------------------------------------------------

@owner_bp.route("/cars/<public_id>/edit", methods=["GET", "POST"])
@login_required
@owner_mode_required
def edit_car(public_id):
    car = _owned_car_or_404(public_id)
    cities = _active_cities()

    if request.method == "POST":
        values, errors = {}, []
        for group in ("basics", "terms", "location"):
            part, errs = parse_car_fields(group, request.form)
            values.update(part)
            errors += errs
        contact_form = request.form.copy()
        contact_form["contact_consent"] = "1"  # согласие уже дано при публикации
        part, errs = parse_car_fields("contact", contact_form)
        values.update(part)
        errors += errs

        if errors:
            for message in errors:
                flash(message, "error")
            return render_template("owner/car_form.html", car=car, cities=cities, form=request.form, body_types=BODY_TYPES), 400

        city = City.query.filter_by(slug=values["city_slug"]).first()
        car.city_id = city.id
        for field in ("brand", "model", "year", "body_type", "seats", "price_per_day", "min_rental_days",
                      "min_driver_age", "with_driver", "for_wedding", "delivery_available",
                      "transmission_auto", "phone", "whatsapp", "telegram"):
            setattr(car, field, values[field])
        car.description = values["description"] or None

        # Менять статус вручную можно только между «опубликовано» и «пауза».
        # Заблокированное модератором/истёкшее нельзя вернуть в каталог самому.
        new_status = request.form.get("status")
        if car.status in ("published", "paused") and new_status in ("published", "paused"):
            car.status = new_status

        db.session.commit()
        flash("Изменения сохранены.", "success")
        return redirect(url_for("owner.dashboard"))

    return render_template("owner/car_form.html", car=car, cities=cities, form=None, body_types=BODY_TYPES)


@owner_bp.route("/cars/<public_id>/toggle-status", methods=["POST"])
@login_required
@owner_mode_required
def toggle_status(public_id):
    car = _owned_car_or_404(public_id)
    if car.status == "published":
        car.status = "paused"
        flash("Объявление скрыто из каталога. Вернуть его можно в любой момент.", "success")
    elif car.status == "paused":
        car.status = "published"
        flash("Объявление снова в каталоге.", "success")
    else:
        flash("Статус этого объявления изменить нельзя. Напишите в поддержку.", "error")
    db.session.commit()
    return redirect(url_for("owner.dashboard"))


@owner_bp.route("/cars/<public_id>/delete", methods=["POST"])
@login_required
@owner_mode_required
def delete_car(public_id):
    car = _owned_car_or_404(public_id)
    purge_car(car)
    db.session.commit()
    flash("Автомобиль удалён.", "success")
    return redirect(url_for("owner.dashboard"))


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
    if car.status != "published":
        flash("Поднять можно только опубликованное объявление.", "error")
    elif car.is_boosted():
        flash(f"Объявление уже в топе до {car.is_boosted_until:%d.%m %H:%M} (UTC).", "error")
    elif boosts_remaining(g.current_user) <= 0:
        flash("Поднятия на этот месяц закончились. Больше поднятий — на тарифах PRO и BUSINESS.", "error")
    else:
        car.is_boosted_until = datetime.utcnow() + timedelta(hours=48)
        db.session.add(BoostLog(car_id=car.id, owner_id=g.current_user.id))
        db.session.commit()
        flash("Автомобиль поднят в выдаче на 48 часов.", "success")
        return redirect(url_for("owner.dashboard"))
    return redirect(url_for("owner.dashboard"))


@owner_bp.route("/cars/<public_id>/photos/<int:photo_id>/delete", methods=["POST"])
@login_required
@owner_mode_required
def delete_photo(public_id, photo_id):
    car = _owned_car_or_404(public_id)
    photo = CarPhoto.query.filter_by(id=photo_id, car_id=car.id).first_or_404()
    if photo.storage_public_id:
        try:
            get_image_storage().delete(photo.storage_public_id)
        except Exception:
            pass
    db.session.delete(photo)
    db.session.flush()
    for position, rest in enumerate(CarPhoto.query.filter_by(car_id=car.id).order_by(CarPhoto.position, CarPhoto.id)):
        rest.position = position
    db.session.commit()
    return jsonify(ok=True)


@owner_bp.route("/cars/<public_id>/photos/<int:photo_id>/primary", methods=["POST"])
@login_required
@owner_mode_required
def primary_photo(public_id, photo_id):
    car = _owned_car_or_404(public_id)
    photos = CarPhoto.query.filter_by(car_id=car.id).order_by(CarPhoto.position, CarPhoto.id).all()
    chosen = next((p for p in photos if p.id == photo_id), None)
    if not chosen:
        abort(404)
    for position, photo in enumerate([chosen] + [p for p in photos if p.id != photo_id]):
        photo.position = position
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

    url, thumb_url, storage_id = _save_image(image)
    photo = CarPhoto(
        car_id=car.id, url=url, thumbnail_url=thumb_url,
        storage_public_id=storage_id, position=len(car.photos),
    )
    db.session.add(photo)
    db.session.commit()
    return jsonify(url=url, thumbnail_url=thumb_url, photo_id=photo.id, token=str(photo.id))


# ---------------------------------------------------------------------------
# Профиль (доступен и клиенту, и владельцу)
# ---------------------------------------------------------------------------

@owner_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = g.current_user
    owner_profile = _ensure_profile(user) if user.is_owner else user.owner_profile

    if request.method == "POST":
        full_name = clean_text(request.form.get("full_name"), 120)
        if not full_name:
            flash("Имя не может быть пустым.", "error")
        else:
            user.full_name = full_name
            if owner_profile:
                owner_profile.display_name = full_name  # держим синхронно — это одно и то же имя
                if user.is_owner:
                    owner_profile.description = clean_text(request.form.get("description"), 1500)
                    owner_profile.work_hours = clean_text(request.form.get("work_hours"), 200)
                    if plan_limits(user.current_plan())["business_page"]:
                        owner_profile.company_name = clean_text(request.form.get("company_name"), 150) or None
            db.session.commit()
            flash("Профиль обновлён.", "success")
        return redirect(url_for("owner.profile"))

    plan = user.current_plan()
    return render_template(
        "owner/profile.html", profile=owner_profile, plan=plan, plan_info=plan_limits(plan),
    )


@owner_bp.route("/profile/password", methods=["POST"])
@login_required
@rate_limit("password_change")
def change_password():
    user = g.current_user
    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    confirm = request.form.get("new_password_confirm", "")

    if is_throttled("login_phone", user.phone):
        flash("Слишком много неудачных попыток. Подождите 15 минут.", "error")
    elif not verify_password(user.password_hash, current):
        record_failure("login_phone", user.phone)
        flash("Текущий пароль указан неверно.", "error")
    elif len(new) < 8:
        flash("Новый пароль должен быть не короче 8 символов.", "error")
    elif len(new) > MAX_PASSWORD_LENGTH:
        flash(f"Пароль слишком длинный (максимум {MAX_PASSWORD_LENGTH} символов).", "error")
    elif new != confirm:
        flash("Новые пароли не совпадают.", "error")
    elif new == current:
        flash("Новый пароль должен отличаться от текущего.", "error")
    else:
        from flask import session
        user.password_hash = hash_password(new)
        db.session.commit()
        session["uv"] = user.session_token()  # здесь остаёмся, на остальных устройствах сессии завершатся
        flash("Пароль изменён. На других устройствах потребуется войти заново.", "success")
    return redirect(url_for("owner.profile"))


@owner_bp.route("/profile/logo", methods=["POST"])
@login_required
@owner_mode_required
@rate_limit("upload")
def upload_logo():
    file = request.files.get("logo")
    if not file or not file.filename:
        flash("Выберите файл с логотипом.", "error")
        return redirect(url_for("owner.profile"))
    try:
        image = validate_and_load_image(file)
    except InvalidImageError as exc:
        flash(str(exc), "error")
        return redirect(url_for("owner.profile"))
    _, thumb_bytes = optimize_image(image)
    url, _ = get_image_storage().upload_bytes(thumb_bytes, "logo.webp")
    _ensure_profile(g.current_user).logo_url = url
    db.session.commit()
    flash("Логотип обновлён.", "success")
    return redirect(url_for("owner.profile"))


# ---------------------------------------------------------------------------
# Подписка, промокоды, заказы
# ---------------------------------------------------------------------------

@owner_bp.route("/subscription")
@login_required
def subscription():
    user = g.current_user
    plan = user.current_plan()
    sub = user.active_subscription()
    orders = (
        PaymentOrder.query.filter_by(user_id=user.id).order_by(PaymentOrder.created_at.desc()).limit(8).all()
    )
    return render_template(
        "owner/subscription.html", plan=plan, plan_info=plan_limits(plan), subscription=sub,
        cars_used=len(user.cars), boosts_left=boosts_remaining(user), orders=orders,
    )


@owner_bp.route("/subscription/redeem", methods=["POST"])
@login_required
@rate_limit("promo_redeem")
def redeem_promo():
    code_value = clean_text(request.form.get("code"), 40).upper()
    promo = PromoCode.query.filter_by(code=code_value).first() if code_value else None

    if not promo or not promo.is_valid():
        flash("Промокод недействителен или уже использован.", "error")
        return redirect(url_for("owner.subscription"))

    blocking = higher_plan_active(g.current_user, promo.plan)
    if blocking:
        flash(f"У вас уже действует тариф {blocking.plan.upper()} — промокод на {promo.plan.upper()} вы не потеряете, "
              "активируйте его после окончания текущего тарифа.", "error")
        return redirect(url_for("owner.subscription"))

    if redeem_promo_code(promo, g.current_user) is None:
        db.session.rollback()
        flash("Промокод недействителен или уже использован.", "error")
        return redirect(url_for("owner.subscription"))
    if not g.current_user.is_owner:
        g.current_user.is_owner = True
    db.session.commit()
    flash(f"Тариф {promo.plan.upper()} активирован!", "success")
    return redirect(url_for("owner.subscription"))
