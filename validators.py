"""
Чистые функции проверки и нормализации пользовательского ввода.

Здесь нет зависимостей от Flask и базы данных — поэтому эти функции легко
тестировать (см. tests/test_validators.py) и безопасно использовать везде.
"""
import re
from urllib.parse import quote, urlparse

_NON_DIGITS = re.compile(r"\D")
_TG_USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")


# ---------------------------------------------------------------------------
# Телефоны
# ---------------------------------------------------------------------------

def normalize_ru_phone(raw: str | None) -> str | None:
    """Российский номер -> '+7XXXXXXXXXX' либо None. Используется для входа."""
    digits = _NON_DIGITS.sub("", raw or "")
    if len(digits) == 11 and digits[0] in "78":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    else:
        return None
    return "+" + digits


def normalize_contact_phone(raw: str | None) -> str | None:
    """Контактный номер в объявлении: российский или международный (11–15 цифр)."""
    ru = normalize_ru_phone(raw)
    if ru:
        return ru
    digits = _NON_DIGITS.sub("", raw or "")
    if 11 <= len(digits) <= 15:
        return "+" + digits
    return None


def sanitize_tel(raw: str | None) -> str | None:
    """Для ссылки tel: — только '+' и цифры. Защита от мусора в старых записях."""
    cleaned = re.sub(r"[^\d+]", "", raw or "")
    digits = cleaned.lstrip("+")
    if not (7 <= len(digits) <= 15):
        return None
    return ("+" if cleaned.startswith("+") else "") + digits


def wa_digits(raw: str | None) -> str | None:
    """Цифры номера для https://wa.me/<digits>."""
    digits = _NON_DIGITS.sub("", raw or "")
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    return digits if 11 <= len(digits) <= 15 else None


def format_phone(raw: str | None) -> str:
    """'+79001234567' -> '+7 (900) 123-45-67'. Нестандартные номера — как есть."""
    value = (raw or "").strip()
    digits = _NON_DIGITS.sub("", value)
    if len(digits) == 11 and digits[0] == "7":
        return f"+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    return value


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def normalize_telegram(raw: str | None) -> str | None:
    """'@name' | 'name' | 't.me/name' | 'https://t.me/name' -> '@name' либо None."""
    value = (raw or "").strip()
    if not value:
        return None
    value = re.sub(r"^(https?://)?(www\.)?(t\.me|telegram\.me)/", "", value, flags=re.I)
    value = value.split("?")[0].split("/")[0].lstrip("@")
    return "@" + value if _TG_USERNAME.match(value) else None


# ---------------------------------------------------------------------------
# Редиректы
# ---------------------------------------------------------------------------

def safe_next_url(target: str | None, default: str | None = None) -> str | None:
    """Разрешает только относительные пути своего сайта (защита от open redirect)."""
    if not target:
        return default
    target = target.strip()
    if not target.startswith("/") or target.startswith("//"):
        return default
    if "\\" in target or any(ord(ch) < 32 for ch in target):
        return default
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return default
    return target


# ---------------------------------------------------------------------------
# Числа и текст
# ---------------------------------------------------------------------------

def to_int(value, *, minimum: int | None = None, maximum: int | None = None):
    """Безопасно превращает ввод в int; вне диапазона или мусор -> None."""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if minimum is not None and number < minimum:
        return None
    if maximum is not None and number > maximum:
        return None
    return number


def clean_text(value, max_length: int) -> str:
    """Обрезает пробелы и управляющие символы, ограничивает длину."""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(value or "")).strip()
    return text[:max_length]


def like_escape(text: str) -> str:
    """Экранирует % и _ для LIKE/ILIKE (используйте escape='\\')."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def whatsapp_link(raw_phone: str | None, message: str | None = None) -> str | None:
    digits = wa_digits(raw_phone)
    if not digits:
        return None
    url = f"https://wa.me/{digits}"
    if message:
        url += "?text=" + quote(message)
    return url


def ru_plural(n: int, one: str, few: str, many: str) -> str:
    """1 автомобиль, 2 автомобиля, 5 автомобилей."""
    n = abs(int(n))
    if 11 <= n % 100 <= 14:
        return many
    last = n % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many
