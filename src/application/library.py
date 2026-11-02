"""内容库：登录后读取账号自己的收藏 / 喜欢 / 发布。

后端只负责"取数 + 建任务"，不碰 UI。每个平台按能力矩阵返回可用入口，
不可用的入口带上原因（例如"B站没有公开的点赞列表"），前端据此置灰。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.platforms.douyin.cookie_export import read_sidecar_cookie
from src.platforms.douyin.enumerator import map_aweme

SUPPORTED = ("douyin", "bilibili", "xiaohongshu")


@dataclass(frozen=True)
class LibraryEntry:
    key: str
    label: str
    available: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "available": self.available,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class LibraryItem:
    item_id: str
    title: str
    url: str
    cover: str | None = None
    duration: float | None = None
    published_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "url": self.url,
            "cover": self.cover,
            "duration": self.duration,
            "published_at": self.published_at,
        }


class LibraryService:
    def __init__(self, service: Any) -> None:
        self._service = service
        self._manager = service.platform_manager

    # -- capability ------------------------------------------------------
    def entries(self, platform: str) -> tuple[LibraryEntry, ...]:
        if platform == "douyin":
            return self._douyin_entries()
        if platform == "bilibili":
            return self._bilibili_entries()
        if platform == "xiaohongshu":
            return self._xhs_entries()
        raise ValueError(f"不支持的内容库平台：{platform}")

    def account(self, platform: str) -> str | None:
        try:
            adapter = self._manager.get(platform)
        except Exception:
            return None
        if platform == "douyin":
            account = getattr(adapter, "account", None)
            return account.sec_uid if account is not None else None
        if platform == "bilibili":
            return self._bilibili_uid()
        return None

    def folders(self, platform: str) -> list[dict[str, Any]]:
        """收藏夹列表（目前只有 B 站有"文件夹"概念）。"""

        if platform != "bilibili":
            return []
        uid = self._bilibili_uid()
        if not uid:
            return []
        adapter = self._bilibili_adapter()
        favorites = getattr(adapter, "favorites", None)
        if favorites is None:
            return []
        return [
            {
                "id": folder.media_id,
                "title": folder.title,
                "count": folder.media_count,
            }
            for folder in favorites.list_folders(uid)
        ]

    # -- items -----------------------------------------------------------
    def items(self, platform: str, kind: str, *, cursor: str = "", folder: str = "") -> dict[str, Any]:
        if platform == "douyin":
            return self._douyin_items(kind, cursor)
        if platform == "bilibili":
            return self._bilibili_items(kind, cursor, folder)
        if platform == "xiaohongshu":
            return self._xhs_items(kind, cursor)
        raise ValueError(f"不支持的内容库平台：{platform}")

    # -- jobs ------------------------------------------------------------
    def create_jobs(
        self,
        platform: str,
        targets: list[str],
        *,
        outputs: tuple[str, ...] = ("episodes", "skill"),
        rag_chunks: bool = False,
    ) -> list[str]:
        from src.application.commands import CreateJobRequest, PreviewRequest

        created: list[str] = []
        for target in targets:
            preview = self._service.preview(PreviewRequest(target, platform, outputs, rag_chunks))
            request = CreateJobRequest(
                target=target,
                preview_fingerprint=preview.fingerprint,
                platform=platform,
                outputs=outputs,
                rag_chunks=rag_chunks,
            )
            created.append(self._service.create(request).job_id)
        return created

    # -- douyin ----------------------------------------------------------
    def _douyin_adapter(self):
        return self._manager.get("douyin")

    def _douyin_entries(self) -> tuple[LibraryEntry, ...]:
        adapter = self._douyin_adapter()
        sidecar = getattr(adapter, "sidecar", None)
        online = bool(sidecar is not None and sidecar.is_available())
        account = getattr(adapter, "account", None)
        has_uid = account is not None
        has_cookie = bool(read_sidecar_cookie(getattr(adapter, "sidecar_dir", None)))

        return (
            LibraryEntry(
                "posts", "我的发布", online and has_uid,
                "" if online and has_uid else "需要 sidecar 在线并已记录 sec_uid",
            ),
            LibraryEntry(
                "likes", "我的喜欢", online and has_uid,
                "" if online and has_uid else "需要 sidecar 在线并已记录 sec_uid",
            ),
            LibraryEntry(
                "collect", "我的收藏", online and has_cookie,
                "" if online and has_cookie else "需要先登录并同步 cookie 给 sidecar",
            ),
        )

    def _douyin_items(self, kind: str, cursor: str) -> dict[str, Any]:
        adapter = self._douyin_adapter()
        sidecar = getattr(adapter, "sidecar", None)
        if sidecar is None:
            raise RuntimeError("抖音 sidecar 未配置")
        account = getattr(adapter, "account", None)
        page = int(cursor or 0)
        if kind == "collect":
            cookie = read_sidecar_cookie(getattr(adapter, "sidecar_dir", None))
            if not cookie:
                raise RuntimeError("尚未同步 cookie，无法读取我的收藏")
            payload = sidecar.user_collection(cookie, cursor=page)
        elif kind in {"posts", "likes"}:
            if account is None:
                raise RuntimeError("尚未记录 sec_uid，请先扫码登录")
            fetch = sidecar.user_likes if kind == "likes" else sidecar.user_posts
            payload = fetch(account.sec_uid, cursor=page)
        else:
            raise ValueError(f"抖音不支持的内容类型：{kind}")

        items = []
        for raw in payload.get("aweme_list") or []:
            try:
                item = map_aweme(raw, account.sec_uid if account else "self")
            except Exception:
                continue
            items.append(
                LibraryItem(
                    item_id=item.item_id,
                    title=item.title,
                    url=item.canonical_url,
                    cover=item.cover_url,
                    duration=item.duration_seconds,
                    published_at=item.published_at.isoformat() if item.published_at else None,
                )
            )
        return {
            "items": [item.to_dict() for item in items],
            "cursor": str(payload.get("max_cursor") or ""),
            "has_more": bool(payload.get("has_more")),
        }

    # -- bilibili --------------------------------------------------------
    def _bilibili_adapter(self):
        return self._manager.get("bilibili")

    def _bilibili_uid(self) -> str | None:
        config = getattr(self._service, "config", None)
        if config is None:
            from src.config import load_config

            config = load_config()
        cache = getattr(config, "credentials_cache", None)
        if cache is None:
            return None
        from src.crawl.auth import load_cached_credential

        loaded = load_cached_credential(cache)
        if not loaded:
            return None
        credential = loaded[0]
        uid = str(getattr(credential, "dedeuserid", "") or "")
        return uid or None

    def _bilibili_entries(self) -> tuple[LibraryEntry, ...]:
        uid = self._bilibili_uid()
        return (
            LibraryEntry(
                "favorites", "我的收藏夹", bool(uid),
                "" if uid else "需要 B站登录凭据",
            ),
            LibraryEntry("posts", "我的投稿", bool(uid), "" if uid else "需要 B站登录凭据"),
            LibraryEntry("likes", "我赞过的", False, "B站没有公开的点赞列表接口"),
        )

    def _bilibili_items(self, kind: str, cursor: str, folder: str) -> dict[str, Any]:
        adapter = self._bilibili_adapter()
        if kind != "favorites":
            raise ValueError(f"B站内容库暂不支持：{kind}")
        if not folder:
            raise ValueError("需要指定收藏夹 id")
        favorites = getattr(adapter, "favorites", None)
        if favorites is None:
            raise RuntimeError("B站收藏夹模块未初始化")
        pages = favorites.iter_pages(folder)
        medias, has_more = next(pages)
        items = [
            LibraryItem(
                item_id=str(media.get("bvid") or ""),
                title=str(media.get("title") or ""),
                url=f"https://www.bilibili.com/video/{media.get('bvid')}",
                cover=media.get("cover"),
                duration=float(media["duration"]) if isinstance(media.get("duration"), (int, float)) else None,
            )
            for media in medias
        ]
        return {"items": [item.to_dict() for item in items], "cursor": "", "has_more": has_more}

    # -- xiaohongshu -----------------------------------------------------
    def _xhs_adapter(self):
        return self._manager.get("xiaohongshu")

    def _xhs_entries(self) -> tuple[LibraryEntry, ...]:
        adapter = self._xhs_adapter()
        sidecar = getattr(adapter, "sidecar", None)
        authed = bool(sidecar is not None and sidecar.is_authenticated())
        return (
            LibraryEntry("posts", "我的笔记", authed, "" if authed else "需要先登录小红书"),
            LibraryEntry("collect", "我的收藏", authed, "" if authed else "需要先登录小红书"),
        )

    def _xhs_items(self, kind: str, cursor: str) -> dict[str, Any]:
        adapter = self._xhs_adapter()
        sidecar = getattr(adapter, "sidecar", None)
        if sidecar is None:
            raise RuntimeError("小红书 sidecar 未配置")
        user_id = str(getattr(sidecar, "user_id", "") or "")
        if not user_id:
            raise RuntimeError("尚未识别小红书账号，请重新登录")
        payload = (
            sidecar.user_collect(user_id, cursor=cursor)
            if kind == "collect"
            else sidecar.user_notes(user_id, cursor=cursor)
        )
        raw_items = payload.get("items") or []
        return {
            "items": [
                {
                    "item_id": str(entry.get("note_id") or ""),
                    "title": str(entry.get("title") or ""),
                    "url": str(entry.get("url") or ""),
                    "cover": entry.get("cover"),
                    "duration": None,
                    "published_at": None,
                }
                for entry in raw_items
            ],
            "cursor": str(payload.get("cursor") or ""),
            "has_more": bool(payload.get("has_more")),
        }
