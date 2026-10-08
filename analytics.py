"""Учёт посетителей: кто заходит на сайт, с какого устройства, с какого IP.

Принципы: пишем не на каждый запрос, а не чаще раза в 5 минут на посетителя; боты не считаются;
записи старше 90 дней удаляются; админка и служебные запросы в учёт не попадают.
Идентификатор посетителя — случайная строка в cookie kc_vid (она не связана с аккаунтом).
"""
import random
import re
import secrets
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse

from flask import Blueprint, current_app, g, request

from models import SiteVisitor, db
from security import rate_limit
from uaparse import iphone_guess, is_bot, parse_ua

analytics_bp = Blueprint("analytics", __name__)

COOKIE = "kc_vid"
_VID_RE = re.compile(r"^[0-9a-f]{24}$")
WRITE_EVERY = 300        # секунд между записями об одном посетителе
SESSION_GAP = 1800       # пауза, после которой визит считается новым
RETENTION_DAYS = 90
_recent: dict[str, float] = {}


def track_visit(response):
    """after_request: фиксирует просмотр HTML-страницы. Любая ошибка здесь не должна ломать сайт."""
    try:
        if request.method != "GET" or response.status_code != 200 or response.mimetype != "text/html":
            return response
        if request.endpoint in (None, "static"):
            return response
        prefix = current_app.config.get("ADMIN_PREFIX", "")
        if prefix and (request.path == prefix or request.path.startswith(prefix + "/")):
            return response
        ua = request.headers.get("User-Agent", "")
        if is_bot(ua):
            return response

        vid = request.cookies.get(COOKIE, "")
        is_new_cookie = not _VID_RE.match(vid)
        if is_new_cookie:
            vid = secrets.token_hex(12)

        now = time.time()
        last = _recent.get(vid)
        if last is None or now - last > WRITE_EVERY:
            if len(_recent) > 5000:
                _recent.clear()
            _recent[vid] = now
            _record(vid, ua)

        if is_new_cookie:
            response.set_cookie(
                COOKIE, vid, max_age=365 * 24 * 3600, httponly=True, samesite="Lax", secure=request.is_secure,
            )
    except Exception:
        db.session.rollback()
        current_app.logger.warning("Не удалось записать посещение", exc_info=True)
    return response


def _record(vid: str, ua: str) -> None:
    now = datetime.utcnow()
    ip = (request.remote_addr or "")[:45]
    path = request.path[:200]
    user_id = g.current_user.id if g.get("current_user") else None

    row = SiteVisitor.query.filter_by(visitor_id=vid).first()
    if row:
        if (now - row.last_seen).total_seconds() > SESSION_GAP:
            row.visits = (row.visits or 1) + 1
        row.last_seen = now
        row.ip = ip
        row.last_path = path
        row.user_agent = ua[:400]
        if user_id:
            row.user_id = user_id
    else:
        info = parse_ua(ua)
        referrer = ""
        ref = urlparse(request.referrer or "")
        if ref.netloc and ref.netloc != request.host:
            referrer = (ref.netloc + ref.path)[:200]
        db.session.add(SiteVisitor(
            visitor_id=vid, ip=ip, user_agent=ua[:400], device_type=info["device_type"], brand=info["brand"],
            model=info["model"], os_name=info["os_name"], os_version=info["os_version"], browser=info["browser"],
            user_id=user_id, referrer=referrer or None, first_path=path, last_path=path,
            visits=1, first_seen=now, last_seen=now,
        ))
        if random.random() < 0.02:  # изредка чистим старые записи
            SiteVisitor.query.filter(
                SiteVisitor.last_seen < now - timedelta(days=RETENTION_DAYS)
            ).delete(synchronize_session=False)
    db.session.commit()


@analytics_bp.route("/api/visit-info", methods=["POST"])
@rate_limit("visit_info")
def visit_info():
    """Браузер один раз присылает размер экрана: по нему определяем группу моделей iPhone."""
    vid = request.cookies.get(COOKIE, "")
    if not _VID_RE.match(vid):
        return ("", 204)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return ("", 204)
    try:
        width, height, dpr = int(data.get("w")), int(data.get("h")), float(data.get("dpr"))
    except (TypeError, ValueError):
        return ("", 204)
    if not (200 <= width <= 4000 and 200 <= height <= 8000 and 1 <= dpr <= 5):
        return ("", 204)

    row = SiteVisitor.query.filter_by(visitor_id=vid).first()
    if not row:
        return ("", 204)
    row.screen = f"{width}x{height} @{dpr:g}x"
    if row.brand == "Apple" and (row.model or "").startswith("iPhone"):
        guess = iphone_guess(width, height, dpr)
        if guess:
            row.model = guess
    db.session.commit()
    return ("", 204)
