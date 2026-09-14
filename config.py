"""
Конфигурация KAVKAZ-CAR.

Все бизнес-параметры (тарифы, лимиты, города, категории) собраны здесь,
а не разбросаны по файлам. Секреты берутся только из переменных окружения.
"""
import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class Config:
    # --- Секреты и окружение ---
    SECRET_KEY = os.environ.get("SECRET_KEY", "")
    ENV = os.environ.get("FLASK_ENV", "production")
    DEBUG = _env_bool("FLASK_DEBUG", False)

    # --- База данных ---
    # Продакшн: PostgreSQL. Локальная разработка допускает SQLite для удобства,
    # но это НЕ рекомендуется для боевого окружения (см. README).
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'dev.db')}"
    )
    # Render иногда отдаёт "postgres://", SQLAlchemy 2.x требует "postgresql://"
    if SQLALCHEMY_DATABASE_URI.startswith("postgres://"):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace(
            "postgres://", "postgresql://", 1
        )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # --- Сессии / cookies ---
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_bool("FORCE_HTTPS", True)
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 14  # 14 дней для клиента
    ADMIN_SESSION_LIFETIME = 60 * 30  # 30 минут неактивности для админки

    # --- Загрузка фото ---
    UPLOAD_PROVIDER = os.environ.get("UPLOAD_PROVIDER", "local")  # local | cloudinary
    CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME", "")
    CLOUDINARY_API_KEY = os.environ.get("CLOUDINARY_API_KEY", "")
    CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET", "")
    MAX_CONTENT_LENGTH = 12 * 1024 * 1024  # 12 MB на один HTTP-запрос загрузки
    MAX_PHOTO_SOURCE_MB = 10  # максимальный размер одного исходного файла

    # --- Админка ---
    ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")  # доп. секрет для /admin/login

    # --- Прочее ---
    SITE_URL = os.environ.get("SITE_URL", "https://kavkaz-car.ru")
    SUPPORT_PHONE = os.environ.get("SUPPORT_PHONE", "")


# ---------------------------------------------------------------------------
# Тарифы владельцев. Цены и лимиты — единственный источник правды для всего
# приложения (шаблоны, бизнес-логика, админка) читают именно отсюда.
# ---------------------------------------------------------------------------
PLANS = {
    "free": {
        "name": "FREE",
        "price": 0,
        "max_cars": 1,
        "max_photos_per_car": 5,
        "boosts_per_month": 0,
        "priority": 0,
        "badge": None,
        "stats": "basic",
        "multi_city": False,
        "business_page": False,
    },
    "pro": {
        "name": "PRO",
        "price": 499,
        "max_cars": 5,
        "max_photos_per_car": 15,
        "boosts_per_month": 1,
        "priority": 1,
        "badge": "PRO",
        "stats": "extended",
        "multi_city": False,
        "business_page": False,
    },
    "business": {
        "name": "BUSINESS",
        "price": 999,
        "max_cars": 20,
        "max_photos_per_car": 30,
        "boosts_per_month": 4,
        "priority": 2,
        "badge": "BUSINESS",
        "stats": "extended",
        "multi_city": True,
        "business_page": True,
    },
}

PLAN_ORDER = ["free", "pro", "business"]

# ---------------------------------------------------------------------------
# Города первого этапа запуска. is_primary=True — приоритетные города,
# остальные регионы можно включать позже флагом is_primary=False.
# ---------------------------------------------------------------------------
LAUNCH_CITIES = [
    {"slug": "grozny", "name": "Грозный", "region": "Чеченская Республика", "is_primary": True},
    {"slug": "makhachkala", "name": "Махачкала", "region": "Республика Дагестан", "is_primary": True},
    {"slug": "nazran", "name": "Назрань", "region": "Республика Ингушетия", "is_primary": True},
    {"slug": "vladikavkaz", "name": "Владикавказ", "region": "Северная Осетия — Алания", "is_primary": True},
    {"slug": "nalchik", "name": "Нальчик", "region": "Кабардино-Балкарская Республика", "is_primary": True},
    {"slug": "cherkessk", "name": "Черкесск", "region": "Карачаево-Черкесская Республика", "is_primary": True},
    {"slug": "pyatigorsk", "name": "Пятигорск", "region": "Ставропольский край", "is_primary": True},
    {"slug": "kislovodsk", "name": "Кисловодск", "region": "Ставропольский край", "is_primary": True},
    {"slug": "stavropol", "name": "Ставрополь", "region": "Ставропольский край", "is_primary": True},
]

CAR_BRANDS = [
    "Mercedes-Benz", "BMW", "Toyota", "Lexus", "Porsche", "Audi",
    "Maybach", "Range Rover", "Genesis", "Hyundai", "Kia", "Другая марка",
]

BODY_TYPES = [
    ("sedan", "Седан"),
    ("suv", "Внедорожник"),
    ("minivan", "Минивэн"),
    ("business", "Бизнес-класс"),
    ("premium", "Премиум"),
]

EVENT_TYPES = [
    ("wedding", "Свадьба"),
    ("date", "Свидание"),
    ("photoshoot", "Фотосессия"),
    ("trip", "Поездка"),
    ("airport", "Встреча в аэропорту"),
    ("event", "Мероприятие"),
    ("filming", "Съёмка"),
    ("ride", "Просто покататься"),
]

# Небольшая база синонимов/опечаток для нормализации поиска (раздел 65 ТЗ).
SEARCH_SYNONYMS = {
    "мерс": "mercedes-benz",
    "мерседес": "mercedes-benz",
    "мерин": "mercedes-benz",
    "mers": "mercedes-benz",
    "бмв": "bmw",
    "gelendvagen": "g-class",
    "гелик": "g-class",
    "гелентваген": "g-class",
    "гелендваген": "g-class",
    "g63": "g-class",
    "g500": "g-class",
    "лексус": "lexus",
    "тойота": "toyota",
    "рендж ровер": "range rover",
    "рэндж ровер": "range rover",
    "порше": "porsche",
    "ауди": "audi",
    "майбах": "maybach",
}

REPORT_REASONS = [
    ("not_exist", "Автомобиль не существует"),
    ("wrong_price", "Неправильная цена"),
    ("fraud", "Мошенничество"),
    ("prohibited", "Запрещённый контент"),
    ("stolen_photos", "Чужие фотографии"),
    ("not_available", "Автомобиль уже не сдаётся"),
    ("other", "Другое"),
]

CAR_STATUSES = ["draft", "pending", "published", "paused", "blocked", "expired"]

CONTACT_CHANNELS = ["call", "whatsapp", "telegram"]

# --- Лимиты частоты действий (см. security.py) --------------------------
# (макс. попыток, окно в секундах)
RATE_LIMITS = {
    "login": (5, 60),
    "register": (5, 300),
    "admin_login": (5, 300),
    "contact_click": (30, 60),
    "report": (5, 600),
    "upload": (20, 300),
    "promo_redeem": (10, 300),
}

# Через сколько дней объявление считается «неактуальным», если владелец
# не подтвердил цену (раздел 72 ТЗ).
LISTING_STALE_AFTER_DAYS = 14
LISTING_EXPIRE_AFTER_DAYS = 45
