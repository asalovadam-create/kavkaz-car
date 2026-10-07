"""
Сквозные тесты с настоящей базой (SQLite в памяти).
Запуск:  python -m unittest tests.test_smoke_db -v

Требуют установленных зависимостей из requirements.txt (Flask-SQLAlchemy и т.д.).
"""
import os
import sys
import unittest

os.environ["SECRET_KEY"] = "k" * 48
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["AUTO_BOOTSTRAP"] = "0"
os.environ["ADMIN_SECRET"] = ""
os.environ["PAYMENT_PROVIDER"] = "manual"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import security  # noqa: E402
from app import app  # noqa: E402
from bootstrap import ensure_schema  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402

from models import AdminUser, Car, City, Notification, PaymentOrder, Subscription, User, View, db  # noqa: E402


def post(client, url, data=None, **kwargs):
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "tok"
    return client.post(url, data={"csrf_token": "tok", **(data or {})}, **kwargs)


def register_owner(client, phone="+7 900 111-22-33"):
    return post(client, "/register", {
        "full_name": "Тест Владелец", "phone": phone, "age": "30", "role": "owner",
        "password": "password123", "password_confirm": "password123",
    })


class SmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        ensure_schema(app)

    def setUp(self):
        security._attempts.clear()
        self.client = app.test_client()
        with app.app_context():
            for model in (Notification, Subscription, View, PaymentOrder, Car, User, AdminUser):
                db.session.query(model).delete()
            db.session.commit()

    def _make_car(self, owner_phone="+79001112233", status="published"):
        with app.app_context():
            owner = User.query.filter_by(phone=owner_phone).first()
            city = City.query.filter_by(slug="grozny").first()
            car = Car(owner_id=owner.id, city_id=city.id, brand="BMW", model="X5", phone="+79001112233",
                      contact_consent=True, status=status, price_per_day=9000)
            db.session.add(car)
            db.session.commit()
            return car.public_id, car.id

    def test_owner_registration_goes_to_wizard(self):
        resp = register_owner(self.client)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/owner/cars/new", resp.headers["Location"])

    def test_client_registration_is_not_owner(self):
        post(self.client, "/register", {
            "full_name": "Клиент", "phone": "+7 900 222-33-44", "age": "25", "role": "client",
            "password": "password123", "password_confirm": "password123",
        })
        with app.app_context():
            self.assertFalse(User.query.filter_by(phone="+79002223344").first().is_owner)
        # клиенту закрыт кабинет владельца и ведёт на «Стать владельцем», а не в профиль
        resp = self.client.get("/owner/dashboard")
        self.assertIn("/owner/become", resp.headers["Location"])

    def test_login_ignores_external_next(self):
        register_owner(self.client)
        self.client.post("/logout", data={"csrf_token": "x"})  # без токена — не выйдет, это нормально
        fresh = app.test_client()
        resp = post(fresh, "/login?next=//evil.example.com", {"phone": "+79001112233", "password": "password123"})
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn("evil.example.com", resp.headers["Location"])

    def test_wrong_password_is_rejected_and_throttled(self):
        register_owner(self.client)
        fresh = app.test_client()
        codes = [post(fresh, "/login", {"phone": "+79001112233", "password": "nope"}).status_code for _ in range(9)]
        self.assertEqual(codes[0], 400)
        self.assertEqual(codes[-1], 429)

    def test_wizard_cannot_publish_without_photo(self):
        register_owner(self.client)
        post(self.client, "/owner/cars/new/basics", {"brand": "BMW", "model": "X5"})
        post(self.client, "/owner/cars/new/terms", {"price_per_day": "9000"})
        post(self.client, "/owner/cars/new/location", {"city_slug": "grozny"})
        post(self.client, "/owner/cars/new/contact", {"phone": "+79001112233", "contact_consent": "1"})
        resp = post(self.client, "/owner/cars/new/review")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/photos", resp.headers["Location"])
        with app.app_context():
            self.assertEqual(Car.query.count(), 0)

    def test_wizard_validates_price_and_manual_transmission(self):
        register_owner(self.client)
        bad = post(self.client, "/owner/cars/new/terms", {"price_per_day": "-5"})
        self.assertEqual(bad.status_code, 400)
        ok = post(self.client, "/owner/cars/new/terms", {"price_per_day": "9000"})  # чекбокс коробки не отмечен
        self.assertEqual(ok.status_code, 302)

    def test_owner_cannot_unblock_blocked_car(self):
        register_owner(self.client)
        public_id, car_id = self._make_car(status="blocked")
        post(self.client, f"/owner/cars/{public_id}/edit", {
            "brand": "BMW", "model": "X5", "city_slug": "grozny", "phone": "+79001112233", "status": "published",
        })
        with app.app_context():
            self.assertEqual(db.session.get(Car, car_id).status, "blocked")

    def test_delete_car_with_views_works(self):
        register_owner(self.client)
        public_id, car_id = self._make_car()
        with app.app_context():
            db.session.add(View(car_id=car_id, viewer_hash="h"))
            db.session.commit()
        resp = post(self.client, f"/owner/cars/{public_id}/delete")
        self.assertEqual(resp.status_code, 302)
        with app.app_context():
            self.assertIsNone(db.session.get(Car, car_id))

    def test_other_users_car_is_404(self):
        register_owner(self.client)
        public_id, _ = self._make_car()
        other = app.test_client()
        register_owner(other, phone="+7 900 999-88-77")
        self.assertEqual(post(other, f"/owner/cars/{public_id}/delete").status_code, 404)

    def test_pricing_checkout_and_admin_confirmation(self):
        self.assertEqual(self.client.get("/pricing").status_code, 200)
        register_owner(self.client)
        resp = post(self.client, "/checkout", {"plan": "pro", "months": "1"})
        self.assertEqual(resp.status_code, 302)
        with app.app_context():
            order = PaymentOrder.query.one()
            self.assertEqual(order.status, "awaiting_manual")
            order_id = order.id
            admin = AdminUser(username="boss", password_hash=security.hash_password("a-very-long-password"), is_super=True)
            db.session.add(admin)
            db.session.commit()

        admin_client = app.test_client()
        login = post(admin_client, app.config["ADMIN_PREFIX"] + "/login", {"username": "boss", "password": "a-very-long-password"})
        self.assertEqual(login.status_code, 302)
        post(admin_client, f"{app.config['ADMIN_PREFIX']}/orders/{order_id}/confirm")
        post(admin_client, f"{app.config['ADMIN_PREFIX']}/orders/{order_id}/confirm")  # повторное нажатие безопасно
        with app.app_context():
            user = User.query.filter_by(phone="+79001112233").first()
            self.assertEqual(user.current_plan(), "pro")
            self.assertEqual(PaymentOrder.query.one().status, "paid")

    def test_expired_subscription_pauses_extra_cars_and_purchase_restores_them(self):
        """Тариф закончился: остаётся 1 (самое раннее) объявление, остальные приостановлены, но не удалены.
        После новой покупки они возвращаются в каталог."""
        from services import activate_plan, sweep_expired_subscriptions

        register_owner(self.client)
        with app.app_context():
            user = User.query.filter_by(phone="+79001112233").first()
            city = City.query.filter_by(slug="grozny").first()
            for days_ago in (10, 5, 1):
                db.session.add(Car(owner_id=user.id, city_id=city.id, brand="BMW", model=f"X{days_ago}",
                                   phone="+79001112233", contact_consent=True, status="published",
                                   created_at=datetime.utcnow() - timedelta(days=days_ago)))
            db.session.add(Subscription(owner_id=user.id, plan="pro", status="active",
                                        started_at=datetime.utcnow() - timedelta(days=40),
                                        expires_at=datetime.utcnow() - timedelta(days=1)))
            db.session.commit()

            self.assertEqual(sweep_expired_subscriptions(), 1)
            statuses = [c.status for c in Car.query.order_by(Car.created_at)]
            self.assertEqual(statuses, ["published", "expired", "expired"])
            self.assertEqual(Car.query.count(), 3)  # ничего не удалено
            self.assertGreaterEqual(Notification.query.filter_by(user_id=user.id).count(), 1)
            self.assertEqual(sweep_expired_subscriptions(), 0)  # повторный запуск ничего не меняет

            user = User.query.filter_by(phone="+79001112233").first()
            activate_plan(user, "pro", 30, provider="test")
            db.session.commit()
            self.assertEqual([c.status for c in Car.query.order_by(Car.created_at)], ["published"] * 3)

    def test_notifications_page_and_admin_message(self):
        register_owner(self.client)
        with app.app_context():
            user = User.query.filter_by(phone="+79001112233").first()
            from services import notify_user
            notify_user(user.id, "Привет от админа", "Текст", kind="admin")
            db.session.commit()
        resp = self.client.get("/notifications")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Привет от админа", resp.get_data(as_text=True))
        with app.app_context():
            self.assertEqual(Notification.query.filter_by(is_read=False).count(), 0)

    def test_sitemap_does_not_reveal_admin(self):
        body = self.client.get("/sitemap.xml").get_data(as_text=True).lower()
        self.assertNotIn("admin", body)
        self.assertNotIn(app.config["ADMIN_PREFIX"].lower(), body)

    def test_catalog_and_search_render(self):
        register_owner(self.client)
        self._make_car()
        for url in ("/", "/catalog", "/catalog?q=bmw", "/catalog?q=грозный&sort=price_asc", "/grozny", "/help-me-choose?go=1"):
            self.assertEqual(self.client.get(url).status_code, 200, url)


if __name__ == "__main__":
    unittest.main()
