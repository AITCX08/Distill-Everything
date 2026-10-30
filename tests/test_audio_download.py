"""
audio_download.py 单元测试

覆盖：generate_cookies_file 函数
"""

import io
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.crawl import audio_download
from src.crawl.audio_download import generate_cookies_file


def test_ytdlp_command_falls_back_to_installed_python_module(monkeypatch):
    monkeypatch.setattr(audio_download, "_shell_ytdlp", lambda: None, raising=False)

    assert audio_download._yt_dlp_command() == [sys.executable, "-m", "yt_dlp"]


def make_credential(sessdata="test_sessdata", bili_jct="test_bili_jct"):
    cred = MagicMock()
    cred.sessdata = sessdata
    cred.bili_jct = bili_jct
    return cred


class TestGenerateCookiesFile:
    def test_generates_netscape_header(self, tmp_path):
        out = tmp_path / "cookies.txt"
        result = generate_cookies_file(make_credential(), output_path=out)
        content = result.read_text(encoding="utf-8")
        assert content.startswith("# Netscape HTTP Cookie File")

    def test_contains_sessdata_and_bili_jct(self, tmp_path):
        out = tmp_path / "cookies.txt"
        generate_cookies_file(make_credential(sessdata="AAA", bili_jct="BBB"), output_path=out)
        content = out.read_text(encoding="utf-8")
        assert "SESSDATA\tAAA" in content
        assert "bili_jct\tBBB" in content

    def test_includes_buvid3_when_provided(self, tmp_path):
        out = tmp_path / "cookies.txt"
        generate_cookies_file(make_credential(), buvid3="my_buvid3", output_path=out)
        content = out.read_text(encoding="utf-8")
        assert "buvid3\tmy_buvid3" in content

    def test_excludes_buvid3_when_empty(self, tmp_path):
        out = tmp_path / "cookies.txt"
        generate_cookies_file(make_credential(), buvid3="", output_path=out)
        content = out.read_text(encoding="utf-8")
        assert "buvid3" not in content

    def test_returns_output_path(self, tmp_path):
        out = tmp_path / "cookies.txt"
        result = generate_cookies_file(make_credential(), output_path=out)
        assert result == out

    def test_uses_temp_file_when_no_path(self):
        cred = make_credential()
        result = generate_cookies_file(cred)
        assert result.exists()
        content = result.read_text(encoding="utf-8")
        assert "SESSDATA" in content
        # cleanup
        result.unlink(missing_ok=True)

    def test_cookie_format_has_correct_columns(self, tmp_path):
        """Each cookie line should have 7 tab-separated fields."""
        out = tmp_path / "cookies.txt"
        generate_cookies_file(make_credential(), buvid3="b", output_path=out)
        lines = out.read_text(encoding="utf-8").strip().split("\n")
        cookie_lines = [line for line in lines if not line.startswith("#")]
        for line in cookie_lines:
            fields = line.split("\t")
            assert len(fields) == 7, f"Expected 7 fields, got {len(fields)}: {line}"

    def test_domain_is_bilibili(self, tmp_path):
        out = tmp_path / "cookies.txt"
        generate_cookies_file(make_credential(), output_path=out)
        lines = out.read_text(encoding="utf-8").strip().split("\n")
        cookie_lines = [line for line in lines if not line.startswith("#")]
        for line in cookie_lines:
            assert line.startswith(".bilibili.com")


def test_callback_download_reports_ytdlp_transfer_before_completion(tmp_path, monkeypatch):
    output = tmp_path / "BV1abc.wav"
    updates = []
    launched = {}

    class FakeYtDlp:
        stdout = io.StringIO("__distill_progress__\t1024\t4096\t512.5\n")

        def wait(self, timeout):
            assert 0 < timeout <= 300
            assert updates == [(1024, 4096, 512.5)]
            output.write_bytes(b"audio")
            return 0

    def launch(*args, **kwargs):
        launched.update(kwargs)
        return FakeYtDlp()

    monkeypatch.setattr(audio_download.subprocess, "Popen", launch)

    result = audio_download.download_audio_with_progress(
        "BV1abc",
        tmp_path,
        progress_callback=lambda downloaded, total, speed: updates.append(
            (downloaded, total, speed)
        ),
    )

    assert result == output
    assert launched["creationflags"] == getattr(audio_download.subprocess, "CREATE_NO_WINDOW", 0)


def test_callback_download_times_out_a_silent_stdout_and_kills_process(tmp_path, monkeypatch):
    release_stdout = threading.Event()
    completed = threading.Event()

    class SilentStdout:
        def readline(self):
            release_stdout.wait()
            return ""

        def __iter__(self):
            return self

        def __next__(self):
            line = self.readline()
            if not line:
                raise StopIteration
            return line

        def close(self):
            release_stdout.set()

    class FakeYtDlp:
        def __init__(self):
            self.stdout = SilentStdout()
            self.killed = False

        def kill(self):
            self.killed = True
            release_stdout.set()

        def wait(self, timeout=None):
            return 0

    process = FakeYtDlp()
    monkeypatch.setattr(audio_download, "_DOWNLOAD_TIMEOUT_SECONDS", 0.02, raising=False)
    monkeypatch.setattr(audio_download.subprocess, "Popen", lambda *args, **kwargs: process)

    def download():
        try:
            assert audio_download.download_audio_with_progress(
                "BV1silent", tmp_path, progress_callback=lambda *_: None
            ) is None
        finally:
            completed.set()

    worker = threading.Thread(target=download)
    worker.start()
    try:
        assert completed.wait(0.25)
        assert process.killed
    finally:
        release_stdout.set()
        worker.join(timeout=1)


def test_callback_download_measures_throughput_when_ytdlp_speed_is_unavailable(tmp_path, monkeypatch):
    output = tmp_path / "BV1speed.wav"
    updates = []

    class DelayedProgressStdout:
        def __init__(self):
            self.lines = iter(
                (
                    "__distill_progress__\t100\tNA\tNA\n",
                    "__distill_progress__\t300\tNA\tNA\n",
                )
            )
            self.first = True

        def readline(self):
            try:
                line = next(self.lines)
            except StopIteration:
                return ""
            if self.first:
                self.first = False
            else:
                time.sleep(0.01)
            return line

        def __iter__(self):
            return self

        def __next__(self):
            line = self.readline()
            if not line:
                raise StopIteration
            return line

    class FakeYtDlp:
        stdout = DelayedProgressStdout()

        def wait(self, timeout):
            output.write_bytes(b"audio")
            return 0

    monkeypatch.setattr(audio_download.subprocess, "Popen", lambda *args, **kwargs: FakeYtDlp())

    result = audio_download.download_audio_with_progress(
        "BV1speed",
        tmp_path,
        progress_callback=lambda downloaded, total, speed: updates.append(
            (downloaded, total, speed)
        ),
    )

    assert result == output
    assert updates
    assert all(speed > 0 for _, _, speed in updates)

