"""本地音频 / 视频 → 转写 → cleaned JSON。

流程：ffmpeg 抽音轨并转 16kHz 单声道 WAV → FunASR 转写 → 组装与文档路径同 schema 的
cleaned_doc，从而复用阶段 4-5（知识建模 + SKILL.md）。
"""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

AUDIO_EXTENSIONS = {
    ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".amr", ".aiff", ".aif",
}
VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".avi", ".flv", ".webm", ".wmv", ".ts", ".m4v", ".mpeg", ".mpg",
}
MEDIA_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS

FFMPEG_BIN = "ffmpeg"


def is_media(path: Path) -> bool:
    return path.suffix.lower() in MEDIA_EXTENSIONS


def media_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    raise ValueError(f"不是媒体文件：{suffix or path.name}")


def extract_audio(src: Path, dst: Path, *, ffmpeg_bin: str = FFMPEG_BIN) -> None:
    """抽出 16kHz 单声道 WAV；视频会顺带丢掉画面。"""

    dst.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_bin, "-y", "-i", str(src),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(dst),
    ]
    process = subprocess.run(command, capture_output=True, text=True)
    if process.returncode != 0 or not dst.exists() or dst.stat().st_size < 1024:
        detail = (process.stderr or "").strip().splitlines()[-3:]
        raise RuntimeError(f"ffmpeg 抽音轨失败（exit={process.returncode}）：{' / '.join(detail)}")


def generate_media_id(path: Path) -> str:
    digest = hashlib.md5(str(path.resolve()).encode("utf-8")).hexdigest()[:6]
    return f"MEDIA_{path.stem}_{digest}"


def media_to_cleaned(
    file_path: Path,
    *,
    config: Any = None,
    engine: Any = None,
    ffmpeg_bin: str | None = None,
    title: str | None = None,
    keep_audio: bool = False,
) -> dict:
    """把音频/视频转成与文档路径同 schema 的 cleaned_doc。"""

    file_path = Path(file_path)
    kind = media_kind(file_path)
    media_id = generate_media_id(file_path)
    binary = ffmpeg_bin or getattr(config, "ffmpeg_bin", FFMPEG_BIN)

    with tempfile.TemporaryDirectory(prefix="distill-media-") as tmp:
        wav = Path(tmp) / f"{file_path.stem}.wav"
        extract_audio(file_path, wav, ffmpeg_bin=binary)

        asr = engine
        if asr is None:
            from src.asr.funasr_engine import FunASREngine

            asr = FunASREngine()
        result = asr.transcribe(wav, bvid=media_id)

        if keep_audio:
            audio_dir = Path(getattr(config, "audio_dir", file_path.parent))
            audio_dir.mkdir(parents=True, exist_ok=True)
            target = audio_dir / f"{media_id}.wav"
            target.write_bytes(wav.read_bytes())

    full_text = (getattr(result, "full_text", "") or "").strip()
    if not full_text:
        raise RuntimeError("转写结果为空，请确认音频里有可识别的人声")

    segments = [
        {
            "id": segment.id or str(index),
            "text": segment.text,
            "start": segment.start,
            "end": segment.end,
            "speaker": getattr(segment, "speaker", ""),
        }
        for index, segment in enumerate(getattr(result, "segments", []) or [], start=1)
    ]
    topics = [
        {"topic": segment["text"][:30] or f"片段 {index}", "segment_ids": [segment["id"]]}
        for index, segment in enumerate(segments, start=1)
    ]

    return {
        "bvid": media_id,
        "title": title or file_path.stem,
        "source": f"local_{kind}",
        "full_text": full_text,
        "topics": topics,
        "segments": segments,
        "metadata": {
            "source_type": kind,
            "file_path": str(file_path.resolve()),
            "file_format": file_path.suffix.lower(),
            "char_count": len(full_text),
            "segment_count": len(segments),
            "model_name": getattr(result, "model_name", ""),
        },
    }


def ingest_any(
    file_path: Path,
    *,
    config: Any = None,
    llm_client: Callable | None = None,
    title: str | None = None,
) -> dict:
    """统一入口：文档走 reader，音视频走转写。"""

    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(file_path)
    if is_media(file_path):
        return media_to_cleaned(file_path, config=config, title=title)
    from src.reader.document_reader import document_to_cleaned

    return document_to_cleaned(file_path, llm_client=llm_client, doc_title=title)
