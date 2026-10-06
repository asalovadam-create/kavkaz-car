"""Тесты чистой логики (без базы данных). Запуск: python -m unittest discover -s tests -v"""
import hashlib
import hmac
import json
import os
import sys
import unittest

os.environ.setdefault("SECRET_KEY", "k" * 48)
os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import validators as v  # noqa: E402


class PhoneTests(unittest.TestCase):
    def test_ru_phone_variants(self):
        for raw in ("8 (989) 912-91-40", "+7 989 912 91 40", "79899129140", "9899129140"):
            self.assertEqual(v.normalize_ru_phone(raw), "+79899129140", raw)

    def test_ru_phone_rejects_garbage(self):
        for raw in ("", None, "123", "abc", "+1 202 555 0100", "8989912914012"):
            self.assertIsNone(v.normalize_ru_phone(raw), raw)

    def test_contact_phone_accepts_international(self):
        self.assertEqual(v.normalize_contact_phone("+971 50 123 4567"), "+971501234567")
        self.assertIsNone(v.normalize_contact_phone("12345"))

    def test_sanitize_tel_blocks_injection(self):
        self.assertEqual(v.sanitize_tel("+7 (900) 123-45-67"), "+79001234567")
        self.assertIsNone(v.sanitize_tel("javascript:alert(1)"))
        self.assertIsNone(v.sanitize_tel("12"))

    def test_whatsapp_link(self):
        link = v.whatsapp_link("+79001234567", "Привет!")
        self.assertTrue(link.startswith("https://wa.me/79001234567?text="))
        self.assertIsNone(v.whatsapp_link("../evil"))

    def test_format_phone(self):
        self.assertEqual(v.format_phone("+79001234567"), "+7 (900) 123-45-67")


class TelegramTests(unittest.TestCase):
    def test_valid(self):
        for raw in ("@kavkaz_car", "kavkaz_car", "t.me/kavkaz_car", "https://t.me/kavkaz_car?start=1"):
            self.assertEqual(v.normalize_telegram(raw), "@kavkaz_car", raw)

    def test_invalid(self):
        for raw in ("@ab", "@1abcde", "a b c d e f", "@bad-name!", "https://evil.com/x"):
            self.assertIsNone(v.normalize_telegram(raw), raw)


class SafeRedirectTests(unittest.TestCase):
    def test_blocks_open_redirects(self):
        attacks = [
            "//evil.com", "https://evil.com", "http://evil.com/x", "/\\evil.com", "\\\\evil.com",
            "javascript:alert(1)", "evil.com", "/ok\nSet-Cookie: a=b", "///evil.com", "/\t/evil.com",
        ]
        for attack in attacks:
            self.assertIsNone(v.safe_next_url(attack), attack)

    def test_allows_local_paths(self):
        self.assertEqual(v.safe_next_url("/pricing?months=3"), "/pricing?months=3")
        self.assertEqual(v.safe_next_url("/owner/dashboard"), "/owner/dashboard")
        self.assertEqual(v.safe_next_url(None, "/home"), "/home")


class MiscTests(unittest.TestCase):
    def test_to_int_bounds(self):
        self.assertEqual(v.to_int("500", minimum=500, maximum=1000), 500)
        self.assertIsNone(v.to_int("-5", minimum=0))
        self.assertIsNone(v.to_int("1e9"))
        self.assertIsNone(v.to_int(None))

    def test_like_escape(self):
        self.assertEqual(v.like_escape("50%_off"), "50\\%\\_off")

    def test_clean_text(self):
        self.assertEqual(v.clean_text("  hi\x00there  ", 50), "hithere")
        self.assertEqual(len(v.clean_text("a" * 500, 10)), 10)

    def test_plural(self):
        self.assertEqual(v.ru_plural(1, "автомобиль", "автомобиля", "автомобилей"), "автомобиль")
        self.assertEqual(v.ru_plural(3, "автомобиль", "автомобиля", "автомобилей"), "автомобиля")
        self.assertEqual(v.ru_plural(11, "автомобиль", "автомобиля", "автомобилей"), "автомобилей")
        self.assertEqual(v.ru_plural(21, "автомобиль", "автомобиля", "автомобилей"), "автомобиль")


