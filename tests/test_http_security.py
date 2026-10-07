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
        # Обычная форма: человека мягко возвращают назад с сообщением, а не показывают страницу ошибки.
        resp = self.client.post("/login", data={"phone": "1", "password": "x"})
        self.assertEqual(resp.status_code, 302)
        # Запрос из JS получает честный JSON с кодом 400.
        resp = self.client.post("/login", data={}, headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(resp.is_json)

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

    def test_admin_is_not_at_the_obvious_address(self):
        """/admin и /admin/login отвечают обычной 404 — никто не узнает, что админка существует."""
        for path in ("/admin", "/admin/", "/admin/login", "/administrator", "/admin/users"):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 404, path)
            self.assertNotIn("admin", resp.get_data(as_text=True).lower().replace("kavkaz", ""), path)

    def test_admin_lives_on_secret_prefix_and_anonymous_is_sent_to_login(self):
        prefix = app.config["ADMIN_PREFIX"]
        self.assertNotIn(prefix, ("/admin", "/administrator"))
        resp = self.client.get(prefix + "/")
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["Location"].endswith(prefix + "/login"))
        self.assertEqual(self.client.get(prefix + "/login").status_code, 200)

    def test_robots_does_not_reveal_admin(self):
        body = self.client.get("/robots.txt").get_data(as_text=True).lower()
        self.assertNotIn("admin", body)
        self.assertNotIn(app.config["ADMIN_PREFIX"].lower(), body)

    def test_error_pages_are_noindex(self):
        html = self.client.get("/definitely-not-a-page").get_data(as_text=True)
        self.assertIn('content="noindex, nofollow"', html)
        self.assertNotIn("definitely-not-a-page", html)  # адрес не отражается в canonical/og:url

    def test_admin_and_site_use_separate_cookies(self):
        """Регрессия: вход в админку больше не стирает клиентский вход и наоборот."""
        prefix = app.config["ADMIN_PREFIX"]
        site = self.client.get("/login")
        admin = self.client.get(prefix + "/login")
        site_cookie = next(c for c in site.headers.getlist("Set-Cookie"))
        admin_cookie = next(c for c in admin.headers.getlist("Set-Cookie"))
        self.assertTrue(site_cookie.startswith("session="))
        self.assertTrue(admin_cookie.startswith("kc_adm="))
        self.assertIn(f"Path={prefix}", admin_cookie)
        self.assertIn("SameSite=Strict", admin_cookie)
        self.assertIn("HttpOnly", admin_cookie)

    def test_visiting_admin_does_not_invalidate_site_csrf_token(self):
        import re
        html = self.client.get("/register").get_data(as_text=True)
        token = re.search(r'name="csrf-token" content="([0-9a-f]+)"', html).group(1)
        self.client.get(app.config["ADMIN_PREFIX"] + "/login")  # заходим в админку в той же «вкладке»/браузере
        resp = self.client.post("/register", data={"csrf_token": token})
        self.assertEqual(resp.status_code, 400)  # дошло до проверки полей формы, а не «сессия устарела» (302)

    def test_admin_ip_allowlist_returns_plain_404(self):
        prefix = app.config["ADMIN_PREFIX"]
        app.config["ADMIN_ALLOWED_IPS"] = ["203.0.113.77"]
        try:
            self.assertEqual(self.client.get(prefix + "/login").status_code, 404)
            ok = self.client.get(prefix + "/login", environ_overrides={"REMOTE_ADDR": "203.0.113.77"})
            self.assertEqual(ok.status_code, 200)
        finally:
            app.config["ADMIN_ALLOWED_IPS"] = []

    def test_forged_session_cookie_is_useless(self):
        """Cookie с user_id, но без токена сессии (uv), не даёт доступа к кабинету.
        Токен считается от хэша пароля, поэтому его нельзя подделать, зная только SECRET_KEY."""
        with self.client.session_transaction() as sess:
            sess["user_id"] = 1
        resp = self.client.get("/owner/dashboard")
        self.assertIn(resp.status_code, (302, 401))

    def test_manifest_makes_the_site_installable_as_app(self):
        import json
        resp = self.client.get("/manifest.webmanifest")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("manifest+json", resp.mimetype)
        data = json.loads(resp.get_data(as_text=True))
        self.assertEqual(data["display"], "standalone")  # без адресной строки браузера
        self.assertEqual(data["scope"], "/")
        sizes = {icon["sizes"] for icon in data["icons"]}
        self.assertTrue({"192x192", "512x512"} <= sizes)
        self.assertIn("maskable", {icon["purpose"] for icon in data["icons"]})
        for icon in data["icons"]:  # каждый файл иконки реально существует
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), icon["src"].split("?")[0].lstrip("/"))
            self.assertTrue(os.path.exists(path), path)
        self.assertNotIn(app.config["ADMIN_PREFIX"], resp.get_data(as_text=True))  # админка не упоминается

    def test_service_worker_is_served_from_root_with_version(self):
        resp = self.client.get("/sw.js")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["Service-Worker-Allowed"], "/")
        self.assertEqual(resp.headers["Cache-Control"], "no-cache")
        body = resp.get_data(as_text=True)
        self.assertNotIn("__VERSION__", body)
        self.assertIn(app.config["ASSET_VERSION"], body)
        # страницы с личными данными в кэш не попадают: кэшируется только статика
        self.assertIn("(css|js|icons|img)", body)
        self.assertNotIn("/owner", body)  # личные страницы service worker не трогает

    def test_offline_page_contains_no_session_data(self):
        html = self.client.get("/offline").get_data(as_text=True)
        self.assertIn("Нет соединения", html)
        self.assertNotIn("csrf-token", html)

    def test_pages_link_the_manifest_and_apple_icon(self):
        html = self.client.get("/login").get_data(as_text=True)
        self.assertIn('rel="manifest"', html)
        self.assertIn('rel="apple-touch-icon"', html)
        self.assertIn('name="apple-mobile-web-app-capable" content="yes"', html)
        admin_html = self.client.get(app.config["ADMIN_PREFIX"] + "/login").get_data(as_text=True)
        self.assertNotIn('rel="manifest"', admin_html)  # админку нельзя «установить»

    def test_robots_hides_private_areas(self):
        body = self.client.get("/robots.txt").get_data(as_text=True)
        for path in ("/owner/", "/orders/", "/payments/"):
            self.assertIn(f"Disallow: {path}", body)


if __name__ == "__main__":
    unittest.main()
