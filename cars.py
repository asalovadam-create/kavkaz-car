"""
Публичная часть KAVKAZ-CAR: каталог, поиск, карточка автомобиля,
избранное, сравнение и контакт с владельцем.

Клиенту НЕ нужна регистрация, чтобы найти машину и позвонить владельцу —
это принципиально (раздел 6 ТЗ).
"""
from datetime import datetime, timedelta

from flask import (
    Blueprint, abort, current_app, flash, g, jsonify, redirect,
    render_template, request, url_for,
)

from config import BODY_TYPES, EVENT_TYPES, LAUNCH_CITIES, REPORT_REASONS
from models import Car, City, ContactClick, Favorite, Report, View, db
from security import login_required, rate_limit
from services import build_car_slug, normalize_search_query, sort_key_for_catalog

cars_bp = Blueprint("cars", __name__)

CONTACT_REDIRECTS = {
    "call": lambda car: f"tel:{car.phone}",
    "whatsapp": lambda car: f"https://wa.me/{car.whatsapp.lstrip('+')}" if car.whatsapp else None,
    "telegram": lambda car: f"https://t.me/{car.telegram.lstrip('@')}" if car.telegram else None,
}


def _base_query():
    return Car.query.filter(Car.status == "published")


def _apply_filters(query, args):
    city_slug = args.get("city")
    if city_slug:
        query = query.join(City).filter(City.slug == city_slug)

    q = normalize_search_query(args.get("q", ""))
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Car.brand.ilike(like), Car.model.ilike(like)))

    price_max = args.get("price_max", type=int)
    if price_max:
        query = query.filter(db.or_(Car.price_per_day.is_(None), Car.price_per_day <= price_max))

    body_type = args.get("body_type")
    if body_type:
        query = query.filter(Car.body_type == body_type)

    year_min = args.get("year_min", type=int)
    if year_min:
        query = query.filter(Car.year >= year_min)

    seats_min = args.get("seats_min", type=int)
    if seats_min:
        query = query.filter(Car.seats >= seats_min)

    if args.get("with_driver") == "1":
        query = query.filter(Car.with_driver.is_(True))
    if args.get("no_driver") == "1":
        query = query.filter(Car.with_driver.is_(False))
    if args.get("for_wedding") == "1":
        query = query.filter(Car.for_wedding.is_(True))
    if args.get("delivery") == "1":
        query = query.filter(Car.delivery_available.is_(True))
    if args.get("automatic") == "1":
        query = query.filter(Car.transmission_auto.is_(True))
    if args.get("premium") == "1":
        query = query.filter(Car.is_premium.is_(True))

    return query


@cars_bp.route("/catalog")
def catalog():
    cities = City.query.filter_by(is_active=True).order_by(City.is_primary.desc(), City.name).all()
    query = _apply_filters(_base_query(), request.args)
    results = query.all()
    results.sort(key=sort_key_for_catalog)

    page = request.args.get("page", 1, type=int)
    per_page = 24
    start = (page - 1) * per_page
    page_items = results[start:start + per_page]
    has_more = start + per_page < len(results)

    context = dict(
        cars=page_items,
        cities=cities,
        body_types=BODY_TYPES,
        total_count=len(results),
        has_more=has_more,
        next_page=page + 1,
    )

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return render_template("partials/car_grid.html", **context)
    return render_template("catalog.html", **context)


@cars_bp.route("/<city_slug>")
def city_page(city_slug):
    valid_slugs = {c["slug"] for c in LAUNCH_CITIES}
    if city_slug not in valid_slugs:
        abort(404)
    city = City.query.filter_by(slug=city_slug).first_or_404()
    cars = _apply_filters(_base_query(), request.args)
    cars = cars.filter(Car.city_id == city.id).all()
    cars.sort(key=sort_key_for_catalog)
    return render_template("city.html", city=city, cars=cars[:24])


@cars_bp.route("/cars/<path:slug>")
def car_detail(slug):
    public_id = slug.rsplit("-", 1)[-1]
    car = Car.query.filter_by(public_id=public_id).first_or_404()

    if car.status != "published":
        # Владелец видит своё объявление в любом статусе, остальные — только опубликованное.
        if not g.current_user or g.current_user.id != car.owner_id:
            abort(404)

    canonical_slug = build_car_slug(car)
    if slug != canonical_slug:
        return redirect(url_for("cars.car_detail", slug=canonical_slug), code=301)

    _record_view(car)

    is_favorite = False
    if g.current_user:
        is_favorite = Favorite.query.filter_by(user_id=g.current_user.id, car_id=car.id).first() is not None

    similar = (
        Car.query.filter(Car.owner_id == car.owner_id, Car.id != car.id, Car.status == "published")
        .limit(4)
        .all()
    )

    return render_template(
        "car_detail.html", car=car, is_favorite=is_favorite,
        report_reasons=REPORT_REASONS, similar_cars=similar,
    )


