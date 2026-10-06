"""
Общие маршруты: главная страница, помощь, юридические документы, служебные эндпоинты.
"""
from xml.sax.saxutils import escape

from flask import Blueprint, Response, current_app, jsonify, render_template, url_for
from sqlalchemy import func, text
from sqlalchemy.orm import joinedload, selectinload

from config import BODY_TYPES, EVENT_TYPES, LAUNCH_CITIES
from models import Car, City, User, db
from services import build_car_slug, preload_plans, rank_cars

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
        "Disallow: /admin/",
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
               (site_url + url_for("payments.pricing"), None), (site_url + url_for("main.support"), None)]
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
