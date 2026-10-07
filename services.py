"""
Сервисный слой KAVKAZ-CAR: всё, что не является ни моделью, ни маршрутом.

Здесь же — абстракция хранилища фотографий (раздел 12 ТЗ): PostgreSQL хранит
только URL/metadata, сами файлы лежат в object storage, и провайдер можно
поменять, не переписывая остальной проект.
"""
import io
import json
import os
import re
import secrets
import urllib.request
import uuid
from datetime import datetime, timedelta

from flask import current_app

from config import (
    LISTING_STALE_AFTER_DAYS, PLAN_ORDER, PLANS, SEARCH_SYNONYMS,
)
from validators import like_escape


# ---------------------------------------------------------------------------
# Обработка изображений (Pillow) — раздел 13 ТЗ
# ---------------------------------------------------------------------------

MAIN_MAX_DIMENSION = 1600
THUMB_MAX_DIMENSION = 400


def optimize_image(image):
    """Принимает PIL.Image (уже провалидированный в security.py) и
    возвращает (основной_webp_bytes, миниатюра_webp_bytes).

    Что делаем: убираем EXIF, ограничиваем разрешение, конвертируем в WebP —
    чтобы вместо 20 MB оригинала карточка весила ~200-300 KB.
    """
    from PIL import Image, ImageOps

    image = ImageOps.exif_transpose(image)  # учитываем поворот с камеры до удаления EXIF
    if image.mode in ("RGBA", "LA", "P"):
        # Прозрачный PNG: кладём на белый фон, а не на чёрный (так бывает при convert("RGB")).
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        image = background
    elif image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    main = _resize_within(image, MAIN_MAX_DIMENSION)
    thumb = _resize_within(image, THUMB_MAX_DIMENSION)

    main_bytes = _to_webp_bytes(main, quality=82)
    thumb_bytes = _to_webp_bytes(thumb, quality=75)
    return main_bytes, thumb_bytes


def _resize_within(image, max_dimension: int):
    width, height = image.size
    scale = min(1.0, max_dimension / max(width, height))
    if scale >= 1.0:
        return image.copy()
    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return image.resize(new_size, resample=3)  # BICUBIC — обновляемо без доп. зависимостей


def _to_webp_bytes(image, quality: int) -> bytes:
    buffer = io.BytesIO()
    # save() без параметра exif намеренно — метаданные не переносятся.
    image.save(buffer, format="WEBP", quality=quality, method=4)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Хранилище фото — сменный провайдер
# ---------------------------------------------------------------------------

class ImageStorage:
    """Интерфейс. Конкретные провайдеры реализуют upload_bytes/delete."""

    def upload_bytes(self, data: bytes, filename_hint: str) -> tuple[str, str]:
        """Возвращает (public_url, storage_public_id)."""
        raise NotImplementedError

    def delete(self, storage_public_id: str) -> None:
        raise NotImplementedError


class LocalImageStorage(ImageStorage):
    """Хранение на локальном диске — только для разработки/тестов.

    ВАЖНО: диск Render эфемерный (раздел 122 ТЗ) — в production обязательно
    использовать CloudinaryImageStorage или совместимый object storage.
    """

    def __init__(self, upload_dir: str):
        self.upload_dir = upload_dir
        os.makedirs(upload_dir, exist_ok=True)

    def upload_bytes(self, data: bytes, filename_hint: str) -> tuple[str, str]:
        public_id = f"{uuid.uuid4().hex}"
        filename = f"{public_id}.webp"
        path = os.path.join(self.upload_dir, filename)
        with open(path, "wb") as fh:
            fh.write(data)
        return f"/static/uploads/{filename}", public_id

    def delete(self, storage_public_id: str) -> None:
        path = os.path.join(self.upload_dir, f"{storage_public_id}.webp")
        if os.path.exists(path):
            os.remove(path)


class CloudinaryImageStorage(ImageStorage):
    """Object storage поверх Cloudinary. Такой же интерфейс позволяет
    позже заменить на Selectel/S3-совместимое хранилище без изменений
    в остальном коде — меняется только этот класс и точка сборки в app.py.
    """

    def __init__(self, cloud_name: str, api_key: str, api_secret: str):
        import cloudinary

        cloudinary.config(
            cloud_name=cloud_name, api_key=api_key, api_secret=api_secret, secure=True
        )

    def upload_bytes(self, data: bytes, filename_hint: str) -> tuple[str, str]:
        import cloudinary.uploader

        result = cloudinary.uploader.upload(
            io.BytesIO(data),
            folder="kavkaz-car/cars",
            resource_type="image",
            format="webp",
        )
        return result["secure_url"], result["public_id"]

    def delete(self, storage_public_id: str) -> None:
        import cloudinary.uploader

        cloudinary.uploader.destroy(storage_public_id)


