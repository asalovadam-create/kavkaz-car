"""
Сервисный слой KAVKAZ-CAR: всё, что не является ни моделью, ни маршрутом.

Здесь же — абстракция хранилища фотографий (раздел 12 ТЗ): PostgreSQL хранит
только URL/metadata, сами файлы лежат в object storage, и провайдер можно
поменять, не переписывая остальной проект.
"""
import io
import os
import re
import secrets
import uuid
from datetime import datetime

from flask import current_app

from config import PLANS, SEARCH_SYNONYMS


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
    if image.mode not in ("RGB", "L"):
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


def active_cars_count(user) -> int:
    return sum(1 for car in user.cars if car.status in ("draft", "pending", "published", "paused"))


def can_add_car(user) -> bool:
    plan = user.current_plan()
    return active_cars_count(user) < plan_limits(plan)["max_cars"]


def can_add_photo(car, current_count: int) -> bool:
    plan = car.owner.current_plan()
    return current_count < plan_limits(plan)["max_photos_per_car"]


def boosts_remaining(user) -> int:
    sub = user.active_subscription()
    plan = user.current_plan()
    limit = plan_limits(plan)["boosts_per_month"]
    used = sub.boosts_used_this_period if sub else 0
    return max(0, limit - used)


# ---------------------------------------------------------------------------
# Поиск: нормализация запроса (раздел 22, 65 ТЗ)
# ---------------------------------------------------------------------------

def normalize_search_query(raw: str) -> str:
    text = (raw or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return SEARCH_SYNONYMS.get(text, text)


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
# Промокоды
# ---------------------------------------------------------------------------

def generate_promo_code(plan: str) -> str:
    suffix = secrets.token_hex(3).upper()
    return f"KC-{plan.upper()}-{suffix}"


def redeem_promo_code(promo, user):
    """Активирует промокод для пользователя. Возвращает новую подписку.

    Вызывающий код обязан обернуть это в db.session.commit()."""
    from models import Subscription, db

    now = datetime.utcnow()
    from datetime import timedelta

    expires_at = now + timedelta(days=promo.duration_days)

    # деактивируем предыдущую активную подписку
    current = user.active_subscription()
    if current:
        current.status = "cancelled"

    subscription = Subscription(
        owner_id=user.id,
        plan=promo.plan,
        status="active",
        started_at=now,
        expires_at=expires_at,
        payment_provider="promo_code",
        payment_status="paid",
        promo_code_id=promo.id,
    )
    promo.used_at = now
    promo.used_by_id = user.id
    db.session.add(subscription)
    return subscription


# ---------------------------------------------------------------------------
# «Лучшее предложение» — прозрачная эвристика, не завязанная только на оплату
# (раздел 29 ТЗ)
# ---------------------------------------------------------------------------

def best_value_score(car) -> float:
    score = 0.0
    if car.photos and len(car.photos) >= 5:
        score += 1.0
    if car.owner.owner_profile and car.owner.owner_profile.phone_verified:
        score += 1.0
    if car.owner.owner_profile:
        score += car.owner.owner_profile.profile_completion_percent() / 100
    if not car.is_stale():
        score += 1.0
    plan = car.owner.current_plan()
    score += plan_limits(plan)["priority"] * 0.5  # платный тариф даёт небольшой вес, не решающий
    return score


def sort_key_for_catalog(car):
    """Ключ сортировки каталога: сначала буст, затем приоритет тарифа,
    затем best-value скор, затем свежесть."""
    boosted = 1 if (car.is_boosted_until and car.is_boosted_until > datetime.utcnow()) else 0
    plan = car.owner.current_plan()
    return (
        -boosted,
        -plan_limits(plan)["priority"],
        -best_value_score(car),
        -car.created_at.timestamp(),
    )
