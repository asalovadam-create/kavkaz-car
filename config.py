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


def normalize_database_url(url: str) -> str:
    """Приводит адрес PostgreSQL к явному драйверу psycopg2 (он стоит в requirements.txt).

    Без явного драйвера новые версии SQLAlchemy могут искать psycopg (v3), которого нет,
    и сайт не стартует с ошибкой "No module named 'psycopg'". Render и Neon также
    иногда выдают адрес с началом "postgres://". Случайные пробелы и кавычки убираем."""
    url = (url or "").strip().strip("\"'")
    for prefix in ("postgres://", "postgresql://", "postgresql+psycopg://", "postgresql+psycopg2://"):
        if url.startswith(prefix):
            return "postgresql+psycopg2://" + url[len(prefix):]
    return url


class Config:
    # --- Секреты и окружение ---
    SECRET_KEY = os.environ.get("SECRET_KEY", "")
    ENV = os.environ.get("FLASK_ENV", "production")
    DEBUG = _env_bool("FLASK_DEBUG", False)

    # --- База данных ---
    # Продакшн: PostgreSQL. Локальная разработка допускает SQLite для удобства,
    # но это НЕ рекомендуется для боевого окружения (см. README).
    SQLALCHEMY_DATABASE_URI = normalize_database_url(
        os.environ.get("DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'dev.db')}")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # --- Сессии / cookies ---
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_bool("FORCE_HTTPS", True)
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 14  # 14 дней для клиента
    ADMIN_SESSION_LIFETIME = _env_int("ADMIN_SESSION_MINUTES", 120) * 60  # неактивность до выхода из админки (по умолчанию 2 часа)

    # --- Загрузка фото ---
    UPLOAD_PROVIDER = os.environ.get("UPLOAD_PROVIDER", "local")  # local | cloudinary
    CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME", "")
    CLOUDINARY_API_KEY = os.environ.get("CLOUDINARY_API_KEY", "")
    CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET", "")
    MAX_CONTENT_LENGTH = 12 * 1024 * 1024  # 12 MB на один HTTP-запрос загрузки
    MAX_PHOTO_SOURCE_MB = 10  # максимальный размер одного исходного файла

    # --- Админка ---
    ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")  # доп. секрет при входе в админку
    # Секретный адрес админки, например ADMIN_PATH=/ctl-k8f3a9x2q7. Если не задан, адрес
    # вычисляется из SECRET_KEY и выводится в логи при старте. Адреса /admin на сайте НЕТ.
    ADMIN_PATH = os.environ.get("ADMIN_PATH", "")
    # Необязательно: пускать в админку только с этих IP (через запятую). Остальным — обычная 404.
    ADMIN_ALLOWED_IPS = [ip.strip() for ip in os.environ.get("ADMIN_ALLOWED_IPS", "").split(",") if ip.strip()]

    # --- Прочее ---
    SITE_URL = os.environ.get("SITE_URL", "https://kavkaz-car.ru").rstrip("/")
    INSTAGRAM_URL = os.environ.get("INSTAGRAM_URL", "https://www.instagram.com/kavkaz_car95/")
    INSTAGRAM_HANDLE = "@kavkaz_car95"
    SUPPORT_PHONE = os.environ.get("SUPPORT_PHONE", "")
    SUPPORT_TELEGRAM = os.environ.get("SUPPORT_TELEGRAM", "")  # @username или ссылка
    SUPPORT_WHATSAPP = os.environ.get("SUPPORT_WHATSAPP", "")  # номер, например +79001234567

    # Сколько прокси стоит перед приложением (Render = 1). Нужно, чтобы
    # request.remote_addr / request.is_secure были настоящими, а не «внутренними».
    TRUSTED_PROXIES = _env_int("TRUSTED_PROXIES", 1)

    # При старте создавать недостающие таблицы/колонки и города (см. bootstrap.py).
    AUTO_BOOTSTRAP = _env_bool("AUTO_BOOTSTRAP", True)

    # Статика кешируется надолго; версия в URL (?v=) сбрасывает кеш при обновлении.
    SEND_FILE_MAX_AGE_DEFAULT = 60 * 60 * 24 * 30

    # --- Оплата тарифов ---
    # PAYMENT_PROVIDER: "" / "manual" — заявка + ручное подтверждение админом;
    #                   "rollypay"    — онлайн-оплата через Rolly Pay (payments.py).
    PAYMENT_PROVIDER = os.environ.get("PAYMENT_PROVIDER", "manual").strip().lower()
    ROLLYPAY_API_URL = os.environ.get("ROLLYPAY_API_URL", "")
    ROLLYPAY_API_KEY = os.environ.get("ROLLYPAY_API_KEY", "")
    ROLLYPAY_SECRET = os.environ.get("ROLLYPAY_SECRET", "")  # секрет подписи вебхуков
    ROLLYPAY_SHOP_ID = os.environ.get("ROLLYPAY_SHOP_ID", "")
    ROLLYPAY_SIGNATURE_HEADER = os.environ.get("ROLLYPAY_SIGNATURE_HEADER", "X-Signature")

    # --- Уведомления админу (необязательно) ---
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def admin_prefix(secret_key: str, configured: str = "") -> str:
    """Секретный URL-префикс админки. Задан вручную (ADMIN_PATH) или выводится из SECRET_KEY."""
    import hashlib
    import re
    value = "/" + (configured or "").strip().strip("/")
    if re.fullmatch(r"/[A-Za-z0-9_-]{6,60}", value) and value.lower() not in ("/admin", "/administrator", "/login"):
        return value
    return "/panel-" + hashlib.sha256(("admin-path:" + (secret_key or "")).encode()).hexdigest()[:14]


def is_placeholder_admin_secret(value: str) -> bool:
    """ADMIN_SECRET пуст, короткий или остался из примера -> дополнительный
    код при входе в админку не требуется (иначе мы бы вас заблокировали)."""
    v = (value or "").strip().lower()
    return len(v) < 8 or "change-me" in v or "changeme" in v


def is_weak_secret(value: str) -> bool:
    """True, если секрет похож на заглушку из .env.example или слишком короткий."""
    v = (value or "").strip().lower()
    if len(v) < 32:
        return True
    return any(marker in v for marker in ("change-me", "changeme", "dev-only", "your-secret", "secret-key"))


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

# Что показываем на странице тарифов. Только то, что реально работает в коде.
PLAN_PITCH = {
    "free": {
        "tagline": "Чтобы попробовать",
        "features": [
            "1 автомобиль в каталоге",
            "До 5 фото",
            "Звонки, WhatsApp и Telegram без комиссии",
            "Нужно раз в 14 дней подтверждать актуальность",
            "Общее число просмотров и обращений",
        ],
    },
    "pro": {
        "tagline": "Для частных владельцев",
        "features": [
            "До 5 автомобилей",
            "До 15 фото на каждый",
            "1 поднятие в топ в месяц (на 48 часов)",
            "Подтверждать актуальность не нужно",
            "Статистика по каждой машине: просмотры и обращения по дням и каналам",
            "Выше в выдаче, чем бесплатные объявления",
        ],
    },
    "business": {
        "tagline": "Для автопрокатов",
        "features": [
            "До 20 автомобилей",
            "До 30 фото на каждый",
            "4 поднятия в топ в месяц",
            "Своя страница проката с названием и логотипом",
            "Название компании в каждом объявлении",
            "Всё, что есть в PRO",
            "Самый высокий приоритет в выдаче",
        ],
    },
}

# Сроки оплаты: (месяцев, скидка в процентах). Меняйте здесь.
PLAN_TERMS = [(1, 0), (3, 10), (12, 20)]
PLAN_DAYS_PER_MONTH = 30


def plan_price(plan: str, months: int = 1) -> int:
    """Итоговая цена за срок с учётом скидки, в рублях (целое число)."""
    base = PLANS[plan]["price"]
    discount = dict(PLAN_TERMS).get(months, 0)
    return int(round(base * months * (100 - discount) / 100))


def plan_term_label(months: int) -> str:
    return {1: "1 месяц", 3: "3 месяца", 12: "12 месяцев"}.get(months, f"{months} мес.")

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
# Значение — строка или список вариантов: ищем любой из них (OR).
_G_CLASS = ["g-class", "g63", "g500", "g 63", "g class", "гелен"]
SEARCH_SYNONYMS = {
    "мерс": "mercedes", "мерседес": "mercedes", "мерин": "mercedes", "mers": "mercedes",
    "бмв": "bmw", "бэха": "bmw", "бумер": "bmw",
    "гелик": _G_CLASS, "gelik": _G_CLASS, "gelendvagen": _G_CLASS, "gelandewagen": _G_CLASS,
    "гелентваген": _G_CLASS, "гелендваген": _G_CLASS, "g63": _G_CLASS, "g500": _G_CLASS,
    "лексус": "lexus", "тойота": "toyota", "порше": "porsche", "ауди": "audi",
    "майбах": "maybach", "хендай": "hyundai", "хундай": "hyundai", "киа": "kia",
    "дженезис": "genesis", "ровер": "range rover",
    "рендж ровер": "range rover", "рэндж ровер": "range rover", "ренж ровер": "range rover",
    "ландкрузер": ["land cruiser", "landcruiser"], "крузак": ["land cruiser", "landcruiser"],
    "камри": "camry", "прадо": "prado", "каен": "cayenne", "кайен": "cayenne",
    "эскалейд": "escalade", "кадиллак": "cadillac", "бентли": "bentley", "роллс": "rolls",
}

# Семьи «одно и то же по-разному»: русское и латинское написание, сленг, частые ошибки.
# Поиск по любому слову из семьи находит объявления с любым другим словом из неё
# (владельцы пишут «Тайота Камри» или «Toyota Camry» — найтись должно оба).
SEARCH_FAMILIES = [
    ["toyota", "тойота", "тайота", "тоета"],
    ["camry", "камри", "кэмри", "кемри", "камрі"],
    ["land cruiser", "landcruiser", "ленд крузер", "лэнд крузер", "ландкрузер", "крузер", "крузак", "лэнд круизер", "лс"],
    ["prado", "прадо", "ленд крузер прадо"],
    ["corolla", "королла", "корола"],
    ["rav4", "rav 4", "рав4", "рав 4", "рав"],
    ["highlander", "хайлендер", "хайлэндер"],
    ["lexus", "лексус"],
    ["mercedes", "мерседес", "мерс", "мерин", "mers", "mercedes-benz", "мерседес-бенц", "мерседес бенц"],
    ["s-class", "s class", "sclass", "эс класс", "эска", "w222", "w223", "w221"],
    ["e-class", "e class", "eclass", "е класс", "w213", "w212", "w211"],
    ["bmw", "бмв", "бэха", "бумер", "бемве", "бэмвэ"],
    ["audi", "ауди"],
    ["volkswagen", "фольксваген", "vw", "фольц"],
    ["polo", "поло"],
    ["passat", "пассат"],
    ["tiguan", "тигуан"],
    ["skoda", "шкода"],
    ["octavia", "октавия", "октавиа"],
    ["kia", "киа", "кия"],
    ["rio", "рио"],
    ["optima", "оптима"],
    ["sportage", "спортейдж", "спортаж"],
    ["sorento", "соренто"],
    ["hyundai", "хендай", "хундай", "хюндай", "хёндэ", "хендэ"],
    ["solaris", "солярис"],
    ["elantra", "элантра"],
    ["sonata", "соната"],
    ["tucson", "туссан", "туксон"],
    ["creta", "крета"],
    ["genesis", "дженезис", "генезис"],
    ["nissan", "ниссан", "нисан"],
    ["qashqai", "кашкай"],
    ["x-trail", "x trail", "xtrail", "икс трейл", "хтрейл"],
    ["mazda", "мазда"],
    ["honda", "хонда"],
    ["mitsubishi", "митсубиси", "митсубиши"],
    ["lancer", "лансер", "ланцер"],
    ["pajero", "паджеро"],
    ["subaru", "субару"],
    ["infiniti", "инфинити"],
    ["ford", "форд"],
    ["focus", "фокус"],
    ["mustang", "мустанг"],
    ["chevrolet", "шевроле", "шеви"],
    ["opel", "опель"],
    ["renault", "рено"],
    ["logan", "логан"],
    ["duster", "дастер"],
    ["lada", "лада", "ваз"],
    ["granta", "гранта"],
    ["vesta", "веста"],
    ["priora", "приора"],
    ["niva", "нива"],
    ["porsche", "порше", "поршe"],
    ["cayenne", "кайен", "каен", "кайенн"],
    ["macan", "макан"],
    ["panamera", "панамера"],
    ["range rover", "рендж ровер", "рэндж ровер", "ренж ровер", "ровер"],
    ["maybach", "майбах"],
    ["bentley", "бентли"],
    ["rolls-royce", "rolls royce", "rolls", "роллс", "роллс ройс", "роллс-ройс"],
    ["cadillac", "кадиллак", "кадилак"],
    ["escalade", "эскалейд", "эскалейт"],
    ["tesla", "тесла"],
    ["lamborghini", "ламборгини", "ламба"],
    ["ferrari", "феррари"],
    ["maserati", "мазерати", "масерати"],
    ["volvo", "вольво"],
    ["jeep", "джип"],
    ["haval", "хавал", "хавейл"],
    ["chery", "чери"],
    ["geely", "джили", "гили"],
    ["suv", "внедорожник", "джип", "кроссовер", "паркетник"],
    ["sedan", "седан"],
    ["minivan", "минивэн", "минивен"],
    ["g-class", "g class", "gclass", "g63", "g 63", "g500", "g 500", "гелик", "gelik", "гелендваген", "гелентваген", "gelendvagen", "gelandewagen"],
]

# Слова, которые описывают свойство, а не марку: ищем и по галочкам объявления, и по тексту.
SEARCH_FLAGS = {
    "свадеб": "for_wedding", "свадьб": "for_wedding", "невест": "for_wedding", "жених": "for_wedding",
    "водител": "with_driver", "шофер": "with_driver", "шофёр": "with_driver", "трезвый": "with_driver",
    "доставк": "delivery_available", "подач": "delivery_available",
    "автомат": "transmission_auto", "акпп": "transmission_auto",
    "премиум": "is_premium", "люкс": "is_premium", "вип": "is_premium", "vip": "is_premium",
}

# Слова-паразиты: «машина на свадьбу в грозном» -> «свадьбу грозном».
SEARCH_STOPWORDS = {
    "авто", "машина", "машину", "машины", "автомобиль", "автомобили", "аренда", "арендовать", "прокат",
    "взять", "нужна", "нужен", "хочу", "для", "на", "в", "во", "по", "и", "с", "со", "от", "до", "или", "под",
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

STATUS_LABELS = {
    "draft": "Черновик",
    "pending": "На проверке",
    "published": "Опубликовано",
    "paused": "Приостановлено",
    "blocked": "Заблокировано",
    "expired": "Тариф закончился",
}

ORDER_STATUS_LABELS = {
    "created": "Создан",
    "awaiting_payment": "Ждёт оплаты",
    "awaiting_manual": "Ждёт подтверждения оплаты",
    "paid": "Оплачен",
    "failed": "Не оплачен",
    "cancelled": "Отменён",
}

CONTACT_CHANNELS = ["call", "whatsapp", "telegram"]

# --- Лимиты частоты действий (см. security.py) --------------------------
# (макс. попыток, окно в секундах)
RATE_LIMITS = {
    "login": (10, 60),            # попыток входа с одного IP
    "login_phone": (8, 900),      # НЕУДАЧНЫХ попыток на один номер за 15 минут
    "register": (10, 600),
    "admin_login": (6, 300),
    "admin_login_fail": (6, 900), # неудачных попыток входа в админку с одного IP
    "contact_click": (60, 60),
    "report": (5, 600),
    "upload": (40, 300),
    "promo_redeem": (10, 300),
    "password_change": (5, 600),
    "checkout": (10, 600),
    "guest_cards": (60, 60),
    "chat_send": (12, 300),        # сообщений в чат поддержки
    "visit_info": (20, 60),
    "impression": (40, 60),       # пачки показов объявлений в ленте
}

# Через сколько дней объявление считается «неактуальным», если владелец
# не подтвердил цену (раздел 72 ТЗ).
LISTING_STALE_AFTER_DAYS = 14
LISTING_EXPIRE_AFTER_DAYS = 45
