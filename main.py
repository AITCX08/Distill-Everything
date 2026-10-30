"""
Distill-Everything: 多源内容蒸馏工具

将B站知识区UP主的视频内容，通过语音识别和LLM分析，
转化为结构化的SKILL.md知识文件。

用法:
    python main.py crawl    # 阶段1: 爬取视频列表并下载音频
    python main.py asr      # 阶段2: FunASR语音转文字
    python main.py clean    # 阶段3: 文本清洗与结构化
    python main.py model    # 阶段4: 知识建模
    python main.py generate # 阶段5: 生成SKILL.md
    python main.py run      # 一键运行（可通过 --stages 选择阶段）
"""

import sys
import json
import asyncio
import queue
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.live import Live
from rich.panel import Panel

from src.application import (
    DistillationService,
    EventHub,
    JobRepository,
    SourceCreatorRequest,
    SourceDistillationRunner,
)
from src.config import LLM_PROVIDERS
from src.dashboard import run_dashboard

console = Console()

# CLI 中 --llm 可选值
LLM_CHOICES = click.Choice(LLM_PROVIDERS)


def _skill_output_path(output_dir: Path, name: str) -> Path:
    """生成带时间戳的 SKILL.md 路径，每次生产新增不覆盖。

    格式：{output_dir}/{name}-{YYYYMMDD-HHMMSS}.skill.md
    Why: 同一人物多次蒸馏（不同素材组合 / Prompt 调整 / 模型切换）历史可对比，
    避免新版静默覆盖旧版导致结果丢失。
    """
    safe_name = (name or "skill").strip() or "skill"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return output_dir / f"{safe_name}-{timestamp}.skill.md"



_AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg"}


def _is_audio_file(path: Path) -> bool:
    """按后缀判断是否音频文件（阶段二音频路径）。"""
    return path.suffix.lower() in _AUDIO_SUFFIXES


def parse_stages(stages_str: str) -> list[int]:
    """
    解析阶段选择字符串。

    支持格式:
      - "1,2,3"    → [1, 2, 3]
      - "3-5"      → [3, 4, 5]
      - "1,3-5"    → [1, 3, 4, 5]
      - "all"      → [1, 2, 3, 4, 5]

    Returns:
        排序后的阶段编号列表
    """
    if stages_str.strip().lower() == "all":
        return [1, 2, 3, 4, 5]

    result = set()
    for part in stages_str.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            for i in range(int(start), int(end) + 1):
                if 1 <= i <= 5:
                    result.add(i)
        else:
            val = int(part)
            if 1 <= val <= 5:
                result.add(val)
    return sorted(result)


