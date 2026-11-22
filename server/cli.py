"""Operator commands. Generated demo passwords are displayed only once on the local terminal."""
import argparse
import getpass
import os
import secrets
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .accounts import create_user
from .db import default_url, make_engine, migrate
from .models import Child, User
from .security import Vault


def main():
    parser = argparse.ArgumentParser(description="Quản trị OpenGuard Kids trên máy chủ.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="Cập nhật lược đồ SQLite.")
    sub.add_parser("seed-demo", help="Tạo hai tài khoản giả trên database trống.")
    history = sub.add_parser("seed-history", help="Thêm 10 ngày dữ liệu giả cho thiết bị demo đã ghép.")
    history.add_argument("--device-id", required=True, type=UUID)
    accounts = sub.add_parser("create-user", help="Tạo tài khoản cục bộ; nhập mật khẩu tại dấu nhắc.")
    accounts.add_argument("username")
    accounts.add_argument("--role", choices=("parent", "admin", "child-view"), default="parent")
    accounts.add_argument("--child-id", type=int)
    args = parser.parse_args()
    url = default_url()
    migrate(url)
    if args.cmd == "migrate":
        print("Database đã cập nhật.")
        return
    engine = make_engine(url)
    try:
        with Session(engine) as db, db.begin():
            if args.cmd == "create-user":
                password = getpass.getpass("Mật khẩu (từ 10 ký tự): ")
                if password != getpass.getpass("Nhập lại mật khẩu: "):
                    parser.error("Hai lần nhập mật khẩu khác nhau.")
                create_user(db, args.username, password, args.role, args.child_id)
            elif args.cmd == "seed-history":
                from .demo_history import seed_history
                count = seed_history(db, Vault(os.environ.get("OGK_SECRET_KEY", "")), args.device_id)
            else:
                if db.scalar(select(User.id).limit(1)) is not None:
                    parser.error("Seed chỉ chạy trên database trống. Dữ liệu hiện tại được giữ nguyên.")
                vault = Vault(os.environ.get("OGK_SECRET_KEY", ""))
                parent_password, child_password = (secrets.token_urlsafe(18) for _ in range(2))
                parent = create_user(db, "demo.parent", parent_password)
                child = Child(parent_id=parent.id, name_cipher=vault.encrypt("An (hồ sơ mẫu)"))
                db.add(child)
                db.flush()
                create_user(db, "demo.child", child_password, "child-view", child.id)
        if args.cmd == "seed-demo":
            print(f"Demo / phụ huynh: demo.parent   {parent_password}")
            print(f"Demo / trẻ:       demo.child    {child_password}")
        elif args.cmd == "seed-history":
            print(f"Đã thêm {count} sự kiện giả cho thiết bị demo.")
        else:
            print("Đã tạo tài khoản.")
    except (IntegrityError, ValueError) as exc:
        parser.exit(1, f"{exc}\n" if isinstance(exc, ValueError) else "Tên đã tồn tại hoặc hồ sơ liên kết không hợp lệ.\n")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
