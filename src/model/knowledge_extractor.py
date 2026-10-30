"""
知识建模模块

使用 LLM（Claude / OpenAI / Qwen / DeepSeek）从清洗后的文本中提取
结构化知识，并综合所有视频生成UP主的知识画像。
"""

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from src.clean.text_processor import LLMClient


def _safe_json_loads(json_str: str):
    """解析 JSON（对象或数组），自动修复 LLM 输出中常见的格式问题，多轮尝试。

    适用于 ``{...}`` 和 ``[...]`` 两种顶层结构；返回类型随输入而定。
    """
    # 第1轮：直接解析
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    cleaned = json_str

    # 第2轮：修复控制字符 + 字符串内裸换行
    cleaned = re.sub(r'(?<!\\)[\x00-\x08\x0b\x0c\x0e-\x1f]', ' ', cleaned)
    cleaned = re.sub(r'("(?:[^"\\]|\\.)*")', _escape_inner_newlines, cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 第3轮：移除尾随逗号（,} 或 ,]）
    cleaned = re.sub(r',\s*([\}\]])', r'\1', cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 第4轮：将 ... 占位符替换为空字符串或空数组
    cleaned = re.sub(r':\s*\.\.\.', ': ""', cleaned)        # value 位置的 ...
    cleaned = re.sub(r',\s*\.\.\.', '', cleaned)             # 数组末尾的 , ...
    cleaned = re.sub(r'\[\s*\.\.\.\s*\]', '[]', cleaned)    # [...] 整体
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 第5轮：替换 Python/JS 字面量为合法 JSON null/false/true（应对 LLM 串味输出）
    # Why: 直播口语等长上下文下，LLM 偶尔会输出 `"key": None` / `NaN` / `undefined` /
    # `Infinity`，标准 json 解析会抛 "Expecting value"。
    cleaned = re.sub(r'(?<![A-Za-z0-9_"])(None|null|undefined)(?![A-Za-z0-9_"])',
                     'null', cleaned)
    cleaned = re.sub(r'(?<![A-Za-z0-9_"])(NaN|Infinity|-Infinity)(?![A-Za-z0-9_"])',
                     'null', cleaned)
    cleaned = re.sub(r'(?<![A-Za-z0-9_"])True(?![A-Za-z0-9_"])', 'true', cleaned)
    cleaned = re.sub(r'(?<![A-Za-z0-9_"])False(?![A-Za-z0-9_"])', 'false', cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 第6轮：补齐缺值场景（"key":  ,  →  "key": null,  ；以及 :} :] 等）
    # Why: LLM 偶尔在 schema 字段后忘了写 value 直接逗号 / 闭合，标准 json 解析抛
    # "Expecting value"。这一轮把所有"冒号后只有空白接着是 , 或 } 或 ]"统一补 null。
    cleaned = re.sub(r':\s*(?=[,\]\}])', ': null', cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 第7轮：截断到最后一个合法的顶层闭合符（} 或 ]）处
    last_close = max(cleaned.rfind('}'), cleaned.rfind(']'))
    if last_close != -1:
        try:
            return json.loads(cleaned[:last_close + 1])
        except json.JSONDecodeError:
            pass

    # 全部失败则抛出原始错误
    return json.loads(json_str)


def _escape_inner_newlines(m: re.Match) -> str:
    """将 JSON 字符串值内的裸换行替换为 \\n。"""
    return m.group(0).replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')


_THINK_TAG_PATTERN = re.compile(
    r"<(think|thinking|reasoning)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_FENCE_PATTERN = re.compile(
    r"```(?:json|JSON)?\s*\n?(.*?)```",
    re.DOTALL,
)


def _find_balanced_block(text: str, open_ch: str, close_ch: str) -> Optional[str]:
    """用括号计数找首个**完整且平衡**的块，避免贪婪匹配吞入额外内容。

    跳过字符串内（含转义）的括号。失败返回 None。
    """
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if in_str:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == open_ch:
            if depth == 0:
                start = i
            depth += 1
        elif ch == close_ch and depth > 0:
            depth -= 1
            if depth == 0 and start != -1:
                return text[start:i + 1]
    return None