class ConfigTests(unittest.TestCase):
    def test_prices_with_discount(self):
        self.assertEqual(config.plan_price("pro", 1), config.PLANS["pro"]["price"])
        self.assertEqual(config.plan_price("pro", 12), round(config.PLANS["pro"]["price"] * 12 * 0.8))
        self.assertEqual(config.plan_price("free", 3), 0)

    def test_weak_secret_detection(self):
        self.assertTrue(config.is_weak_secret("change-me-to-a-random-64-character-string-xxxxxxxxx"))
        self.assertTrue(config.is_weak_secret("short"))
        self.assertFalse(config.is_weak_secret("f3a9" * 16))

    def test_database_url_normalization(self):
        n = config.normalize_database_url
        want = "postgresql+psycopg2://u:p@h/db"
        self.assertEqual(n("postgres://u:p@h/db"), want)
        self.assertEqual(n("postgresql://u:p@h/db"), want)
        self.assertEqual(n("postgresql+psycopg://u:p@h/db"), want)
        self.assertEqual(n('  "postgresql+psycopg2://u:p@h/db" '), want)
        self.assertEqual(n("postgresql://u:p@h/db?sslmode=require"), want + "?sslmode=require")
        self.assertEqual(n("sqlite:///x.db"), "sqlite:///x.db")

    def test_admin_secret_placeholder(self):
        self.assertTrue(config.is_placeholder_admin_secret("change-me-too"))
        self.assertTrue(config.is_placeholder_admin_secret(""))
        self.assertFalse(config.is_placeholder_admin_secret("my-real-long-admin-code"))


class SearchTests(unittest.TestCase):
    def test_synonyms_expand_with_or_inside_group(self):
        import services
        groups = services.expand_search_terms("Гелик Грозный")
        self.assertEqual(len(groups), 2)
        self.assertIn("g63", groups[0])
        self.assertEqual(groups[1], ["грозный"])

    def test_multiword_synonym(self):
        import services
        self.assertEqual(services.expand_search_terms("рендж ровер")[0], ["range rover"])

    def test_empty_and_long(self):
        import services
        self.assertEqual(services.expand_search_terms("   "), [])
        self.assertLessEqual(len(services.expand_search_terms("а " * 100)), 6)


class WebhookSignatureTests(unittest.TestCase):
    """Проверка подписи вебхука: без неё любой мог бы «оплатить» тариф."""

    def setUp(self):
        from flask import Flask
        self.app = Flask(__name__)
        self.app.config.update(ROLLYPAY_SECRET="s3cret", ROLLYPAY_SIGNATURE_HEADER="X-Signature")

    def _provider(self):
        from payments import RollyPayProvider
        return RollyPayProvider()

    def test_valid_signature(self):
        body = json.dumps({"order_id": "abc", "status": "paid", "amount": 499}).encode()
        sig = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
        with self.app.app_context():
            self.assertTrue(self._provider().verify_webhook(body, {"X-Signature": sig}))
            self.assertTrue(self._provider().verify_webhook(body, {"X-Signature": "sha256=" + sig.upper()}))

    def test_rejects_bad_or_missing_signature(self):
        body = b'{"status":"paid"}'
        with self.app.app_context():
            p = self._provider()
            self.assertFalse(p.verify_webhook(body, {}))
            self.assertFalse(p.verify_webhook(body, {"X-Signature": "deadbeef"}))
            tampered_sig = hmac.new(b"s3cret", b'{"status":"failed"}', hashlib.sha256).hexdigest()
            self.assertFalse(p.verify_webhook(body, {"X-Signature": tampered_sig}))

    def test_rejects_when_secret_not_configured(self):
        self.app.config["ROLLYPAY_SECRET"] = ""
        with self.app.app_context():
            self.assertFalse(self._provider().verify_webhook(b"{}", {"X-Signature": "x"}))

    def test_parse_statuses(self):
        with self.app.app_context():
            p = self._provider()
            self.assertEqual(p.parse_webhook({"order_id": "a", "status": "SUCCESS", "amount": "499.00"})["status"], "paid")
            self.assertEqual(p.parse_webhook({"order_id": "a", "status": "declined"})["status"], "failed")
            self.assertEqual(p.parse_webhook({"order_id": "a", "status": "processing"})["status"], "pending")
            self.assertEqual(p.parse_webhook({"order_id": "a", "amount": "oops"})["amount"], None)


if __name__ == "__main__":
    unittest.main()
