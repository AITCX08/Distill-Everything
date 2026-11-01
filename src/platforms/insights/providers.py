"""Per-platform console specs and normalizers.

Markers and page URLs follow the public console routes; the normalizers accept
either a bare payload or one wrapped in ``data``. Calibration against a live
console is recorded in docs/platform-sources.md §9.
"""

from __future__ import annotations

from typing import Any, Mapping

from src.platforms.insights.core import (
    BrowserInsightsSpec,
    InsightsSnapshot,
    build_metrics,
    now_iso,
    pick,
)


def _wrap(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    data = payload.get("data")
    return data if isinstance(data, Mapping) else payload


def _coerce(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return str(value)


def _items(payload: Mapping[str, Any], path: str, mapping: dict[str, str]) -> tuple[dict[str, Any], ...]:
    raw = pick(payload, path)
    if not isinstance(raw, list):
        return ()
    return tuple(
        {target: _coerce(pick(entry, source)) for target, source in mapping.items()}
        for entry in raw
        if isinstance(entry, Mapping)
    )


def _as_text(value: Any) -> str | None:
    return None if value is None else str(value)


def normalize_bilibili(payload: Mapping[str, Any]) -> InsightsSnapshot:
    data = _wrap(payload)
    metrics = build_metrics(
        {
            "views": pick(data, "total_play") or pick(data, "view"),
            "likes": pick(data, "total_like") or pick(data, "like"),
            "comments": pick(data, "total_comment") or pick(data, "comment"),
            "shares": pick(data, "total_share") or pick(data, "share"),
            "followers": pick(data, "fans") or pick(data, "follower"),
            "followers_delta": pick(data, "fans_inc") or pick(data, "follower_inc"),
        }
    )
    return InsightsSnapshot(
        platform="bilibili",
        captured_at=now_iso(),
        account={"id": _as_text(pick(data, "mid")), "name": _as_text(pick(data, "nickname") or pick(data, "name"))},
        metrics=metrics,
        items=_items(data, "list", {"item_id": "bvid", "title": "title", "views": "view"}),
        source={"endpoint": "member.bilibili.com", "captured_by": "browser-xhr"},
    )


def normalize_douyin(payload: Mapping[str, Any]) -> InsightsSnapshot:
    data = _wrap(payload)
    metrics = build_metrics(
        {
            "views": pick(data, "play_count") or pick(data, "total_play"),
            "likes": pick(data, "digg_count") or pick(data, "total_digg"),
            "comments": pick(data, "comment_count") or pick(data, "total_comment"),
            "shares": pick(data, "share_count") or pick(data, "total_share"),
            "followers": pick(data, "follower_count") or pick(data, "fans_count"),
            "followers_delta": pick(data, "new_follower_count") or pick(data, "fans_inc"),
        }
    )
    return InsightsSnapshot(
        platform="douyin",
        captured_at=now_iso(),
        account={"id": _as_text(pick(data, "uid")), "name": _as_text(pick(data, "nickname"))},
        metrics=metrics,
        items=_items(data, "aweme_list", {"item_id": "aweme_id", "title": "desc", "views": "statistics.play_count"}),
        source={"endpoint": "creator.douyin.com", "captured_by": "browser-xhr"},
    )


def normalize_xiaohongshu(payload: Mapping[str, Any]) -> InsightsSnapshot:
    data = _wrap(payload)
    metrics = build_metrics(
        {
            "views": pick(data, "view_count") or pick(data, "views"),
            "likes": pick(data, "like_count") or pick(data, "likes"),
            "comments": pick(data, "comment_count") or pick(data, "comments"),
            "shares": pick(data, "share_count") or pick(data, "shares"),
            "followers": pick(data, "fans_count") or pick(data, "follower_count"),
            "followers_delta": pick(data, "fans_inc") or pick(data, "new_fans_count"),
        }
    )
    return InsightsSnapshot(
        platform="xiaohongshu",
        captured_at=now_iso(),
        account={"id": _as_text(pick(data, "user_id")), "name": _as_text(pick(data, "nickname"))},
        metrics=metrics,
        items=_items(data, "notes", {"item_id": "note_id", "title": "title", "views": "view_count"}),
        source={"endpoint": "creator.xiaohongshu.com", "captured_by": "browser-xhr"},
    )


SPECS: dict[str, BrowserInsightsSpec] = {
    "bilibili": BrowserInsightsSpec(
        platform="bilibili",
        page_url="https://member.bilibili.com/platform/home",
        markers=("/x/web/data/", "/x/web/archive/", "member.bilibili.com/x/"),
        normalize=normalize_bilibili,
    ),
    "douyin": BrowserInsightsSpec(
        platform="douyin",
        page_url="https://creator.douyin.com/creator-micro/home",
        markers=("/aweme/v1/creator/", "/creator/data/", "creator.douyin.com/aweme/"),
        normalize=normalize_douyin,
    ),
    "xiaohongshu": BrowserInsightsSpec(
        platform="xiaohongshu",
        page_url="https://creator.xiaohongshu.com/creator/home",
        markers=("creator.xiaohongshu.com/api/", "/api/galaxy/"),
        normalize=normalize_xiaohongshu,
    ),
}