def get_image_storage() -> ImageStorage:
    provider = current_app.config.get("UPLOAD_PROVIDER", "local")
    if provider == "cloudinary":
        return CloudinaryImageStorage(
            current_app.config["CLOUDINARY_CLOUD_NAME"],
            current_app.config["CLOUDINARY_API_KEY"],
            current_app.config["CLOUDINARY_API_SECRET"],
        )
    upload_dir = os.path.join(current_app.root_path, "static", "uploads")
    return LocalImageStorage(upload_dir)


# ---------------------------------------------------------------------------
# Тарифы и лимиты
# ---------------------------------------------------------------------------

def plan_limits(plan: str) -> dict:
    return PLANS.get(plan, PLANS["free"])


def preload_plans(cars) -> None:
    """Одним запросом подгружает активные подписки владельцев переданных
    объявлений и кладёт их в кеш User. Без этого каждая карточка в каталоге
    делала 3–6 запросов к базе (проблема N+1) — на 200 машинах сайт «задыхался»."""
    from models import Subscription, db

    owners = {}
    for car in cars:
        if car.owner is not None and not hasattr(car.owner, "_sub_cache"):
            owners[car.owner.id] = car.owner
    if not owners:
        return
    now = datetime.utcnow()
    subs = (
        Subscription.query.filter(Subscription.owner_id.in_(list(owners)), Subscription.status == "active")
        .filter(db.or_(Subscription.expires_at.is_(None), Subscription.expires_at > now))
        .order_by(Subscription.started_at.desc())
        .all()
    )
    latest = {}
    for sub in subs:
        latest.setdefault(sub.owner_id, sub)  # список уже от новых к старым
    for owner_id, owner in owners.items():
        owner._sub_cache = latest.get(owner_id)


def active_cars_count(user) -> int:
    return sum(1 for car in user.cars if car.status in ("draft", "pending", "published", "paused"))


def can_add_car(user) -> bool:
    plan = user.current_plan()
    return active_cars_count(user) < plan_limits(plan)["max_cars"]


def can_add_photo(car, current_count: int) -> bool:
    plan = car.owner.current_plan()
    return current_count < plan_limits(plan)["max_photos_per_car"]


def boosts_used_last_30d(user) -> int:
    from models import BoostLog
    since = datetime.utcnow() - timedelta(days=30)
    return BoostLog.query.filter(BoostLog.owner_id == user.id, BoostLog.created_at >= since).count()


def boosts_remaining(user) -> int:
    limit = plan_limits(user.current_plan())["boosts_per_month"]
    if limit <= 0:
        return 0
    return max(0, limit - boosts_used_last_30d(user))


# ---------------------------------------------------------------------------
# Поиск: нормализация запроса (раздел 22, 65 ТЗ)
# ---------------------------------------------------------------------------

def expand_search_terms(raw: str) -> list[list[str]]:
    """Разбирает строку поиска на группы. Группы связаны через AND, варианты
    внутри группы — через OR. «гелик грозный» -> [[g-class, g63, ...], [грозный]]."""
    text = re.sub(r"\s+", " ", (raw or "").strip().lower())[:80]
    if not text:
        return []
    groups: list[list[str]] = []

    # Сначала многословные синонимы («рендж ровер»), потом отдельные слова.
    for phrase in sorted((k for k in SEARCH_SYNONYMS if " " in k), key=len, reverse=True):
        if phrase in text:
            groups.append(_as_list(SEARCH_SYNONYMS[phrase]))
            text = text.replace(phrase, " ")

    for token in text.split():
        if token in SEARCH_SYNONYMS:
            groups.append(_as_list(SEARCH_SYNONYMS[token]))
        else:
            groups.append([token])
    return groups[:6]


def _as_list(value) -> list[str]:
    return list(value) if isinstance(value, (list, tuple)) else [value]


def normalize_search_query(raw: str) -> str:
    """Совместимость со старым кодом: первый вариант первой группы."""
    groups = expand_search_terms(raw)
    return groups[0][0] if groups else ""


def build_car_slug(car) -> str:
    """SEO-адрес вида mercedes-w223-grozny-ab12cd9f (раздел 68 ТЗ)."""
    parts = f"{car.brand}-{car.model}-{car.city.slug}".lower()
    parts = re.sub(r"[^a-z0-9\-]+", "-", parts).strip("-")
    parts = re.sub(r"-{2,}", "-", parts)
    return f"{parts}-{car.public_id}"