def _extract_json_payload(content: str, prefer_array: bool = False) -> Optional[str]:
    """从 LLM 自由文本输出中提取 JSON 字符串。

    Why: 推理模型（DeepSeek-R1 等）经常输出 `<think>...</think>` + 前后缀文字 + 代码块，
    贪婪正则 `re.search(r"\\[.*\\]", ...)` 会把无关内容吞入导致 _safe_json_loads 也救不回来。
    本函数按 4 步降级提取：
      1. 剥离 <think>/<thinking>/<reasoning> 标签内容（应对第三方代理把 reasoning 混入 content）
      2. 优先扫描 ```json...``` / ```...``` 代码块
      3. 用括号计数找首个**完整平衡**的块，而不是贪婪匹配
      4. 全部失败返回 None，由调用方走降级路径
    """
    if not content:
        return None

    cleaned_text = _THINK_TAG_PATTERN.sub("", content).strip()
    open_ch, close_ch = ("[", "]") if prefer_array else ("{", "}")

    for fence in _FENCE_PATTERN.findall(cleaned_text):
        candidate = fence.strip()
        if candidate.startswith(open_ch):
            return candidate
        block = _find_balanced_block(candidate, open_ch, close_ch)
        if block:
            return block

    return _find_balanced_block(cleaned_text, open_ch, close_ch)


def _dump_llm_failure(tag: str, content: str, prompt: str, reason: str) -> None:
    """LLM 调用失败时把原始 content + prompt 落盘到 data/llm_debug/，方便复现定位。

    Why: LLM 输出每次都不同，失败时若不留快照后续无法复盘。落盘条件：知识提取或画像合成
    在 except / 未提取到 JSON 时触发。文件名带时间戳避免相互覆盖。失败本身不阻塞主流程。
    """
    if not content and not prompt:
        return
    try:
        import os
        from datetime import datetime

        debug_root = os.environ.get("LLM_DEBUG_DIR", "data/llm_debug")
        debug_dir = Path(debug_root)
        debug_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_tag = re.sub(r"[^A-Za-z0-9_.\-]", "_", tag)[:60]
        out_path = debug_dir / f"{timestamp}_{safe_tag}_{reason}.txt"
        out_path.write_text(
            f"# tag={tag}\n# reason={reason}\n# content_len={len(content)}\n\n"
            f"==================== LLM RAW CONTENT ====================\n"
            f"{content}\n\n"
            f"==================== PROMPT (preview 2000 chars) ====================\n"
            f"{prompt[:2000]}\n",
            encoding="utf-8",
        )
        console.print(f"[dim]LLM 原始响应已落盘: {out_path}")
    except Exception:
        pass


console = Console()


def _infer_source_type(source_id: str, declared_type: str = "") -> str:
    """根据 source_id 推断来源类型。"""
    if declared_type:
        return declared_type
    if source_id.startswith("BV") or source_id.startswith(("bilibili_", "douyin_")):
        return "video"
    if source_id.startswith("BOOK_") and "_ch" in source_id:
        return "book_chapter"
    return "document"


def _build_source_entries(all_knowledge: list["VideoKnowledge"]) -> tuple[list[dict], list[dict]]:
    """同时构建新旧两套素材来源结构。"""
    sources = []
    video_sources = []
    for knowledge in all_knowledge:
        source_id = knowledge.source_id or knowledge.bvid
        source_type = _infer_source_type(source_id, knowledge.source_type)
        parent_id = None
        if source_type == "book_chapter" and "_ch" in source_id:
            parent_id = source_id.rsplit("_ch", 1)[0]

        sources.append({
            "id": source_id,
            "title": knowledge.title,
            "source_type": source_type,
            "parent_id": parent_id,
            "platform": knowledge.platform,
            "url": knowledge.source_url,
        })
        video_sources.append({
            "bvid": knowledge.bvid or source_id,
            "title": knowledge.title,
        })

    return sources, video_sources


@dataclass
class VideoKnowledge:
    """单个视频的知识提取结果"""
    bvid: str = ""
    source_id: str = ""
    platform: str = ""
    source_type: str = ""
    source_url: str = ""
    title: str = ""
    summary: str = ""
    core_views: list[str] = field(default_factory=list)
    key_concepts: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    arguments: list[dict] = field(default_factory=list)
    # nuwa 新增：思维框架线索、判断准则示例、表达样本
    mental_model_hints: list[dict] = field(default_factory=list)
    decision_examples: list[dict] = field(default_factory=list)
    expression_samples: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.source_id:
            self.source_id = self.bvid
        if not self.bvid:
            self.bvid = self.source_id


