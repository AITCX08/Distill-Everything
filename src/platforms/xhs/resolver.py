"""Resolve Xiaohongshu share links and explicit identifiers.

Short links (``xhslink.com``) need one redirect hop; everything else is pure
string parsing so the resolver stays testable offline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Mapping
from urllib.parse import urlparse

import requests

from src.platforms.errors import TargetResolutionError

_NOTE_PATH_RE = re.compile(r"^/(?:explore|discovery/item)/(?P<note_id>[0-9a-fA-F]{16,32})")
_USER_PATH_RE = re.compile(r"^/user/profile/(?P<user_id>[0-9a-fA-F]{16,32})")
_EXPLICIT_RE = re.compile(r"^xhs:(?P<kind>note|user|collect):(?P<value>[\w-]+)$", re.IGNORECASE)
_SHORT_HOSTS = frozenset({"xhslink.com", "www.xhslink.com"})
_MAIN_HOSTS = frozenset(
    {"xiaohongshu.com", "www.xiaohongshu.com", "m.xiaohongshu.com"}
)
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


@dataclass(frozen=True)
class XhsTarget:
    kind: str  # note | user | collect
    value: str
    canonical_url: str


def parse_xhs_target(text: str) -> XhsTarget | None:
    """Parse a note/user/collect target without network access."""

    value = (text or "").strip()
    explicit = _EXPLICIT_RE.match(value)
    if explicit:
        kind = explicit.group("kind").lower()
        identifier = explicit.group("value")
        if kind == "note":
            return XhsTarget("note", identifier, f"https://www.xiaohongshu.com/explore/{identifier}")
        if kind == "user":
            return XhsTarget("user", identifier, f"https://www.xiaohongshu.com/user/profile/{identifier}")
        return XhsTarget("collect", identifier, f"https://www.xiaohongshu.com/user/profile/{identifier}")

    parts = urlparse(value if "://" in value else f"https://{value}")
    host = (parts.hostname or "").lower()
    if host not in _MAIN_HOSTS:
        return None
    note = _NOTE_PATH_RE.match(parts.path)
    if note:
        note_id = note.group("note_id")
        return XhsTarget("note", note_id, f"https://www.xiaohongshu.com/explore/{note_id}")
    user = _USER_PATH_RE.match(parts.path)
    if user:
        user_id = user.group("user_id")
        return XhsTarget("user", user_id, f"https://www.xiaohongshu.com/user/profile/{user_id}")
    return None


def is_short_link(text: str) -> bool:
    parts = urlparse((text or "").strip())
    return (parts.hostname or "").lower() in _SHORT_HOSTS


def expand_short_link(
    url: str,
    *,
    http_head: Callable[..., object] | None = None,
    timeout: float = 10.0,
) -> str:
    """Follow a ``xhslink.com`` redirect and return the final URL."""

    head = http_head or requests.head
    try:
        response = head(url, allow_redirects=True, timeout=timeout, headers=_HEADERS)
    except Exception as exc:  # network errors must not look like invalid input
        raise TargetResolutionError(f"小红书短链解析失败：{exc}") from exc
    final = str(getattr(response, "url", "") or url)
    if not final or final == url:
        raise TargetResolutionError("小红书短链没有跳转到笔记或用户页")
    return final


def resolve_target(
    text: str,
    *,
    http_head: Callable[..., object] | None = None,
    timeout: float = 10.0,
) -> XhsTarget:
    """Public entry: accept share text, short link, or canonical URL."""

    value = (text or "").strip()
    if not value:
        raise TargetResolutionError("小红书目标不能为空")
    parsed = parse_xhs_target(value)
    if parsed is not None:
        return parsed
    if is_short_link(value):
        return _require(expand_short_link(value, http_head=http_head, timeout=timeout))
    # Share text may carry the URL inside surrounding copy.
    match = re.search(r"https?://[^\s]+", value)
    if match:
        inner = match.group(0).rstrip("，。！？、,;)）]}")
        parsed = parse_xhs_target(inner)
        if parsed is not None:
            return parsed
        if is_short_link(inner):
            return _require(expand_short_link(inner, http_head=http_head, timeout=timeout))
    raise TargetResolutionError(
        "小红书目标需为笔记链接、用户主页、分享短链或 xhs:note|user|collect:<id>"
    )


def _require(url: str) -> XhsTarget:
    parsed = parse_xhs_target(url)
    if parsed is None:
        raise TargetResolutionError(f"无法从小红书跳转结果识别目标：{url}")
    return parsed


def as_metadata(target: XhsTarget) -> Mapping[str, str]:
    return {"kind": target.kind, "value": target.value}
