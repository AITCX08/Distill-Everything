"""
knowledge_extractor.py 单元测试

覆盖：
- _safe_json_loads 的 5 轮修复策略 + 边界输入
- VideoKnowledge / BloggerProfile 向后兼容（整字段缺失 / 部分子 dict / 未知字段）
- save / load roundtrip（完整 schema 保真）
- check_knowledge_integrity 各失败分支（含 UnicodeDecodeError）
- extract_from_video / merge_knowledge 的 LLM → dataclass 字段映射保真
- _fallback_profile 空输入 + top-N 截断边界
"""

import json
import re
import sys
from dataclasses import asdict, fields
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.model.knowledge_extractor import (
    BloggerProfile,
    KnowledgeExtractor,
    VideoKnowledge,
    _extract_json_payload,
    _safe_json_loads,
    check_knowledge_integrity,
    load_blogger_profile,
    load_video_knowledge,
    save_blogger_profile,
    save_video_knowledge,
)


# ===================================================================
# _safe_json_loads — 5 轮修复（parametrize 压缩）
# ===================================================================

class TestSafeJsonLoadsRounds:
    """按轮次分组的参数化用例。每条用例注明依赖哪一轮才能恢复。"""

    @pytest.mark.parametrize(
        "src,expected,round_tag",
        [
            # Round 1：直接解析
            ('{"a": 1, "b": "x"}', {"a": 1, "b": "x"}, "r1-flat"),
            ('{"outer": {"inner": [1, 2, 3]}}', {"outer": {"inner": [1, 2, 3]}}, "r1-nested"),
            ("{}", {}, "r1-empty"),
            # Round 2：控制字符 + 字符串内裸换行
            ('{"a": "hello\nworld"}', {"a": "hello\nworld"}, "r2-newline"),
            ('{"a": "col1\tcol2"}', {"a": "col1\tcol2"}, "r2-tab"),
            ('{"a":\x08 1}', {"a": 1}, "r2-ctrl-char"),
            # Round 3：尾随逗号
            ('{"a": 1,}', {"a": 1}, "r3-obj-comma"),
            ('{"a": [1, 2, 3,]}', {"a": [1, 2, 3]}, "r3-arr-comma"),
            ('{"a": [1, 2,], "b": {"c": 3,},}', {"a": [1, 2], "b": {"c": 3}}, "r3-mixed"),
            # Round 4：... 占位符
            ('{"a": ..., "b": "x"}', {"a": "", "b": "x"}, "r4-ellipsis-value"),
            ('{"items": [1, 2, ...]}', {"items": [1, 2]}, "r4-ellipsis-tail"),
            ('{"items": [...]}', {"items": []}, "r4-ellipsis-only"),
            # Round 5：尾部截断到最后一个 } 或 ]
            ('{"a": 1, "b": "ok"}extra garbage', {"a": 1, "b": "ok"}, "r5-extra-garbage"),
            # 「完整对象 + 残缺第二对象」——证明 R5 相对前 4 轮的不可替代性
            ('{"a": 1}{"b":', {"a": 1}, "r5-partial-second-obj"),
            # 数组路径：尾部垃圾 + array 闭合
            ('[1, 2, 3]extra garbage', [1, 2, 3], "r5-array-garbage"),
            # 数组内对象的顶层 array + array-tail 修复
            ('[{"a": 1}, {"b": 2}]trailing', [{"a": 1}, {"b": 2}], "r5-array-of-objects"),
            # ===== 顶层数组的前 4 轮容错也要覆盖 =====
            # R1：合法 array
            ('[{"title": "t", "tags": ["x"]}]', [{"title": "t", "tags": ["x"]}], "r1-array"),
            # R2：array 内字符串含裸换行（最贴近真实 bug 场景）
            ('[{"title": "T", "content": "line1\nline2"}]',
             [{"title": "T", "content": "line1\nline2"}], "r2-array-inner-newline"),
            # R3：array 尾随逗号
            ('[1, 2, 3,]', [1, 2, 3], "r3-array-trailing-comma"),
            # 混合：R3 去逗号 + R2 裸换行
            ('{"a": "line1\nline2", "b": 2,}', {"a": "line1\nline2", "b": 2}, "compound-r2+r3"),
            # 混合：R3 + R4
            ('{"a": ..., "b": [1, 2,],}', {"a": "", "b": [1, 2]}, "compound-r3+r4"),
        ],
    )
    def test_repair_rounds(self, src, expected, round_tag):
        assert _safe_json_loads(src) == expected, f"repair failed at [{round_tag}]"


