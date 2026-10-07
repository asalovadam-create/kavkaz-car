"""
Общие маршруты: главная страница, помощь, юридические документы, служебные эндпоинты.
"""
import os
from xml.sax.saxutils import escape

from flask import Blueprint, Response, current_app, flash, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func, text
from sqlalchemy.orm import joinedload, selectinload

from config import BODY_TYPES, EVENT_TYPES, LAUNCH_CITIES
from models import Car, City, Notification, User, db
from security import login_required
from services import build_car_slug, get_settings, preload_plans, rank_cars

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def home():
    cities = City.query.filter_by(is_active=True).order_by(City.is_primary.desc(), City.name).all()
    counts = dict(
        db.session.query(Car.city_id, func.count(Car.id)).filter(Car.status == "published").group_by(Car.city_id)
    )
    published = (
        Car.query.filter_by(status="published")
        .options(joinedload(Car.city), selectinload(Car.photos), selectinload(Car.owner).selectinload(User.owner_profile))
        .limit(500).all()
    )
    featured = rank_cars(published, "recommended")[:8]
    # На главной не показываем "Свидание" в чипах — остаётся доступным
    # в форме "Помогите выбрать" как менее публичный, приватный вариант.
    home_event_types = [(v, l) for v, l in EVENT_TYPES if v != "date"]
    return render_template(
        "home.html", cities=cities, city_counts=counts, featured_cars=featured,
        event_types=home_event_types, body_types=BODY_TYPES, total_cars=len(published),
    )


@main_bp.route("/help")
def support():
    return render_template("support.html")


@main_bp.route("/legal/terms")
def terms():
    return render_template("legal/terms.html")


@main_bp.route("/legal/privacy")
def privacy():
    return render_template("legal/privacy.html")


@main_bp.route("/legal/listing-rules")
def listing_rules():
    return render_template("legal/listing_rules.html")


@main_bp.route("/health")
def health():
    """Лёгкая проверка «процесс жив» — для Render. Не трогает базу, чтобы
    кратковременная пауза БД не приводила к перезапуску сервиса."""
    return {"status": "ok"}


@main_bp.route("/health/db")
def health_db():
    try:
        db.session.execute(text("SELECT 1"))
        return {"status": "ok", "db": "ok"}
    except Exception:
        db.session.rollback()
        return jsonify(status="error", db="unavailable"), 503


@main_bp.route("/robots.txt")
def robots():
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /owner/",
        "Disallow: /orders/",
        "Disallow: /checkout",
        "Disallow: /payments/",
        "Disallow: /favorites",
        f"Sitemap: {current_app.config['SITE_URL']}/sitemap.xml",
    ]
    return Response("\n".join(lines), mimetype="text/plain")


@main_bp.route("/sitemap.xml")
def sitemap():
    site_url = current_app.config["SITE_URL"]
    entries = [(site_url + "/", None), (site_url + url_for("cars.catalog"), None),
               (site_url + url_for("payments.pricing"), None), (site_url + url_for("main.support"), None),
               (site_url + url_for("main.about"), None)]
    entries += [(f"{site_url}/{c['slug']}", None) for c in LAUNCH_CITIES]

    cars = (
        Car.query.filter_by(status="published").options(joinedload(Car.city)).limit(5000).all()
    )
    for car in cars:
        entries.append((site_url + url_for("cars.car_detail", slug=build_car_slug(car)), car.updated_at))

    body = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for url, updated in entries:
        lastmod = f"<lastmod>{updated:%Y-%m-%d}</lastmod>" if updated else ""
        body.append(f"<url><loc>{escape(url)}</loc>{lastmod}</url>")
    body.append("</urlset>")
    return Response("\n".join(body), mimetype="application/xml")


# ---------------------------------------------------------------------------
# Уведомления пользователя («колокольчик»)
# ---------------------------------------------------------------------------