def _record_view(car):
    import hashlib

    ua = request.headers.get("User-Agent", "")
    ip = request.remote_addr or ""
    viewer_hash = hashlib.sha256(f"{ip}{ua}{car.id}".encode()).hexdigest()

    recent_cutoff = datetime.utcnow() - timedelta(hours=6)
    already_counted = View.query.filter(
        View.car_id == car.id, View.viewer_hash == viewer_hash, View.created_at > recent_cutoff,
    ).first()
    if not already_counted:
        db.session.add(View(car_id=car.id, viewer_hash=viewer_hash))
        db.session.commit()


@cars_bp.route("/cars/<public_id>/go/<channel>")
@rate_limit("contact_click")
def contact_redirect(public_id, channel):
    if channel not in CONTACT_REDIRECTS:
        abort(404)
    car = Car.query.filter_by(public_id=public_id, status="published").first_or_404()
    target = CONTACT_REDIRECTS[channel](car)
    if not target:
        abort(404)
    db.session.add(ContactClick(car_id=car.id, channel=channel))
    db.session.commit()
    return redirect(target)


@cars_bp.route("/cars/<public_id>/report", methods=["GET", "POST"])
@rate_limit("report")
def report_car(public_id):
    car = Car.query.filter_by(public_id=public_id).first_or_404()
    if request.method == "POST":
        reason = request.form.get("reason")
        valid_reasons = {key for key, _ in REPORT_REASONS}
        if reason not in valid_reasons:
            flash("Выберите причину жалобы.", "error")
            return render_template("report.html", car=car, reasons=REPORT_REASONS), 400

        db.session.add(Report(
            car_id=car.id, reason=reason, message=request.form.get("message", "")[:1000],
        ))
        db.session.commit()
        flash("Спасибо, жалоба отправлена администратору.", "success")
        return redirect(url_for("cars.car_detail", slug=build_car_slug(car)))

    return render_template("report.html", car=car, reasons=REPORT_REASONS)


# ---------------------------------------------------------------------------
# Избранное
# ---------------------------------------------------------------------------

@cars_bp.route("/favorites")
@login_required
def favorites():
    favs = (
        Favorite.query.filter_by(user_id=g.current_user.id)
        .order_by(Favorite.created_at.desc())
        .all()
    )
    cars = [f.car for f in favs] if False else [Car.query.get(f.car_id) for f in favs]
    return render_template("favorites.html", cars=[c for c in cars if c])


@cars_bp.route("/favorites/toggle/<public_id>", methods=["POST"])
@login_required
def toggle_favorite(public_id):
    car = Car.query.filter_by(public_id=public_id).first_or_404()
    existing = Favorite.query.filter_by(user_id=g.current_user.id, car_id=car.id).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        return jsonify(favorite=False)

    db.session.add(Favorite(user_id=g.current_user.id, car_id=car.id))
    db.session.commit()
    return jsonify(favorite=True)


# ---------------------------------------------------------------------------
# Сравнение (до 3 автомобилей)
# ---------------------------------------------------------------------------

@cars_bp.route("/compare")
def compare():
    ids = [pid for pid in request.args.getlist("car") if pid][:3]
    cars = [Car.query.filter_by(public_id=pid, status="published").first() for pid in ids]
    cars = [c for c in cars if c]
    return render_template("compare.html", cars=cars)


# ---------------------------------------------------------------------------
# «Помогите выбрать» — простая правило-ориентированная логика, без AI API
# (раздел 27, 88 ТЗ)
# ---------------------------------------------------------------------------

EVENT_PREFERENCES = {
    "wedding": {"for_wedding": True, "body_type": "premium"},
    "date": {"body_type": "premium"},
    "photoshoot": {"is_premium": True},
    "trip": {"body_type": "suv"},
    "airport": {"body_type": "business"},
    "event": {"body_type": "business"},
    "filming": {"is_premium": True},
    "ride": {},
}


@cars_bp.route("/help-me-choose", methods=["GET", "POST"])
def help_me_choose():
    if request.method == "POST":
        budget = request.form.get("budget", type=int)
        event = request.form.get("event")
        seats = request.form.get("seats", type=int)
        city_slug = request.form.get("city")

        query = _base_query()
        if city_slug:
            query = query.join(City).filter(City.slug == city_slug)
        if budget:
            query = query.filter(db.or_(Car.price_per_day.is_(None), Car.price_per_day <= budget))
        if seats:
            query = query.filter(db.or_(Car.seats.is_(None), Car.seats >= seats))

        prefs = EVENT_PREFERENCES.get(event, {})
        if prefs.get("for_wedding"):
            query = query.filter(Car.for_wedding.is_(True))

        candidates = query.all()
        candidates.sort(key=sort_key_for_catalog)
        return render_template("partials/help_results.html", cars=candidates[:3])

    return render_template("help_me_choose.html", event_types=EVENT_TYPES)
