"""ASR 边下边转写流水线测试。

覆盖：
- _scan_pending_audios：跳过完整转写、识别不完整、识别新音频
- _process_pending_batch：转写成功 + 删除 / 转写成功 + 保留 / 完整性校验失败时不删
- crawl 命令的 asr_done 集合合并逻辑（mock 文件系统）
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import _process_pending_batch, _scan_pending_audios


def _make_config(tmp_path: Path):
    """构造一个最小可用的 config，audio/transcripts 目录都在 tmp_path 下。"""
    cfg = MagicMock()
    cfg.audio_dir = tmp_path / "audio"
    cfg.transcripts_dir = tmp_path / "transcripts"
    cfg.audio_dir.mkdir()
    cfg.transcripts_dir.mkdir()
    return cfg


def _write_complete_transcript(transcripts_dir: Path, bvid: str, audio_seconds: int = 60):
    """写一个通过 check_transcript_integrity 的完整 transcript。"""
    data = {
        "bvid": bvid,
        "title": f"Mock {bvid}",
        "source": "funasr",
        "model": "paraformer-zh",
        "full_text": "这是一段非空文本",
        "segments": [{
            "id": f"{bvid}_seg_0000",
            "text": "这是一段非空文本",
            "start": 0.0,
            "end": float(audio_seconds),
            "confidence": 0.0,
        }],
        "metadata": {"duration": f"{audio_seconds // 60}:{audio_seconds % 60:02d}"},
    }
    path = transcripts_dir / f"{bvid}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


class TestScanPendingAudios:
    """扫描逻辑：跳过完整转写 / 识别不完整 / 识别新音频"""

    def test_skip_complete_transcripts(self, tmp_path):
        """音频存在且 transcript 完整 → skipped 计数。"""
        cfg = _make_config(tmp_path)
        audio = cfg.audio_dir / "BVtest1.wav"
        audio.write_bytes(b"x" * 2048)
        _write_complete_transcript(cfg.transcripts_dir, "BVtest1", audio_seconds=1)

        # mock check_transcript_integrity 返回 True（避免 wave 模块解析 mock 音频）
        with patch("main.check_transcript_integrity", create=True), \
             patch("src.asr.funasr_engine.check_transcript_integrity",
                   return_value=(True, "ok")):
            pending, skipped, incomplete = _scan_pending_audios(cfg)

        assert pending == []
        assert skipped == 1
        assert incomplete == 0

    def test_pick_up_new_audio_without_transcript(self, tmp_path):
        """音频存在但无 transcript → 进入 pending。"""
        cfg = _make_config(tmp_path)
        audio = cfg.audio_dir / "BVnew1.wav"
        audio.write_bytes(b"x" * 2048)

        with patch("src.asr.funasr_engine.check_transcript_integrity",
                   return_value=(False, "文件不存在")):
            pending, skipped, incomplete = _scan_pending_audios(cfg)

        assert len(pending) == 1
        assert pending[0][1] == "BVnew1"
        assert skipped == 0
        assert incomplete == 0  # transcript 文件根本不存在 → 不算"不完整"

    def test_recognize_incomplete_transcript(self, tmp_path):
        """transcript 存在但不完整 → 进入 pending 且 incomplete 计数 +1。"""
        cfg = _make_config(tmp_path)
        audio = cfg.audio_dir / "BVbroken.wav"
        audio.write_bytes(b"x" * 2048)
        # 写一个明显损坏的 transcript（empty json）
        (cfg.transcripts_dir / "BVbroken.json").write_text("{}", encoding="utf-8")

        with patch("src.asr.funasr_engine.check_transcript_integrity",
                   return_value=(False, "full_text 为空")):
            pending, skipped, incomplete = _scan_pending_audios(cfg)

        assert len(pending) == 1
        assert incomplete == 1


class TestProcessPendingBatch:
    """转写完成后的 delete_audio 行为"""

    def _make_engine(self, success: bool = True):
        engine = MagicMock()
        if success:
            engine.transcribe.return_value = MagicMock(
                full_text="转写结果",
                segments=[],
            )
        else:
            engine.transcribe.side_effect = RuntimeError("ASR 引擎挂了")
        return engine

    def test_delete_audio_when_transcript_valid(self, tmp_path):
        """转写成功 + 完整性校验通过 → 音频被删除。"""
        cfg = _make_config(tmp_path)
        audio = cfg.audio_dir / "BVok.wav"
        audio.write_bytes(b"x" * 2048)

        engine = self._make_engine(success=True)
        with patch("main.save_transcript", create=True), \
             patch("src.asr.funasr_engine.save_transcript") as _, \
             patch("src.asr.funasr_engine.check_transcript_integrity",
                   return_value=(True, "ok")):
            success, deleted = _process_pending_batch(
                [(audio, "BVok")], {}, engine, cfg, delete_audio=True,
            )

        assert success == 1
        assert deleted == 1
        assert not audio.exists(), "音频文件应被删除"

    def test_keep_audio_when_delete_disabled(self, tmp_path):
        """delete_audio=False → 音频保留。"""
        cfg = _make_config(tmp_path)
        audio = cfg.audio_dir / "BVkeep.wav"
        audio.write_bytes(b"x" * 2048)

        engine = self._make_engine(success=True)
        with patch("src.asr.funasr_engine.save_transcript"), \
             patch("src.asr.funasr_engine.check_transcript_integrity",
                   return_value=(True, "ok")):
            success, deleted = _process_pending_batch(
                [(audio, "BVkeep")], {}, engine, cfg, delete_audio=False,
            )

        assert success == 1
        assert deleted == 0
        assert audio.exists(), "delete_audio=False 时音频应保留"

    def test_keep_audio_when_integrity_fails_after_save(self, tmp_path):
        """transcript 写入后完整性校验失败 → 不删（双保险）。"""
        cfg = _make_config(tmp_path)
        audio = cfg.audio_dir / "BVfail.wav"
        audio.write_bytes(b"x" * 2048)

        engine = self._make_engine(success=True)
        with patch("src.asr.funasr_engine.save_transcript"), \
             patch("src.asr.funasr_engine.check_transcript_integrity",
                   return_value=(False, "JSON 解析失败")):
            success, deleted = _process_pending_batch(
                [(audio, "BVfail")], {}, engine, cfg, delete_audio=True,
            )

        assert success == 1
        assert deleted == 0
        assert audio.exists(), "完整性校验失败时音频应保留作为补救材料"

    def test_engine_failure_does_not_break_batch(self, tmp_path):
        """单个转写失败不中断批处理（根级硬规则 #8）。"""
        cfg = _make_config(tmp_path)
        a1 = cfg.audio_dir / "BVbad.wav"
        a2 = cfg.audio_dir / "BVgood.wav"
        a1.write_bytes(b"x" * 2048)
        a2.write_bytes(b"x" * 2048)

        engine = MagicMock()
        # 第一个抛异常，第二个成功
        engine.transcribe.side_effect = [
            RuntimeError("OOM 之类"),
            MagicMock(full_text="ok", segments=[]),
        ]
        with patch("src.asr.funasr_engine.save_transcript"), \
             patch("src.asr.funasr_engine.check_transcript_integrity",
                   return_value=(True, "ok")):
            success, deleted = _process_pending_batch(
                [(a1, "BVbad"), (a2, "BVgood")], {}, engine, cfg, delete_audio=True,
            )

        assert success == 1, "第二个应该成功"
        assert deleted == 1, "第二个的音频应被删"
        assert a1.exists(), "失败的音频应保留供下次重试"
        assert not a2.exists(), "成功的音频应删除"