def format_price(value) -> str:
    if value is None:
        return "Цена по запросу"
    return f"{value:,.0f}".replace(",", " ") + " ₽"


# ---------------------------------------------------------------------------
# Удаление объявлений
# ---------------------------------------------------------------------------

def purge_car(car) -> str:
    """Полностью удаляет объявление: фото в хранилище, просмотры, обращения,
    избранное, жалобы, поднятия и саму запись. Раньше удаление падало с ошибкой
    500, если у объявления был хотя бы один просмотр (внешние ключи в БД).
    Возвращает название для журнала. Вызывающий делает commit."""
    from models import BoostLog, ContactClick, Favorite, Report, View, db

    title = car.title()
    storage = get_image_storage()
    for photo in list(car.photos):
        if photo.storage_public_id:
            try:
                storage.delete(photo.storage_public_id)
            except Exception:  # не должны терять удаление из-за недоступного хранилища
                current_app.logger.warning("Не удалось удалить файл %s", photo.storage_public_id)
    for model in (View, ContactClick, Favorite, Report, BoostLog):
        db.session.query(model).filter(model.car_id == car.id).delete(synchronize_session=False)
    db.session.delete(car)
    return title


# ---------------------------------------------------------------------------
# Промокоды и активация тарифов
# ---------------------------------------------------------------------------

def generate_promo_code(plan: str) -> str:
    suffix = secrets.token_hex(3).upper()
    return f"KC-{plan.upper()}-{suffix}"


def _plan_rank(plan: str) -> int:
    return PLAN_ORDER.index(plan) if plan in PLAN_ORDER else 0


def higher_plan_active(user, plan: str):
    """Подписка более высокого уровня, действующая сейчас, либо None."""
    sub = user.active_subscription()
    if sub and sub.plan != "free" and _plan_rank(sub.plan) > _plan_rank(plan):
        return sub
    return None


def activate_plan(user, plan: str, days: int, *, provider: str, transaction_id: str | None = None,
                  promo_code_id: int | None = None, admin_id: int | None = None):
    """Включает тариф пользователю. Правила:
      • тот же тариф — срок продлевается от даты окончания текущего;
      • повышение (PRO -> BUSINESS) — остаток оплаченных дней пересчитывается
        по стоимости и добавляется, чтобы человек ничего не терял;
      • иначе — новый тариф начинается сейчас.
    Вызывающий делает db.session.commit()."""
    from models import Subscription, db

    now = datetime.utcnow()
    expires_at = None if plan == "free" else now + timedelta(days=days)

    current = user.active_subscription()
    if current and current.plan != "free" and current.expires_at and plan != "free":
        remaining_days = max(0.0, (current.expires_at - now).total_seconds() / 86400)
        if current.plan == plan:
            expires_at = max(current.expires_at, now) + timedelta(days=days)
        elif _plan_rank(plan) > _plan_rank(current.plan):
            new_price = PLANS[plan]["price"] or 1
            bonus_days = int(remaining_days * PLANS[current.plan]["price"] / new_price)
            expires_at = now + timedelta(days=days + bonus_days)
    if current:
        current.status = "cancelled"

    subscription = Subscription(
        owner_id=user.id, plan=plan, status="active", started_at=now, expires_at=expires_at,
        payment_provider=provider, payment_status="paid", transaction_id=transaction_id,
        promo_code_id=promo_code_id, activated_by_admin_id=admin_id,
    )
    db.session.add(subscription)
    user.invalidate_plan_cache()
    if plan != "free":
        until = f" до {expires_at:%d.%m.%Y}" if expires_at else ""
        notify_user(user.id, f"Тариф {PLANS[plan]['name']} активирован{until}",
                    "Новые возможности уже доступны в кабинете.", kind="plan", link="/owner/subscription")
        restored = restore_expired_cars(user)
        if restored:
            notify_user(user.id, f"Возвращено в каталог объявлений: {restored}",
                        "Объявления, приостановленные после окончания прошлого тарифа, снова опубликованы.",
                        kind="car", link="/owner/dashboard")
    return subscription


