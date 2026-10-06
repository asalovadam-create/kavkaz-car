"""
Оплата тарифов KAVKAZ-CAR.

Устроено так, чтобы подключение Rolly Pay было заменой ОДНОГО класса, а не
переписыванием сайта:

  Заказ (PaymentOrder) -> провайдер создаёт платёж -> пользователь платит ->
  провайдер шлёт вебхук -> мы проверяем подпись и сумму -> подписка включается.

Пока PAYMENT_PROVIDER=manual (по умолчанию), работает «заявка»: человек выбирает
тариф, получает номер заказа и реквизиты связи, а админ в админке нажимает
«Оплата получена» — подписка включается. Так покупка тарифа работает уже сегодня.

ВАЖНО про Rolly Pay: точный формат их API (адрес, поля запроса/ответа, имя и
алгоритм подписи вебхука) мне неизвестен — класс RollyPayProvider ниже написан по
типовой схеме и помечен местами «СВЕРИТЬ С ДОКУМЕНТАЦИЕЙ». Всё остальное
(заказы, идемпотентность, проверка суммы, активация) от провайдера не зависит.
"""
import hashlib
import hmac
import json
import urllib.error
import urllib.request
from datetime import datetime

from flask import (
    Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for,
)

from config import PLAN_DAYS_PER_MONTH, PLAN_ORDER, PLAN_PITCH, PLAN_TERMS, PLANS, plan_price, plan_term_label
from models import PaymentOrder, User, db
from security import login_required, rate_limit
from services import activate_plan, higher_plan_active, notify_admin_telegram

payments_bp = Blueprint("payments", __name__)

PAID_WORDS = {"paid", "success", "succeeded", "completed", "confirmed", "done", "approved"}
FAILED_WORDS = {"failed", "fail", "error", "declined", "canceled", "cancelled", "expired", "rejected"}


# ---------------------------------------------------------------------------
# Провайдеры
# ---------------------------------------------------------------------------

class PaymentProvider:
    name = "base"

    def is_configured(self) -> bool:
        return False

    def create_payment(self, order, return_url: str, webhook_url: str) -> dict:
        """-> {"provider_order_id": str, "pay_url": str}"""
        raise NotImplementedError

    def verify_webhook(self, raw_body: bytes, headers) -> bool:
        raise NotImplementedError

    def parse_webhook(self, payload: dict) -> dict:
        """-> {"order_ref", "provider_order_id", "status": paid|failed|pending, "amount"}"""
        raise NotImplementedError


class ManualProvider(PaymentProvider):
    """Без онлайн-оплаты: заявка + ручное подтверждение админом."""
    name = "manual"

    def is_configured(self) -> bool:
        return True


class RollyPayProvider(PaymentProvider):
    name = "rollypay"

    def is_configured(self) -> bool:
        cfg = current_app.config
        return bool(cfg.get("ROLLYPAY_API_URL") and cfg.get("ROLLYPAY_API_KEY") and cfg.get("ROLLYPAY_SECRET"))

    def create_payment(self, order, return_url: str, webhook_url: str) -> dict:
        cfg = current_app.config
        # СВЕРИТЬ С ДОКУМЕНТАЦИЕЙ Rolly Pay: имена полей и авторизация.
        payload = {
            "shop_id": cfg.get("ROLLYPAY_SHOP_ID") or None,
            "order_id": order.public_id,
            "amount": order.amount,
            "currency": "RUB",
            "description": f"KAVKAZ-CAR: тариф {PLANS[order.plan]['name']}, {plan_term_label(order.months)}",
            "success_url": return_url,
            "fail_url": return_url,
            "callback_url": webhook_url,
        }
        request_ = urllib.request.Request(
            cfg["ROLLYPAY_API_URL"],
            data=json.dumps({k: v for k, v in payload.items() if v is not None}).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {cfg['ROLLYPAY_API_KEY']}"},
            method="POST",
        )
        with urllib.request.urlopen(request_, timeout=10) as response:  # noqa: S310 — адрес задаёт администратор
            data = json.loads(response.read().decode())
        data = data.get("data", data) if isinstance(data, dict) else {}
        pay_url = data.get("pay_url") or data.get("payment_url") or data.get("url") or data.get("link")
        if not pay_url or not str(pay_url).startswith("https://"):
            raise RuntimeError("Платёжная система не вернула ссылку на оплату")
        return {
            "provider_order_id": str(data.get("id") or data.get("payment_id") or data.get("order_id") or ""),
            "pay_url": str(pay_url),
        }

    def verify_webhook(self, raw_body: bytes, headers) -> bool:
        # СВЕРИТЬ С ДОКУМЕНТАЦИЕЙ: схема подписи. Здесь — HMAC-SHA256(тело, секрет) в hex.
        secret = current_app.config.get("ROLLYPAY_SECRET", "")
        if not secret:
            return False
        header_name = current_app.config.get("ROLLYPAY_SIGNATURE_HEADER", "X-Signature")
        received = (headers.get(header_name) or "").strip().lower().removeprefix("sha256=")
        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return bool(received) and hmac.compare_digest(received, expected)

    def parse_webhook(self, payload: dict) -> dict:
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        raw_status = str(data.get("status", "")).lower()
        status = "paid" if raw_status in PAID_WORDS else "failed" if raw_status in FAILED_WORDS else "pending"
        amount = data.get("amount")
        try:
            amount = float(amount) if amount is not None else None
        except (TypeError, ValueError):
            amount = None
        return {
            "order_ref": str(data.get("order_id") or data.get("merchant_order_id") or data.get("external_id") or ""),
            "provider_order_id": str(data.get("id") or data.get("payment_id") or ""),
            "status": status,
            "amount": amount,
        }


