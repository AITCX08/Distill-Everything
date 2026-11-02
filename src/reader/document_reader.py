"""
文档读取模块

支持 txt��docx、pdf 格式的文本提取，
将文档内容转化为与视频转写兼容的 cleaned JSON 格式，
以复用阶段 4（知识建模）和阶段 5（SKILL.md 生成）。
"""

import hashlib
import re
from pathlib import Path
from typing import Optional

from rich.console import Console

console = Console()

# 支持的文件格式
SUPPORTED_FORMATS = {".txt", ".docx", ".pdf"}


def _all_formats():
    from src.reader.extra_readers import EXTRA_FORMATS

    return SUPPORTED_FORMATS | set(EXTRA_FORMATS)
CHAPTER_PATTERN = re.compile(
    r"^(第[一二三四五六七八九十百千\d]+[章节篇]|"
    r"\d+[\.、]\s*.+|"
    r"[一二三四五六七八九十]+[、\.]\s*.+)",
    re.MULTILINE,
)
FALLBACK_CHAPTER_SIZE = 6500
FALLBACK_CHAPTER_MIN = 5000
FALLBACK_CHAPTER_MAX = 8000


def read_txt(file_path: Path) -> str:
    """读取 txt 文件。"""
    return file_path.read_text(encoding="utf-8")


def read_docx(file_path: Path) -> str:
    """读取 docx 文件。"""
    from docx import Document

    doc = Document(str(file_path))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def read_pdf(file_path: Path) -> str:
    """读取 pdf 文件。"""
    import fitz  # PyMuPDF

    doc = fitz.open(str(file_path))
    pages = []
    for page in doc:
        text = page.get_text()
        if text.strip():
            pages.append(text)
    doc.close()
    return "\n\n".join(pages)


def read_document(file_path: Path) -> str:
    """
    根据文件扩展名自动选择读取方式。

    Args:
        file_path: 文档路径

    Returns:
        提取的纯文本内容

    Raises:
        ValueError: 不支持的文件格式
        FileNotFoundError: ��件不存在
    """
    if not file_path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    suffix = file_path.suffix.lower()
    from src.reader.extra_readers import EXTRA_FORMATS

    if suffix not in SUPPORTED_FORMATS and suffix not in EXTRA_FORMATS:
        raise ValueError(
            f"不支持��文件格式: {suffix}，"
            f"支持: {', '.join(sorted(_all_formats()))}"
        )

    console.print(f"[blue]读取文档: {file_path.name} ({suffix})")

    readers = {
        ".txt": read_txt,
        ".docx": read_docx,
        ".pdf": read_pdf,
    }
    if suffix in readers:
        text = readers[suffix](file_path)
    else:
        from src.reader.extra_readers import READERS

        text = READERS[suffix](file_path)

    text = normalize_document_text(text)

    console.print(f"[green]读取完成: {len(text)} 字")
    return text


def generate_doc_id(file_path: Path) -> str:
    """根据文件名生成唯一 ID（用于替代视频的 bvid）。"""
    name = file_path.stem
    hash_suffix = hashlib.md5(str(file_path.resolve()).encode()).hexdigest()[:6]
    return f"DOC_{name}_{hash_suffix}"


def generate_book_id(file_path: Path) -> str:
    """根据文件名生成书籍唯一 ID。"""
    name = file_path.stem
    hash_suffix = hashlib.md5(str(file_path.resolve()).encode()).hexdigest()[:6]
    return f"BOOK_{name}_{hash_suffix}"


def normalize_document_text(text: str) -> str:
    """规范化文档空白。"""
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _build_topics(
    text: str,
    title: str,
    source_id: str,
    llm_client=None,
) -> list[dict]:
    """为整篇文本或单章节生成 topics。"""
    if llm_client:
        from src.clean.text_processor import TextProcessor

        processor = TextProcessor(llm_client=llm_client)
        raw_topics = processor.segment_by_topic(text, title)
    else:
        raw_topics = [{"title": "全文", "content": text, "tags": []}]

    return [
        {
            "id": f"{source_id}_topic_{i:03d}",
            "title": topic.get("title", ""),
            "content": topic.get("content", ""),
            "tags": topic.get("tags", []),
        }
        for i, topic in enumerate(raw_topics)
    ]


def _fallback_split_parts(text: str) -> list[dict]:
    """章节识别失败时按字符数硬切，确保 5000-8000 字范围。"""
    text_length = len(text)
    segments = []
    start = 0
    index = 1

    while start < text_length:
        remaining = text_length - start
        if remaining <= FALLBACK_CHAPTER_MAX:
            end = text_length
        else:
            candidate_end = min(start + FALLBACK_CHAPTER_SIZE, text_length)
            window_start = min(start + FALLBACK_CHAPTER_MIN, text_length)
            window_end = min(start + FALLBACK_CHAPTER_MAX, text_length)
            split_pos = text.rfind("\n\n", window_start, window_end)
            if split_pos == -1:
                split_pos = text.rfind("\n", window_start, window_end)
            end = split_pos if split_pos > start else candidate_end

        chunk = text[start:end].strip()
        if chunk:
            segments.append({
                "chapter_title": f"第 {index} 部分",
                "text": chunk,
                "start": start,
                "end": end,
            })
            index += 1
        start = end

    return segments


