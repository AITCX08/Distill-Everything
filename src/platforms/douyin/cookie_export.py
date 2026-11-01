"""Bridge the browser login into the sidecar's cookie config.

The Douyin sidecar is a separate process: scanning the QR code only fills the
Playwright profile, so ``fetch_user_post_videos`` keeps returning 400 until the
sidecar's ``headers.Cookie`` is updated. This module reads cookies out of the
persistent context and rewrites that single line in place, keeping every comment
and neighbouring key untouched.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Mapping

COOKIE_LINE_RE = re.compile(r"^(?P<prefix>\s*Cookie:\s*).*$", re.MULTILINE)
DEFAULT_SIDECAR_DIR = Path(".local-artifacts") / "douyin-sidecar"
DOUYIN_CONFIG = Path("crawlers") / "douyin" / "web" / "config.yaml"


def cookies_to_header(cookies: Iterable[Mapping[str, Any]]) -> str:
    """Serialize Playwright cookies into a ``name=value; ...`` header value."""

    pairs: list[str] = []
    seen: set[str] = set()
    for cookie in cookies:
        name = str(cookie.get("name") or "").strip()
        value = str(cookie.get("value") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def write_cookie(config_path: Path, header: str) -> None:
    """Replace the first ``Cookie:`` line, preserving all comments."""

    text = config_path.read_text(encoding="utf-8")
    if not COOKIE_LINE_RE.search(text):
        raise ValueError(f"{config_path} 中没有找到 Cookie 行")
    updated = COOKIE_LINE_RE.sub(lambda match: f"{match.group('prefix')}{header}", text, count=1)
    config_path.write_text(updated, encoding="utf-8")


def read_browser_cookie(session: Any) -> str:
    """Pull ``douyin.com`` cookies from the persistent browser profile."""

    with session.open_context(headless=True, task="cookie-sync") as context:
        cookies = context.cookies(["https://www.douyin.com", "https://creator.douyin.com"])
    header = cookies_to_header(cookies)
    if "sessionid" not in header:
        raise RuntimeError("浏览器里没有抖音登录态：请先执行 source login douyin 并扫码")
    return header


def sync(session: Any, sidecar_dir: Path | None = None) -> tuple[Path, int]:
    """Read cookies, write them into the sidecar config, return (path, count)."""

    root = Path(sidecar_dir) if sidecar_dir else DEFAULT_SIDECAR_DIR
    config_path = root / DOUYIN_CONFIG
    if not config_path.exists():
        raise FileNotFoundError(f"未找到 sidecar 配置：{config_path}")
    header = read_browser_cookie(session)
    write_cookie(config_path, header)
    return config_path, header.count("=")


def has_sidecar_cookie(sidecar_dir: Path | None = None) -> bool:
    """True when the sidecar config carries a real session cookie."""

    root = Path(sidecar_dir) if sidecar_dir else DEFAULT_SIDECAR_DIR
    config_path = root / DOUYIN_CONFIG
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return False
    match = COOKIE_LINE_RE.search(text)
    if not match:
        return False
    return "sessionid=" in match.group(0)