"""
Публичная часть KAVKAZ-CAR: каталог, поиск, карточка автомобиля,
избранное, сравнение, подбор и контакт с владельцем.

Клиенту НЕ нужна регистрация, чтобы найти машину и позвонить владельцу —
это принципиально (раздел 6 ТЗ).
"""
import hashlib
from datetime import datetime, timedelta

from flask import (
    Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template,
    request, url_for,
)
from sqlalchemy.orm import joinedload, selectinload

from config import BODY_TYPES, EVENT_TYPES, LAUNCH_CITIES, REPORT_REASONS
from models import Car, City, ContactClick, Favorite, OwnerProfile, Report, User, View, db
from security import login_required, rate_limit
from services import (
    SORT_MODES, build_car_slug, expand_search_terms, freshness_label, notify_admin_telegram,
    plan_limits, preload_plans, rank_cars,
)
from validators import (
    clean_text, like_escape, normalize_telegram, sanitize_tel, whatsapp_link,
)

cars_bp = Blueprint("cars", __name__)

PER_PAGE = 24
# В ссылки пагинации и «плашки» фильтров попадают только известные параметры.
# Иначе специальные имена url_for (_external, _scheme...) из адресной строки
# могли бы вызвать ошибку 500.
CATALOG_ARGS = {
    "q", "city", "body_type", "price_max", "year_min", "seats_min", "with_driver", "no_driver",
    "for_wedding", "delivery", "automatic", "premium", "sort", "page",
}


def _catalog_args() -> dict:
    return {k: v for k, v in request.args.to_dict(flat=True).items() if k in CATALOG_ARGS}
MAX_RESULTS = 3000  # потолок выборки, чтобы одна страница не грузила всю базу
BOT_MARKERS = ("bot", "crawl", "spider", "slurp", "preview", "curl", "python-requests", "headless", "monitor")
BODY_TYPE_KEYS = {key for key, _ in BODY_TYPES}


def _base_query():
    return (
        Car.query.filter(Car.status == "published")
        .options(
            joinedload(Car.city),
            selectinload(Car.photos),
            selectinload(Car.owner).selectinload(User.owner_profile),
        )
    )


def _apply_filters(query, args):
    city_slug = args.get("city")
    q_groups = expand_search_terms(args.get("q", ""))
    if city_slug or q_groups:
        query = query.join(City, Car.city_id == City.id)
    if city_slug:
        query = query.filter(City.slug == city_slug)

    # Каждое слово запроса должно найтись (AND), но у слова может быть несколько
    # вариантов написания (OR): «гелик» -> g-class / g63 / g500 ...
    for variants in q_groups:
        conditions = []
        for variant in variants:
            like = f"%{like_escape(variant)}%"
            conditions += [
                Car.brand.ilike(like, escape="\\"),
                Car.model.ilike(like, escape="\\"),
                City.name.ilike(like, escape="\\"),
            ]
        query = query.filter(db.or_(*conditions))

    price_max = args.get("price_max", type=int)
    if price_max and price_max > 0:
        query = query.filter(db.or_(Car.price_per_day.is_(None), Car.price_per_day <= price_max))

    body_type = args.get("body_type")
    if body_type in BODY_TYPE_KEYS:
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


def _active_cities():
    return City.query.filter_by(is_active=True).order_by(City.is_primary.desc(), City.name).all()


@cars_bp.route("/catalog")
def catalog():
    page = max(1, request.args.get("page", 1, type=int) or 1)
    sort = request.args.get("sort") if request.args.get("sort") in SORT_MODES else "recommended"

    results = rank_cars(_apply_filters(_base_query(), request.args).limit(MAX_RESULTS).all(), sort)
    total = len(results)
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    # Через JS («Показать ещё») подгружаем только следующую порцию; без JS
    # ссылка ?page=N показывает всё с первой страницы по N-ю — ничего не пропадает.
    start = (page - 1) * PER_PAGE if is_ajax else 0
    end = page * PER_PAGE
    items = results[start:end]
    has_more = end < total

    next_args = {**_catalog_args(), "page": page + 1}
    next_url = url_for("cars.catalog", **next_args)

    if is_ajax:
        html = render_template("partials/car_cards.html", cars=items)
        return jsonify(html=html, has_more=has_more, next_url=next_url, total=total)

    cities = _active_cities()
    return render_template(
        "catalog.html", cars=items, cities=cities, body_types=BODY_TYPES,
        total_count=total, has_more=has_more, next_url=next_url, sort=sort,
        sort_modes=SORT_MODES, chips=_filter_chips(cities),
    )


