from src.asr.funasr_engine import (
    _spk_label, _parse_result_segments, FunASREngine,
)


def test_spk_label():
    assert _spk_label(0) == "说话人 1"
    assert _spk_label(2) == "说话人 3"
    assert _spk_label(None) == ""
    assert _spk_label("x") == ""        # 非法值不崩，归空


def test_parse_result_segments_reads_spk():
    result = [{
        "text": "大家好我来开会",
        "sentence_info": [
            {"text": "大家好", "start": 0, "end": 1500, "spk": 0},
            {"text": "我来开会", "start": 1500, "end": 3000, "spk": 1},
        ],
    }]
    full_text, segments = _parse_result_segments(result, bvid="")
    assert full_text == "大家好我来开会"
    assert len(segments) == 2
    assert segments[0].speaker == "说话人 1"
    assert segments[1].start == 1.5          # ms→s: 1500ms → 1.5s
    assert segments[1].speaker == "说话人 2"


def test_parse_result_segments_no_spk_keeps_empty_speaker():
    result = [{
        "text": "一句话",
        "sentence_info": [{"text": "一句话", "start": 0, "end": 1000}],
    }]
    _, segments = _parse_result_segments(result, bvid="BV1")
    assert segments[0].speaker == ""
    assert segments[0].id == "BV1_seg_0000"


def test_parse_result_segments_fallback_reads_spk():
    # 无 sentence_info 的整块降级路径也应读 item 级 spk（Minor 修复）
    result = [{"text": "整块文本", "timestamp": [[0, 2000]], "spk": 1}]
    _, segments = _parse_result_segments(result)
    assert len(segments) == 1
    assert segments[0].speaker == "说话人 2"
    assert segments[0].start == 0.0
    assert segments[0].end == 2.0


def test_parse_result_segments_fallback_no_spk_empty():
    # 降级路径无 spk 时 speaker 仍为空（向后兼容）
    result = [{"text": "整块文本", "timestamp": [[0, 1000]]}]
    _, segments = _parse_result_segments(result)
    assert segments[0].speaker == ""


def test_engine_passes_spk_model_to_automodel(monkeypatch):
    captured = {}

    class FakeAutoModel:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    import funasr
    monkeypatch.setattr(funasr, "AutoModel", FakeAutoModel)
    FunASREngine(device="cpu", spk_model="cam++")
    assert captured.get("spk_model") == "cam++"


def test_engine_default_no_spk_model(monkeypatch):
    captured = {}

    class FakeAutoModel:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    import funasr
    monkeypatch.setattr(funasr, "AutoModel", FakeAutoModel)
    FunASREngine(device="cpu")
    assert "spk_model" not in captured     # 默认不传，现有 asr 行为不变
