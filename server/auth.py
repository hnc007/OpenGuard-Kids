import hmac
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from .db import get_db, get_vault
from .models import Child, LoginSession, LoginWindow, User
from .security import digest, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["Tài khoản"])
SESSION_LIFETIME = 12 * 60 * 60
LOCKOUT = 15 * 60
IP_LIMIT = 30
SESSION_COOKIE = "ogk_session"
GUEST_COOKIE = "ogk_guest"


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=10, max_length=256)


def now() -> int:
    return int(time.time())


def _set_cookie(request: Request, response: Response, name: str, value: str, lifetime: int):
    response.set_cookie(name, value, max_age=lifetime, httponly=True, secure=request.app.state.secure_cookies,
                        samesite="lax", path="/")


def _csrf(request: Request, binding: str) -> str:
    return get_vault(request).tag("csrf-v1", binding)


def require_csrf(request: Request) -> None:
    """The submitted token must match a secret bound to this browser's cookie."""
    origin = request.headers.get("origin")
    expected_origin = f"{request.url.scheme}://{request.url.netloc}"
    if origin and origin != expected_origin:
        raise HTTPException(403, "Nguồn yêu cầu không hợp lệ.")
    binding = request.cookies.get(SESSION_COOKIE) or request.cookies.get(GUEST_COOKIE)
    supplied = request.headers.get("x-csrf-token", "")
    if not binding or not hmac.compare_digest(_csrf(request, binding).encode(), supplied.encode()):
        raise HTTPException(403, "Phiên xác nhận đã thay đổi. Hãy tải lại trang.")


@router.get("/csrf")
def csrf(request: Request, response: Response):
    binding = request.cookies.get(SESSION_COOKIE) or request.cookies.get(GUEST_COOKIE)
    if not binding:
        binding = secrets.token_urlsafe(32)
        _set_cookie(request, response, GUEST_COOKIE, binding, LOCKOUT)
    return {"csrf_token": _csrf(request, binding)}


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(SESSION_COOKIE, "")
    user = db.scalar(select(User).join(LoginSession, LoginSession.user_id == User.id).where(
        LoginSession.token_hash == digest(token), LoginSession.expires_at > now()))
    if not user:
        raise HTTPException(401, "Vui lòng đăng nhập.")
    return user


def require_parent(user: User = Depends(current_user)) -> User:
    if user.role != "parent":
        raise HTTPException(403, "Thao tác này dành cho phụ huynh.")
    return user


def child_scope(user: User):
    if user.role == "parent":
        return Child.parent_id == user.id
    if user.role == "child-view":
        return Child.id == user.child_id
    raise HTTPException(403, "Tài khoản này không có quyền xem hồ sơ gia đình.")


def owned_child(db: Session, user: User, child_id: int) -> Child:
    child = db.scalar(select(Child).where(Child.id == child_id, child_scope(user)))
    if not child:
        raise HTTPException(404, "Không tìm thấy hồ sơ.")
    return child


def _profile(request: Request, user: User, binding: str) -> dict:
    return {"id": user.id, "username": user.username, "role": user.role, "child_id": user.child_id,
            "csrf_token": _csrf(request, binding)}


