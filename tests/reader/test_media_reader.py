"""本地音视频：抽音轨 + 转写 → cleaned。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.reader import media_reader


class FakeSegment:
    def __init__(self, index: int, text: str) -> None:
        self.id = str(index)
        self.text = text
        self.start = float(index)
        self.end = float(index) + 1.0
        self.speaker = ""


class FakeResult:
    def __init__(self, text: str) -> None:
        self.full_text = text
        self.segments = [FakeSegment(1, "第一段"), FakeSegment(2, "第二段")]
        self.model_name = "fake-asr"


class FakeEngine:
    def __init__(self, text: str = "转写全文") -> None:
        self.text = text

    def transcribe(self, _path, bvid=""):
        return FakeResult(self.text)


def test_media_kind_detection():
    assert media_reader.media_kind(Path("a.mp4")) == "video"
    assert media_reader.media_kind(Path("a.flac")) == "audio"
    assert media_reader.is_media(Path("a.mkv")) is True
    assert media_reader.is_media(Path("a.pdf")) is False
    with pytest.raises(ValueError):
        media_reader.media_kind(Path("a.pdf"))


def test_media_to_cleaned_builds_document_schema(tmp_path, monkeypatch):
    source = tmp_path / "talk.mp3"
    source.write_bytes(b"fake")

    def fake_extract(src, dst, **_kwargs):
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        Path(dst).write_bytes(b"x" * 4096)

    monkeypatch.setattr(media_reader, "extract_audio", fake_extract)

    cleaned = media_reader.media_to_cleaned(source, engine=FakeEngine("完整转写文本"), title="讲座")

    assert cleaned["title"] == "讲座"
    assert cleaned["source"] == "local_audio"
    assert cleaned["full_text"] == "完整转写文本"
    assert len(cleaned["segments"]) == 2
    assert cleaned["segments"][0]["text"] == "第一段"
    assert cleaned["metadata"]["source_type"] == "audio"


def test_media_to_cleaned_rejects_empty_transcript(tmp_path, monkeypatch):
    source = tmp_path / "silent.wav"
    source.write_bytes(b"fake")
    monkeypatch.setattr(media_reader, "extract_audio", lambda src, dst, **k: Path(dst).write_bytes(b"x" * 4096))

    with pytest.raises(RuntimeError):
        media_reader.media_to_cleaned(source, engine=FakeEngine(""))


def test_ingest_any_routes_documents_to_reader(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("# 标题\n正文内容", encoding="utf-8")

    cleaned = media_reader.ingest_any(path)

    assert cleaned["metadata"]["source_type"] == "document"
    assert "正文内容" in cleaned["full_text"]