class TestSafeJsonLoadsFailure:
    def test_no_braces_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _safe_json_loads("not json at all")

    def test_only_opening_brace_raises(self):
        # 没有任何 } → rfind 返回 -1 → 跳过第 5 轮 → 抛原始错
        with pytest.raises(json.JSONDecodeError):
            _safe_json_loads('{"a": "unterminated')

    def test_empty_string_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _safe_json_loads("")

    def test_none_input_raises(self):
        # 当前实现不防 None，会在 json.loads 时抛 TypeError —— 是合约的一部分
        with pytest.raises(TypeError):
            _safe_json_loads(None)


class TestSafeJsonLoadsUnicodeAndLarge:
    def test_chinese_content(self):
        assert _safe_json_loads('{"观点": "我是谁"}') == {"观点": "我是谁"}

    def test_emoji_content(self):
        assert _safe_json_loads('{"msg": "hi 👋 world"}') == {"msg": "hi 👋 world"}

    def test_fullwidth_punctuation(self):
        src = '{"title": "测试，分号；引号"}'
        assert _safe_json_loads(src) == {"title": "测试，分号；引号"}

    def test_large_input_does_not_crash(self):
        # 8 万字符的合法 JSON，验证 regex 修复不产生指数级退化
        big_str = "x" * 80_000
        src = json.dumps({"data": big_str})
        assert _safe_json_loads(src) == {"data": big_str}


# ===================================================================
# VideoKnowledge save / load roundtrip + 兼容性
# ===================================================================

class TestVideoKnowledgeRoundtrip:
    def test_source_id_and_legacy_bvid_stay_in_sync(self):
        legacy = VideoKnowledge(bvid="BV1legacy")
        modern = VideoKnowledge(source_id="douyin_123")

        assert legacy.source_id == "BV1legacy"
        assert modern.bvid == "douyin_123"

    def test_save_then_load_preserves_full_schema(self, tmp_path):
        vk = VideoKnowledge(
            bvid="BV1abc",
            title="测试视频",
            summary="摘要",
            core_views=["观点1", "观点2"],
            key_concepts=["概念A"],
            topics=["话题1"],
            arguments=[{"claim": "A", "evidence": "B"}],
            mental_model_hints=[{"hint": "h", "context": "c"}],
            decision_examples=[{"scenario": "s", "reasoning": "r", "conclusion": "c"}],
            expression_samples=["原话1"],
        )
        saved = save_video_knowledge(vk, tmp_path / "out")
        loaded = load_video_knowledge(saved)
        # 完整 schema 保真：asdict 相等即保证字段逐一无丢失
        assert asdict(loaded) == asdict(vk)

    def test_load_old_json_without_nuwa_fields(self, tmp_path):
        # 老版 JSON 只有前 5 个字段，缺 nuwa 新增的 3 个
        old_json = {
            "bvid": "BV1xyz",
            "title": "老视频",
            "summary": "概要",
            "core_views": ["v1"],
            "key_concepts": ["k1"],
            "topics": ["t1"],
            "arguments": [],
        }
        path = tmp_path / "old.json"
        path.write_text(json.dumps(old_json), encoding="utf-8")
        loaded = load_video_knowledge(path)
        assert loaded.bvid == "BV1xyz"
        assert loaded.mental_model_hints == []
        assert loaded.decision_examples == []
        assert loaded.expression_samples == []

    def test_load_strips_unknown_fields(self, tmp_path):
        data = {
            "bvid": "BV1",
            "title": "t",
            "future_field_1": "should be stripped",
            "future_field_2": 999,
        }
        path = tmp_path / "future.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        loaded = load_video_knowledge(path)
        assert loaded.bvid == "BV1"
        assert not hasattr(loaded, "future_field_1")


