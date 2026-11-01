"""登录后解析并保存抖音账号的 sec_uid。

`/user/self` 会 302 到 `/user/<sec_uid>`，这是拿到自己 sec_uid 最稳的方式
（不依赖任何私有接口）；失败时退回 localStorage 里的 `MS4wLjAB...`。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.distillation.store import atomic_write_bytes

SEC_UID_PREFIX = "MS4wLjAB"
ACCOUNT_FILE = ".account.json"
SELF_URL = "https://www.douyin.com/user/self"

_FIND_IN_STORAGE_JS = """() => {
  const hit = (value) => typeof value === 'string' && value.startsWith('MS4wLjAB');
  for (let i = 0; i < localStorage.length; i += 1) {
    const key = localStorage.key(i);
    const value = localStorage.getItem(key) || '';
    if (hit(value)) return value;
    const match = value.match(/MS4wLjAB[A-Za-z0-9_-]{20,}/);
    if (match) return match[0];
  }
  return null;
}"""


@dataclass(frozen=True)
class DouyinAccount:
    sec_uid: str
    nickname: str | None = None
    captured_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sec_uid": self.sec_uid,
            "nickname": self.nickname,
            "captured_at": self.captured_at
            or datetime.now(timezone.utc).isoformat(),
        }


class AccountStore:
    def __init__(self, profile_dir: Path) -> None:
        self.path = Path(profile_dir) / ACCOUNT_FILE

    def load(self) -> DouyinAccount | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        sec_uid = str(payload.get("sec_uid") or "")
        if not sec_uid.startswith(SEC_UID_PREFIX):
            return None
        return DouyinAccount(
            sec_uid=sec_uid,
            nickname=payload.get("nickname"),
            captured_at=payload.get("captured_at"),
        )

    def save(self, sec_uid: str, nickname: str | None = None) -> DouyinAccount:
        if not sec_uid.startswith(SEC_UID_PREFIX):
            raise ValueError(f"不是合法的 sec_uid：{sec_uid[:12]}")
        account = DouyinAccount(sec_uid=sec_uid, nickname=nickname)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(
            self.path,
            json.dumps(account.to_dict(), ensure_ascii=False).encode("utf-8"),
        )
        return account


def _sec_uid_from_url(url: str) -> str | None:
    marker = "/user/"
    if marker not in url:
        return None
    candidate = url.split(marker, 1)[1].split("?", 1)[0].strip("/")
    return candidate if candidate.startswith(SEC_UID_PREFIX) else None


def resolve_sec_uid(page: Any, *, timeout_ms: int = 15_000) -> str | None:
    """Navigate to ``/user/self`` and read the redirected sec_uid."""

    try:
        page.goto(SELF_URL, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(1_500)
    except Exception:
        pass
    try:
        found = _sec_uid_from_url(str(getattr(page, "url", "") or ""))
    except Exception:
        found = None
    if found:
        return found
    try:
        value = page.evaluate(_FIND_IN_STORAGE_JS)
    except Exception:
        value = None
    return value if isinstance(value, str) and value.startswith(SEC_UID_PREFIX) else None


def capture(profile_dir: Path, page: Any) -> DouyinAccount | None:
    """Resolve the current account and persist it next to the profile."""

    sec_uid = resolve_sec_uid(page)
    if not sec_uid:
        return None
    try:
        return AccountStore(profile_dir).save(sec_uid)
    except ValueError:
        return None