def save_rag_chunks(chunked_doc: dict, output_dir: Path) -> Path:
    """保存 RAG chunks JSON。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{chunked_doc['source_id']}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(chunked_doc, f, ensure_ascii=False, indent=2)
    return output_path


def collect_matching_files(base_dir: Path, patterns: tuple[str, ...]) -> list[Path]:
    """根据多个 glob 模式收集文件。"""
    matched = set()
    for pattern in patterns:
        matched.update(base_dir.glob(pattern))
    return sorted(path for path in matched if path.is_file())


def cleanup_book_artifacts(config, book_id: str):
    """删除同一本书旧的章节级产物，避免 stale 文件污染。"""
    patterns = (
        (config.cleaned_dir, f"{book_id}_ch*.json"),
        (config.knowledge_dir, f"{book_id}_ch*.json"),
        (config.rag_chunks_dir, f"{book_id}_ch*.json"),
    )
    for base_dir, pattern in patterns:
        for path in base_dir.glob(pattern):
            path.unlink()


@click.group()
@click.version_option(version="0.4.0", prog_name="Distill-Everything")
def cli():
    """Distill-Everything: 多源内容蒸馏工具

    将B站知识区UP主的视频内容转化为结构化的SKILL.md知识文件。
    """
    pass


def _require_platform(manager, platform: str):
    """Resolve a platform by name, listing the registered ones on a typo."""

    from src.platforms.errors import UnknownPlatformError

    try:
        return manager.get(platform)
    except UnknownPlatformError as exc:
        names = ", ".join(item.name for item in manager.list_descriptors())
        raise click.BadParameter(
            f"未注册的平台 {platform!r}；可选：{names}",
            param_hint="platform",
        ) from exc


def build_platform_manager(config):
    from src.platforms.bilibili import BilibiliAdapter
    from src.platforms.douyin import DouyinAdapter, DouyinSession
    from src.platforms.manager import PlatformManager
    from src.platforms.registry import PlatformRegistry
    from src.platforms.youtube import YoutubeAdapter

    douyin = DouyinAdapter(
        DouyinSession(
            config.data_dir,
            profile_dir=config.douyin.profile_dir,
            login_timeout=config.douyin.login_timeout,
        )
    )
    return PlatformManager(
        PlatformRegistry([BilibiliAdapter(config), douyin, YoutubeAdapter()])
    )


async def _run_engine_with_live_async(engine, request, events):
    from src.distillation.progress import RichProgressView

    subscription = events.subscribe(job_id=request.job_id)
    task = asyncio.create_task(engine.run(request))
    view = RichProgressView()
    try:
        with Live(console=console, refresh_per_second=8, transient=False) as live:
            while not task.done():
                try:
                    event = await asyncio.to_thread(subscription.get, 0.1)
                except queue.Empty:
                    continue
                if event.event_type == "progress.snapshot":
                    live.update(view.render(event.payload["snapshot"]), refresh=True)
            result = await task
            snapshots = [
                event
                for event in events.snapshot(job_id=request.job_id)
                if event.event_type == "progress.snapshot"
            ]
            if snapshots:
                live.update(view.render(snapshots[-1].payload["snapshot"]), refresh=True)
            return result
    finally:
        subscription.close()


def _run_engine_with_live(engine, request, events):
    return asyncio.run(_run_engine_with_live_async(engine, request, events))


def build_source_event_hub(config) -> EventHub:
    """Compose the production source event stream with a redacted local log."""

    from src.application.event_log import SanitizedEventLog

    return EventHub(event_log=SanitizedEventLog(config.data_dir / "dashboard-events.jsonl"))


def build_dashboard_service(config) -> DistillationService:
    """Compose the loopback Dashboard with the same local application boundary."""

    events = build_source_event_hub(config)
    runner = SourceDistillationRunner(
        config=config,
        platform_manager=build_platform_manager(config),
        events=events,
        owner="dashboard",
    )
    return DistillationService(
        repository=JobRepository(config.data_dir / "jobs"),
        events=events,
        source_runner=runner,
    )


def execute_source_request(request: SourceCreatorRequest) -> int:
    """Execute through the same application service consumed by the Dashboard."""
    from src.config import load_config
    from src.platforms.errors import PlatformError

    config = load_config()
    events = build_source_event_hub(config)
    runner = SourceDistillationRunner(
        config=config,
        platform_manager=build_platform_manager(config),
        events=events,
        engine_executor=_run_engine_with_live,
        owner="cli",
    )
    service = DistillationService(
        repository=JobRepository(config.data_dir / "jobs"),
        events=events,
        source_runner=runner,
    )
    try:
        result = service.run_source(request)
    except PlatformError as exc:
        raise click.ClickException(str(exc)) from exc
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    console.print(
        f"[cyan]平台[/cyan]: {result.platform}  [cyan]博主[/cyan]: {result.creator_name}\n"
        f"[cyan]枚举[/cyan]: {result.total}  [yellow]unsupported[/yellow]: "
        f"{result.unsupported}  [cyan]complete[/cyan]: {result.enumeration_complete}"
    )
    if not result.dry_run:
        console.print(
            f"[green]完成 {result.completed}[/green] / [red]失败 {result.failed}[/red] / "
            f"[yellow]不支持 {result.unsupported}[/yellow]"
        )
    return result.exit_code


@cli.command("dashboard")
@click.option("--port", type=click.IntRange(1, 65535), default=8765, show_default=True)
@click.option("--open/--no-open", "open_browser", default=True, show_default=True)
def dashboard(port: int, open_browser: bool):
    """Run the Dashboard on the local loopback interface only."""

    from src.config import load_config

    run_dashboard(build_dashboard_service(load_config()), port=port, open_browser=open_browser)


@cli.command("mcp")
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse", "streamable-http"]),
    default="stdio",
    show_default=True,
    help="stdio 给 Claude Desktop/Cursor；sse/streamable-http 给本地 HTTP 客户端",
)
@click.option("--host", default="127.0.0.1", show_default=True, help="HTTP 传输绑定地址")
@click.option("--port", type=click.IntRange(1, 65535), default=8877, show_default=True)
def mcp_command(transport: str, host: str, port: int):
    """以 MCP Server 暴露蒸馏能力，供其他 AI Agent 调用。"""

    from src.config import load_config
    from src.mcp_server.server import run as run_mcp_server

    run_mcp_server(
        build_dashboard_service(load_config()),
        transport=transport,
        host=host,
        port=port,
    )


@cli.group()
def source():
    """管理内容平台并蒸馏创作者作品。"""


@source.command("platforms")
def source_platforms():
    """列出已注册平台和能力。"""
    from src.config import load_config

    for descriptor in build_platform_manager(load_config()).list_descriptors():
        types = ",".join(sorted(item.value for item in descriptor.item_types))
        console.print(
            f"[cyan]{descriptor.name}[/cyan]  types={types}  "
            f"browser={descriptor.requires_browser} auth={descriptor.requires_auth}"
        )


@source.command("status")
@click.argument("platform", type=str)
def source_status(platform):
    """检查平台登录状态。"""
    from src.config import load_config

    manager = build_platform_manager(load_config())
    adapter = _require_platform(manager, platform)
    status = adapter.auth_status()
    console.print(f"{platform}: [cyan]{status.status}[/cyan] {status.message}")


@source.command("login")
@click.argument("platform", type=str)
@click.option("--headful/--headless", default=True, help="使用外部可见浏览器扫码")
def source_login(platform, headful):
    """打开平台扫码登录流程。"""
    from src.config import load_config

    manager = build_platform_manager(load_config())
    adapter = _require_platform(manager, platform)
    adapter.authenticate(headful=headful)


@source.command("creator")
@click.argument("target")
@click.option("--platform", type=str, default="auto")
@click.option("--emit", type=click.Choice(["episodes", "skill", "both"]), default=None)
@click.option("--rag-chunks/--no-rag-chunks", default=None)
@click.option("--download-workers", type=click.IntRange(min=1), default=None)
@click.option("--asr-workers", type=click.IntRange(min=1, max=1), default=None)
@click.option("--llm-workers", type=click.IntRange(min=1), default=None)
@click.option("--max-active", "max_active_items", type=click.IntRange(min=1), default=None)
@click.option("--retry-limit", type=click.IntRange(min=0), default=None)
@click.option("--resume/--no-resume", default=True)
@click.option("--retry-failed", is_flag=True)
@click.option("--keep-media/--cleanup-media", default=None)
@click.option("--headful/--headless", default=False)
@click.option("--dry-run", is_flag=True)
@click.option("--llm", "llm_provider", type=LLM_CHOICES, default=None)
def source_creator(
    target,
    platform,
    emit,
    rag_chunks,
    download_workers,
    asr_workers,
    llm_workers,
    max_active_items,
    retry_limit,
    resume,
    retry_failed,
    keep_media,
    headful,
    dry_run,
    llm_provider,
):
    """枚举并蒸馏一个创作者的全部可见作品。"""
    from src.config import load_config

    defaults = load_config().distillation
    if emit is None:
        emit_targets = tuple(defaults.emit)
    else:
        emit_targets = ("episodes", "skill") if emit == "both" else (emit,)
    request = SourceCreatorRequest(
        target=target,
        platform=platform,
        emit=emit_targets,
        rag_chunks=defaults.rag_chunks if rag_chunks is None else rag_chunks,
        download_workers=defaults.download_workers if download_workers is None else download_workers,
        asr_workers=defaults.asr_workers if asr_workers is None else asr_workers,
        llm_workers=defaults.llm_workers if llm_workers is None else llm_workers,
        max_active_items=(
            defaults.max_active_items if max_active_items is None else max_active_items
        ),
        retry_limit=defaults.retry_limit if retry_limit is None else retry_limit,
        resume=resume,
        retry_failed=retry_failed,
        keep_media=defaults.keep_media if keep_media is None else keep_media,
        headful=headful,
        dry_run=dry_run,
        llm_provider=llm_provider,
    )
    exit_code = execute_source_request(request)
    if exit_code:
        raise click.exceptions.Exit(exit_code)


@cli.command()
def login():
    """登录B站账号（扫码）"""
    from src.config import load_config
    from src.crawl.auth import run_qrcode_login, save_credential

    config = load_config()
    console.print(Panel("[bold]B站扫码登录[/bold]", title="Distill-Everything"))

    try:
        credential, buvid3 = run_qrcode_login()
        save_credential(credential, buvid3, config.credentials_cache)
    except Exception as e:
        console.print(f"[red]登录失败: {e}")
        sys.exit(1)


@cli.command()
@click.option("--uid", type=int, default=None, help="UP主UID（覆盖.env中的配置）")
@click.option("--max-videos", type=int, default=0, help="最大获取视频数量，0为全部")
def crawl(uid, max_videos):
    """阶段1: 爬取视频列表并下载音频"""
    from src.config import load_config
    from src.crawl.video_list import run_crawl, load_video_list
    from src.crawl.audio_download import (
        generate_cookies_file,
        check_audio_completeness,
        download_audio,
    )
    from src.crawl.auth import get_credential
    from src.asr.funasr_engine import check_transcript_integrity

    config = load_config()
    target_uid = uid or config.up_uid

    if not target_uid:
        console.print("[red]错误: 请指定UP主UID（--uid 参数或 .env 中配置 UP_UID）")
        sys.exit(1)

    limit_desc = "全部" if max_videos == 0 else f"最多 {max_videos} 个新视频"
    console.print(Panel(
        f"[bold]阶段1: 数据采集[/bold]\nUP主UID: {target_uid}\n获取数量: {limit_desc}",
        title="Distill-Everything",
    ))

    # 获取B站认证凭据（自动：.env > 缓存 > 扫码登录）
    credential, buvid3 = get_credential(config)

    # 加载本地已有视频列表（用于时长对比和合并）
    video_list_path = config.data_dir / "video_list.json"
    existing_videos = load_video_list(video_list_path) if video_list_path.exists() else []
    existing_meta_map = {v["bvid"]: v for v in existing_videos}

    # 检查本地已有音频的完整性
    existing_audio_files = list(config.audio_dir.glob("BV*.*"))
    complete_bvids = set()
    incomplete_videos = []   # 需要重新下载的视频（已有但不完整）

    for audio_file in existing_audio_files:
        bvid = audio_file.stem
        meta = existing_meta_map.get(bvid, {})
        duration_str = meta.get("duration", "")
        ok, reason = check_audio_completeness(audio_file, duration_str)
        if ok:
            complete_bvids.add(bvid)
        else:
            console.print(f"[yellow]音频不完整，将重新下载: {bvid} ({reason})")
            incomplete_videos.append(meta if meta else {"bvid": bvid})

    # 把已转写完整的 BV 也算"已处理"，避免被 ASR 删掉音频后又被 crawl 重复下载
    # （ASR 完成后默认 unlink 音频以释放磁盘，transcripts/{bvid}.json 是天然的"已处理"记录）
    asr_done_bvids = set()
    if config.transcripts_dir.exists():
        for tf in config.transcripts_dir.glob("BV*.json"):
            valid, _ = check_transcript_integrity(tf)
            if valid:
                asr_done_bvids.add(tf.stem)
    asr_done_no_audio = asr_done_bvids - complete_bvids
    if asr_done_no_audio:
        console.print(
            f"[dim]已 ASR 完整: {len(asr_done_no_audio)} 个（音频已被 ASR 删除），跳过下载"
        )
    complete_bvids = complete_bvids | asr_done_bvids

    if complete_bvids:
        console.print(f"[dim]本地完整音频: {len(complete_bvids)} 个，跳过")
    if incomplete_videos:
        console.print(f"[yellow]检测到不完整音频: {len(incomplete_videos)} 个，将重新下载")

    # 获取新增视频列表：完整和不完整的都排除（不完整的单独处理，避免重复计数）
    all_existing_bvids = complete_bvids | {v["bvid"] for v in incomplete_videos if v.get("bvid")}
    # 计算需要获取的候选数量：留一些余量应对下载失败（1.5 倍）
    needed = max(0, max_videos - len(complete_bvids)) if max_videos > 0 else 0
    fetch_limit = int(needed * 1.5) + len(incomplete_videos) if needed > 0 else 0
    new_videos = run_crawl(
        target_uid, credential, video_list_path, max_videos,
        existing_bvids=all_existing_bvids,
        existing_videos=existing_videos,
        max_candidates=fetch_limit,
    )

    # 合并：新增视频 + 不完整需重下的视频
    to_download = new_videos + [v for v in incomplete_videos if v.get("bvid")]

    if not to_download:
        console.print("[yellow]没有视频需要下载")
        return

    # 生成cookies文件（用完后清理）
    cookies_file = generate_cookies_file(credential, buvid3=buvid3)

    try:
        # 下载循环：失败的视频跳过且不计入配额
        # max_videos=0 不限制；否则表示"本地最终总共有N个完整音频"
        # 已有完整音频数已计在内，本次最多再下 (max_videos - len(complete_bvids)) 个
        incomplete_bvids = {v["bvid"] for v in incomplete_videos}
        from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn
        success = 0
        skipped_fail = 0
        if max_videos > 0:
            quota = max(0, max_videos - len(complete_bvids))
            console.print(f"[dim]目标总量: {max_videos} 个，已有完整: {len(complete_bvids)} 个，本次最多新下: {quota} 个")
        else:
            quota = 0  # 不限制

        with Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            total_display = quota if quota > 0 else len(to_download)
            task = progress.add_task("下载音频", total=total_display)

            for video in to_download:
                if quota > 0 and success >= quota:
                    break

                bvid = video["bvid"]
                force = bvid in incomplete_bvids
                progress.update(task, description=f"{'重下' if force else '下载'} {bvid}")
                path = download_audio(bvid, config.audio_dir, cookies_file=cookies_file, force=force)

                if path:
                    success += 1
                    progress.advance(task)
                else:
                    skipped_fail += 1
                    console.print(f"[dim]跳过不可用视频: {bvid}，不计入配额")

        summary = f"下载成功: {success}"
        if quota > 0:
            summary += f"/{quota}"
        if skipped_fail:
            summary += f"，跳过不可用: {skipped_fail} 个"
        console.print(f"[bold green]阶段1完成! {summary}")
    finally:
        if cookies_file.exists():
            cookies_file.unlink()


def _scan_pending_audios(config):
    """扫描 audio 目录，返回待转写的 (audio_file, bvid) 列表 + 统计。

    Why: 抽出来给 watch 模式循环复用。返回 (pending_pairs, skipped, incomplete)。
    """
    from src.asr.funasr_engine import check_transcript_integrity

    audio_files = list(config.audio_dir.glob("BV*.*"))
    pending = []
    skipped = 0
    incomplete = 0
    for audio_file in audio_files:
        bvid = audio_file.stem
        transcript_path = config.transcripts_dir / f"{bvid}.json"
        valid, reason = check_transcript_integrity(transcript_path, audio_path=audio_file)
        if valid:
            skipped += 1
        else:
            if transcript_path.exists():
                console.print(f"[yellow]转写不完整，重新转写: {bvid} ({reason})")
                incomplete += 1
            pending.append((audio_file, bvid))
    return pending, skipped, incomplete


def _process_pending_batch(pending, video_meta_map, engine, config, delete_audio: bool):
    """转写一批 pending 音频。转写成功后按 delete_audio 决定是否 unlink。"""
    from src.asr.funasr_engine import save_transcript, check_transcript_integrity

    success = 0
    deleted = 0
    for i, (audio_file, bvid) in enumerate(pending, 1):
        console.print(f"[blue]转写 [{i}/{len(pending)}] {bvid}")
        try:
            result = engine.transcribe(audio_file, bvid)
            meta = video_meta_map.get(bvid, {})
            save_transcript(result, meta, config.transcripts_dir)
            success += 1

            # 删除音频前再做一次完整性校验，避免误删导致永久丢失（双保险）
            if delete_audio:
                transcript_path = config.transcripts_dir / f"{bvid}.json"
                valid, reason = check_transcript_integrity(transcript_path, audio_path=audio_file)
                if valid:
                    try:
                        audio_file.unlink()
                        deleted += 1
                        console.print(f"[dim]已删除音频 {audio_file.name}（释放磁盘）")
                    except OSError as e:
                        console.print(f"[yellow]删除音频失败 {audio_file.name}: {e}")
                else:
                    console.print(f"[yellow]转写后完整性校验未通过，保留音频 {bvid}: {reason}")
        except Exception as e:
            console.print(f"[red]转写失败 {bvid}: {e}")

    return success, deleted


@cli.command()
@click.option("--delete-audio/--keep-audio", default=True,
              help="转写并校验通过后删除音频以节省磁盘（默认开启，--keep-audio 关闭）")
@click.option("--watch", is_flag=True, default=False,
              help="持续监听 audio 目录，新音频出现就转写（适合 crawl 边下边转写场景，Ctrl+C 退出）")
@click.option("--watch-interval", type=int, default=60,
              help="watch 模式下两次扫描之间的秒数（默认 60）")
def asr(delete_audio, watch, watch_interval):
    """阶段2: FunASR语音转文字（支持转写后自动删除音频 + watch 长跑模式）"""
    import time
    from src.config import load_config
    from src.asr.funasr_engine import FunASREngine
    from src.crawl.video_list import load_video_list

    config = load_config()

    panel_title = "阶段2: 语音识别"
    if watch:
        panel_title += f"（watch 模式，每 {watch_interval}s 扫描一次）"
    if delete_audio:
        panel_title += "  [转写后自动删除音频]"
    console.print(Panel(f"[bold]{panel_title}[/bold]", title="Distill-Everything"))

    # 加载视频列表（用于元信息）
    video_list_path = config.data_dir / "video_list.json"
    if not video_list_path.exists():
        console.print("[red]错误: 请先运行 crawl 阶段获取视频列表")
        sys.exit(1)

    videos = load_video_list(video_list_path)
    video_meta_map = {v["bvid"]: v for v in videos}

    # 单次扫描（非 watch 模式）：必须有音频才继续
    if not watch:
        pending, skipped, incomplete = _scan_pending_audios(config)
        if skipped:
            console.print(f"[dim]已跳过完整转写: {skipped} 个")
        if incomplete:
            console.print(f"[yellow]检测到不完整转写: {incomplete} 个，将重新转写")
        if not pending:
            console.print("[yellow]所有音频文件已转写完毕")
            return
        console.print(f"[blue]待转写: {len(pending)} 个音频文件")

        engine = FunASREngine(
            model_name=config.funasr.model,
            vad_model=config.funasr.vad_model,
            punc_model=config.funasr.punc_model,
            model_dir=config.model_cache_dir,
        )
        success, deleted = _process_pending_batch(
            pending, video_meta_map, engine, config, delete_audio,
        )
        summary = f"成功: {success}/{len(pending)}"
        if delete_audio:
            summary += f"，已删除音频: {deleted} 个"
        console.print(f"[bold green]阶段2完成! {summary}")
        return

    # watch 模式：先加载引擎（30-60s 慢），再循环扫描，Ctrl+C 退出
    console.print("[dim]初始化 ASR 引擎中（首次加载较慢）...")
    engine = FunASREngine(
        model_name=config.funasr.model,
        vad_model=config.funasr.vad_model,
        punc_model=config.funasr.punc_model,
        model_dir=config.model_cache_dir,
    )
    total_success = 0
    total_deleted = 0
    round_no = 0
    try:
        while True:
            round_no += 1
            pending, skipped, incomplete = _scan_pending_audios(config)
            console.print(
                f"[dim][轮 {round_no}] 扫描结果: 已跳过 {skipped} / 不完整 {incomplete} / 待转写 {len(pending)}"
            )
            if pending:
                # 也刷新一次 video_list（crawl 可能刚增量写入新视频元信息）
                if video_list_path.exists():
                    videos = load_video_list(video_list_path)
                    video_meta_map = {v["bvid"]: v for v in videos}
                success, deleted = _process_pending_batch(
                    pending, video_meta_map, engine, config, delete_audio,
                )
                total_success += success
                total_deleted += deleted
                console.print(
                    f"[blue][轮 {round_no}] 完成: 成功 {success} / 已删 {deleted}"
                )
            else:
                console.print(
                    f"[dim][轮 {round_no}] 无待转写音频，{watch_interval}s 后再次扫描"
                )
            time.sleep(watch_interval)
    except KeyboardInterrupt:
        console.print(
            f"\n[bold green]watch 模式退出。累计: 成功 {total_success} / 已删 {total_deleted}"
        )


@cli.command()
@click.option("--llm", "llm_provider", type=LLM_CHOICES,
              default=None, help="LLM提供商（覆盖.env中的 LLM_PROVIDER 配置）")
def clean(llm_provider):
    """阶段3: 文本清洗与结构化"""
    from src.config import load_config
    from src.clean.text_processor import (
        TextProcessor, save_cleaned, create_llm_client, check_cleaned_integrity,
    )
    from src.asr.funasr_engine import load_transcript

    config = load_config()
    provider = llm_provider or config.llm_provider

    console.print(Panel(
        f"[bold]阶段3: 文本清洗[/bold]\nLLM: {provider}",
        title="Distill-Everything",
    ))

    # 查找所有转写结果
    transcript_files = list(config.transcripts_dir.glob("*.json"))
    if not transcript_files:
        console.print("[red]错误: 未找到转写结果，请先运行 asr 阶段")
        sys.exit(1)

    # 过滤待清洗文件：未清洗、不完整、或转写已更新
    pending_files = []
    skipped = 0
    incomplete = 0
    outdated = 0
    for f in transcript_files:
        cleaned_path = config.cleaned_dir / f.name
        valid, reason = check_cleaned_integrity(cleaned_path)

        if not valid:
            if cleaned_path.exists():
                console.print(f"[yellow]清洗不完整，重新清洗: {f.stem} ({reason})")
                incomplete += 1
            pending_files.append(f)
            continue

        # 完整性通过，再对比转写文本长度，判断转写是否已更新
        try:
            import json as _json
            transcript_len = len(_json.loads(f.read_text(encoding="utf-8")).get("full_text", ""))
            cleaned_len = len(_json.loads(cleaned_path.read_text(encoding="utf-8")).get("full_text", ""))
            if transcript_len > cleaned_len * 1.1:
                console.print(f"[yellow]转写已更新，重新清洗: {f.stem} (转写 {transcript_len} 字 > 已清洗 {cleaned_len} 字)")
                pending_files.append(f)
                outdated += 1
                continue
        except Exception:
            pass

        skipped += 1

    if skipped:
        console.print(f"[dim]已跳过完整清洗: {skipped} 个")
    if incomplete:
        console.print(f"[yellow]检测到不完整清洗: {incomplete} 个，将重新清洗")
    if outdated:
        console.print(f"[yellow]检测到转写已更新: {outdated} 个，将重新清洗")

    if not pending_files:
        console.print("[yellow]所有文本已清洗完毕")
        return

    console.print(f"[blue]待清洗: {len(pending_files)} 个文件")

    # 根据配置创建 LLM 客户端
    llm_client = create_llm_client(provider, config)
    processor = TextProcessor(llm_client=llm_client)

    # 逐文件清洗并立即保存
    success = 0
    for i, f in enumerate(pending_files, 1):
        try:
            transcript_data = load_transcript(f)
            console.print(f"[blue]清洗 [{i}/{len(pending_files)}] {f.stem}")
            cleaned_doc = processor.process_transcript(transcript_data)
            save_cleaned(cleaned_doc, config.cleaned_dir)
            success += 1
        except Exception as e:
            console.print(f"[red]清洗失败 {f.stem}: {e}")

    console.print(f"[bold green]阶段3完成! 成功: {success}/{len(pending_files)}")


@cli.command("model")
@click.option("--llm", "llm_provider", type=LLM_CHOICES,
              default=None, help="LLM提供商（覆盖.env中的 LLM_PROVIDER 配置）")
def model_cmd(llm_provider):
    """阶段4: 知识建模"""
    from src.config import load_config
    from src.clean.text_processor import load_cleaned, create_llm_client
    from src.model.knowledge_extractor import (
        KnowledgeExtractor,
        save_video_knowledge,
        save_blogger_profile,
    )

    config = load_config()
    provider = llm_provider or config.llm_provider

    console.print(Panel(
        f"[bold]阶段4: 知识建模[/bold]\nLLM: {provider}",
        title="Distill-Everything",
    ))

    # 查找所有清洗结果
    cleaned_files = list(config.cleaned_dir.glob("*.json"))
    if not cleaned_files:
        console.print("[red]错误: 未找到清洗结果，请先运行 clean 阶段")
        sys.exit(1)

    # 创建 LLM 客户端
    llm_client = create_llm_client(provider, config)
    if not llm_client:
        console.print("[red]错误: 知识建模需要可用的 LLM，请配置对应的 API Key")
        sys.exit(1)

    extractor = KnowledgeExtractor(llm_client=llm_client)

    # 过滤已提取知识的文件，加载已有结果
    from src.model.knowledge_extractor import load_video_knowledge, check_knowledge_integrity
    pending_files = []
    all_knowledge = []
    skipped = 0
    for f in cleaned_files:
        knowledge_path = config.knowledge_dir / f.name
        valid, reason = check_knowledge_integrity(knowledge_path)
        if valid:
            try:
                all_knowledge.append(load_video_knowledge(knowledge_path))
                skipped += 1
            except Exception:
                pending_files.append(f)
        else:
            if knowledge_path.exists():
                console.print(f"[yellow]重新提取 {f.stem}: {reason}")
            pending_files.append(f)

    if skipped:
        console.print(f"[dim]已跳过 {skipped} 个（已完整提取）")

    # 逐个视频提取知识
    success = 0
    for i, f in enumerate(pending_files, 1):
        try:
            cleaned_doc = load_cleaned(f)
            console.print(f"[blue]知识提取 [{i}/{len(pending_files)}] {f.stem}")
            knowledge = extractor.extract_from_video(cleaned_doc)
            save_video_knowledge(knowledge, config.knowledge_dir)
            all_knowledge.append(knowledge)
            success += 1
        except Exception as e:
            console.print(f"[red]提取失败 {f.stem}: {e}")

    if pending_files:
        console.print(f"[dim]本次提取: {success}/{len(pending_files)}")

    # 综合生成博主画像（每次都重新合成，因为可能有新增视频）
    console.print("[blue]正在合成博主画像...")
    profile = extractor.merge_knowledge(all_knowledge, up_uid=config.up_uid)
    profile_path = config.knowledge_dir / "blogger_profile.json"
    save_blogger_profile(profile, profile_path)

    console.print("[bold green]阶段4完成!")


@cli.command()
def generate():
    """阶段5: 生成SKILL.md"""
    from src.config import load_config
    from src.model.knowledge_extractor import load_blogger_profile
    from src.generate.skill_generator import SkillGenerator

    config = load_config()

    console.print(Panel("[bold]阶段5: 生成SKILL.md[/bold]", title="Distill-Everything"))

    # 加载博主画像
    profile_path = config.knowledge_dir / "blogger_profile.json"
    if not profile_path.exists():
        console.print("[red]错误: 未找到博主画像，请先运行 model 阶段")
        sys.exit(1)

    profile = load_blogger_profile(profile_path)

    # 生成SKILL.md（带时间戳，每次新增不覆盖）
    generator = SkillGenerator(template_dir="templates")
    output_path = _skill_output_path(config.output_dir, profile.name)
    generator.generate_and_save(profile, output_path)

    console.print("[bold green]阶段5完成!")
    console.print(f"[green]输出文件: {output_path}")


@cli.command()
@click.option("--file", "file_path", type=click.Path(exists=True), required=True,
              help="文档文件路径（支持 .txt .docx .pdf）")
@click.option("--llm", "llm_provider", type=LLM_CHOICES,
              default=None, help="LLM提供商")
@click.option("--name", "author_name", type=str, default=None,
              help="作者/人物名称（默认用文件名）")
@click.option("--by-chapter/--no-by-chapter", default=True,
              help="是否按章节独立处理文档（默认开启）")
@click.option("--rag-chunks/--no-rag-chunks", default=True,
              help="是否输出 RAG chunks JSON（默认开启）")
def distill(file_path, llm_provider, author_name, by_chapter, rag_chunks):
    """文档蒸馏: 从 txt/docx/pdf 文件生成 SKILL.md"""
    from src.config import load_config
    from src.reader.document_reader import (
        document_to_cleaned,
        book_to_chapter_cleaneds,
        generate_book_id,
    )
    from src.clean.text_processor import create_llm_client, save_cleaned
    from src.model.knowledge_extractor import (
        KnowledgeExtractor, save_video_knowledge, save_blogger_profile,
    )
    from src.generate.skill_generator import SkillGenerator
    from src.rag.chunker import build_chunks

    config = load_config()
    provider = llm_provider or config.llm_provider
    fpath = Path(file_path)

    console.print(Panel(
        f"[bold]文档蒸馏[/bold]\n文件: {fpath.name}\nLLM: {provider}",
        title="Distill-Everything",
    ))

    llm_client = create_llm_client(provider, config)
    if not llm_client:
        console.print("[red]错误: 文档蒸馏需要可用的 LLM")
        sys.exit(1)

    extractor = KnowledgeExtractor(llm_client=llm_client)
    all_knowledge = []

    if by_chapter:
        book_id = generate_book_id(fpath)
        cleanup_book_artifacts(config, book_id)
        cleaned_docs = book_to_chapter_cleaneds(
            fpath,
            llm_client=llm_client,
            doc_title=author_name,
        )
        console.print(f"[blue]按章节处理: {len(cleaned_docs)} 个章节")

        for i, cleaned_doc in enumerate(cleaned_docs, 1):
            console.print(f"[blue]章节 [{i}/{len(cleaned_docs)}] {cleaned_doc['bvid']}")
            save_cleaned(cleaned_doc, config.cleaned_dir)
            knowledge = extractor.extract_from_video(cleaned_doc)
            save_video_knowledge(knowledge, config.knowledge_dir)
            all_knowledge.append(knowledge)
            if rag_chunks:
                save_rag_chunks(
                    build_chunks(cleaned_doc, knowledge),
                    config.rag_chunks_dir,
                )
    else:
        cleaned_doc = document_to_cleaned(fpath, llm_client=llm_client, doc_title=author_name)
        save_cleaned(cleaned_doc, config.cleaned_dir)
        console.print(f"[green]文档清洗完成: {cleaned_doc['bvid']}")

        knowledge = extractor.extract_from_video(cleaned_doc)
        save_video_knowledge(knowledge, config.knowledge_dir)
        all_knowledge.append(knowledge)
        if rag_chunks:
            save_rag_chunks(
                build_chunks(cleaned_doc, knowledge),
                config.rag_chunks_dir,
            )

    console.print("[blue]合成画像...")
    profile = extractor.merge_knowledge(all_knowledge, up_name=author_name or fpath.stem, up_uid=0)
    if author_name:
        profile.name = author_name
    profile_path = config.knowledge_dir / "blogger_profile.json"
    save_blogger_profile(profile, profile_path)

    generator = SkillGenerator(template_dir="templates")
    output_name = author_name or fpath.stem
    output_path = _skill_output_path(config.output_dir, output_name)
    generator.generate_and_save(profile, output_path)

    console.print("\n[bold green]文档蒸馏完成!")
    console.print(f"[green]输出文件: {output_path}")


@cli.command()
@click.option("--file", "file_path", type=click.Path(exists=True), required=True,
              help="飞书妙记文字记录 txt，或录音文件 (.mp3/.wav/.m4a/.flac/.aac/.ogg)")
@click.option("--llm", "llm_provider", type=LLM_CHOICES,
              default=None, help="LLM提供商")
@click.option("--title", "meeting_title", type=str, default=None,
              help="会议主题（默认用文件名）")
@click.option("--no-pdf", is_flag=True, default=False,
              help="只生成 Markdown，不生成 PDF")
def meeting(file_path, llm_provider, meeting_title, no_pdf):
    """会议纪要: 从飞书妙记文字记录 txt 生成飞书风格智能纪要（MD + PDF）"""
    from src.config import load_config
    from src.reader.document_reader import read_document
    from src.clean.text_processor import create_llm_client
    from src.meeting.transcript_parser import parse_feishu_txt
    from src.meeting.pipeline import transcript_to_minutes_files

    config = load_config()
    provider = llm_provider or config.llm_provider
    fpath = Path(file_path)

    console.print(Panel(
        f"[bold]会议纪要[/bold]\n文件: {fpath.name}\nLLM: {provider}",
        title="Distill-Everything",
    ))

    # 1. 读取输入：音频走 ASR+说话人分离，txt 走飞书文字记录解析
    if _is_audio_file(fpath):
        from src.meeting.audio_transcriber import audio_to_transcript
        transcript = audio_to_transcript(fpath, config)
    else:
        text = read_document(fpath)
        transcript = parse_feishu_txt(text)
    transcript.title = meeting_title or fpath.stem
    console.print(
        f"[blue]解析完成: {len(transcript.lines)} 段发言, "
        f"{len(transcript.speakers)} 位说话人, {len(transcript.keywords)} 个关键词"
    )

    # 2. LLM 生成智能纪要 + 渲染 MD/PDF（复用 meeting 管线）
    llm_client = create_llm_client(provider, config)
    if not llm_client:
        console.print("[red]错误: 生成智能纪要需要可用的 LLM（请在 .env 配置对应 API Key）")
        sys.exit(1)
    transcript_to_minutes_files(transcript, llm_client, config.output_dir, no_pdf)

    console.print("[bold green]会议纪要完成!")


@cli.command("feishu-meeting")
@click.option("--url", "minute_url", type=str, required=True,
              help="飞书妙记分享链接或 24 位 minute_token")
@click.option("--llm", "llm_provider", type=LLM_CHOICES,
              default=None, help="LLM提供商")
@click.option("--title", "meeting_title", type=str, default=None,
              help="会议主题（默认用 minute_token）")
@click.option("--no-pdf", is_flag=True, default=False,
              help="只生成 Markdown，不生成 PDF")
def feishu_meeting(minute_url, llm_provider, meeting_title, no_pdf):
    """飞书妙记录音 → 自动下载 → 本地转写(说话人分离) → 智能纪要（MD + PDF）"""
    from src.config import load_config
    from src.clean.text_processor import create_llm_client
    from src.feishu.client import FeishuClient
    from src.feishu.errors import FeishuError
    from src.feishu.minutes import extract_minute_token, download_minute_media
    from src.meeting.audio_transcriber import audio_to_transcript
    from src.meeting.pipeline import transcript_to_minutes_files

    config = load_config()
    provider = llm_provider or config.llm_provider

    try:
        minute_token = extract_minute_token(minute_url)
    except ValueError as e:
        console.print(f"[red]链接或 token 无法解析: {e}")
        sys.exit(1)

    console.print(Panel(
        f"[bold]飞书妙记纪要[/bold]\nminute_token: {minute_token}\nLLM: {provider}",
        title="Distill-Everything",
    ))

    # 1. 从飞书下载妙记录音到本地（config.audio_dir 已由 ensure_dirs 建好）
    try:
        client = FeishuClient(config.feishu.app_id, config.feishu.app_secret)
        media_path = config.audio_dir / f"feishu-{minute_token}.media"
        console.print("[blue]下载妙记录音中...")
        download_minute_media(client, minute_token, media_path)
    except FeishuError as e:
        console.print(f"[red]下载妙记录音失败: {e}")
        sys.exit(1)
    console.print(f"[green]录音已下载: {media_path}")

    # 2. 本地转写（ffmpeg + FunASR cam++ 说话人分离）
    console.print("[blue]本地转写中（首次会下载 cam++ 模型）...")
    transcript = audio_to_transcript(media_path, config)
    transcript.title = meeting_title or minute_token
    console.print(
        f"[blue]转写完成: {len(transcript.lines)} 段发言, {len(transcript.speakers)} 位说话人"
    )

    # 3. LLM 生成纪要 + 渲染 MD/PDF（复用 meeting 管线）
    llm_client = create_llm_client(provider, config)
    if not llm_client:
        console.print("[red]错误: 生成智能纪要需要可用的 LLM（请在 .env 配置对应 API Key）")
        sys.exit(1)
    transcript_to_minutes_files(transcript, llm_client, config.output_dir, no_pdf)

    console.print("[bold green]飞书妙记纪要完成!")


@cli.command()
@click.option("--name", "author_name", type=str, required=True, help="画像名称 / 输出文件名")
@click.option("--llm", "llm_provider", type=LLM_CHOICES,
              default=None, help="LLM提供商")
@click.option("--sources", "source_patterns", multiple=True, required=True,
              help="cleaned 目录匹配模式，可传多次")
def fuse(author_name, llm_provider, source_patterns):
    """融合多个视频 / 章节 cleaned 为统一画像"""
    from src.config import load_config
    from src.clean.text_processor import create_llm_client, load_cleaned
    from src.model.knowledge_extractor import (
        KnowledgeExtractor,
        load_video_knowledge,
        check_knowledge_integrity,
        save_video_knowledge,
        save_blogger_profile,
    )
    from src.generate.skill_generator import SkillGenerator

    config = load_config()
    provider = llm_provider or config.llm_provider
    cleaned_files = collect_matching_files(config.cleaned_dir, source_patterns)
    if not cleaned_files:
        console.print("[red]错误: 未找到匹配的 cleaned 文件")
        sys.exit(1)

    llm_client = create_llm_client(provider, config)
    if not llm_client:
        console.print("[red]错误: fuse 需要可用的 LLM")
        sys.exit(1)

    extractor = KnowledgeExtractor(llm_client=llm_client)
    all_knowledge = []
    for cleaned_file in cleaned_files:
        knowledge_path = config.knowledge_dir / cleaned_file.name
        valid, _ = check_knowledge_integrity(knowledge_path)
        if valid:
            all_knowledge.append(load_video_knowledge(knowledge_path))
            continue

        cleaned_doc = load_cleaned(cleaned_file)
        console.print(f"[blue]补提知识: {cleaned_file.stem}")
        knowledge = extractor.extract_from_video(cleaned_doc)
        save_video_knowledge(knowledge, config.knowledge_dir)
        all_knowledge.append(knowledge)

    profile = extractor.merge_knowledge(all_knowledge, up_name=author_name, up_uid=0)
    profile.name = author_name
    save_blogger_profile(profile, config.knowledge_dir / "blogger_profile.json")

    generator = SkillGenerator(template_dir="templates")
    output_path = _skill_output_path(config.output_dir, author_name)
    generator.generate_and_save(profile, output_path)
    console.print(f"[bold green]融合完成! 输出文件: {output_path}")


@cli.command()
@click.option("--source-id", "source_patterns", multiple=True, required=True,
              help="source_id 匹配模式，可传多次")
def chunks(source_patterns):
    """从 cleaned / knowledge 重建 rag_chunks"""
    from src.config import load_config
    from src.clean.text_processor import load_cleaned
    from src.model.knowledge_extractor import (
        load_video_knowledge,
        check_knowledge_integrity,
    )
    from src.rag.chunker import build_chunks

    config = load_config()
    cleaned_files = collect_matching_files(config.cleaned_dir, source_patterns)
    if not cleaned_files:
        console.print("[red]错误: 未找到匹配的 cleaned 文件")
        sys.exit(1)

    for cleaned_file in cleaned_files:
        cleaned_doc = load_cleaned(cleaned_file)
        knowledge_path = config.knowledge_dir / cleaned_file.name
        knowledge = None
        valid, _ = check_knowledge_integrity(knowledge_path)
        if valid:
            knowledge = load_video_knowledge(knowledge_path)

        save_rag_chunks(
            build_chunks(cleaned_doc, knowledge),
            config.rag_chunks_dir,
        )
        console.print(f"[green]已生成 chunks: {cleaned_file.stem}")


@cli.command()
@click.option("--uid", type=int, default=None, help="UP主UID")
@click.option("--max-videos", type=int, default=0, help="最大获取视频数量")
@click.option("--llm", "llm_provider", type=LLM_CHOICES,
              default=None, help="LLM提供商（覆盖.env中的 LLM_PROVIDER 配置）")
@click.option("--stages", "stages_str", type=str, default="all",
              help="要执行的阶段，如: all, 1,2,3, 3-5, 1,3-5（默认 all）")
def run(uid, max_videos, llm_provider, stages_str):
    """一键运行流水线（可通过 --stages 选择阶段）"""
    from src.config import load_config

    config = load_config()
    target_uid = uid or config.up_uid
    provider = llm_provider or config.llm_provider

    try:
        stages = parse_stages(stages_str)
    except ValueError:
        console.print(f"[red]错误: 无效的阶段格式 '{stages_str}'，示例: all, 1,2,3, 3-5")
        sys.exit(1)

    if not stages:
        console.print("[red]错误: 未选择任何阶段")
        sys.exit(1)

    # 旧的一键命令在完整流水线场景委托给统一 Bilibili Source Adapter；
    # 分阶段和限量运行继续保留原行为，避免破坏已有脚本。
    if stages == [1, 2, 3, 4, 5] and target_uid and max_videos == 0:
        exit_code = execute_source_request(
            SourceCreatorRequest(
                target=f"https://space.bilibili.com/{target_uid}",
                platform="bilibili",
                emit=("skill",),
                llm_provider=provider,
            )
        )
        if exit_code:
            raise click.exceptions.Exit(exit_code)
        return

    # 阶段1需要UID
    if 1 in stages and not target_uid:
        console.print("[red]错误: 请指定UP主UID")
        sys.exit(1)

    stage_names = {1: "数据采集", 2: "语音识别", 3: "文本清洗", 4: "知识建模", 5: "生成SKILL.md"}
    selected = ", ".join(f"{s}-{stage_names[s]}" for s in stages)

    console.print(Panel(
        f"[bold]Distill-Everything 流水线[/bold]\n"
        f"UP主UID: {target_uid or '(无需)'}\n"
        f"最大视频数: {'不限' if max_videos == 0 else max_videos}\n"
        f"LLM: {provider}\n"
        f"执行阶段: {selected}",
        title="Distill-Everything",
    ))

    ctx = click.Context(cli)
    total = len(stages)

    if 1 in stages:
        console.print(f"\n[bold]═══ [{stages.index(1)+1}/{total}] 阶段1: 数据采集 ═══[/bold]")
        ctx.invoke(crawl, uid=target_uid, max_videos=max_videos)

    if 2 in stages:
        console.print(f"\n[bold]═══ [{stages.index(2)+1}/{total}] 阶段2: 语音识别 ═══[/bold]")
        ctx.invoke(asr)

    if 3 in stages:
        console.print(f"\n[bold]═══ [{stages.index(3)+1}/{total}] 阶段3: 文本清洗 ═══[/bold]")
        ctx.invoke(clean, llm_provider=provider)

    if 4 in stages:
        console.print(f"\n[bold]═══ [{stages.index(4)+1}/{total}] 阶段4: 知识建模 ═══[/bold]")
        ctx.invoke(model_cmd, llm_provider=provider)

    if 5 in stages:
        console.print(f"\n[bold]═══ [{stages.index(5)+1}/{total}] 阶段5: 生成SKILL.md ═══[/bold]")
        ctx.invoke(generate)

    console.print(f"\n[bold green]流水线完成! 已执行阶段: {selected}")


if __name__ == "__main__":
    cli()