# ===================================================================
# BloggerProfile save / load roundtrip + 兼容性
# ===================================================================

def _default_expression_dna() -> dict:
    """从 BloggerProfile 动态取 expression_dna 的 factory 默认值，字段增删时测试自动跟随。"""
    for f in fields(BloggerProfile):
        if f.name == "expression_dna":
            return f.default_factory()
    raise AssertionError("expression_dna field not found")


class TestBloggerProfileRoundtrip:
    def test_save_then_load_preserves_full_schema(self, tmp_path):
        """完整字段保真 —— blogger_profile.json 是全项目最敏感 schema 契约，必须逐字段无丢失。"""
        profile = BloggerProfile(
            name="测试博主",
            uid=12345,
            domain=["AI", "教育"],
            self_intro="我是测试博主",
            signature_quote="金句",
            core_philosophy="核心理念",
            identity_who="我是...",
            identity_origin="我的起点",
            identity_now="我现在",
            mental_models=[
                {"name": "模型A", "one_liner": "一句话", "evidence": ["e1", "e2", "e3"],
                 "application": "用法", "limitation": "局限"}
            ],
            decision_heuristics=[{"rule": "准则1", "scenario": "场景", "case": "案例"}],
            style="整体风格",
            signature_phrases=["口头禅1"],
            expression_dna={
                "sentence_style": "长句",
                "vocabulary": "高频词",
                "rhythm": "节奏",
                "humor": "自嘲",
                "certainty": "高",
                "citation_habit": "引数据",
                "debate_strategy": "重新定义",
            },
            values_pursued=["追求1"],
            values_rejected=["拒绝1"],
            inner_tensions=["张力1", "张力2"],
            anti_patterns=["反模式1"],
            honest_boundaries=["局限1"],
            knowledge_boundary={"strong": ["领域A"], "weak": ["领域B"]},
            timeline=[{"time": "2020", "event": "事件", "impact": "影响"}],
            influenced_by=["来源1"],
            influenced_who=["群体1"],
            typical_qa_pairs=[{"question": "Q", "answer": "A"}],
            video_sources=[{"bvid": "BV1", "title": "视频"}],
            key_quotes=["原话1"],
            research_date="2026-04-21",
            core_views=["legacy_v1"],
            values=["legacy_val1"],
        )
        out_path = tmp_path / "profile.json"
        save_blogger_profile(profile, out_path)
        loaded = load_blogger_profile(out_path)
        # 完整 schema 逐字段保真
        assert asdict(loaded) == asdict(profile)

    def test_save_creates_parent_dirs(self, tmp_path):
        """对齐 tests/test_auth.py::test_creates_parent_dirs 的风格。"""
        profile = BloggerProfile(name="嵌套测试", uid=1)
        out_path = tmp_path / "nested" / "deep" / "profile.json"
        save_blogger_profile(profile, out_path)
        assert out_path.exists()
        assert out_path.parent.is_dir()

    def test_load_legacy_profile_missing_nuwa_fields(self, tmp_path):
        # 最早版本只有 name / core_views / values / style / domain
        legacy = {
            "name": "老画像",
            "uid": 1,
            "domain": ["d1"],
            "style": "风格描述",
            "core_views": ["v1", "v2"],
            "values": ["val1"],
        }
        path = tmp_path / "legacy.json"
        path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        loaded = load_blogger_profile(path)
        # 老字段读到
        assert loaded.name == "老画像"
        assert loaded.core_views == ["v1", "v2"]
        assert loaded.values == ["val1"]
        # nuwa 新字段由 factory 默认填充
        assert loaded.mental_models == []
        assert loaded.decision_heuristics == []
        assert loaded.inner_tensions == []
        assert loaded.knowledge_boundary == {"strong": [], "weak": []}
        # 动态取默认，字段增删时测试自动跟随
        assert loaded.expression_dna == _default_expression_dna()

    def test_partial_expression_dna_is_not_merged(self, tmp_path):
        """陷阱提醒：老 JSON 含不完整 expression_dna 时，loader 不会 merge 其它子键。
        这个行为由 dataclass 的 default_factory 语义决定——只在字段整体缺失时才触发。"""
        legacy = {
            "name": "部分 dna",
            "expression_dna": {"sentence_style": "长句"},  # 只有 1 个子键，缺 6 个
        }
        path = tmp_path / "partial.json"
        path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        loaded = load_blogger_profile(path)
        assert loaded.expression_dna == {"sentence_style": "长句"}
        assert "vocabulary" not in loaded.expression_dna  # 不会自动补齐
        assert len(loaded.expression_dna) == 1

    def test_load_strips_unknown_fields(self, tmp_path):
        data = {
            "name": "future",
            "deprecated_field_v0": "legacy value",
            "some_new_experimental_field": [1, 2, 3],
        }
        path = tmp_path / "mix.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        loaded = load_blogger_profile(path)
        assert loaded.name == "future"
        assert not hasattr(loaded, "deprecated_field_v0")


