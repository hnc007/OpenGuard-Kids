from sqlalchemy.orm import Session

from .models import User
from .security import hash_password, normalize_username


def create_user(db: Session, username: str, password: str, role: str = "parent", child_id: int | None = None) -> User:
    if role not in {"parent", "child-view", "admin"} or (role == "child-view") != (child_id is not None):
        raise ValueError("Vai trò và liên kết hồ sơ trẻ chưa hợp lệ.")
    user = User(username=normalize_username(username), password_hash=hash_password(password), role=role, child_id=child_id)
    db.add(user)
    db.flush()
    return user
