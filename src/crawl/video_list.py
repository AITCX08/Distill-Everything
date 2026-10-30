"""
视频列表获取模块

使用 bilibili-api-python 获取指定UP主的所有视频列表，
包括BV号、标题、时长、发布时间等元信息。
"""

import asyncio
import json
import random
from pathlib import Path
from typing import Optional

from bilibili_api import user, Credential
from rich.console import Console

console = Console()


async def fetch_user_videos(
    uid: int,
    credential: Credential,
    existing_bvids: Optional[set] = None,
    max_candidates: int = 0,
) -> list[dict]:
    """
    获取指定UP主的新增视频列表。

    Args:
        uid: UP主的UID
        credential: B站认证凭据
        existing_bvids: 本地已存在的bvid集合，这些视频将被跳过
        max_candidates: 最多获取的候选视频数量，0 表示不限制

    Returns:
        新增视频信息列表
    """
    existing_bvids = existing_bvids or set()
    u = user.User(uid=uid, credential=credential)
    new_videos = []
    skipped_count = 0
    page = 1
    page_size = 30

    console.print("[blue]正在获取视频列表...")

    while True:
        # 带重试的请求：412风控时等待后重试
        resp = None
        for attempt in range(3):
            try:
                resp = await u.get_videos(pn=page, ps=page_size)
                break
            except Exception as e:
                err_str = str(e)
                if "412" in err_str and attempt < 2:
                    wait = 10 + attempt * 10  # 10s, 20s
                    console.print(f"[yellow]触发B站风控(412)，等待 {wait}s 后重试...")
                    await asyncio.sleep(wait)
                else:
                    console.print(f"[red]获取第 {page} 页视频列表失败: {e}")
                    break
        if resp is None:
            break

        vlist = resp.get("list", {}).get("vlist", [])
        if not vlist:
            break

        total_count = resp.get("page", {}).get("count", 0)

        for v in vlist:
            bvid = v.get("bvid", "")
            if bvid in existing_bvids:
                skipped_count += 1
                console.print(f"[dim]  跳过已存在: {bvid} 《{v.get('title', '')}》")
                continue

            new_videos.append({
                "bvid": bvid,
                "title": v.get("title", ""),
                "duration": v.get("length", ""),
                "pubdate": v.get("created", 0),
                "description": v.get("description", ""),
                "view_count": v.get("play", 0),
                "comment_count": v.get("comment", 0),
                "aid": v.get("aid", 0),
            })

        console.print(
            f"[blue]  第 {page} 页: 获取 {len(vlist)} 个 | "
            f"候选 {len(new_videos)} 个 | 跳过 {skipped_count} 个 | 共 {total_count} 个"
        )

        # 候选数量已够，提前停止翻页
        if max_candidates > 0 and len(new_videos) >= max_candidates:
            console.print(f"[dim]已获取足够候选视频（{len(new_videos)} 个），停止翻页")
            break

        if page * page_size >= total_count:
            break

        page += 1
        # 随机延迟 3-6 秒，避免固定间隔被识别
        await asyncio.sleep(3 + random.uniform(0, 3))

    if skipped_count:
        console.print(f"[yellow]跳过本地已有视频: {skipped_count} 个")
    console.print(f"[green]新增视频: {len(new_videos)} 个")
    return new_videos


def save_video_list(videos: list[dict], output_path: Path) -> None:
    """将视频列表保存为JSON文件。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(videos, f, ensure_ascii=False, indent=2)
    console.print(f"[green]视频列表已保存到 {output_path}")


def load_video_list(input_path: Path) -> list[dict]:
    """从JSON文件加载视频列表。"""
    with open(input_path, "r", encoding="utf-8") as f:
        return json.load(f)


def create_credential(sessdata: str, bili_jct: str, buvid3: str) -> Credential:
    """从Cookie参数创建B站认证凭据。"""
    return Credential(sessdata=sessdata, bili_jct=bili_jct, buvid3=buvid3)


def run_crawl(
    uid: int,
    credential: Credential,
    output_path: Path,
    max_videos: int = 0,
    existing_bvids: Optional[set] = None,
    existing_videos: Optional[list] = None,
    max_candidates: int = 0,
) -> list[dict]:
    """
    同步入口：获取新增视频列表并与本地已有列表合并保存。

    Args:
        uid: UP主UID
        credential: B站认证凭据
        output_path: 视频列表保存路径
        max_videos: 最大成功下载视频数量（下载失败不计），0表示全部
        existing_bvids: 本地已有bvid集合，用于跳过
        existing_videos: 本地已有视频列表，用于合并
        max_candidates: 最多获取的候选视频数量，0 表示不限制

    Returns:
        新增视频列表
    """
    new_videos = asyncio.run(
        fetch_user_videos(uid, credential, existing_bvids, max_candidates)
    )

    # 合并新旧视频列表后保存（保留历史记录）
    if existing_videos:
        existing_map = {v["bvid"]: v for v in existing_videos}
        for v in new_videos:
            existing_map[v["bvid"]] = v
        merged = list(existing_map.values())
    else:
        merged = new_videos

    save_video_list(merged, output_path)
    return new_videos
