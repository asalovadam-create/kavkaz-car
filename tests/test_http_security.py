"""HTTP-тесты защиты, не требующие базы данных. Запуск: python -m unittest discover -s tests -v"""
import os
import sys
import unittest

os.environ["SECRET_KEY"] = "k" * 48
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["AUTO_BOOTSTRAP"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import security  # noqa: E402
from app import app  # noqa: E402


def with_csrf(client):
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "test-token"
    return {"csrf_token": "test-token"}


class HttpSecurityTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        security._attempts.clear()
        self.client = app.test_client()

    def test_post_without_csrf_is_rejected(self):
        self.assertEqual(self.client.post("/login", data={"phone": "1", "password": "x"}).status_code, 400)

    def test_page_views_do_not_consume_login_limit(self):
        """Регрессия: раньше 5 открытий страницы входа приводили к 429."""
        for _ in range(25):
            self.assertEqual(self.client.get("/register").status_code, 200)
            self.assertEqual(self.client.get("/login").status_code, 200)

    def test_post_rate_limit_kicks_in(self):
        data = with_csrf(self.client)
        codes = [self.client.post("/register", data=data).status_code for _ in range(12)]
        self.assertEqual(codes[0], 400)  # пустая форма — обычная ошибка валидации
        self.assertIn(429, codes)

    def test_spoofed_forwarded_for_does_not_bypass_limit(self):
        """Клиент не может обойти лимит, подставляя свой X-Forwarded-For."""
        data = with_csrf(self.client)
        codes = []
        for i in range(12):
            headers = {"X-Forwarded-For": f"10.0.0.{i}, 203.0.113.9"}  # последний адрес добавил наш прокси
            codes.append(self.client.post("/register", data=data, headers=headers).status_code)
        self.assertIn(429, codes)

    def test_hsts_and_security_headers_behind_proxy(self):
        resp = self.client.get("/health", headers={"X-Forwarded-Proto": "https"})
        self.assertIn("max-age", resp.headers.get("Strict-Transport-Security", ""))
        csp = resp.headers["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertIn("object-src 'none'", csp)
        self.assertEqual(resp.headers["X-Frame-Options"], "DENY")
        self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")

    def test_html_cache_header_for_anonymous(self):
        self.assertEqual(self.client.get("/login").headers["Cache-Control"], "private, no-cache")

    def test_webhook_is_csrf_exempt_but_not_open(self):
        # Без CSRF-токена запрос доходит до обработчика (404: провайдер manual), а не 400.
        self.assertEqual(self.client.post("/payments/webhook/rollypay", data="{}").status_code, 404)

    def test_open_redirect_target_is_not_echoed(self):
        html = self.client.get("/login?next=//evil.example.com").get_data(as_text=True)
        self.assertNotIn("evil.example.com", html)

    def test_ajax_errors_are_json(self):
        resp = self.client.get("/nope-not-a-city", headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(resp.is_json)

    def test_admin_area_redirects_anonymous(self):
        resp = self.client.get("/admin/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/login", resp.headers["Location"])

    def test_forged_session_cookie_is_useless(self):
        """Cookie с user_id, но без токена сессии (uv), не даёт доступа к кабинету.
        Токен считается от хэша пароля, поэтому его нельзя подделать, зная только SECRET_KEY."""
        with self.client.session_transaction() as sess:
            sess["user_id"] = 1
        resp = self.client.get("/owner/dashboard")
        self.assertIn(resp.status_code, (302, 401))

    def test_robots_hides_private_areas(self):
        body = self.client.get("/robots.txt").get_data(as_text=True)
        for path in ("/owner/", "/admin/", "/orders/", "/payments/"):
            self.assertIn(f"Disallow: {path}", body)


if __name__ == "__main__":
    unittest.main()
