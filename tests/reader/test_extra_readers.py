"""本地文件格式读取：扩展文档格式。"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from src.reader.extra_readers import (
    EXTRA_FORMATS,
    read_csv_text,
    read_epub,
    read_html,
    read_json_text,
    read_markdown,
    read_pptx,
    read_subtitle,
)


def test_markdown_strips_syntax(tmp_path):
    path = tmp_path / "a.md"
    path.write_text("# 标题\n\n- 要点一\n- 要点二\n\n[链接](https://example.com)\n", encoding="utf-8")

    text = read_markdown(path)

    assert "标题" in text and "要点一" in text
    assert "- " not in text and "](" not in text


def test_html_keeps_body_text(tmp_path):
    path = tmp_path / "a.html"
    path.write_text("<html><body><h1>标题</h1><script>x()</script><p>正文</p></body></html>", encoding="utf-8")

    text = read_html(path)

    assert "标题" in text and "正文" in text
    assert "<h1>" not in text


def test_csv_and_json(tmp_path):
    csv_path = tmp_path / "a.csv"
    csv_path.write_text("姓名,年龄\n张三,30\n", encoding="utf-8")
    json_path = tmp_path / "a.json"
    json_path.write_text('{"k": "v"}', encoding="utf-8")

    assert "姓名 | 年龄" in read_csv_text(csv_path)
    assert '"k": "v"' in read_json_text(json_path)


def test_subtitle_drops_timestamps(tmp_path):
    path = tmp_path / "a.srt"
    path.write_text("1\n00:00:01,000 --> 00:00:03,000\n你好\n\n2\n00:00:04,000 --> 00:00:05,000\n世界\n", encoding="utf-8")

    text = read_subtitle(path)

    assert text == "你好\n世界"


def test_pptx_reads_slide_text(tmp_path):
    path = tmp_path / "a.pptx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", "<p:sld><a:t>第一页</a:t></p:sld>")
        archive.writestr("ppt/slides/slide2.xml", "<p:sld><a:t>第二页</a:t></p:sld>")

    text = read_pptx(path)

    assert "第 1 页" in text and "第一页" in text
    assert "第二页" in text


def test_epub_reads_chapters(tmp_path):
    path = tmp_path / "a.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ch1.xhtml", "<html><body><p>第一章</p></body></html>")
        archive.writestr("ch2.html", "<html><body><p>第二章</p></body></html>")

    text = read_epub(path)

    assert "第一章" in text and "第二章" in text


def test_unknown_format_is_rejected(tmp_path):
    from src.reader.extra_readers import read_any

    path = tmp_path / "a.bin"
    path.write_bytes(b"\x00\x01")

    with pytest.raises(ValueError):
        read_any(path)


def test_extra_formats_cover_common_kinds():
    for suffix in (".md", ".html", ".csv", ".json", ".srt", ".pptx", ".xlsx", ".epub"):
        assert suffix in EXTRA_FORMATS


def test_document_reader_dispatches_extra_formats(tmp_path):
    from src.reader.document_reader import read_document

    path = tmp_path / "note.md"
    path.write_text("# 标题\n正文", encoding="utf-8")

    assert "标题" in read_document(Path(path))
