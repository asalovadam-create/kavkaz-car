"""
Переносит фото из Cloudinary на диск сервера и меняет ссылки в базе.

Запуск (на сервере, из папки проекта):
    docker compose run --rm web python tools/migrate_photos.py --dry-run   # только посмотреть
    docker compose run --rm web python tools/migrate_photos.py             # перенести

Безопасно запускать повторно: уже перенесённые фото пропускаются, а если файл
не скачался — запись в базе не меняется и старая ссылка продолжает работать.
"""
import io
import os
import sys
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

from app import app  # noqa: E402
from models import CarPhoto, OwnerProfile, db  # noqa: E402

DRY = "--dry-run" in sys.argv
UPLOAD_DIR = os.path.join(app.root_path, "static", "uploads")
_cache: dict[str, tuple[str, str]] = {}


def is_remote(url) -> bool:
    return bool(url) and url.startswith(("http://", "https://"))


def download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "kavkaz-car-migration"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return resp.read()


def save_local(url: str) -> tuple[str, str]:
    """Скачивает картинку, сохраняет как webp, возвращает (новая_ссылка, public_id)."""
    if url in _cache:
        return _cache[url]
    data = download(url)
    if not (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        image = Image.open(io.BytesIO(data)).convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="WEBP", quality=82, method=4)
        data = buffer.getvalue()
    public_id = uuid.uuid4().hex
    with open(os.path.join(UPLOAD_DIR, f"{public_id}.webp"), "wb") as fh:
        fh.write(data)
    result = (f"/static/uploads/{public_id}.webp", public_id)
    _cache[url] = result
    return result


def main() -> None:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    ok = failed = skipped = 0
    with app.app_context():
        for photo in CarPhoto.query.order_by(CarPhoto.id).all():
            if not (is_remote(photo.url) or is_remote(photo.thumbnail_url)):
                skipped += 1
                continue
            try:
                if DRY:
                    print(f"[dry] фото #{photo.id}: {photo.url}")
                    ok += 1
                    continue
                if is_remote(photo.url):
                    photo.url, photo.storage_public_id = save_local(photo.url)
                if is_remote(photo.thumbnail_url):
                    photo.thumbnail_url, _ = save_local(photo.thumbnail_url)
                db.session.commit()
                ok += 1
            except Exception as exc:  # одно битое фото не должно останавливать перенос
                db.session.rollback()
                failed += 1
                print(f"ОШИБКА фото #{photo.id}: {exc}")

        for profile in OwnerProfile.query.all():
            if not is_remote(profile.logo_url):
                continue
            try:
                if DRY:
                    print(f"[dry] логотип профиля #{profile.id}: {profile.logo_url}")
                    continue
                profile.logo_url, _ = save_local(profile.logo_url)
                db.session.commit()
                ok += 1
            except Exception as exc:
                db.session.rollback()
                failed += 1
                print(f"ОШИБКА логотипа профиля #{profile.id}: {exc}")

    print(f"Готово. Перенесено: {ok}, пропущено (уже локальные): {skipped}, ошибок: {failed}")


if __name__ == "__main__":
    main()