# ===================================================================
# check_knowledge_integrity
# ===================================================================

class TestCheckKnowledgeIntegrity:
    def test_missing_file(self, tmp_path):
        ok, reason = check_knowledge_integrity(tmp_path / "does_not_exist.json")
        assert ok is False
        assert "不存在" in reason

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text("", encoding="utf-8")
        ok, reason = check_knowledge_integrity(p)
        assert ok is False
        assert "空" in reason

    def test_invalid_json(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        ok, reason = check_knowledge_integrity(p)
        assert ok is False
        assert "JSON" in reason

    def test_invalid_utf8_bytes(self, tmp_path):
        """实现里显式捕获 UnicodeDecodeError，覆盖这条分支。"""
        p = tmp_path / "bad_utf8.json"
        p.write_bytes(b"\xff\xfe not valid utf8 at all \x80\x81")
        ok, reason = check_knowledge_integrity(p)
        assert ok is False
        assert "JSON" in reason  # 消息格式是 "JSON 解析失败: ..."

    def test_missing_summary(self, tmp_path):
        p = tmp_path / "no_summary.json"
        p.write_text(json.dumps({"summary": "", "core_views": ["v"]}), encoding="utf-8")
        ok, reason = check_knowledge_integrity(p)
        assert ok is False
        assert "summary" in reason

    def test_empty_core_views(self, tmp_path):
        p = tmp_path / "no_views.json"
        p.write_text(json.dumps({"summary": "s", "core_views": []}), encoding="utf-8")
        ok, reason = check_knowledge_integrity(p)
        assert ok is False
        assert "core_views" in reason

    def test_valid(self, tmp_path):
        p = tmp_path / "ok.json"
        p.write_text(
            json.dumps({"summary": "s", "core_views": ["v1"]}),
            encoding="utf-8",
        )
        ok, reason = check_knowledge_integrity(p)
        assert ok is True
        assert reason == "ok"


# ===================================================================
# KnowledgeExtractor — LLM → dataclass 字段映射
# ===================================================================

class TestExtractFromVideo:
    """mock LLM 返回完整 JSON，验证 extract_from_video 的手动字段映射无丢失。
    根级硬规则 #9 的核心陷阱——加字段忘了同步赋值，单测必须抓到。"""

    def test_all_fields_mapped_from_llm_response(self):
        llm_response = """```json
{
  "summary": "视频摘要",
  "core_views": ["观点1", "观点2"],
  "key_concepts": ["概念A", "概念B"],
  "topics": ["话题1"],
  "arguments": [{"claim": "C", "evidence": "E"}],
  "mental_model_hints": [{"hint": "h", "context": "c"}],
  "decision_examples": [{"scenario": "s", "reasoning": "r", "conclusion": "c"}],
  "expression_samples": ["原话片段"]
}
```"""
        mock_client = MagicMock()
        mock_client.chat.return_value = llm_response

        extractor = KnowledgeExtractor(llm_client=mock_client)
        cleaned_doc = {"bvid": "BV1", "title": "T", "full_text": "text..."}
        result = extractor.extract_from_video(cleaned_doc)

        assert result.bvid == "BV1"
        assert result.title == "T"
        assert result.summary == "视频摘要"
        assert result.core_views == ["观点1", "观点2"]
        assert result.key_concepts == ["概念A", "概念B"]
        assert result.topics == ["话题1"]
        assert result.arguments == [{"claim": "C", "evidence": "E"}]
        assert result.mental_model_hints == [{"hint": "h", "context": "c"}]
        assert result.decision_examples == [
            {"scenario": "s", "reasoning": "r", "conclusion": "c"}
        ]
        assert result.expression_samples == ["原话片段"]

    def test_llm_failure_returns_placeholder(self):
        mock_client = MagicMock()
        mock_client.chat.side_effect = RuntimeError("network error")

        extractor = KnowledgeExtractor(llm_client=mock_client)
        result = extractor.extract_from_video({"bvid": "BV_X", "title": "T", "full_text": ""})

        # LLM 失败时返回占位（不是抛异常中断批处理——根级硬规则 #8）
        assert result.bvid == "BV_X"
        assert result.title == "T"
        assert result.summary == ""
        assert result.core_views == []


class TestMergeKnowledge:
    """验证 merge_knowledge 的 LLM JSON → BloggerProfile 的完整字段映射。"""

    def _build_full_llm_response(self) -> str:
        """构造完整 schema 的 LLM 返回。字段覆盖 PROFILE_SYNTHESIS_PROMPT 中列出的全部。"""
        data = {
            "name": "完整画像",
            "domain": ["AI"],
            "signature_quote": "金句",
            "core_philosophy": "核心",
            "self_intro": "介绍",
            "identity_who": "who",
            "identity_origin": "origin",
            "identity_now": "now",
            "mental_models": [{"name": "m", "one_liner": "o", "evidence": ["e1", "e2", "e3"],
                               "application": "a", "limitation": "l"}],
            "decision_heuristics": [{"rule": "r", "scenario": "s", "case": "c"}],
            "style": "style",
            "signature_phrases": ["p1"],
            "expression_dna": {
                "sentence_style": "s1", "vocabulary": "v1", "rhythm": "r1",
                "humor": "h1", "certainty": "c1", "citation_habit": "ch1",
                "debate_strategy": "ds1",
            },
            "values_pursued": ["vp1"],
            "values_rejected": ["vr1"],
            "inner_tensions": ["t1", "t2"],
            "anti_patterns": ["ap1"],
            "honest_boundaries": ["hb1"],
            "knowledge_boundary": {"strong": ["S"], "weak": ["W"]},
            "timeline": [{"time": "2020", "event": "e", "impact": "i"}],
            "influenced_by": ["ib1"],
            "influenced_who": ["iw1"],
            "typical_qa_pairs": [{"question": "Q", "answer": "A"}],
            "key_quotes": ["quote1"],
            "research_date": "2026-04-21",
            "core_views": ["cv1"],
            "values": ["val1"],
        }
        return "```json\n" + json.dumps(data, ensure_ascii=False) + "\n```"

    def test_all_fields_mapped_to_profile(self):
        mock_client = MagicMock()
        mock_client.chat.return_value = self._build_full_llm_response()

        extractor = KnowledgeExtractor(llm_client=mock_client)
        knowledge = [VideoKnowledge(bvid="BV1", title="视频1")]
        profile = extractor.merge_knowledge(knowledge, up_name="UP", up_uid=99)

        # 基础
        assert profile.name == "完整画像"
        assert profile.uid == 99  # uid 从参数来，不从 LLM
        assert profile.domain == ["AI"]
        # 身份卡三段
        assert profile.identity_who == "who"
        assert profile.identity_origin == "origin"
        assert profile.identity_now == "now"
        # 心智模型（schema 敏感）
        assert profile.mental_models[0]["name"] == "m"
        assert len(profile.mental_models[0]["evidence"]) == 3
        # 决策启发式
        assert profile.decision_heuristics[0]["rule"] == "r"
        # 表达 DNA 7 维度
        assert set(profile.expression_dna.keys()) == set(_default_expression_dna().keys())
        assert profile.expression_dna["sentence_style"] == "s1"
        # 价值观三层
        assert profile.values_pursued == ["vp1"]
        assert profile.values_rejected == ["vr1"]
        assert len(profile.inner_tensions) >= 2
        # 边界
        assert profile.knowledge_boundary == {"strong": ["S"], "weak": ["W"]}
        # 时间线
        assert profile.timeline[0]["event"] == "e"
        # 示例与溯源
        assert profile.typical_qa_pairs[0]["question"] == "Q"
        # video_sources 由 merge_knowledge 自己从 knowledge 列表构造，不是 LLM 返回
        assert profile.video_sources == [{"bvid": "BV1", "title": "视频1"}]
        # research_date 格式合规
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", profile.research_date)
        # 旧兼容
        assert profile.core_views == ["cv1"]
        assert profile.values == ["val1"]


# ===================================================================
# _fallback_profile — LLM 失败路径
# ===================================================================

class TestFallbackProfile:
    def test_empty_knowledge_list(self):
        extractor = KnowledgeExtractor(llm_client=MagicMock())
        profile = extractor._fallback_profile([], up_name="测试", up_uid=999)
        assert isinstance(profile, BloggerProfile)
        assert profile.name == "测试"
        assert profile.uid == 999
        assert profile.domain == []
        assert profile.core_views == []
        assert profile.video_sources == []

    def test_aggregates_from_knowledge(self):
        extractor = KnowledgeExtractor(llm_client=MagicMock())
        knowledge = [
            VideoKnowledge(
                bvid="BV1", title="视频1",
                core_views=["v1", "v2"], key_concepts=["c1"], topics=["t1", "t1"],
            ),
            VideoKnowledge(
                bvid="BV2", title="视频2",
                core_views=["v3"], key_concepts=["c1", "c2"], topics=["t1", "t2"],
            ),
        ]
        profile = extractor._fallback_profile(knowledge, up_name="博主", up_uid=1)
        assert profile.name == "博主"
        assert "t1" in profile.domain
        assert len(profile.video_sources) == 2
        assert profile.video_sources[0]["bvid"] == "BV1"
        assert len(profile.core_views) <= 10
        assert "c1" in profile.knowledge_boundary["strong"]

    def test_top_n_truncation(self):
        """11+ core_views / 6+ topics / 11+ concepts 时验证 top-N 截断边界。"""
        extractor = KnowledgeExtractor(llm_client=MagicMock())
        # 构造：12 个不同 core_views、6 个 topics（t0 最高频）、11 个 concepts（c0 最高频）
        knowledge = [
            VideoKnowledge(
                bvid=f"BV{i}", title=f"视频{i}",
                core_views=[f"v{i}"],
                key_concepts=[f"c{min(i, 10)}"] + ["c0"] * (1 if i < 5 else 0),  # c0 出现多次
                topics=[f"t{min(i, 5)}"] + ["t0"] * (1 if i < 3 else 0),  # t0 出现多次
            )
            for i in range(12)
        ]
        profile = extractor._fallback_profile(knowledge, up_name="多视频", up_uid=1)

        # core_views 总共 12 个，应截断到 top 10
        assert len(profile.core_views) == 10
        # domain (top topics) 取前 5
        assert len(profile.domain) <= 5
        # knowledge_boundary.strong (top concepts) 取前 10
        assert len(profile.knowledge_boundary["strong"]) <= 10
        # 高频词应排在前面：t0 出现最多次，必在 domain 里
        assert "t0" in profile.domain
        assert "c0" in profile.knowledge_boundary["strong"]


# ===================================================================
# _extract_json_payload — 推理模型 / 第三方代理输出鲁棒性
# ===================================================================


class TestExtractJsonPayload:
    """覆盖 R1/Reasoner 模型常见输出抖动，防止贪婪正则吞入额外内容导致 _safe_json_loads 失败。"""

    def test_pure_array(self):
        """纯净 JSON 数组直接返回。"""
        content = '[{"title": "a", "tags": []}]'
        assert _extract_json_payload(content, prefer_array=True) == content

    def test_pure_object(self):
        """纯净 JSON 对象直接返回。"""
        content = '{"summary": "x"}'
        assert _extract_json_payload(content, prefer_array=False) == content

    def test_strip_think_tag_then_extract_fence(self):
        """R1 风格输出：<think> 推理 + ```json 代码块 + 后缀文字。"""
        content = (
            '<think>\n让我先分析一下，比如 [示例1, 示例2]，然后输出 JSON。\n</think>\n\n'
            '好的：\n```json\n[{"title": "x", "tags": ["a"]}]\n```\n希望对你有用。'
        )
        result = _extract_json_payload(content, prefer_array=True)
        assert json.loads(result) == [{"title": "x", "tags": ["a"]}]

    def test_no_fence_with_inner_brackets(self):
        """无代码块 + 思考标签里有举例 [...] + 真正 JSON 后又有 [...]。"""
        content = (
            '<thinking>我会输出 [主题A, 主题B] 这样的结构</thinking>\n'
            '[{"title": "A", "tags": []}]\n'
            '补充：[这只是说明文字]'
        )
        result = _extract_json_payload(content, prefer_array=True)
        # 不能贪婪到吞掉后面的 "[这只是说明文字]"
        assert json.loads(result) == [{"title": "A", "tags": []}]

    def test_object_with_nested_arrays(self):
        """对象中嵌套数组时不应被错误地切到第一个 ]。"""
        content = '{"core_views": ["v1", "v2"], "topics": ["t1"]}'
        result = _extract_json_payload(content, prefer_array=False)
        assert json.loads(result) == {"core_views": ["v1", "v2"], "topics": ["t1"]}

    def test_fence_without_json_marker(self):
        """``` 代码块（不带 json 标记）也要识别。"""
        content = '```\n{"x": 1}\n```'
        result = _extract_json_payload(content, prefer_array=False)
        assert json.loads(result) == {"x": 1}

    def test_empty_returns_none(self):
        assert _extract_json_payload("", prefer_array=True) is None
        assert _extract_json_payload(None, prefer_array=True) is None

    def test_no_valid_json_returns_none(self):
        """完全没有 JSON 时返回 None，让调用方走降级。"""
        content = "我无法完成这个任务，因为输入太长了。"
        assert _extract_json_payload(content, prefer_array=True) is None
        assert _extract_json_payload(content, prefer_array=False) is None

    def test_strings_with_brackets_inside(self):
        """JSON 字符串内的 } 不应误触发括号配对结束。"""
        content = '{"text": "包含 } 和 { 的内容", "list": [1, 2]}'
        result = _extract_json_payload(content, prefer_array=False)
        assert json.loads(result) == {"text": "包含 } 和 { 的内容", "list": [1, 2]}


# ===================================================================
# _safe_json_loads — 第 5/6 轮新增修复（裸字面量 + 缺值场景）
# ===================================================================


class TestSafeJsonLoadsExtended:
    """长上下文 / 直播闲聊场景下 LLM 常见的"Expecting value"抖动模式。"""

    def test_python_none_literal(self):
        """LLM 输出 Python 风格 None → 应转为 null。"""
        result = _safe_json_loads('{"a": None, "b": "x"}')
        assert result == {"a": None, "b": "x"}

    def test_nan_infinity_undefined(self):
        """JS/数值字面量泄漏 → 统一转为 null。"""
        result = _safe_json_loads('{"a": NaN, "b": Infinity, "c": undefined}')
        assert result == {"a": None, "b": None, "c": None}

    def test_python_true_false(self):
        """Python 风格 True/False → JSON true/false。"""
        result = _safe_json_loads('{"x": True, "y": False}')
        assert result == {"x": True, "y": False}

    def test_missing_value_before_comma(self):
        '''"key": , → 补 null。这是 BV1XEZMBsE4U 直播回放报错的核心模式。'''
        result = _safe_json_loads('{"a": "x", "core_views": ,"b": []}')
        assert result == {"a": "x", "core_views": None, "b": []}

    def test_missing_value_before_close(self):
        '''"key":} 或 "key":] → 补 null。'''
        result = _safe_json_loads('{"a": "x", "trailing":}')
        assert result == {"a": "x", "trailing": None}

    def test_string_content_with_keywords_intact(self):
        """字符串值内的 None/True/NaN 字样不能被误替换。"""
        result = _safe_json_loads('{"text": "他说了 None，又提到 True 和 NaN"}')
        assert result == {"text": "他说了 None，又提到 True 和 NaN"}