FLAG_LABELS = {
    "with_driver": "С водителем", "no_driver": "Без водителя", "for_wedding": "Для свадьбы",
    "delivery": "Доставка", "automatic": "Автомат", "premium": "Премиум",
}


def _filter_chips(cities):
    """Плашки активных фильтров: нажатие на плашку снимает этот фильтр."""
    args = _catalog_args()
    city_names = {c.slug: c.name for c in cities}
    body_names = dict(BODY_TYPES)
    labels = []
    if args.get("q"):
        labels.append(("q", f"«{args['q'][:30]}»"))
    if args.get("city") in city_names:
        labels.append(("city", city_names[args["city"]]))
    if args.get("body_type") in body_names:
        labels.append(("body_type", body_names[args["body_type"]]))
    if args.get("price_max", "").isdigit():
        labels.append(("price_max", f"до {int(args['price_max']):,} ₽".replace(",", " ")))
    if args.get("year_min", "").isdigit():
        labels.append(("year_min", f"от {args['year_min']} г."))
    if args.get("seats_min", "").isdigit():
        labels.append(("seats_min", f"от {args['seats_min']} мест"))
    for key, label in FLAG_LABELS.items():
        if args.get(key) == "1":
            labels.append((key, label))

    chips = []
    for key, label in labels:
        rest = {k: v for k, v in args.items() if k not in (key, "page")}
        chips.append({"label": label, "url": url_for("cars.catalog", **rest)})
    return chips


@cars_bp.route("/<city_slug>")
def city_page(city_slug):
    if city_slug not in {c["slug"] for c in LAUNCH_CITIES}:
        abort(404)
    city = City.query.filter_by(slug=city_slug, is_active=True).first_or_404()
    cars = _base_query().filter(Car.city_id == city.id).limit(MAX_RESULTS).all()
    cars = rank_cars(cars, "recommended")
    return render_template("city.html", city=city, cars=cars[:PER_PAGE], total=len(cars))


def _is_bot() -> bool:
    ua = request.headers.get("User-Agent", "").lower()
    return not ua or any(marker in ua for marker in BOT_MARKERS)


def _record_view(car):
    """Считаем только реальные просмотры: без ботов и без владельца."""
    if request.method != "GET" or _is_bot():
        return
    if g.current_user and g.current_user.id == car.owner_id:
        return
    ip = request.remote_addr or ""
    ua = request.headers.get("User-Agent", "")
    viewer_hash = hashlib.sha256(f"{ip}{ua}{car.id}".encode()).hexdigest()

    recent_cutoff = datetime.utcnow() - timedelta(hours=6)
    already_counted = View.query.filter(
        View.car_id == car.id, View.viewer_hash == viewer_hash, View.created_at > recent_cutoff,
    ).first()
    if not already_counted:
        db.session.add(View(car_id=car.id, viewer_hash=viewer_hash))
        db.session.commit()


def _car_jsonld(car, absolute_url: str) -> dict:
    data = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": f"{car.title()} — аренда в {car.city.name}",
        "description": (car.description or f"{car.title()} в аренду в {car.city.name}")[:300],
        "url": absolute_url,
    }
    photo = car.primary_photo()
    if photo:
        data["image"] = photo.url if photo.url.startswith("http") else current_app.config["SITE_URL"] + photo.url
    if car.price_per_day:
        data["offers"] = {
            "@type": "Offer", "price": car.price_per_day, "priceCurrency": "RUB",
            "availability": "https://schema.org/InStock", "url": absolute_url,
        }
    return data


