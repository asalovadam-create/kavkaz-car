"""
Пересоздаёт миниатюры уже загруженных фото в новом размере (800 px вместо 400 px).

    docker compose run --rm web python tools/rebuild_thumbs.py

Миниатюры получают НОВЫЕ имена файлов: иначе браузеры до 30 дней показывали бы старые размытые
из кэша. Старый файл миниатюры удаляется. Повторный запуск безопасен: уже обновлённые пропускаются.
Если основное фото само было маленьким или размытым (скачано из мессенджера), чётче оно не станет:
такое фото нужно загрузить заново, лучше оригинал с телефона.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

from app import app  # noqa: E402
from models import CarPhoto, db  # noqa: E402
from services import THUMB_MAX_DIMENSION, optimize_image  # noqa: E402

UPLOAD_DIR = os.path.join(app.root_path, "static", "uploads")


def local_path(url):
    if url and url.startswith("/static/uploads/"):
        return os.path.join(UPLOAD_DIR, os.path.basename(url))
    return None


def main() -> None:
    done = skipped = failed = 0
    with app.app_context():
        for photo in CarPhoto.query.order_by(CarPhoto.id).all():
            main_path = local_path(photo.url)
            if not main_path or not os.path.exists(main_path):
                skipped += 1
                continue
            try:
                old_path = local_path(photo.thumbnail_url)
                with Image.open(main_path) as img:
                    img.load()
                    target = min(THUMB_MAX_DIMENSION, max(img.size))
                    old_size = 0
                    if old_path and old_path != main_path and os.path.exists(old_path):
                        with Image.open(old_path) as old_img:
                            old_size = max(old_img.size)
                    if old_size >= target:
                        skipped += 1
                        continue
                    _, thumb_bytes = optimize_image(img)
                name = f"{uuid.uuid4().hex}.webp"
                with open(os.path.join(UPLOAD_DIR, name), "wb") as fh:
                    fh.write(thumb_bytes)
                photo.thumbnail_url = f"/static/uploads/{name}"
                db.session.commit()
                if old_path and old_path != main_path and os.path.exists(old_path):
                    os.remove(old_path)
                done += 1
            except Exception as exc:  # одно битое фото не останавливает остальные
                db.session.rollback()
                failed += 1
                print(f"ОШИБКА фото #{photo.id}: {exc}")
    print(f"Готово. Обновлено: {done}, пропущено: {skipped}, ошибок: {failed}")


if __name__ == "__main__":
    main()
