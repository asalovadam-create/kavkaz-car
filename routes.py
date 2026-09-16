"""
Общие маршруты: главная страница, юридические документы, служебные эндпоинты.
"""
from flask import Blueprint, Response, current_app, render_template, url_for

from config import BODY_TYPES, EVENT_TYPES, LAUNCH_CITIES
from models import Car, City
from services import build_car_slug, sort_key_for_catalog

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def home():
    cities = City.query.filter_by(is_active=True).order_by(City.is_primary.desc(), City.name).all()
    featured = Car.query.filter_by(status="published").all()
    featured.sort(key=sort_key_for_catalog)
    # На главной не показываем "Свидание" в чипах — остаётся доступным
    # в форме "Помогите выбрать" как менее публичный, приватный вариант.
    home_event_types = [(v, l) for v, l in EVENT_TYPES if v != "date"]
    return render_template(
        "home.html", cities=cities, featured_cars=featured[:12], event_types=home_event_types,
        body_types=BODY_TYPES,
    )


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
    return {"status": "ok"}


@main_bp.route("/robots.txt")
def robots():
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /owner/",
        "Disallow: /admin/",
        f"Sitemap: {current_app.config['SITE_URL']}/sitemap.xml",
    ]
    return Response("\n".join(lines), mimetype="text/plain")


@main_bp.route("/sitemap.xml")
def sitemap():
    site_url = current_app.config["SITE_URL"]
    urls = [site_url, f"{site_url}/catalog"]
    urls += [f"{site_url}/{c['slug']}" for c in LAUNCH_CITIES]

    cars = Car.query.filter_by(status="published").limit(5000).all()
    for car in cars:
        urls.append(f"{site_url}{url_for('cars.car_detail', slug=build_car_slug(car))}")

    body = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    body += [f"<url><loc>{u}</loc></url>" for u in urls]
    body.append("</urlset>")
    return Response("\n".join(body), mimetype="application/xml")
