"""
Модели базы данных KAVKAZ-CAR.

Используется Flask-SQLAlchemy поверх PostgreSQL. Все запросы идут через
ORM — это автоматически даёт параметризацию (защита от SQL injection)
и понятные связи между таблицами (раздел 89 ТЗ).
"""
import secrets
from datetime import datetime, timedelta

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint

db = SQLAlchemy()


def _short_id(length: int = 8) -> str:
    """Короткий непредсказуемый публичный идентификатор для URL.

    Используется вместо числового id в публичных ссылках, чтобы нельзя
    было перебрать /cars/1, /cars/2, ... (раздел 47 ТЗ, IDOR/enumeration).
    """
    alphabet = "abcdefghijkmnopqrstuvwxyz23456789"  # без похожих символов
    return "".join(secrets.choice(alphabet) for _ in range(length))


class User(db.Model):
    """Единый аккаунт клиента/владельца. Роль не хранит привилегий выше
    обычного пользователя — админка использует отдельную модель AdminUser."""

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(20), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(120), nullable=True)
    age = db.Column(db.Integer, nullable=True)
    is_blocked = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_login_at = db.Column(db.DateTime, nullable=True)

    owner_profile = db.relationship(
        "OwnerProfile", back_populates="user", uselist=False,
        cascade="all, delete-orphan",
    )
    cars = db.relationship("Car", back_populates="owner", cascade="all, delete-orphan")
    favorites = db.relationship("Favorite", back_populates="user", cascade="all, delete-orphan")

    def active_subscription(self):
        """Возвращает текущую активную подписку либо None (тогда действует FREE)."""
        now = datetime.utcnow()
        return (
            Subscription.query.filter_by(owner_id=self.id, status="active")
            .filter(db.or_(Subscription.expires_at.is_(None), Subscription.expires_at > now))
            .order_by(Subscription.started_at.desc())
            .first()
        )

    def current_plan(self) -> str:
        sub = self.active_subscription()
        return sub.plan if sub else "free"


class OwnerProfile(db.Model):
    """Публичный профиль владельца/автопроката (раздел 32, 36 ТЗ)."""

    __tablename__ = "owner_profiles"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    display_name = db.Column(db.String(120), nullable=False)
    company_name = db.Column(db.String(150), nullable=True)  # только для BUSINESS
    logo_url = db.Column(db.String(500), nullable=True)
    description = db.Column(db.Text, nullable=True)
    work_hours = db.Column(db.String(200), nullable=True)
    phone_verified = db.Column(db.Boolean, default=False, nullable=False)
    contact_consent = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", back_populates="owner_profile")

    def profile_completion_percent(self) -> int:
        """Простая оценка заполненности профиля для карточки доверия."""
        fields = [self.display_name, self.description, self.work_hours, self.logo_url]
        filled = sum(1 for f in fields if f) + (1 if self.phone_verified else 0)
        return round(filled / (len(fields) + 1) * 100)


class City(db.Model):
    __tablename__ = "cities"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(60), unique=True, nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    region = db.Column(db.String(120), nullable=True)
    is_primary = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    cars = db.relationship("Car", back_populates="city")


owner_cities = db.Table(
    "owner_cities",
    db.Column("owner_profile_id", db.Integer, db.ForeignKey("owner_profiles.id"), primary_key=True),
    db.Column("city_id", db.Integer, db.ForeignKey("cities.id"), primary_key=True),
)  # для BUSINESS: несколько городов присутствия


class Car(db.Model):
    __tablename__ = "cars"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(12), unique=True, nullable=False, default=_short_id, index=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    city_id = db.Column(db.Integer, db.ForeignKey("cities.id"), nullable=False, index=True)

    brand = db.Column(db.String(80), nullable=False, index=True)
    model = db.Column(db.String(80), nullable=False, index=True)
    year = db.Column(db.Integer, nullable=True)
    body_type = db.Column(db.String(30), nullable=True)
    seats = db.Column(db.Integer, nullable=True)
    transmission_auto = db.Column(db.Boolean, default=True, nullable=False)

    price_per_day = db.Column(db.Integer, nullable=True, index=True)  # null = «цена по запросу»
    min_rental_days = db.Column(db.Integer, default=1, nullable=False)
    min_driver_age = db.Column(db.Integer, default=18, nullable=False)  # владелец может ужесточить (18/21/25)
    with_driver = db.Column(db.Boolean, default=False, nullable=False)
    for_wedding = db.Column(db.Boolean, default=False, nullable=False)
    delivery_available = db.Column(db.Boolean, default=False, nullable=False)
    is_premium = db.Column(db.Boolean, default=False, nullable=False)

    description = db.Column(db.Text, nullable=True)
    extra_terms = db.Column(db.Text, nullable=True)

    phone = db.Column(db.String(20), nullable=False)
    telegram = db.Column(db.String(120), nullable=True)
    whatsapp = db.Column(db.String(20), nullable=True)
    contact_consent = db.Column(db.Boolean, default=False, nullable=False)

    status = db.Column(db.String(20), default="draft", nullable=False, index=True)
    is_boosted_until = db.Column(db.DateTime, nullable=True)
    last_confirmed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    owner = db.relationship("User", back_populates="cars")
    city = db.relationship("City", back_populates="cars")
    photos = db.relationship(
        "CarPhoto", back_populates="car", cascade="all, delete-orphan",
        order_by="CarPhoto.position",
    )
    features = db.relationship("CarFeature", back_populates="car", cascade="all, delete-orphan")

    def primary_photo(self):
        return self.photos[0] if self.photos else None

    def is_stale(self) -> bool:
        from config import LISTING_STALE_AFTER_DAYS
        return datetime.utcnow() - self.last_confirmed_at > timedelta(days=LISTING_STALE_AFTER_DAYS)

    def title(self) -> str:
        parts = [self.brand, self.model]
        if self.year:
            parts.append(str(self.year))
        return " ".join(parts)


