"""Policy decisions. These functions have no access to applications, accounts or the network."""
from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


CATEGORIES = {"education", "entertainment", "social", "games", "inappropriate", "unknown"}
DATA = Path(__file__).with_name("data")
SAFESEARCH_TARGET = "forcesafesearch.google.com"
SAFESEARCH_NAMES = {"www.google.com", "www.google.com.vn"}
ESSENTIAL_APPS = {
    "system", "csrss.exe", "wininit.exe", "services.exe", "lsass.exe", "winlogon.exe",
    "svchost.exe", "explorer.exe", "sihost.exe", "dwm.exe", "taskmgr.exe",
    "smss.exe", "msmpeng.exe", "securityhealthservice.exe", "smartscreen.exe",
    "securityhealthsystray.exe", "narrator.exe", "utilman.exe", "osk.exe",
    "lockapp.exe", "logonui.exe", "consent.exe", "magnify.exe", "tabtip.exe", "ctfmon.exe",
}
# Keep the browser and communication path available for a child's request for help.
# Website decisions are made separately, including the safety-domain exceptions.
HELP_APPS = {
    "phoneexperiencehost.exe", "yourphone.exe", "dialer.exe", "skype.exe", "teams.exe",
    "ms-teams.exe", "zoom.exe", "zalo.exe", "signal.exe", "whatsapp.exe", "telegram.exe",
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "iexplore.exe",
    "vivaldi.exe", "msedgewebview2.exe",
}
LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.ASCII)


def domain_name(value: str) -> str:
    name = value.removesuffix(".").encode("idna").decode("ascii").lower()
    if len(name) > 253 or not all(LABEL.fullmatch(label) for label in name.split(".")):
        raise ValueError("Tên miền không hợp lệ")
    return name


def ancestors(name: str):
    labels = name.split(".")
    return [".".join(labels[index:]) for index in range(len(labels))]


@dataclass(frozen=True)
class Scope:
    consent: bool
    tray_visible: bool
    child_session: bool

    @property
    def active(self) -> bool:
        return self.consent and self.tray_visible and self.child_session


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    issuer: str = ""

    def explain(self, subject: str) -> str:
        if self.action == "block":
            return f"{subject} bị chặn. {self.reason} Người đặt luật: {self.issuer}. Em có thể gửi yêu cầu xem lại."
        return self.reason


@lru_cache(maxsize=1)
def catalogue() -> dict[str, str]:
    with (DATA / "domains.csv").open(encoding="utf-8", newline="") as stream:
        return {row["domain"]: row["category"] for row in csv.DictReader(stream)}


@lru_cache(maxsize=1)
def safety_domains() -> frozenset[str]:
    with (DATA / "safety.csv").open(encoding="utf-8", newline="") as stream:
        return frozenset(row["domain"] for row in csv.DictReader(stream))


class DomainRules:
    """Compile once per verified policy change; DNS caches never cache a policy decision."""

    def __init__(self, policy: dict):
        self.issuer = policy.get("issuer", "Phụ huynh")
        self.allow = {domain_name(name) for name in policy["allowed_domains"]}
        self.block = {domain_name(name) for name in policy["blocked_domains"]}
        self.categories = set(policy["blocked_categories"])
        self.safe_search = policy["safe_search"]

    def decide(self, name: str, scope: Scope) -> Decision:
        if not scope.active:
            return Decision("allow", "Tạm dừng hỗ trợ.")
        name = domain_name(name)
        parents = ancestors(name)
        if safety_domains().intersection(parents):
            return Decision("allow", "Trang học tập hoặc trợ giúp luôn được phép.")
        if not self.allow.intersection(parents):
            if self.block.intersection(parents):
                return Decision("block", "Trang nằm trong danh sách giới hạn của em.", self.issuer)
            groups = catalogue()
            category = next((groups[p] for p in parents if p in groups), "unknown")
            if category in self.categories:
                return Decision("block", "Quy tắc của em đang giới hạn nhóm trang này.", self.issuer)
        if self.safe_search and name in SAFESEARCH_NAMES:
            return Decision("safesearch", "Bật tìm kiếm an toàn.", self.issuer)
        return Decision("allow", "Em có thể dùng trang này.")


@lru_cache(maxsize=128)
def _hash_file(path: Path, size: int, created: int, modified: int) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def executable_hash(path: Path) -> str:
    path = path.resolve(strict=True)
    stat = path.stat()
    return _hash_file(path, stat.st_size, stat.st_ctime_ns, stat.st_mtime_ns)


@dataclass(frozen=True)
class Application:
    name: str
    sha256: str
    owner_sid: str
    protected: bool = False  # OS, security, emergency and agent processes supplied by the host.


def app_decision(policy: dict, app: Application, child_sid: str, scope: Scope) -> Decision:
    if not scope.active or not child_sid or app.owner_sid != child_sid:
        return Decision("allow", "Tiến trình nằm ngoài phạm vi hỗ trợ.")
    name, digest = app.name.casefold(), app.sha256.casefold()
    if app.protected or name in ESSENTIAL_APPS:
        return Decision("allow", "Ứng dụng thiết yếu luôn được phép.")
    if name in HELP_APPS:
        return Decision("allow", "Em có thể dùng ứng dụng này để liên hệ hoặc tìm trợ giúp.")

    def matches(rule: dict) -> bool:
        expected = rule.get("sha256")
        if expected:
            return digest == expected.casefold()
        return name == (rule.get("name") or "").casefold()

    if any(matches(item) for item in policy["blocked_apps"]):
        return Decision("block", "Ứng dụng nằm trong danh sách giới hạn của em.", policy["issuer"])
    if policy["allowed_apps"] and not any(matches(item) for item in policy["allowed_apps"]):
        return Decision("block", "Ứng dụng chưa nằm trong danh sách được phép.", policy["issuer"])
    return Decision("allow", "Em có thể dùng ứng dụng này.")
