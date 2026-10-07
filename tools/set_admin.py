"""
Создаёт администратора или меняет ему пароль (спрашивает логин и пароль, пароль не показывается).

    docker compose run --rm web python tools/set_admin.py
"""
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402
from models import AdminUser, db  # noqa: E402
from security import hash_password  # noqa: E402

MIN_LEN = 12


def main() -> None:
    username = input("Логин администратора [admin]: ").strip() or "admin"
    password = getpass.getpass(f"Пароль (минимум {MIN_LEN} символов): ")
    if len(password) < MIN_LEN:
        print(f"Пароль слишком короткий: нужно не меньше {MIN_LEN} символов. Запустите команду ещё раз.")
        sys.exit(1)
    if getpass.getpass("Повторите пароль: ") != password:
        print("Пароли не совпали. Запустите команду ещё раз.")
        sys.exit(1)

    with app.app_context():
        admin = AdminUser.query.filter_by(username=username).first()
        if admin:
            admin.password_hash = hash_password(password)
            admin.is_active = True
            db.session.commit()
            print(f"Пароль администратора «{username}» обновлён.")
        else:
            db.session.add(AdminUser(username=username, password_hash=hash_password(password), is_super=True))
            db.session.commit()
            print(f"Администратор «{username}» создан.")


if __name__ == "__main__":
    main()