def redeem_promo_code(promo, user):
    """Активирует промокод. Вызывающий обязан сделать commit.
    Код гасится атомарным UPDATE ... WHERE used_at IS NULL, поэтому два
    одновременных запроса не смогут использовать один код дважды."""
    from models import PromoCode, db

    now = datetime.utcnow()
    claimed = (
        db.session.query(PromoCode)
        .filter(PromoCode.id == promo.id, PromoCode.used_at.is_(None))
        .update({"used_at": now, "used_by_id": user.id}, synchronize_session=False)
    )
    if not claimed:
        return None
    return activate_plan(
        user, promo.plan, promo.duration_days, provider="promo_code", promo_code_id=promo.id,
    )


# ---------------------------------------------------------------------------
# Уведомления пользователям
# ---------------------------------------------------------------------------

def notify_user(user_id: int, title: str, body: str = "", *, kind: str = "system", link: str | None = None) -> None:
    """Кладёт уведомление в «колокольчик» пользователя. Commit делает вызывающий."""
    from models import Notification, db
    db.session.add(Notification(user_id=user_id, kind=kind, title=title[:160], body=body or None, link=link))


# ---------------------------------------------------------------------------
# Что происходит с объявлениями, когда тариф заканчивается
# ---------------------------------------------------------------------------

LIVE_STATUSES = ("published", "paused", "pending")


def enforce_plan_limits(user) -> int:
    """Приостанавливает объявления сверх лимита ТЕКУЩЕГО тарифа (ничего не удаляет).

    Остаются самые ранние объявления — их столько, сколько разрешает тариф (на бесплатном — 1).
    Более поздние (созданные уже под платный тариф) получают статус «expired» и пропадают из
    каталога. Вернуть их можно, продлив тариф: см. restore_expired_cars. Возвращает, сколько
    объявлений приостановлено. Commit делает вызывающий."""
    from models import Car

    limit = plan_limits(user.current_plan())["max_cars"]
    live = (
        Car.query.filter(Car.owner_id == user.id, Car.status.in_(LIVE_STATUSES))
        .order_by(Car.created_at.asc(), Car.id.asc()).all()
    )
    changed = 0
    for car in live[limit:]:
        car.status = "expired"
        changed += 1
    return changed


def restore_expired_cars(user) -> int:
    """После покупки тарифа возвращает приостановленные объявления (самые ранние — первыми),
    пока хватает лимита нового тарифа."""
    from models import Car

    limit = plan_limits(user.current_plan())["max_cars"]
    live = Car.query.filter(Car.owner_id == user.id, Car.status.in_(LIVE_STATUSES)).count()
    room = max(0, limit - live)
    if room == 0:
        return 0
    expired = (
        Car.query.filter_by(owner_id=user.id, status="expired")
        .order_by(Car.created_at.asc(), Car.id.asc()).limit(room).all()
    )
    for car in expired:
        car.status = "published"
    return len(expired)


def sweep_expired_subscriptions() -> int:
    """Находит подписки, срок которых вышел, закрывает их и приостанавливает лишние объявления.
    Вызывается из приложения не чаще раза в минуту (см. app.py) — отдельный планировщик не нужен.
    Возвращает число обработанных владельцев."""
    from models import Subscription, User, db

    now = datetime.utcnow()
    due = (
        Subscription.query.filter(
            Subscription.status == "active", Subscription.plan != "free",
            Subscription.expires_at.isnot(None), Subscription.expires_at <= now,
        ).all()
    )
    owners = set()
    for sub in due:
        sub.status = "expired"
        owners.add(sub.owner_id)
    for owner_id in owners:
        user = db.session.get(User, owner_id)
        if user is None:
            continue
        user.invalidate_plan_cache()
        paused = enforce_plan_limits(user)
        if user.current_plan() == "free":
            body = (f"Приостановлено объявлений: {paused}. Они не удалены — вернутся в каталог после продления тарифа."
                    if paused else "Ваши объявления остаются в каталоге в пределах бесплатного тарифа.")
            notify_user(user.id, "Тариф закончился", body, kind="plan", link="/pricing")
    if owners:
        db.session.commit()
    return len(owners)


# ---------------------------------------------------------------------------
# Настройки сайта, которые админ редактирует из панели
# ---------------------------------------------------------------------------

_settings_cache: dict = {"at": 0.0, "data": {}}
SETTING_KEYS = (
    "support_phone", "support_telegram", "support_whatsapp",
    "founder_name", "founder_role", "founder_bio", "founder_links",
)


def get_settings() -> dict:
    """Все настройки одним запросом, с кэшем на 60 секунд (не нагружает базу на каждый показ страницы)."""
    import time
    from models import SiteSetting

    if time.time() - _settings_cache["at"] < 60:
        return _settings_cache["data"]
    try:
        data = {row.key: row.value for row in SiteSetting.query.all()}
    except Exception:
        from models import db
        db.session.rollback()
        data = _settings_cache["data"]
    _settings_cache.update(at=time.time(), data=data)
    return data


