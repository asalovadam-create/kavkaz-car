"""
Безопасная подготовка базы при старте приложения.

Почему это нужно: папка migrations/ в проекте пустая, поэтому `flask db upgrade`
ничего не делает, а новые таблицы/колонки сами не появляются. Здесь — только
АДДИТИВНЫЕ изменения (создать недостающее), ничего не удаляется и не
переписывается, поэтому запускать безопасно сколько угодно раз и из нескольких
worker-процессов одновременно.

Если вы позже начнёте вести настоящие миграции Alembic — отключите это,
поставив AUTO_BOOTSTRAP=0.
"""
from sqlalchemy import inspect, text

from config import LAUNCH_CITIES
from models import City, OwnerProfile, db, _short_id

# (таблица, колонка, DDL типа) — колонки, добавленные после первого релиза.
ADDITIVE_COLUMNS = [
    ("owner_profiles", "public_id", "VARCHAR(12)"),
    ("admin_users", "totp_secret", "VARCHAR(64)"),
]


def ensure_schema(app) -> None:
    with app.app_context():
        try:
            db.create_all()  # создаёт только отсутствующие таблицы
            _add_missing_columns(app)
            _backfill_owner_public_ids()
            _seed_cities()
        except Exception:  # старт сайта не должен падать из-за временной недоступности БД
            db.session.rollback()
            app.logger.exception("Не удалось подготовить схему БД при старте (повторите: flask seed-db)")


def _add_missing_columns(app) -> None:
    inspector = inspect(db.engine)
    dialect = db.engine.dialect.name
    for table, column, ddl in ADDITIVE_COLUMNS:
        if table not in inspector.get_table_names():
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        if column in existing:
            continue
        try:
            if dialect == "postgresql":
                db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}"))
            else:
                db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            db.session.commit()
            app.logger.info("Добавлена колонка %s.%s", table, column)
        except Exception:
            db.session.rollback()  # другой worker мог добавить её первым — это нормально
    try:
        db.session.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_owner_profiles_public_id ON owner_profiles (public_id)"
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()


def _backfill_owner_public_ids() -> None:
    missing = OwnerProfile.query.filter(OwnerProfile.public_id.is_(None)).all()
    if not missing:
        return
    used = {pid for (pid,) in db.session.query(OwnerProfile.public_id).filter(OwnerProfile.public_id.isnot(None))}
    for profile in missing:
        pid = _short_id()
        while pid in used:
            pid = _short_id()
        used.add(pid)
        profile.public_id = pid
    db.session.commit()


def _seed_cities() -> None:
    existing = {slug for (slug,) in db.session.query(City.slug)}
    added = False
    for data in LAUNCH_CITIES:
        if data["slug"] not in existing:
            db.session.add(City(**data))
            added = True
    if added:
        db.session.commit()