@cars_bp.route("/cars/<path:slug>")
def car_detail(slug):
    public_id = slug.rsplit("-", 1)[-1]
    car = (
        Car.query.filter_by(public_id=public_id)
        .options(joinedload(Car.city), selectinload(Car.photos), selectinload(Car.owner).selectinload(User.owner_profile))
        .first_or_404()
    )

    if car.status != "published":
        # Владелец видит своё объявление в любом статусе, остальные — только опубликованное.
        if not g.current_user or g.current_user.id != car.owner_id:
            abort(404)

    canonical_slug = build_car_slug(car)
    if slug != canonical_slug:
        return redirect(url_for("cars.car_detail", slug=canonical_slug), code=301)

    _record_view(car)

    similar = (
        _base_query().filter(Car.owner_id == car.owner_id, Car.id != car.id).limit(4).all()
    )
    if len(similar) < 4:
        taken = {c.id for c in similar} | {car.id}
        same_city = _base_query().filter(Car.city_id == car.city_id, ~Car.id.in_(taken)).limit(8).all()
        similar += rank_cars(same_city, "recommended")[: 4 - len(similar)]
    preload_plans(similar + [car])

    absolute_url = current_app.config["SITE_URL"] + url_for("cars.car_detail", slug=canonical_slug)
    fresh_text, fresh_ok = freshness_label(car)
    return render_template(
        "car_detail.html", car=car, similar_cars=similar, report_reasons=REPORT_REASONS,
        jsonld=_car_jsonld(car, absolute_url), canonical_url=absolute_url,
        fresh_text=fresh_text, fresh_ok=fresh_ok,
        is_own_car=bool(g.current_user and g.current_user.id == car.owner_id),
        body_labels=dict(BODY_TYPES),
    )


# ---------------------------------------------------------------------------
# Связь с владельцем
# ---------------------------------------------------------------------------

def _contact_target(car, channel):
    """Строим ссылку из ПРОВЕРЕННЫХ данных, а не вставляем сырой текст из базы."""
    if channel == "call":
        tel = sanitize_tel(car.phone)
        return f"tel:{tel}" if tel else None
    if channel == "whatsapp":
        message = f"Здравствуйте! Нашёл(а) ваш автомобиль {car.title()} на KAVKAZ-CAR. Он свободен?"
        return whatsapp_link(car.whatsapp, message)
    if channel == "telegram":
        username = normalize_telegram(car.telegram)
        return f"https://t.me/{username[1:]}" if username else None
    return None


@cars_bp.route("/cars/<public_id>/go/<channel>")
@rate_limit("contact_click", methods=("GET",))
def contact_redirect(public_id, channel):
    if channel not in ("call", "whatsapp", "telegram"):
        abort(404)
    car = Car.query.filter_by(public_id=public_id, status="published").first_or_404()
    target = _contact_target(car, channel)
    if not target:
        abort(404)
    if not _is_bot():
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

        message = clean_text(request.form.get("message"), 1000)
        db.session.add(Report(car_id=car.id, reason=reason, message=message or None))
        db.session.commit()
        notify_admin_telegram(f"Новая жалоба на {car.title()} ({car.public_id}): {reason}")
        flash("Спасибо, жалоба отправлена администратору.", "success")
        return redirect(url_for("cars.car_detail", slug=build_car_slug(car)))

    return render_template("report.html", car=car, reasons=REPORT_REASONS)


# ---------------------------------------------------------------------------
# Избранное: для гостей хранится в браузере, после входа переносится в аккаунт
# ---------------------------------------------------------------------------

@cars_bp.route("/favorites")
def favorites():
    cars = []
    if g.current_user:
        cars = (
            _base_query()
            .join(Favorite, Favorite.car_id == Car.id)
            .filter(Favorite.user_id == g.current_user.id)
            .order_by(Favorite.created_at.desc())
            .all()
        )
        preload_plans(cars)
    return render_template("favorites.html", cars=cars)


@cars_bp.route("/favorites/cards")
@rate_limit("guest_cards", methods=("GET",))
def favorites_cards():
    """HTML карточек по списку public_id из localStorage гостя."""
    ids = [i for i in request.args.get("ids", "").split(",") if 4 <= len(i) <= 12][:40]
    cars = _base_query().filter(Car.public_id.in_(ids)).all() if ids else []
    order = {pid: n for n, pid in enumerate(ids)}
    cars.sort(key=lambda c: order.get(c.public_id, 0))
    preload_plans(cars)
    return jsonify(html=render_template("partials/car_cards.html", cars=cars), count=len(cars))


@cars_bp.route("/favorites/sync", methods=["POST"])
@login_required
def favorites_sync():
    """После входа переносит избранное гостя в аккаунт."""
    ids = [i for i in request.form.get("ids", "").split(",") if 4 <= len(i) <= 12][:100]
    if ids:
        cars = Car.query.filter(Car.public_id.in_(ids), Car.status == "published").all()
        have = {f.car_id for f in Favorite.query.filter_by(user_id=g.current_user.id).all()}
        for car in cars:
            if car.id not in have:
                db.session.add(Favorite(user_id=g.current_user.id, car_id=car.id))
        db.session.commit()
    return jsonify(ok=True)