@dataclass
class BloggerProfile:
    """UP主知识画像（nuwa-skill / 张雪峰.skill 格式兼容版）

    字段分组（详见 DEVELOPMENT.md §2.6）：
      - 身份与基础：name / uid / domain / self_intro / signature_quote / core_philosophy
      - 身份卡三段：identity_who / identity_origin / identity_now
      - 心智模型与决策：mental_models / decision_heuristics
      - 表达：style / expression_dna（7 维度）/ signature_phrases
      - 价值观：values_pursued / values_rejected / inner_tensions
      - 边界：anti_patterns / honest_boundaries / knowledge_boundary
      - 时间线与谱系：timeline / influenced_by / influenced_who
      - 示例与溯源：typical_qa_pairs / sources / video_sources / key_quotes / research_date
      - 旧兼容：core_views / values（保留兜底，不再是主渲染字段）
    """
    # ---- 基础身份 ----
    name: str = ""
    uid: int = 0
    domain: list[str] = field(default_factory=list)
    self_intro: str = ""                          # 用本人口吻的自我介绍（50字）
    signature_quote: str = ""                     # 文首斜体大金句（1 句）
    core_philosophy: str = ""                     # 核心理念段（2-3 句）

    # ---- 身份卡（张雪峰风格三段） ----
    identity_who: str = ""                        # "我是谁"
    identity_origin: str = ""                     # "我的起点"
    identity_now: str = ""                        # "我现在在做什么"

    # ---- 心智模型与判断 ----
    # mental_models schema: [{name, one_liner, evidence: [str,...], application, limitation}]
    mental_models: list[dict] = field(default_factory=list)
    # decision_heuristics schema: [{rule, scenario, case}]
    decision_heuristics: list[dict] = field(default_factory=list)

    # ---- 表达风格 ----
    style: str = ""                               # 整体风格描述（150字）
    signature_phrases: list[str] = field(default_factory=list)
    # expression_dna 7 维度
    expression_dna: dict = field(default_factory=lambda: {
        "sentence_style": "",   # 句式
        "vocabulary": "",       # 词汇
        "rhythm": "",           # 节奏
        "humor": "",            # 幽默
        "certainty": "",        # 确定性
        "citation_habit": "",   # 引用习惯
        "debate_strategy": "",  # 辩论策略
    })

    # ---- 价值观三层 ----
    values_pursued: list[str] = field(default_factory=list)    # 追求的（按优先级）
    values_rejected: list[str] = field(default_factory=list)   # 拒绝的
    inner_tensions: list[str] = field(default_factory=list)    # 自己也没想清楚的（矛盾与张力）

    # ---- 边界 ----
    anti_patterns: list[str] = field(default_factory=list)     # 反模式（明确拒绝的做法）
    honest_boundaries: list[str] = field(default_factory=list) # 诚实边界/局限性
    knowledge_boundary: dict = field(default_factory=lambda: {"strong": [], "weak": []})

    # ---- 时间线与谱系 ----
    # timeline schema: [{time, event, impact}]
    timeline: list[dict] = field(default_factory=list)
    influenced_by: list[str] = field(default_factory=list)     # 影响过我的
    influenced_who: list[str] = field(default_factory=list)    # 我影响了谁

    # ---- 示例与溯源 ----
    typical_qa_pairs: list[dict] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    video_sources: list[dict] = field(default_factory=list)
    key_quotes: list[str] = field(default_factory=list)        # 5-10 句可传播原话
    research_date: str = ""                                    # 调研日期 YYYY-MM-DD

    # ---- 旧字段兼容（不再作为主渲染字段） ----
    core_views: list[str] = field(default_factory=list)
    values: list[str] = field(default_factory=list)            # 老字段，新版用 values_pursued


