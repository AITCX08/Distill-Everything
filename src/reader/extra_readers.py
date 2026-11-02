"""补充文档格式的读取器（不引入新依赖，全部基于标准库 / 已装库）。

覆盖：Markdown、HTML、CSV、JSON、SRT/VTT 字幕、PPTX、XLSX、EPUB。
PDF / DOCX / TXT 仍在 ``document_reader`` 里，保持原有行为。
"""

from __future__ import annotations

import csv
import html
import json
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path

from src.reader.document_reader import read_docx, read_pdf, read_txt

EXTRA_FORMATS = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".html": "html",
    ".htm": "html",
    ".csv": "csv",
    ".json": "json",
    ".srt": "subtitle",
    ".vtt": "subtitle",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
    ".epub": "epub",
}

_SUBTITLE_TIME = re.compile(r"^\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->|^WEBVTT|^\d+$", re.MULTILINE)
_TAG = re.compile(r"<[^>]+>")


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)


def _html_to_text(raw: str) -> str:
    parser = _TextCollector()
    parser.feed(raw)
    return "\n".join(parser.parts)


def read_markdown(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="ignore")
    text = re.sub(r"```.*?```", lambda m: m.group(0).strip("`"), text, flags=re.DOTALL)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    return re.sub(r"^[#>\-\*]+\s?", "", text, flags=re.MULTILINE).strip()


def read_html(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    body = re.search(r"<body[^>]*>(.*)</body>", raw, re.DOTALL | re.IGNORECASE)
    return _html_to_text(body.group(1) if body else raw)


def read_csv_text(path: Path) -> str:
    rows: list[str] = []
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        for row in csv.reader(handle):
            cells = [cell.strip() for cell in row if cell.strip()]
            if cells:
                rows.append(" | ".join(cells))
    return "\n".join(rows)


def read_json_text(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except ValueError:
        return path.read_text(encoding="utf-8", errors="ignore")
    return json.dumps(payload, ensure_ascii=False, indent=2)


def read_subtitle(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    lines = [line.strip() for line in raw.splitlines()]
    return "\n".join(line for line in lines if line and not _SUBTITLE_TIME.match(line))


def read_pptx(path: Path) -> str:
    parts: list[str] = []
    with zipfile.ZipFile(path) as archive:
        slides = sorted(
            (name for name in archive.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", name)),
            key=lambda name: int(re.findall(r"\d+", name)[0]),
        )
        for name in slides:
            xml = archive.read(name).decode("utf-8", errors="ignore")
            texts = re.findall(r"<a:t>(.*?)</a:t>", xml, re.DOTALL)
            if texts:
                parts.append(f"【第 {len(parts) + 1} 页】\n" + html.unescape("\n".join(texts)))
    return "\n\n".join(parts)


def read_xlsx(path: Path) -> str:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    parts: list[str] = []
    for sheet in workbook.worksheets:
        parts.append(f"【工作表：{sheet.title}】")
        for row in sheet.iter_rows(values_only=True):
            cells = [str(cell) for cell in row if cell not in (None, "")]
            if cells:
                parts.append(" | ".join(cells))
    workbook.close()
    return "\n".join(parts)


def read_epub(path: Path) -> str:
    parts: list[str] = []
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.lower().endswith((".xhtml", ".html", ".htm")):
                continue
            raw = archive.read(name).decode("utf-8", errors="ignore")
            text = _html_to_text(raw)
            if text:
                parts.append(text)
    return "\n\n".join(parts)


READERS = {
    ".md": read_markdown,
    ".markdown": read_markdown,
    ".html": read_html,
    ".htm": read_html,
    ".csv": read_csv_text,
    ".json": read_json_text,
    ".srt": read_subtitle,
    ".vtt": read_subtitle,
    ".pptx": read_pptx,
    ".xlsx": read_xlsx,
    ".epub": read_epub,
}


def read_any(path: Path) -> str:
    """按后缀读取文本；覆盖内置三种 + 扩展格式。"""

    suffix = path.suffix.lower()
    if suffix == ".txt":
        return read_txt(path)
    if suffix == ".docx":
        return read_docx(path)
    if suffix == ".pdf":
        return read_pdf(path)
    reader = READERS.get(suffix)
    if reader is None:
        raise ValueError(f"不支持的文件格式：{suffix or '（无后缀）'}")
    return reader(path)