@cars_bp.route("/favorites/toggle/<public_id>", methods=["POST"])
@login_required
def toggle_favorite(public_id):
    car = Car.query.filter_by(public_id=public_id).first_or_404()
    existing = Favorite.query.filter_by(user_id=g.current_user.id, car_id=car.id).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        return jsonify(favorite=False)

    if car.status != "published":
        return jsonify(error="Объявление недоступно."), 404
    db.session.add(Favorite(user_id=g.current_user.id, car_id=car.id))
    db.session.commit()
    return jsonify(favorite=True)


# ---------------------------------------------------------------------------
# Сравнение (до 3 автомобилей)
# ---------------------------------------------------------------------------

@cars_bp.route("/compare")
def compare():
    ids = [pid for pid in request.args.getlist("car") if pid][:3]
    cars = _base_query().filter(Car.public_id.in_(ids)).all() if ids else []
    order = {pid: n for n, pid in enumerate(ids)}
    cars.sort(key=lambda c: order.get(c.public_id, 0))
    return render_template("compare.html", cars=cars)


# ---------------------------------------------------------------------------
# «Помогите выбрать» — прозрачная балльная логика, без AI API
# (раздел 27, 88 ТЗ). Результат — обычная страница с адресом, им можно
# поделиться («вот что мне подобрали»).
# ---------------------------------------------------------------------------

EVENT_PREFERENCES = {
    "wedding": {"for_wedding": True, "body_types": ("premium", "business"), "premium": True},
    "date": {"body_types": ("premium", "business"), "premium": True},
    "photoshoot": {"premium": True, "body_types": ("premium", "suv")},
    "trip": {"body_types": ("suv", "minivan")},
    "airport": {"body_types": ("business", "sedan", "minivan")},
    "event": {"body_types": ("business", "premium")},
    "filming": {"premium": True, "body_types": ("suv", "premium")},
    "ride": {},
}


def _match_score(car, event: str) -> float:
    prefs = EVENT_PREFERENCES.get(event, {})
    score = 0.0
    if prefs.get("for_wedding") and car.for_wedding:
        score += 3
    if car.body_type in prefs.get("body_types", ()):
        score += 2
    if prefs.get("premium") and car.is_premium:
        score += 1.5
    return score


@cars_bp.route("/help-me-choose")
def help_me_choose():
    args = request.args
    event = args.get("event") if args.get("event") in dict(EVENT_TYPES) else "ride"
    budget = args.get("budget", 15000, type=int)
    budget = min(max(budget or 15000, 1000), 200000)
    seats = min(max(args.get("seats", 2, type=int) or 2, 1), 9)
    city_slug = args.get("city") or ""

    form = dict(event=event, budget=budget, seats=seats, city=city_slug)
    results, relaxed = None, False

    if args.get("go") == "1":
        query = _base_query()
        if city_slug:
            query = query.join(City, Car.city_id == City.id).filter(City.slug == city_slug)
        candidates = query.limit(MAX_RESULTS).all()
        preload_plans(candidates)
        candidates = [c for c in candidates if c.seats is None or c.seats >= seats]

        def ranked(cars):
            return sorted(cars, key=lambda c: (-_match_score(c, event), c.price_per_day is None, c.price_per_day or 0))

        strict = ranked([c for c in candidates if c.price_per_day is None or c.price_per_day <= budget])
        results = strict[:6]
        if len(results) < 3:
            # Мало вариантов — честно добавляем чуть дороже и говорим об этом.
            extra = ranked([c for c in candidates if c.price_per_day and budget < c.price_per_day <= budget * 1.3])
            results += extra[: 6 - len(results)]
            relaxed = bool(extra)

    return render_template(
        "help_me_choose.html", event_types=EVENT_TYPES, cities=_active_cities(),
        form=form, results=results, relaxed=relaxed, budget=budget,
    )


# ---------------------------------------------------------------------------
# Публичная страница владельца / проката
# ---------------------------------------------------------------------------

@cars_bp.route("/rental/<public_id>")
def owner_page(public_id):
    profile = OwnerProfile.query.filter_by(public_id=public_id).first_or_404()
    plan = profile.user.current_plan()
    # Страница проката — часть платного тарифа (см. страницу «Тарифы»).
    if profile.user.is_blocked or not plan_limits(plan)["business_page"]:
        abort(404)
    cars = _base_query().filter(Car.owner_id == profile.user_id).all()
    cars = rank_cars(cars, "recommended")
    cities = sorted({c.city.name for c in cars})
    return render_template("owner_page.html", profile=profile, cars=cars, cities=cities, plan=plan)
