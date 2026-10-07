"""
Двухфакторный вход (TOTP, RFC 6238) для администраторов — без сторонних библиотек.
Совместим с Google Authenticator, Microsoft Authenticator, Authy, 1Password.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP_SECONDS = 30
DIGITS = 6


def generate_secret() -> str:
    """Случайный секрет в base32 (то, что вводится в приложение-аутентификатор)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code_for_counter(secret: str, counter: int) -> str:
    padded = secret.upper() + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITS)
    return str(number).zfill(DIGITS)


def code_at(secret: str, timestamp: float | None = None) -> str:
    return _code_for_counter(secret, int((timestamp if timestamp is not None else time.time()) // STEP_SECONDS))


def verify(secret: str | None, submitted: str | None, timestamp: float | None = None, window: int = 1) -> bool:
    """Проверяет код с допуском ±1 шаг (30 с) на расхождение часов телефона."""
    if not secret or not submitted:
        return False
    submitted = submitted.strip().replace(" ", "")
    if not (submitted.isdigit() and len(submitted) == DIGITS):
        return False
    now = timestamp if timestamp is not None else time.time()
    counter = int(now // STEP_SECONDS)
    ok = False
    for delta in range(-window, window + 1):  # проверяем все варианты, чтобы время ответа не зависело от попадания
        ok |= hmac.compare_digest(_code_for_counter(secret, counter + delta), submitted)
    return ok


def otpauth_uri(secret: str, account: str, issuer: str = "KAVKAZ-CAR") -> str:
    return (f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}"
            f"&issuer={quote(issuer)}&digits={DIGITS}&period={STEP_SECONDS}")
