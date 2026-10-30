from unittest.mock import Mock

from src.platforms.douyin.downloader import DouyinDownloader
from src.platforms.models import ItemType, SourceAsset, SourceItem


def make_item(url="https://expired.example/video"):
    return SourceItem(
        platform="douyin",
        item_id="123",
        creator_id="creator-1",
        item_type=ItemType.VIDEO,
        title="Video",
        description="",
        canonical_url="https://www.douyin.com/video/123",
        assets=(SourceAsset("video", url),),
    )


class FakeResponse:
    def __init__(self, status, chunks=(), headers=None):
        self.status = status
        self._chunks = chunks
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def iter_bytes(self, chunk_size):
        yield from self._chunks


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def get(self, url, *, headers):
        self.urls.append(url)
        return self.responses.pop(0)


def test_downloader_reports_real_bytes(tmp_path):
    http = FakeHttp(
        [FakeResponse(200, [b"abc", b"def"], {"Content-Length": "6"})]
    )
    progress = []

    result = DouyinDownloader(http).download(make_item(), tmp_path, progress.append)

    assert result.video_path.read_bytes() == b"abcdef"
    assert progress[-1].completed_bytes == 6
    assert progress[-1].total_bytes == 6
    assert progress[-1].bytes_per_second >= 0


def test_expired_media_url_is_refreshed_once(tmp_path):
    http = FakeHttp(
        [
            FakeResponse(403),
            FakeResponse(200, [b"fresh"], {"Content-Length": "5"}),
        ]
    )
    refresh = Mock(return_value=make_item("https://fresh.example/video"))
    downloader = DouyinDownloader(http, refresh_item=refresh)

    downloader.download(make_item(), tmp_path, Mock())

    refresh.assert_called_once()
    assert http.urls == ["https://expired.example/video", "https://fresh.example/video"]


def test_partial_file_is_removed_after_download_failure(tmp_path):
    http = FakeHttp([FakeResponse(500)])

    try:
        DouyinDownloader(http).download(make_item(), tmp_path, Mock())
    except Exception:
        pass

    assert not list(tmp_path.glob("*.part"))
    assert not (tmp_path / "123.mp4").exists()