def split_into_chapters(text: str) -> list[dict]:
    """
    按章节/段落拆分文档为 segments。

    尝试识别章节标题（如"第X章"、数字编号等），
    失败则按段落拆分。
    """
    matches = list(CHAPTER_PATTERN.finditer(text))

    if len(matches) >= 3:
        segments = []
        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            chunk = text[start:end].strip()
            if chunk:
                segments.append({
                    "chapter_title": match.group().strip(),
                    "text": chunk,
                    "start": start,
                    "end": end,
                })
        return segments

    return _fallback_split_parts(text)


def chapter_to_cleaned(
    book_id: str,
    chapter_index: int,
    chapter_title: str,
    chapter_text: str,
    llm_client=None,
    *,
    total_chapters: int,
    char_range: tuple[int, int] | list[int],
    file_path: Path,
    book_title: str,
) -> dict:
    """将单章节转换为兼容 cleaned schema 的 dict。"""
    chapter_id = f"{book_id}_ch{chapter_index:02d}"
    title = f"{book_title} - {chapter_title}"
    topics = _build_topics(chapter_text, title, chapter_id, llm_client=llm_client)
    chapter_range = [int(char_range[0]), int(char_range[1])]

    return {
        "bvid": chapter_id,
        "title": title,
        "source": f"document:{file_path.name}",
        "full_text": chapter_text,
        "topics": topics,
        "segments": [{
            "text": chapter_text,
            "start": chapter_range[0],
            "end": chapter_range[1],
        }],
        "metadata": {
            "source_type": "book_chapter",
            "chapter_index": chapter_index,
            "chapter_title": chapter_title,
            "parent_book_id": book_id,
            "total_chapters": total_chapters,
            "char_range": chapter_range,
            "file_path": str(file_path.resolve()),
            "file_format": file_path.suffix.lower(),
            "char_count": len(chapter_text),
            "segment_count": 1,
        },
    }


def book_to_chapter_cleaneds(
    file_path: Path,
    llm_client=None,
    doc_title: Optional[str] = None,
) -> list[dict]:
    """读取文档并按章节输出多个 cleaned dict。"""
    text = read_document(file_path)
    book_id = generate_book_id(file_path)
    book_title = doc_title or file_path.stem
    chapters = split_into_chapters(text)
    total_chapters = len(chapters)
    console.print(f"[blue]拆分为 {total_chapters} 个章节")

    cleaned_docs = []
    for index, chapter in enumerate(chapters, 1):
        cleaned_docs.append(chapter_to_cleaned(
            book_id=book_id,
            chapter_index=index,
            chapter_title=chapter.get("chapter_title", f"第 {index} 部分"),
            chapter_text=chapter["text"],
            llm_client=llm_client,
            total_chapters=total_chapters,
            char_range=(chapter["start"], chapter["end"]),
            file_path=file_path,
            book_title=book_title,
        ))

    return cleaned_docs


def document_to_cleaned(
    file_path: Path,
    llm_client=None,
    doc_title: Optional[str] = None,
) -> dict:
    """
    读取文档并转化为 cleaned JSON 格式（兼容阶段 4 输入）。

    Args:
        file_path: 文档文件路径
        llm_client: LLM 客���端（用于主题切分，可选）
        doc_title: 文档标题（默认用文件名）

    Returns:
        cleaned_doc dict，格式与 process_transcript 输出一致
    """
    text = read_document(file_path)
    doc_id = generate_doc_id(file_path)
    title = doc_title or file_path.stem

    # 拆分为 segments
    segments = split_into_chapters(text)
    console.print(f"[blue]拆分为 {len(segments)} 个段落")
    topics = _build_topics(text, title, doc_id, llm_client=llm_client)

    cleaned_doc = {
        "bvid": doc_id,
        "title": title,
        "source": f"document:{file_path.name}",
        "full_text": text,
        "topics": topics,
        "segments": segments,
        "metadata": {
            "source_type": "document",
            "file_path": str(file_path.resolve()),
            "file_format": file_path.suffix.lower(),
            "char_count": len(text),
            "segment_count": len(segments),
        },
    }

    console.print(
        f"[green]文档处理完成: {title}，"
        f"{len(text)} 字，{len(topics)} 个主题段"
    )
    return cleaned_doc
