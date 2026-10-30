from src.platforms.douyin.enumerator import DouyinEnumerator, map_aweme
from src.platforms.models import EnumerationCheckpoint, ItemType, SourceCreator


def make_creator():
    return SourceCreator(
        platform="douyin",
        creator_id="SEC_UID",
        display_name="Creator",
        canonical_url="https://www.douyin.com/user/SEC_UID",
    )


def aweme(aweme_id: str, *, media_type: int = 4):
    raw = {
        "aweme_id": aweme_id,
        "media_type": media_type,
        "desc": f"Work {aweme_id}",
        "create_time": 1_700_000_000,
        "author": {"sec_uid": "SEC_UID", "nickname": "Creator"},
        "statistics": {"play_count": 10, "digg_count": 2, "comment_count": 1},
    }
    if media_type == 2:
        raw["images"] = [
            {"url_list": [f"https://cdn.example/{aweme_id}/1.jpg"]},
            {"url_list": [f"https://cdn.example/{aweme_id}/2.jpg"]},
        ]
    else:
        raw["video"] = {
            "duration": 12_500,
            "play_addr": {
                "url_list": [f"https://cdn.example/{aweme_id}.mp4"],
                "data_size": 1234,
            },
            "cover": {"url_list": [f"https://cdn.example/{aweme_id}.jpg"]},
        }
    return raw


def page(next_cursor: int, ids, *, has_more: bool, total: int | None = None):
    value = {
        "aweme_list": [aweme(item_id) for item_id in ids],
        "max_cursor": next_cursor,
        "has_more": 1 if has_more else 0,
    }
    if total is not None:
        value["total"] = total
    return value


class FakeRoute:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requested_cursors = []

    def __call__(self, creator, cursor):
        self.requested_cursors.append(cursor)
        return self.responses.pop(0)


def test_pages_stop_on_has_more_false_and_deduplicate():
    route = FakeRoute(
        [
            page(2, ["1", "2"], has_more=True),
            page(3, ["2", "3"], has_more=False),
        ]
    )

    pages = list(DouyinEnumerator(route).iter_pages(make_creator(), None))

    assert [item.item_id for result in pages for item in result.items] == ["1", "2", "3"]
    assert pages[-1].checkpoint.complete is True
    assert pages[-1].has_more is False
    assert route.requested_cursors == ["0", "2"]


def test_resume_starts_from_saved_cursor():
    route = FakeRoute([page(30, ["9"], has_more=False)])
    checkpoint = EnumerationCheckpoint(cursor="20", seen_ids=frozenset({"1"}))

    pages = list(DouyinEnumerator(route).iter_pages(make_creator(), checkpoint))

    assert route.requested_cursors == ["20"]
    assert pages[0].checkpoint.seen_ids == frozenset({"1", "9"})


def test_completed_checkpoint_restarts_at_first_page_for_incremental_probe():
    route = FakeRoute([page(2, ["new", "old"], has_more=False, total=2)])
    checkpoint = EnumerationCheckpoint(
        cursor=None,
        seen_ids=frozenset({"old"}),
        complete=True,
        expected_count=1,
    )

    pages = list(DouyinEnumerator(route).iter_pages(make_creator(), checkpoint))

    assert route.requested_cursors == ["0"]
    assert [item.item_id for item in pages[0].items] == ["new"]
    assert pages[0].checkpoint.expected_count == 2


def test_known_boundary_only_stops_early_when_reliable_total_is_unchanged():
    route = FakeRoute(
        [
            page(2, ["1"], has_more=True, total=2),
            page(4, ["2"], has_more=True, total=2),
            page(6, ["3"], has_more=True, total=2),
        ]
    )
    checkpoint = EnumerationCheckpoint(
        seen_ids=frozenset({"1", "2"}),
        complete=True,
        expected_count=2,
    )

    pages = list(
        DouyinEnumerator(route, known_boundary_pages=2).iter_pages(make_creator(), checkpoint)
    )

    assert route.requested_cursors == ["0", "2"]
    assert pages[-1].checkpoint.complete is True
    assert pages[-1].items == ()


def test_repeated_cursor_is_incomplete_instead_of_claiming_success():
    route = FakeRoute([page(0, ["1"], has_more=True)])

    pages = list(DouyinEnumerator(route).iter_pages(make_creator(), None))

    assert pages[-1].checkpoint.complete is False
    assert pages[-1].has_more is True


def test_map_aweme_preserves_video_and_gallery_shapes():
    video = map_aweme(aweme("1"), "SEC_UID")
    gallery = map_aweme(aweme("2", media_type=2), "SEC_UID")

    assert video.item_type is ItemType.VIDEO
    assert video.duration_seconds == 12.5
    assert video.assets[0].kind == "video"
    assert video.assets[0].expected_bytes == 1234
    assert gallery.item_type is ItemType.GALLERY
    assert [asset.kind for asset in gallery.assets] == ["image", "image"]
    assert gallery.canonical_url.endswith("/note/2")
    assert gallery.raw_metadata["author"]["nickname"] == "Creator"


def test_api_markers_switch_for_collection_pages():
    from src.platforms.douyin.enumerator import api_markers_for

    creator_markers = api_markers_for("https://www.douyin.com/user/SEC_UID")
    collect_markers = api_markers_for("https://www.douyin.com/user/self?showTab=collect")
    collection_markers = api_markers_for("https://www.douyin.com/collection/7123")

    assert "/aweme/v1/web/aweme/post/" in creator_markers
    assert collect_markers == collection_markers
    assert any("listcollection" in marker for marker in collection_markers)
    assert collect_markers != creator_markers


def test_route_accepts_collection_payloads_and_keeps_the_cursor():
    from src.platforms.douyin.enumerator import DouyinBrowserRoute, api_markers_for

    class Response:
        def __init__(self, url, payload):
            self.url = url
            self._payload = payload

        def json(self):
            return self._payload

    route = DouyinBrowserRoute(None)
    route._api_markers = api_markers_for("https://www.douyin.com/collection/7123")

    route._capture(Response("https://www.douyin.com/aweme/v1/web/aweme/post/?max_cursor=0", {"aweme_list": []}))
    route._capture(
        Response(
            "https://www.douyin.com/aweme/v1/web/aweme/listcollection/?max_cursor=42",
            {"aweme_list": [{"aweme_id": "1"}], "has_more": 1},
        )
    )

    assert [cursor for cursor, _ in route._responses] == ["42"]

    route._api_markers = api_markers_for("https://www.douyin.com/user/SEC_UID")
    before = len(route._responses)
    route._capture(
        Response(
            "https://www.douyin.com/aweme/v1/web/aweme/listcollection/?max_cursor=42",
            {"aweme_list": []},
        )
    )
    assert len(route._responses) == before