@router.post("/login", dependencies=[Depends(require_csrf)])
def login(body: Login, request: Request, response: Response, db: Session = Depends(get_db)):
    timestamp = now()
    address = request.client.host if request.client else "unknown"
    bucket = get_vault(request).tag("login-IP-v1", address)
    # Reserve the attempt under a short writer lock before the expensive password verification.
    db.execute(text("BEGIN IMMEDIATE"))
    db.execute(delete(LoginWindow).where(LoginWindow.expires_at <= timestamp))
    window = db.get(LoginWindow, bucket)
    if window is None:
        db.add(LoginWindow(bucket=bucket, attempts=1, expires_at=timestamp + LOCKOUT))
    elif window.attempts >= IP_LIMIT:
        raise HTTPException(429, "Quá nhiều lần đăng nhập. Hãy thử lại sau.",
                            headers={"Retry-After": str(window.expires_at - timestamp)})
    else:
        window.attempts += 1
    db.commit()

    username = body.username.strip().lower()
    password_hash = db.scalar(select(User.password_hash).where(User.username == username))
    # Unknown and locked accounts return the same response and perform one scrypt operation.
    ok = verify_password(body.password, password_hash or request.app.state.dummy_hash)
    db.rollback()
    db.execute(text("BEGIN IMMEDIATE"))
    user = db.scalar(select(User).where(User.username == username))
    timestamp = now()
    if not user or user.locked_until > timestamp:
        raise HTTPException(401, "Tên đăng nhập hoặc mật khẩu chưa đúng, hoặc tài khoản đang tạm khóa.")
    if user.locked_until:
        user.failures = 0
        user.locked_until = 0
    if not ok or user.password_hash != password_hash:
        user.failures += 1
        if user.failures >= 5:
            user.locked_until = timestamp + LOCKOUT
        db.commit()
        raise HTTPException(401, "Tên đăng nhập hoặc mật khẩu chưa đúng, hoặc tài khoản đang tạm khóa.")
    user.failures = 0
    user.locked_until = 0
    previous = request.cookies.get(SESSION_COOKIE, "")
    db.execute(delete(LoginSession).where(
        (LoginSession.expires_at <= timestamp) | (LoginSession.token_hash == digest(previous))))
    token = secrets.token_urlsafe(32)
    db.add(LoginSession(token_hash=digest(token), user_id=user.id, expires_at=timestamp + SESSION_LIFETIME))
    db.commit()
    _set_cookie(request, response, SESSION_COOKIE, token, SESSION_LIFETIME)
    response.delete_cookie(GUEST_COOKIE, secure=request.app.state.secure_cookies, httponly=True, samesite="lax")
    return _profile(request, user, token)


@router.get("/me")
def me(request: Request, user: User = Depends(current_user)):
    return _profile(request, user, request.cookies[SESSION_COOKIE])


@router.post("/password", dependencies=[Depends(require_csrf)], status_code=204)
def change_password(body: PasswordChange, request: Request, user: User = Depends(require_parent),
                    db: Session = Depends(get_db)):
    from .audit import record

    if body.current_password == body.new_password:
        raise HTTPException(422, "Hãy chọn mật khẩu khác mật khẩu hiện tại.")
    snapshot = user.password_hash
    user_id = user.id
    valid = verify_password(body.current_password, snapshot)
    replacement = hash_password(body.new_password) if valid else None
    db.rollback()
    db.execute(text("BEGIN IMMEDIATE"))
    active = db.scalar(select(User).join(LoginSession, LoginSession.user_id == User.id).where(
        User.id == user_id, User.password_hash == snapshot,
        LoginSession.token_hash == digest(request.cookies[SESSION_COOKIE]), LoginSession.expires_at > now()))
    if not active or active.locked_until > now():
        raise HTTPException(403, "Chưa thể xác nhận mật khẩu hiện tại.")
    if active.locked_until:
        active.locked_until = 0
        active.failures = 0
    if not valid:
        active.failures += 1
        if active.failures >= 5:
            active.locked_until = now() + LOCKOUT
        db.commit()
        raise HTTPException(403, "Chưa thể xác nhận mật khẩu hiện tại.")
    active.password_hash = replacement
    active.failures = 0
    db.execute(delete(LoginSession).where(LoginSession.user_id == active.id))
    record(db, request, active, None, "account.password_changed", {}, {})
    db.commit()
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, secure=request.app.state.secure_cookies, httponly=True, samesite="lax")
    return response


@router.post("/logout", dependencies=[Depends(require_csrf)], status_code=204)
def logout(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    token = request.cookies[SESSION_COOKIE]
    db.execute(delete(LoginSession).where(LoginSession.token_hash == digest(token), LoginSession.user_id == user.id))
    db.commit()
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, secure=request.app.state.secure_cookies, httponly=True, samesite="lax")
    return response
