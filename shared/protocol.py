"""Validate and minimise the wire format before storage at either endpoint."""

import base64
import ipaddress
import json
import re
from typing import Annotated, Literal, Self
from uuid import UUID

import tldextract
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def canonical(body: dict) -> bytes:
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def domain(value: str) -> str:
    value = value.lower().removesuffix(".")
    if len(value) > 253 or "." not in value:
        raise ValueError("Cần tên miền hợp lệ.")
    labels = value.split(".")
    if not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
        raise ValueError("Chỉ nhập tên miền, bỏ đường dẫn và chuỗi truy vấn.")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        if not labels[-1].isdigit():
            return value
    raise ValueError("Không ghi nhận địa chỉ IP.")


# Use only the bundled Public Suffix List, including multi-label/private suffixes.
_extract = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True)


def reporting_domain(value: str) -> str:
    parsed = _extract(domain(value))
    if not parsed.suffix or not parsed.domain:
        raise ValueError("Tên miền không có hậu tố công cộng được nhận diện.")
    return f"{parsed.domain}.{parsed.suffix}"


def executable(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_() -][a-zA-Z0-9_.() -]{0,90}\.exe", value, flags=re.I):
        raise ValueError("Cần tên tệp .exe, không kèm đường dẫn.")
    return value.lower()


class Application(WireModel):
    name: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    _name = field_validator("name")(executable)


class PolicySettings(WireModel):
    weekday_minutes: int = Field(default=90, ge=0, le=1440)
    weekend_minutes: int = Field(default=90, ge=0, le=1440)
    schedule: list[str] = Field(default_factory=lambda: ["1" * 48 for _ in range(7)], min_length=7, max_length=7)
    allowed_apps: list[Application] = Field(default_factory=list, max_length=128)
    blocked_apps: list[Application] = Field(default_factory=list, max_length=128)
    allowed_domains: list[str] = Field(default_factory=list, max_length=256)
    blocked_domains: list[str] = Field(default_factory=list, max_length=256)
    blocked_categories: list[str] = Field(default_factory=lambda: ["inappropriate", "social"], max_length=6)
    safe_search: bool = True

    @field_validator("schedule")
    @classmethod
    def schedule_cells(cls, days: list[str]) -> list[str]:
        if any(not re.fullmatch(r"[01]{48}", day) for day in days):
            raise ValueError("Mỗi ngày cần 48 ô có giá trị 0 hoặc 1.")
        return days

    @field_validator("allowed_domains", "blocked_domains")
    @classmethod
    def domains(cls, values: list[str]) -> list[str]:
        return sorted(set(domain(value) for value in values))

    @field_validator("blocked_categories")
    @classmethod
    def categories(cls, values: list[str]) -> list[str]:
        allowed = {"education", "entertainment", "social", "games", "inappropriate", "unknown"}
        if set(values) - allowed:
            raise ValueError("Nhóm website không hợp lệ.")
        return sorted(set(values))


class PolicyChange(PolicySettings):
    version: int = Field(ge=1)


class UsagePayload(WireModel):
    ts: int = Field(gt=0)
    device_id: UUID
    child_id: int = Field(gt=0)
    type: Literal["app_start", "app_stop", "domain_query", "blocked_app", "blocked_domain",
                  "quota_warning", "locked", "unlock_request", "override_granted"]
    subject: str = Field(max_length=253)
    duration_sec: int = Field(ge=0, le=86400)
    policy_id: int = Field(ge=1)

    @model_validator(mode="after")
    def minimise(self) -> Self:
        if self.type in {"domain_query", "blocked_domain"}:
            self.subject = reporting_domain(self.subject)
        elif self.type in {"app_start", "app_stop", "blocked_app"}:
            self.subject = executable(self.subject)
        elif self.subject != "":
            raise ValueError("Sự kiện thời gian không chứa nội dung tự do.")
        if self.type != "app_stop" and self.duration_sec:
            raise ValueError("Chỉ app_stop mang thời lượng của phiên ứng dụng.")
        return self


class UsageEnvelope(WireModel):
    event_id: UUID
    payload: UsagePayload


class EventBatch(WireModel):
    events: Annotated[list[UsageEnvelope], Field(min_length=1, max_length=100)]


def verified_policy(envelope: dict, device_id: str, verification_key: str, now: int) -> dict:
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(verification_key, validate=True))
        body = envelope["policy"]
        signature = base64.b64decode(envelope["signature"], validate=True)
        key.verify(signature, canonical(body))
        if body["device_id"] != device_id or not body["issued_at"] <= now + 120 or now >= body["expires_at"]:
            raise ValueError("Chính sách sai thiết bị hoặc ngoài thời hạn.")
        if body["version"] < 1:
            raise ValueError("Phiên bản không hợp lệ.")
        PolicySettings.model_validate({name: body[name] for name in PolicySettings.model_fields})
    except (KeyError, TypeError, ValueError, InvalidSignature) as error:
        raise ValueError("Không xác nhận được chính sách.") from error
    return body
