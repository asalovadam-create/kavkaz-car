"""Чат пользователя с поддержкой прямо на сайте. Ответы админа видны в админке и приходят
пользователю в «колокольчик» уведомлений."""
from datetime import datetime

from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, url_for

from models import SupportMessage, SupportThread, db
from security import login_required, rate_limit, wants_json
from validators import clean_text

chat_bp = Blueprint("chat", __name__)

MAX_LEN = 2000
HISTORY = 100


def _serialize(message: SupportMessage) -> dict:
    return {
        "id": message.id,
        "sender": message.sender,
        "body": message.body,
        "time": message.created_at.strftime("%d.%m %H:%M"),
    }


@chat_bp.route("/chat")
@login_required
def chat():
    thread = SupportThread.query.filter_by(user_id=g.current_user.id).first()
    messages = []
    if thread:
        messages = (
            SupportMessage.query.filter_by(thread_id=thread.id)
            .order_by(SupportMessage.id.desc()).limit(HISTORY).all()
        )[::-1]
        if thread.user_unread:
            thread.user_unread = False
            db.session.commit()
    last_id = messages[-1].id if messages else 0
    return render_template("chat.html", messages=messages, last_id=last_id)


@chat_bp.route("/chat/send", methods=["POST"])
@login_required
@rate_limit("chat_send")
def chat_send():
    body = clean_text(request.form.get("message"), MAX_LEN)
    if not body:
        if wants_json():
            return jsonify(error="Напишите сообщение."), 400
        flash("Напишите сообщение.", "error")
        return redirect(url_for("chat.chat"))

    thread = SupportThread.query.filter_by(user_id=g.current_user.id).first()
    now = datetime.utcnow()
    if not thread:
        thread = SupportThread(user_id=g.current_user.id, created_at=now, updated_at=now)
        db.session.add(thread)
        db.session.flush()
    message = SupportMessage(thread_id=thread.id, sender="user", body=body, created_at=now)
    db.session.add(message)
    thread.status = "open"
    thread.admin_unread = True
    thread.updated_at = now
    thread.last_message = body[:200]
    db.session.commit()

    if wants_json():
        return jsonify(ok=True, message=_serialize(message))
    return redirect(url_for("chat.chat"))


@chat_bp.route("/chat/poll")
@login_required
def chat_poll():
    """Новые сообщения после указанного id (браузер спрашивает раз в несколько секунд)."""
    thread = SupportThread.query.filter_by(user_id=g.current_user.id).first()
    if not thread:
        return jsonify(messages=[])
    try:
        after = int(request.args.get("after", 0))
    except ValueError:
        after = 0
    fresh = (
        SupportMessage.query.filter(SupportMessage.thread_id == thread.id, SupportMessage.id > after)
        .order_by(SupportMessage.id).limit(50).all()
    )
    if fresh and thread.user_unread:
        thread.user_unread = False
        db.session.commit()
    return jsonify(messages=[_serialize(m) for m in fresh])