def get_provider() -> PaymentProvider:
    if current_app.config.get("PAYMENT_PROVIDER") == "rollypay":
        provider = RollyPayProvider()
        if provider.is_configured():
            return provider
        current_app.logger.error("PAYMENT_PROVIDER=rollypay, но ключи не заданы — работаем в режиме заявок")
    return ManualProvider()


# ---------------------------------------------------------------------------
# Страница тарифов (публичная) и оформление заказа
# ---------------------------------------------------------------------------

@payments_bp.route("/pricing")
def pricing():
    months = request.args.get("months", 1, type=int)
    if months not in dict(PLAN_TERMS):
        months = 1
    terms = [
        {"months": m, "discount": d, "label": plan_term_label(m)} for m, d in PLAN_TERMS
    ]
    cards = []
    for key in PLAN_ORDER:
        info = PLANS[key]
        cards.append({
            "key": key, "info": info, "pitch": PLAN_PITCH[key],
            "price": plan_price(key, months), "per_month": round(plan_price(key, months) / months),
            "full_price": info["price"] * months,
        })
    current_plan = g.current_user.current_plan() if g.current_user else None
    sub = g.current_user.active_subscription() if g.current_user else None
    for card in cards:
        if not g.current_user:
            card["state"] = "guest"
        elif card["key"] == "free":
            card["state"] = "current" if current_plan == "free" else "free"
        elif card["key"] == current_plan:
            card["state"] = "renew"  # тот же тариф — можно продлить
        elif higher_plan_active(g.current_user, card["key"]):
            card["state"] = "blocked"
        else:
            card["state"] = "buy"
    return render_template(
        "pricing.html", cards=cards, terms=terms, months=months, current_plan=current_plan,
        subscription=sub, popular="pro",
    )


@payments_bp.route("/checkout", methods=["POST"])
@login_required
@rate_limit("checkout")
def checkout():
    plan = request.form.get("plan")
    months = request.form.get("months", 1, type=int)
    if plan not in PLANS or plan == "free" or months not in dict(PLAN_TERMS):
        flash("Выберите тариф и срок.", "error")
        return redirect(url_for("payments.pricing"))

    user = g.current_user
    blocking = higher_plan_active(user, plan)
    if blocking:
        flash(
            f"У вас уже действует тариф {blocking.plan.upper()} до {blocking.expires_at:%d.%m.%Y}. "
            "Снизить тариф можно после его окончания.", "error",
        )
        return redirect(url_for("payments.pricing"))

    # Повторный клик по «Оплатить» не должен плодить заказы: переиспользуем открытый.
    order = PaymentOrder.query.filter_by(user_id=user.id, plan=plan, months=months).filter(
        PaymentOrder.status.in_(("created", "awaiting_payment", "awaiting_manual"))
    ).order_by(PaymentOrder.created_at.desc()).first()
    if order:
        return redirect(url_for("payments.order_page", public_id=order.public_id))

    if not user.is_owner:
        user.is_owner = True  # покупка тарифа = человек хочет сдавать авто

    provider = get_provider()
    order = PaymentOrder(
        user_id=user.id, plan=plan, months=months, amount=plan_price(plan, months), provider=provider.name,
    )
    db.session.add(order)
    db.session.flush()

    if provider.name == "manual":
        order.status = "awaiting_manual"
        notify_admin_telegram(
            f"Заявка на тариф {PLANS[plan]['name']} ({plan_term_label(months)}), {order.amount} ₽ — "
            f"{user.phone}, заказ {order.public_id}"
        )
    else:
        try:
            created = provider.create_payment(
                order,
                return_url=current_app.config["SITE_URL"] + url_for("payments.order_page", public_id=order.public_id),
                webhook_url=current_app.config["SITE_URL"] + url_for("payments.webhook", provider_name=provider.name),
            )
            order.provider_order_id = created.get("provider_order_id") or None
            order.pay_url = created["pay_url"]
            order.status = "awaiting_payment"
        except (urllib.error.URLError, RuntimeError, ValueError, KeyError, TimeoutError):
            current_app.logger.exception("Не удалось создать платёж %s", order.public_id)
            order.status = "failed"
            order.note = "Ошибка создания платежа"
            flash("Платёжная система сейчас недоступна. Попробуйте позже или напишите в поддержку.", "error")

    db.session.commit()
    return redirect(url_for("payments.order_page", public_id=order.public_id))


