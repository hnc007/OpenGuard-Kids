"""Password, token and field protection using separate keys for separate purposes."""
import base64
import binascii
import hashlib
import hmac
import os
import re
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def normalize_username(value: str) -> str:
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", value):
        raise ValueError("Tên đăng nhập cần 3–64 ký tự a–z, số, dấu chấm, gạch dưới hoặc gạch ngang.")
    return value


def _scrypt(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=3, dklen=32, maxmem=64*1024*1024)


def hash_password(password: str) -> str:
    if not 10 <= len(password) <= 256:
        raise ValueError("Mật khẩu cần từ 10 đến 256 ký tự.")
    salt = secrets.token_bytes(16)
    return f"scrypt-v1${salt.hex()}${_scrypt(password, salt).hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, salt, expected = encoded.split("$")
        if scheme != "scrypt-v1" or len(salt) != 32 or len(expected) != 64:
            return False
        return hmac.compare_digest(_scrypt(password, bytes.fromhex(salt)), bytes.fromhex(expected))
    except ValueError:
        return False


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Vault:
    def __init__(self, encoded_key: str):
        try:
            self._key = base64.b64decode(encoded_key, altchars=b"-_", validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("OGK_SECRET_KEY cần là khóa 32 byte ở dạng base64.") from exc
        if len(self._key) != 32:
            raise ValueError("OGK_SECRET_KEY cần là khóa 32 byte ở dạng base64.")
        self._aes = AESGCM(self.derive("fields-v1"))

    def derive(self, purpose: str) -> bytes:
        return hmac.digest(self._key, b"openguard-kids\0" + purpose.encode(), "sha256")

    def encrypt(self, value: str) -> str:
        nonce = os.urandom(12)
        ciphertext = self._aes.encrypt(nonce, value.encode(), None)
        return base64.urlsafe_b64encode(nonce + ciphertext).decode()

    def decrypt(self, value: str) -> str:
        raw = base64.urlsafe_b64decode(value)
        return self._aes.decrypt(raw[:12], raw[12:], None).decode()

    def tag(self, purpose: str, value: str) -> str:
        return hmac.new(self.derive(purpose), value.encode(), "sha256").hexdigest()