@main_bp.route("/notifications")
@login_required
def notifications():
    items = (
        Notification.query.filter_by(user_id=g.current_user.id)
        .order_by(Notification.created_at.desc()).limit(100).all()
    )
    unread_ids = [n.id for n in items if not n.is_read]
    page = render_template("notifications.html", items=items, unread_ids=set(unread_ids))
    if unread_ids:  # открыл страницу — значит прочитал (подсветка «новое» уже отрисована)
        Notification.query.filter(Notification.id.in_(unread_ids)).update({"is_read": True}, synchronize_session=False)
        db.session.commit()
    return page


@main_bp.route("/notifications/clear", methods=["POST"])
@login_required
def notifications_clear():
    Notification.query.filter_by(user_id=g.current_user.id, is_read=True).delete(synchronize_session=False)
    db.session.commit()
    flash("Прочитанные уведомления удалены.", "success")
    return redirect(url_for("main.notifications"))


# ---------------------------------------------------------------------------
# О проекте и основателе (данные вводит админ в «Настройках сайта»)
# ---------------------------------------------------------------------------

@main_bp.route("/about")
def about():
    saved = get_settings()
    links = [line.strip() for line in saved.get("founder_links", "").splitlines() if line.strip().startswith("https://")]
    site_url = current_app.config["SITE_URL"]
    name = saved.get("founder_name", "").strip()
    organization = {
        "@context": "https://schema.org", "@type": "Organization", "name": "KAVKAZ-CAR",
        "url": site_url, "description": "Платформа аренды автомобилей на Северном Кавказе.",
    }
    if name:
        person = {"@type": "Person", "name": name, "url": f"{site_url}/about"}
        if saved.get("founder_role"):
            person["jobTitle"] = saved["founder_role"].strip()
        if links:
            person["sameAs"] = links
        organization["founder"] = person
    return render_template("about.html", name=name, role=saved.get("founder_role", "").strip(),
                           bio=saved.get("founder_bio", "").strip(), links=links, jsonld=organization)


# ---------------------------------------------------------------------------
# Установка на телефон как приложение (PWA)
# ---------------------------------------------------------------------------

@main_bp.route("/manifest.webmanifest")
def manifest():
    """Описание приложения: имя, цвета, иконки. Благодаря ему ярлык на экране «Домой» открывается
    во весь экран, без адресной строки браузера. Админка здесь не упоминается."""
    v = current_app.config["ASSET_VERSION"]

    def icon(file, size, purpose="any"):
        return {"src": f"/static/icons/{file}?v={v}", "sizes": f"{size}x{size}", "type": "image/png", "purpose": purpose}

    data = {
        "id": "/",
        "name": "KAVKAZ-CAR — аренда авто на Кавказе",
        "short_name": "KAVKAZ-CAR",
        "description": "Аренда автомобилей на Северном Кавказе: цены и условия видны сразу, звонок владельцу в одно касание.",
        "start_url": "/?source=app",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#0b0c0e",
        "theme_color": "#0b0c0e",
        "lang": "ru",
        "categories": ["travel", "business"],
        "icons": [icon("icon-192.png", 192), icon("icon-512.png", 512), icon("icon-maskable-512.png", 512, "maskable")],
        "shortcuts": [
            {"name": "Каталог", "url": "/catalog?source=shortcut", "icons": [icon("icon-192.png", 192)]},
            {"name": "Подобрать автомобиль", "url": "/help-me-choose?source=shortcut", "icons": [icon("icon-192.png", 192)]},
            {"name": "Избранное", "url": "/favorites?source=shortcut", "icons": [icon("icon-192.png", 192)]},
        ],
    }
    response = jsonify(data)
    response.mimetype = "application/manifest+json"
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@main_bp.route("/sw.js")
def service_worker():
    """Service worker должен отдаваться с корня сайта, иначе он не сможет управлять всеми страницами."""
    path = os.path.join(current_app.static_folder, "js", "sw.js")
    with open(path, encoding="utf-8") as handle:
        body = handle.read().replace("__VERSION__", current_app.config["ASSET_VERSION"])
    response = Response(body, mimetype="text/javascript")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@main_bp.route("/offline")
def offline():
    return render_template("offline.html")