@payments_bp.route("/orders/<public_id>")
@login_required
def order_page(public_id):
    order = PaymentOrder.query.filter_by(public_id=public_id).first_or_404()
    if order.user_id != g.current_user.id:
        abort(404)
    return render_template("order.html", order=order, plan_info=PLANS[order.plan], term=plan_term_label(order.months))


@payments_bp.route("/orders/<public_id>/cancel", methods=["POST"])
@login_required
def cancel_order(public_id):
    order = PaymentOrder.query.filter_by(public_id=public_id).first_or_404()
    if order.user_id != g.current_user.id:
        abort(404)
    if order.is_open:
        order.status = "cancelled"
        db.session.commit()
        flash("Заказ отменён.", "success")
    return redirect(url_for("payments.pricing"))


# ---------------------------------------------------------------------------
# Вебхук платёжной системы
# ---------------------------------------------------------------------------

def mark_order_paid(order, *, provider: str, transaction_id: str | None = None, admin_id: int | None = None) -> bool:
    """Идемпотентно включает тариф по заказу. Возвращает True, если включили сейчас.
    Повторный вызов (повторный вебхук, двойной клик админа) ничего не сломает."""
    if order.status == "paid":
        return False
    user = db.session.get(User, order.user_id)
    activate_plan(
        user, order.plan, order.months * PLAN_DAYS_PER_MONTH, provider=provider,
        transaction_id=transaction_id or order.provider_order_id or order.public_id, admin_id=admin_id,
    )
    order.status = "paid"
    order.paid_at = datetime.utcnow()
    return True


@payments_bp.route("/payments/webhook/<provider_name>", methods=["POST"])
def webhook(provider_name):
    """Принимает уведомления об оплате. Защита: подпись (иначе любой мог бы
    «оплатить» тариф запросом с интернета), сверка суммы и идемпотентность."""
    provider = get_provider()
    if provider.name != provider_name or provider.name == "manual":
        abort(404)

    raw = request.get_data(cache=True)
    if not provider.verify_webhook(raw, request.headers):
        current_app.logger.warning("Вебхук оплаты с неверной подписью")
        abort(403)

    try:
        event = provider.parse_webhook(json.loads(raw.decode() or "{}"))
    except (ValueError, UnicodeDecodeError):
        abort(400)

    query = PaymentOrder.query.with_for_update()  # блокируем строку: два вебхука не включат тариф дважды
    order = query.filter_by(public_id=event["order_ref"]).first() if event["order_ref"] else None
    if order is None and event["provider_order_id"]:
        order = query.filter_by(provider_order_id=event["provider_order_id"]).first()
    if order is None:
        abort(404)

    if event["status"] == "paid":
        if event["amount"] is not None and abs(event["amount"] - order.amount) > 0.01:
            current_app.logger.error("Сумма вебхука %s не совпала с заказом %s (%s)", event["amount"], order.public_id, order.amount)
            order.note = f"Несовпадение суммы: пришло {event['amount']}"
            db.session.commit()
            abort(400)
        mark_order_paid(order, provider=provider.name, transaction_id=event["provider_order_id"])
    elif event["status"] == "failed" and order.status != "paid":
        order.status = "failed"
    db.session.commit()
    return jsonify(ok=True)