class KnowledgeExtractor:
    """
    知识提取器。

    使用统一的 LLMClient 接口从清洗后的视频文本中提取结构化知识，
    并综合多个视频生成UP主的完整知识画像。
    支持 Claude / OpenAI / Qwen / DeepSeek 等后端。
    """

    def __init__(self, llm_client: "LLMClient"):
        """
        初始化知识提取器。

        Args:
            llm_client: 统一的 LLM 客户端（由 create_llm_client 创建）
        """
        self.llm_client = llm_client

    def extract_from_video(self, cleaned_doc: dict) -> VideoKnowledge:
        """
        从单个视频的清洗文本中提取知识。

        Args:
            cleaned_doc: 清洗后的文档数据

        Returns:
            VideoKnowledge 视频知识提取结果
        """
        source_id = cleaned_doc.get("source_id") or cleaned_doc.get("bvid", "")
        bvid = cleaned_doc.get("bvid") or source_id
        metadata = cleaned_doc.get("metadata", {})
        title = cleaned_doc.get("title", "")
        full_text = cleaned_doc.get("full_text", "")

        console.print(f"[blue]提取知识: {bvid} - {title}")

        # 限制文本长度，防止超出上下文窗口
        text_input = full_text[:10000]

        prompt = VIDEO_KNOWLEDGE_PROMPT.format(
            title=title,
            text=text_input,
        )

        content = ""
        try:
            content = self.llm_client.chat(prompt, max_tokens=4096)
            json_str = _extract_json_payload(content, prefer_array=False)
            if json_str:
                data = _safe_json_loads(json_str)
                return VideoKnowledge(
                    bvid=bvid,
                    source_id=source_id,
                    platform=metadata.get("platform", ""),
                    source_type=metadata.get("source_type") or metadata.get("item_type", ""),
                    source_url=metadata.get("source_url") or cleaned_doc.get("canonical_url", ""),
                    title=title,
                    summary=data.get("summary", ""),
                    core_views=data.get("core_views", []),
                    key_concepts=data.get("key_concepts", []),
                    topics=data.get("topics", []),
                    arguments=data.get("arguments", []),
                    mental_model_hints=data.get("mental_model_hints", []),
                    decision_examples=data.get("decision_examples", []),
                    expression_samples=data.get("expression_samples", []),
                )
            console.print(f"[red]知识提取失败 {bvid}: 未能从 LLM 输出中提取到 JSON 对象")
            _dump_llm_failure(bvid, content, prompt, "no_json_payload")
        except Exception as e:
            console.print(f"[red]知识提取失败 {bvid}: {e}")
            _dump_llm_failure(bvid, content, prompt, type(e).__name__)

        return VideoKnowledge(
            bvid=bvid,
            source_id=source_id,
            platform=metadata.get("platform", ""),
            source_type=metadata.get("source_type") or metadata.get("item_type", ""),
            source_url=metadata.get("source_url") or cleaned_doc.get("canonical_url", ""),
            title=title,
        )

    def merge_knowledge(self, all_knowledge: list[VideoKnowledge],
                        up_name: str = "", up_uid: int = 0) -> BloggerProfile:
        """
        综合所有视频知识，生成UP主的知识画像。

        Args:
            all_knowledge: 所有视频的知识提取结果
            up_name: UP主名称
            up_uid: UP主UID

        Returns:
            BloggerProfile 博主画像
        """
        console.print(f"[blue]综合 {len(all_knowledge)} 个视频，生成知识画像...")

        # 汇总所有视频的知识摘要，包含 nuwa 新增字段
        summaries = []
        for k in all_knowledge:
            source_type = _infer_source_type(k.source_id or k.bvid, k.source_type)
            source_label = (
                "[书章节]" if source_type == "book_chapter"
                else "[视频]" if source_type == "video"
                else "[文档]"
            )
            mental_hint = ""
            if k.mental_model_hints:
                mental_hint = f"\n  思维框架线索: {k.mental_model_hints[0].get('hint', '')}"
            decision_ex = ""
            if k.decision_examples:
                decision_ex = f"\n  决策示例: {k.decision_examples[0].get('scenario', '')}"
            expr_sample = ""
            if k.expression_samples:
                expr_sample = f"\n  表达样本: {k.expression_samples[0][:80]}"
            summaries.append(
                f"{source_label} 《{k.title}》:\n"
                f"  摘要: {k.summary}\n"
                f"  核心观点: {', '.join(k.core_views[:3])}\n"
                f"  关键概念: {', '.join(k.key_concepts[:5])}"
                f"{mental_hint}{decision_ex}{expr_sample}"
            )

        summary_text = "\n\n".join(summaries[:50])  # 限制数量
        sources, video_sources = _build_source_entries(all_knowledge)

        from datetime import datetime
        research_date = datetime.now().strftime("%Y-%m-%d")
        prompt = PROFILE_SYNTHESIS_PROMPT.format(
            up_name=up_name or "未知",
            video_count=len(all_knowledge),
            summaries=summary_text,
            research_date=research_date,
        )

        content = ""
        try:
            # 画像字段增多，显著提高 max_tokens 防止 JSON 截断
            content = self.llm_client.chat(prompt, max_tokens=12288)
            json_str = _extract_json_payload(content, prefer_array=False)
            if json_str:
                data = _safe_json_loads(json_str)
                from datetime import datetime
                return BloggerProfile(
                    # 基础身份
                    name=data.get("name", up_name),
                    uid=up_uid,
                    domain=data.get("domain", []),
                    self_intro=data.get("self_intro", ""),
                    signature_quote=data.get("signature_quote", ""),
                    core_philosophy=data.get("core_philosophy", ""),
                    # 身份卡
                    identity_who=data.get("identity_who", ""),
                    identity_origin=data.get("identity_origin", ""),
                    identity_now=data.get("identity_now", ""),
                    # 心智模型
                    mental_models=data.get("mental_models", []),
                    decision_heuristics=data.get("decision_heuristics", []),
                    # 表达
                    style=data.get("style", ""),
                    signature_phrases=data.get("signature_phrases", []),
                    expression_dna=data.get("expression_dna", {}),
                    # 价值观
                    values_pursued=data.get("values_pursued", []),
                    values_rejected=data.get("values_rejected", []),
                    inner_tensions=data.get("inner_tensions", []),
                    # 边界
                    anti_patterns=data.get("anti_patterns", []),
                    honest_boundaries=data.get("honest_boundaries", []),
                    knowledge_boundary=data.get("knowledge_boundary",
                                                {"strong": [], "weak": []}),
                    # 时间线与谱系
                    timeline=data.get("timeline", []),
                    influenced_by=data.get("influenced_by", []),
                    influenced_who=data.get("influenced_who", []),
                    # 示例与溯源
                    typical_qa_pairs=data.get("typical_qa_pairs", []),
                    sources=sources,
                    video_sources=video_sources,
                    key_quotes=data.get("key_quotes", []),
                    research_date=data.get("research_date",
                                           datetime.now().strftime("%Y-%m-%d")),
                    # 旧字段兼容
                    core_views=data.get("core_views", []),
                    values=data.get("values", []),
                )
        except Exception as e:
            console.print(f"[red]知识画像生成失败: {e}")
            _dump_llm_failure("blogger_profile", content, prompt, type(e).__name__)

        # 降级：基于规则生成基础画像
        return self._fallback_profile(all_knowledge, up_name, up_uid)

    def _fallback_profile(self, all_knowledge: list[VideoKnowledge],
                          up_name: str, up_uid: int) -> BloggerProfile:
        """LLM调用失败时的降级画像生成。"""
        all_views = []
        all_concepts = []
        all_topics = []
        for k in all_knowledge:
            all_views.extend(k.core_views)
            all_concepts.extend(k.key_concepts)
            all_topics.extend(k.topics)

        # 统计词频取Top
        from collections import Counter
        top_topics = [t for t, _ in Counter(all_topics).most_common(5)]
        top_concepts = [c for c, _ in Counter(all_concepts).most_common(10)]
        sources, video_sources = _build_source_entries(all_knowledge)

        return BloggerProfile(
            name=up_name,
            uid=up_uid,
            domain=top_topics,
            core_views=all_views[:10],
            style="（自动生成，建议人工补充）",
            signature_phrases=[],
            knowledge_boundary={"strong": top_concepts, "weak": []},
            typical_qa_pairs=[],
            sources=sources,
            video_sources=video_sources,
        )


