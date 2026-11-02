"""本地文件蒸馏编排：读取/转写 → 清洗 → 知识建模 → 生成 SKILL.md。

与 CLI 的 ``distill`` 共用同一套下游，只是把过程包装成可被 Dashboard 调用的函数。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


def _skill_filename(name: str) -> str:
    from datetime import datetime

    safe = (name or "skill").strip() or "skill"
    return f"{safe}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.skill.md"


def save_rag_chunks(chunked_doc: dict, output_dir: Path) -> Path:
    from src.distillation.store import atomic_write_json

    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{chunked_doc.get('bvid', 'chunks')}.chunks.json"
    atomic_write_json(target, chunked_doc)
    return target


@dataclass(frozen=True)
class LocalDistillResult:
    cleaned_path: Path
    knowledge_path: Path | None
    skill_path: Path | None
    title: str
    char_count: int


def distill_local_file(
    file_path: Path,
    *,
    config: Any,
    llm_client: Any,
    title: str | None = None,
    rag_chunks: bool = False,
    progress: Callable[[str], None] | None = None,
) -> LocalDistillResult:
    from src.clean.text_processor import save_cleaned
    from src.generate.skill_generator import SkillGenerator
    from src.model.knowledge_extractor import (
        KnowledgeExtractor,
        save_blogger_profile,
        save_video_knowledge,
    )
    from src.reader.media_reader import ingest_any, is_media

    def report(message: str) -> None:
        if progress is not None:
            progress(message)

    file_path = Path(file_path)
    report("正在转写音轨…" if is_media(file_path) else "正在读取文档…")
    cleaned = ingest_any(file_path, config=config, llm_client=llm_client, title=title)
    cleaned_path = save_cleaned(cleaned, config.cleaned_dir)
    report(f"已解析 {len(cleaned['full_text'])} 字，开始建模…")

    extractor = KnowledgeExtractor(llm_client=llm_client)
    knowledge = extractor.extract_from_video(cleaned)
    save_video_knowledge(knowledge, config.knowledge_dir)
    knowledge_path = config.knowledge_dir / f"{cleaned['bvid']}.json"
    report("知识已提取，正在合成画像…")

    name = title or file_path.stem
    profile = extractor.merge_knowledge([knowledge], up_name=name, up_uid=0)
    profile.name = name
    save_blogger_profile(profile, config.knowledge_dir / "blogger_profile.json")

    report("正在生成技能文件…")
    generator = SkillGenerator(template_dir="templates")
    skill_path = config.output_dir / _skill_filename(name)
    generator.generate_and_save(profile, skill_path)
    if rag_chunks:
        from src.rag.chunker import build_chunks

        save_rag_chunks(build_chunks(cleaned, knowledge), config.rag_chunks_dir)

    return LocalDistillResult(
        cleaned_path=cleaned_path,
        knowledge_path=knowledge_path if knowledge_path.exists() else None,
        skill_path=Path(skill_path),
        title=name,
        char_count=len(cleaned["full_text"]),
    )