class CarPhoto(db.Model):
    __tablename__ = "car_photos"

    id = db.Column(db.Integer, primary_key=True)
    car_id = db.Column(db.Integer, db.ForeignKey("cars.id"), nullable=False, index=True)
    url = db.Column(db.String(500), nullable=False)
    thumbnail_url = db.Column(db.String(500), nullable=True)
    storage_public_id = db.Column(db.String(255), nullable=True)  # id в object storage
    position = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    car = db.relationship("Car", back_populates="photos")


class CarFeature(db.Model):
    """Дополнительные опции автомобиля (доступно PRO/BUSINESS)."""

    __tablename__ = "car_features"

    id = db.Column(db.Integer, primary_key=True)
    car_id = db.Column(db.Integer, db.ForeignKey("cars.id"), nullable=False, index=True)
    key = db.Column(db.String(60), nullable=False)

    car = db.relationship("Car", back_populates="features")


class Subscription(db.Model):
    """История подписок владельца. Активна одна запись со status='active'."""

    __tablename__ = "subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    plan = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), default="active", nullable=False)  # active|expired|cancelled
    started_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=True)  # null = бессрочно (FREE)

    # Поля готовы для будущей платёжной интеграции (раздел 134 ТЗ).
    payment_provider = db.Column(db.String(40), nullable=True)
    payment_status = db.Column(db.String(20), nullable=True)
    transaction_id = db.Column(db.String(120), nullable=True)

    activated_by_admin_id = db.Column(db.Integer, nullable=True)
    promo_code_id = db.Column(db.Integer, db.ForeignKey("promo_codes.id"), nullable=True)

    boosts_used_this_period = db.Column(db.Integer, default=0, nullable=False)


class Favorite(db.Model):
    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("user_id", "car_id", name="uq_favorite_user_car"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    car_id = db.Column(db.Integer, db.ForeignKey("cars.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", back_populates="favorites")


class ContactClick(db.Model):
    """Минимум данных: только чтобы владелец видел статистику обращений.
    Содержимое переписки/звонков не хранится (раздел 35, 44 ТЗ)."""

    __tablename__ = "contact_clicks"

    id = db.Column(db.Integer, primary_key=True)
    car_id = db.Column(db.Integer, db.ForeignKey("cars.id"), nullable=False, index=True)
    channel = db.Column(db.String(20), nullable=False)  # call|whatsapp|telegram
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)


class View(db.Model):
    __tablename__ = "views"

    id = db.Column(db.Integer, primary_key=True)
    car_id = db.Column(db.Integer, db.ForeignKey("cars.id"), nullable=False, index=True)
    viewer_hash = db.Column(db.String(64), nullable=True)  # для дедупликации, не PII
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)


class Report(db.Model):
    __tablename__ = "reports"

    id = db.Column(db.Integer, primary_key=True)
    car_id = db.Column(db.Integer, db.ForeignKey("cars.id"), nullable=False, index=True)
    reason = db.Column(db.String(30), nullable=False)
    message = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default="new", nullable=False)  # new|reviewed|actioned|dismissed
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    resolved_at = db.Column(db.DateTime, nullable=True)
    resolved_by_admin_id = db.Column(db.Integer, nullable=True)

    car = db.relationship("Car")


class AdminUser(db.Model):
    """Отдельная модель для администраторов — отдельный auth flow,
    полностью изолированный от обычных пользователей (раздел 41 ТЗ)."""

    __tablename__ = "admin_users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(60), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_super = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_login_at = db.Column(db.DateTime, nullable=True)


class AdminLog(db.Model):
    __tablename__ = "admin_logs"

    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, db.ForeignKey("admin_users.id"), nullable=False)
    action = db.Column(db.String(80), nullable=False)
    target_type = db.Column(db.String(40), nullable=True)
    target_id = db.Column(db.Integer, nullable=True)
    ip = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class PromoCode(db.Model):
    __tablename__ = "promo_codes"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), unique=True, nullable=False, index=True)
    plan = db.Column(db.String(20), nullable=False)
    duration_days = db.Column(db.Integer, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=True)  # до какого момента код можно активировать
    used_at = db.Column(db.DateTime, nullable=True)
    used_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_admin_id = db.Column(db.Integer, nullable=True)

    @property
    def is_used(self) -> bool:
        return self.used_at is not None

    def is_valid(self) -> bool:
        if self.is_used:
            return False
        if self.expires_at and datetime.utcnow() > self.expires_at:
            return False
        return True