def save_settings(values: dict) -> None:
    from models import SiteSetting, db

    for key, value in values.items():
        if key not in SETTING_KEYS:
            continue
        row = db.session.get(SiteSetting, key)
        if row is None:
            db.session.add(SiteSetting(key=key, value=value))
        else:
            row.value = value
    db.session.commit()
    _settings_cache["at"] = 0.0  # сбросить кэш


# ---------------------------------------------------------------------------
# «Лучшее предложение» — прозрачная эвристика, не завязанная только на оплату
# (раздел 29 ТЗ)
# ---------------------------------------------------------------------------

def best_value_score(car) -> float:
    score = 0.0
    if car.photos and len(car.photos) >= 5:
        score += 1.0
    profile = car.owner.owner_profile
    if profile and profile.phone_verified:
        score += 1.0
    if profile:
        score += profile.profile_completion_percent() / 100
    if not car.is_stale():
        score += 1.0
    score += plan_limits(car.owner.current_plan())["priority"] * 0.5  # платный тариф даёт небольшой вес, не решающий
    return score


def notify_admin_telegram(message: str) -> None:
    """Необязательное уведомление админу (жалобы, заявки на оплату).

    Не настроено по умолчанию — если TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID не
    заданы, функция тихо ничего не делает. Ошибка отправки не должна ронять
    запрос пользователя, поэтому исключения гасятся здесь."""
    token = current_app.config.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = current_app.config.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        payload = json.dumps({"chat_id": chat_id, "text": message[:3500]}).encode()
        request_ = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(request_, timeout=4).close()  # noqa: S310 — фиксированный https-адрес
    except Exception:
        current_app.logger.warning("Не удалось отправить уведомление в Telegram")


def sort_key_for_catalog(car):
    """Ключ сортировки «рекомендуемые».

    Среди ОДНОВРЕМЕННО поднятых объявлений порядок каждый час честно
    перемешивается (детерминированно внутри часа — список не «прыгает» при
    обновлении страницы). Так топ не превращается в «кто раньше поднял — тот и
    главный» и не зависит только от цены тарифа. Естественный ограничитель —
    лимит поднятий по тарифу (0 / 1 / 4 в месяц)."""
    boosted = 1 if car.is_boosted() else 0

    rotation_key = 0
    if boosted:
        import hashlib
        current_hour_bucket = datetime.utcnow().strftime("%Y%m%d%H")
        digest = hashlib.md5(f"{car.id}-{current_hour_bucket}".encode()).hexdigest()  # noqa: S324 — не для безопасности
        rotation_key = int(digest, 16) % 1000

    return (
        -boosted,
        rotation_key,
        -plan_limits(car.owner.current_plan())["priority"],
        -best_value_score(car),
        -car.created_at.timestamp(),
    )


SORT_MODES = {
    "recommended": "Рекомендуемые",
    "price_asc": "Сначала дешевле",
    "price_desc": "Сначала дороже",
    "newest": "Сначала новые",
}


def rank_cars(cars: list, sort: str | None = "recommended") -> list:
    """Сортирует список объявлений выбранным способом (с предзагрузкой тарифов)."""
    preload_plans(cars)
    if sort == "price_asc":
        return sorted(cars, key=lambda c: (c.price_per_day is None, c.price_per_day or 0, sort_key_for_catalog(c)))
    if sort == "price_desc":
        return sorted(cars, key=lambda c: (c.price_per_day is None, -(c.price_per_day or 0), sort_key_for_catalog(c)))
    if sort == "newest":
        return sorted(cars, key=lambda c: -c.created_at.timestamp())
    return sorted(cars, key=sort_key_for_catalog)


def freshness_label(car):
    """Подпись актуальности для публичной страницы: (текст | None, всё_хорошо).
    Тариф владельца публично НЕ упоминается; для платных тарифов подпись не показываем."""
    if car.owner.current_plan() != "free":
        return None, True
    days = (datetime.utcnow() - car.last_confirmed_at).days
    if days <= 0:
        return "Актуальность подтверждена сегодня", True
    if days == 1:
        return "Актуальность подтверждена вчера", True
    if days <= LISTING_STALE_AFTER_DAYS:
        return f"Актуальность подтверждена {days} дн. назад", True
    return "Цена и наличие могли измениться — уточните у владельца", False
