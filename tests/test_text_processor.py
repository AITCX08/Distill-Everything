"""
text_processor.py 单元测试

重点覆盖 segment_by_topic 的 LLM JSON 容错（bug 回归锁定：
"Expecting ',' delimiter: line 25 column 49 (char 3277)"）。
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.clean.text_processor import TextProcessor


class TestSegmentByTopicJsonResilience:
    """验证 segment_by_topic 不再因 LLM 输出轻微格式抖动而崩到降级。"""

    def _make_processor(self, llm_return: str) -> TextProcessor:
        mock = MagicMock()
        mock.chat.return_value = llm_return
        return TextProcessor(llm_client=mock)

    def test_recovers_from_inner_bare_newline(self):
        """重现原 bug：LLM 返回的 content 字段内含裸换行 → 裸 json.loads 抛
        "Expecting ',' delimiter"，复用 _safe_json_loads 的 Round 2 应能修复。"""
        llm_return = (
            '[\n'
            '  {"title": "开篇", "content": "普通人穷孩子老实人\n没必要自卑",'
            ' "tags": ["励志"]}\n'
            ']'
        )
        proc = self._make_processor(llm_return)
        topics = proc.segment_by_topic(full_text="原文片段", video_title="E647")
        assert isinstance(topics, list)
        assert len(topics) == 1
        assert topics[0]["title"] == "开篇"
        # 裸换行在修复后被转义成 \n，字符串语义保留
        assert "普通人" in topics[0]["content"]
        assert "没必要自卑" in topics[0]["content"]

    def test_recovers_from_trailing_comma(self):
        """Round 3：array 尾随逗号。"""
        llm_return = '[{"title": "A", "content": "c", "tags": ["t1",]},]'
        proc = self._make_processor(llm_return)
        topics = proc.segment_by_topic(full_text="x", video_title="T")
        assert isinstance(topics, list)
        assert topics[0]["title"] == "A"

    def test_recovers_from_extra_trailing_chars(self):
        """Round 5：array 闭合后 LLM 又多输出一段自然语言。"""
        llm_return = '[{"title": "A", "content": "c", "tags": []}]\n以上是我的分段。'
        proc = self._make_processor(llm_return)
        topics = proc.segment_by_topic(full_text="x", video_title="T")
        assert isinstance(topics, list)
        assert len(topics) == 1

    def test_valid_json_array_passes_through(self):
        """Round 1 直接解析路径不应回归。"""
        llm_return = '[{"title": "T1", "content": "c1", "tags": ["x", "y"]},' \
                     ' {"title": "T2", "content": "c2", "tags": []}]'
        proc = self._make_processor(llm_return)
        topics = proc.segment_by_topic(full_text="x", video_title="T")
        assert len(topics) == 2
        assert topics[1]["title"] == "T2"

    def test_fallback_on_non_array_return(self):
        """LLM 返回对象而非数组 → 不崩，走降级分段。"""
        llm_return = '{"title": "just an object, not array", "content": "c"}'
        proc = self._make_processor(llm_return)
        # 正则 r"\[.*\]" 匹配不到 → 走降级
        topics = proc.segment_by_topic(full_text="原文兜底", video_title="T")
        assert isinstance(topics, list)
        assert len(topics) == 1
        assert topics[0]["title"] == "全文"
        assert topics[0]["content"] == "原文兜底"

    def test_fallback_on_completely_broken_json(self):
        """5 轮修复都救不回来 → 走降级，不抛异常。"""
        llm_return = "LLM 今天不配合，给你一堆废话没有 JSON"
        proc = self._make_processor(llm_return)
        topics = proc.segment_by_topic(full_text="全文", video_title="T")
        assert topics == [{"title": "全文", "content": "全文", "tags": []}]

    def test_no_llm_uses_simple_split(self):
        """无 llm_client 时按段落简单分段，不走 LLM 路径。"""
        proc = TextProcessor(llm_client=None)
        topics = proc.segment_by_topic(
            full_text="段落一\n段落二\n段落三",
            video_title="T",
        )
        assert len(topics) == 3
        assert topics[0] == {"title": "段落1", "content": "段落一", "tags": []}


class TestRemoveFillerWords:
    """顺带守一下规则清洗 —— 这是阶段 3 的另一半基础能力。"""

    def test_removes_common_fillers(self):
        proc = TextProcessor()
        result = proc.remove_filler_words("那个我觉得这个想法嗯挺好的")
        # "那个" / "这个" / "嗯" 均在 FILLER_WORDS_PATTERN 里
        assert "那个" not in result
        assert "嗯" not in result
        assert "我觉得" in result
        assert "挺好的" in result

    def test_compresses_repeated_punct(self):
        proc = TextProcessor()
        assert proc.remove_filler_words("好！！！对。。。") == "好！对。"

    def test_empty_text(self):
        proc = TextProcessor()
        assert proc.remove_filler_words("") == ""