def save_video_knowledge(knowledge: VideoKnowledge, output_dir: Path) -> Path:
    """保存单个视频的知识提取结果。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{knowledge.source_id or knowledge.bvid}.json"
    from src.distillation.store import atomic_write_json

    atomic_write_json(output_path, asdict(knowledge))
    return output_path


def save_blogger_profile(profile: BloggerProfile, output_path: Path) -> Path:
    """保存博主画像。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(asdict(profile), f, ensure_ascii=False, indent=2)
    console.print(f"[green]博主画像已保存到 {output_path}")
    return output_path


def check_knowledge_integrity(knowledge_path: Path) -> tuple[bool, str]:
    """
    检查单视频知识文件的完整性。

    Returns:
        (is_valid, reason)
    """
    if not knowledge_path.exists():
        return False, "文件不存在"
    if knowledge_path.stat().st_size == 0:
        return False, "文件为空"
    try:
        with open(knowledge_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return False, f"JSON 解析失败: {e}"
    if not data.get("summary", "").strip():
        return False, "summary 为空"
    if not data.get("core_views"):
        return False, "core_views 为空"
    return True, "ok"


def load_video_knowledge(input_path: Path) -> VideoKnowledge:
    """从JSON文件加载单个视频的知识提取结果。"""
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    valid_keys = {f.name for f in VideoKnowledge.__dataclass_fields__.values()}
    return VideoKnowledge(**{k: v for k, v in data.items() if k in valid_keys})


def load_blogger_profile(input_path: Path) -> BloggerProfile:
    """从JSON文件加载博主画像。"""
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "sources" not in data and "video_sources" in data:
        data["sources"] = [
            {
                "id": item.get("bvid", ""),
                "title": item.get("title", ""),
                "source_type": _infer_source_type(item.get("bvid", "")),
                "parent_id": item.get("bvid", "").rsplit("_ch", 1)[0]
                if item.get("bvid", "").startswith("BOOK_") and "_ch" in item.get("bvid", "")
                else None,
            }
            for item in data.get("video_sources", [])
        ]
    if "video_sources" not in data and "sources" in data:
        data["video_sources"] = [
            {"bvid": item.get("id", ""), "title": item.get("title", "")}
            for item in data.get("sources", [])
        ]
    valid_keys = {f.name for f in BloggerProfile.__dataclass_fields__.values()}
    return BloggerProfile(**{k: v for k, v in data.items() if k in valid_keys})


# ===== Prompt 模板 =====

VIDEO_KNOWLEDGE_PROMPT = """你是一个深度知识提取专家。请从以下视频转写文本中提取结构化知识，\
重点捕捉博主独特的思维方式、决策逻辑和表达风格。

视频标题：{title}

转写文本：
{text}

请提取以下信息并以JSON格式返回：
{{
  "summary": "视频内容的简要概括（100-200字）",
  "core_views": ["核心观点1", "核心观点2", ...],
  "key_concepts": ["关键概念/术语1", "关键概念2", ...],
  "topics": ["涉及的主题领域1", "主题2", ...],
  "arguments": [
    {{"claim": "论点", "evidence": "论据/例证"}},
    ...
  ],
  "mental_model_hints": [
    {{"hint": "博主用来分析问题的框架或方法论线索（如：先看本质再看表象）", "context": "使用场景简述"}}
  ],
  "decision_examples": [
    {{"scenario": "面对什么情境", "reasoning": "博主的推理过程", "conclusion": "得出的结论或行动"}}
  ],
  "expression_samples": [
    "博主的原话片段，能体现其独特表达方式（1-2句）"
  ]
}}

只返回JSON对象，不要有其他内容。"""

PROFILE_SYNTHESIS_PROMPT = """你是一位资深的人物认知蒸馏专家。你的任务是从以下UP主的多个视频知识摘要中，\
提炼出一套可运行的「认知操作系统」——让AI助手能够以该UP主的身份，用他/她真实的思维框架和表达DNA，\
分析用户问题、给出判断。

**关键原则**：你捕捉的是 HOW they think（思维方式），不是 WHAT they said（具体观点的复读）。
每个心智模型必须给出 **3 条具体证据**（引用视频名 / 原话 / 场景），而不是"反复强调""经常提到"这种泛称。
每条决策启发式必须给出 **1 个具体案例**。
素材可能同时包含视频与书籍章节：书籍章节提供更系统的框架，视频提供表达方式、案例与语气细节。请综合两类来源，不要偏废其一。

UP主名称：{up_name}
视频总数：{video_count}
调研日期：{research_date}

各视频知识摘要：
{summaries}

请以严格的 JSON 格式返回完整画像。输出必须包含下列所有字段，不得省略任何字段，空字段请用 ""、[] 或 {{}} 占位：

{{
  "name": "UP主名称",
  "domain": ["擅长领域1", "擅长领域2", "..."],

  "signature_quote": "一句代表其核心理念的标志性金句（用于文首斜体引言，越简洁有力越好）",
  "core_philosophy": "2-3 句话概括这位博主的核心世界观（不是复读观点，而是提炼底层逻辑）",
  "self_intro": "用本人第一人称口吻的自我介绍（50字左右，体现核心身份认同）",

  "identity_who": "第一人称：'我是谁'——身份、职业、标签（2-3句）",
  "identity_origin": "第一人称：'我的起点'——出身、转折点、早期经历（2-3句）",
  "identity_now": "第一人称：'我现在在做什么'——当前主业、追求、困境（2-3句）",

  "mental_models": [
    {{
      "name": "心智模型名称（要有辨识度，如'社会筛子论'而非'社会学视角'）",
      "one_liner": "一句话讲清楚这个模型（不超过30字）",
      "evidence": [
        "证据1：引用具体视频名 / 原话 / 场景（必须具体可验证）",
        "证据2：...",
        "证据3：..."
      ],
      "application": "这个模型在什么问题上怎么用（2-3句，说清楚 how）",
      "limitation": "这个模型的盲区和失效场景（1-2句，坦诚局限）"
    }}
  ],

  "decision_heuristics": [
    {{
      "rule": "判断准则名称（如：'中位数原则'、'不可替代性检验'）",
      "scenario": "什么场景触发使用这条规则",
      "case": "一个具体案例（从视频中提炼）"
    }}
  ],

  "style": "整体表达风格 150-200 字：语气特点、思维习惯、互动方式、常用比喻等",

  "signature_phrases": ["标志性口头禅1", "常用句式2", "..."],

  "expression_dna": {{
    "sentence_style": "句式特征（长短句偏好、反问/设问/感叹的使用）",
    "vocabulary": "词汇偏好（高频词、方言、禁忌词）",
    "rhythm": "叙事节奏（铺垫→反转→金句→重复，或其他模式）",
    "humor": "幽默风格（夸张 / 自嘲 / 反差 / 双关 等）",
    "certainty": "确定性表达：高（'一定''绝对'）还是模糊（'或许''可能'）",
    "citation_habit": "引用习惯：引数据 / 引名人 / 引俗语 / 不引用",
    "debate_strategy": "辩论策略：借力打力 / 重新定义 / 身份降维 等"
  }},

  "values_pursued": [
    "我追求的（按重要性优先级排列）1：具体而非泛泛",
    "追求2",
    "..."
  ],
  "values_rejected": [
    "我明确拒绝的做法/观点1",
    "拒绝2"
  ],
  "inner_tensions": [
    "我自己也没想清楚的内在矛盾1（如：寒门代言人 vs 亿万富翁）",
    "张力2"
  ],

  "anti_patterns": ["在回答里绝对不会做的事1", "反模式2"],
  "honest_boundaries": [
    "知识局限声明1（如：我不是XX专业，只是基于经验）",
    "局限2"
  ],
  "knowledge_boundary": {{
    "strong": ["深入了解的领域1", "领域2"],
    "weak": ["较少涉及或主动回避的领域1", "领域2"]
  }},

  "timeline": [
    {{"time": "YYYY 或 YYYY.MM", "event": "关键事件", "impact": "对我思维的影响"}}
  ],

  "influenced_by": ["影响过我的人/书/经历1", "来源2"],
  "influenced_who": ["我影响了谁/什么群体1", "影响2"],

  "key_quotes": [
    "原话引用1（有传播力、体现风格的那种）",
    "原话2",
    "..."
  ],

  "typical_qa_pairs": [
    {{
      "question": "用户可能问的具体问题（覆盖该博主的核心领域）",
      "answer": "完全按该博主风格、运用具体心智模型给出的回答（200 字以上，要像真人说话而非总结陈述）"
    }}
  ],

  "research_date": "{research_date}"
}}

**硬性要求**：
1. 每个 mental_model 必须有 **至少 3 条 evidence**，且每条 evidence 必须是具体的（引用视频、原话或场景），而不是"反复提到""经常强调"这种泛称。
2. 每个 decision_heuristic 必须有 **具体 case**。
3. inner_tensions **至少 2 条**——拒绝脸谱化，真实的人都有矛盾。
4. typical_qa_pairs 输出 **3 个**，每个回答 200+ 字，语气完全模仿博主本人。
5. 所有字段都要填（缺数据时给合理推断，但不要编造无中生有的事实）。

只返回 JSON 对象，不要有其他内容、不要 markdown 代码块标记。"""
