from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

from src.application.events import EventHub
from src.application.commands import PreviewRequest
from src.application.leases import JobLeaseConflict, JobLeaseManager
from src.application.source_runner import (
    SourceCreatorRequest,
    SourceDistillationRunner,
    SourceRunResult,
)
from src.platforms.bilibili.adapter import BilibiliAdapter
from src.platforms.models import (
    AuthStatus,
    EnumerationCheckpoint,
    EnumerationPage,
    ItemType,
    ResolvedTarget,
    SourceCreator,
    SourceItem,
)
from src.distillation.state import JobState


class GuardedLeaseManager:
    def __init__(self):
        self.active = False
        self.acquired = []

    @contextmanager
    def acquire(self, job_id, *, owner):
        self.acquired.append((job_id, owner))
        self.active = True
        try:
            yield SimpleNamespace()
        finally:
            self.active = False


class GuardedAdapter:
    descriptor = SimpleNamespace(name="douyin")

    def __init__(self, lease_manager):
        self.lease_manager = lease_manager

    def auth_status(self):
        return AuthStatus("ready", "")

    def resolve(self, target):
        return ResolvedTarget("douyin", "creator", target, target)

    def get_creator(self, target):
        return SourceCreator("douyin", target.creator_id, "Creator", target.canonical_url)

    def iter_items(self, creator, *, checkpoint):
        assert self.lease_manager.active, "enumeration must run inside the job lease"
        item = SourceItem(
            platform="douyin",
            item_id="1",
            creator_id=creator.creator_id,
            item_type=ItemType.VIDEO,
            title="One",
            description="",
            canonical_url="https://www.douyin.com/video/1",
        )
        yield EnumerationPage(
            items=(item,),
            checkpoint=EnumerationCheckpoint(
                seen_ids=frozenset({"1"}), complete=True, expected_count=1
            ),
            has_more=False,
        )


class PreviewAdapter:
    descriptor = SimpleNamespace(name="douyin")

    def auth_status(self):
        return AuthStatus("ready", "")

    def resolve(self, target):
        return ResolvedTarget("douyin", "creator", target, target)

    def get_creator(self, target):
        return SourceCreator("douyin", target.creator_id, "Creator", target.canonical_url)

    def iter_items(self, creator, *, checkpoint):
        del checkpoint
        yield EnumerationPage(
            items=(
                SourceItem(
                    platform="douyin",
                    item_id="video-1",
                    creator_id=creator.creator_id,
                    item_type=ItemType.VIDEO,
                    title="One",
                    description="",
                    canonical_url="https://fixture.invalid/video-1",
                ),
                SourceItem(
                    platform="douyin",
                    item_id="gallery-1",
                    creator_id=creator.creator_id,
                    item_type=ItemType.GALLERY,
                    title="Gallery",
                    description="",
                    canonical_url="https://fixture.invalid/gallery-1",
                ),
            ),
            checkpoint=EnumerationCheckpoint(complete=True),
            has_more=False,
        )


def make_config(tmp_path):
    return SimpleNamespace(
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "output",
        llm_provider="test",
    )


def test_runner_holds_lease_during_enumeration_and_pipeline(tmp_path):
    leases = GuardedLeaseManager()
    adapter = GuardedAdapter(leases)
    manager = SimpleNamespace(select=lambda target, platform: adapter)
    pipeline_calls = []

    def pipeline(context):
        assert leases.active, "pipeline must run inside the same job lease"
        pipeline_calls.append(context)
        return SimpleNamespace(completed=1, failed=0, unsupported=0, paused=False)

    runner = SourceDistillationRunner(
        config=make_config(tmp_path),
        platform_manager=manager,
        events=EventHub(),
        lease_manager=leases,
        pipeline_executor=pipeline,
    )

    result = runner.run(SourceCreatorRequest("https://www.douyin.com/user/creator"))

    assert result.completed == 1
    assert len(pipeline_calls) == 1
    assert leases.acquired[0][1] == "source-runner"


def test_dry_run_does_not_write_state_or_invoke_pipeline(tmp_path):
    leases = GuardedLeaseManager()
    adapter = GuardedAdapter(leases)
    manager = SimpleNamespace(select=lambda target, platform: adapter)
    runner = SourceDistillationRunner(
        config=make_config(tmp_path),
        platform_manager=manager,
        events=EventHub(),
        lease_manager=leases,
        pipeline_executor=lambda context: (_ for _ in ()).throw(AssertionError()),
    )

    result = runner.run(
        SourceCreatorRequest(
            "https://www.douyin.com/user/creator",
            dry_run=True,
        )
    )

    assert result.total == 1
    assert result.dry_run is True
    assert not list((tmp_path / "data").rglob("job_state.json"))


def test_preview_enumerates_without_writing_and_returns_stable_job_id(tmp_path):
    adapter = PreviewAdapter()
    runner = SourceDistillationRunner(
        config=make_config(tmp_path),
        platform_manager=SimpleNamespace(select=lambda target, platform: adapter),
        events=EventHub(),
    )

    preview = runner.preview(
        PreviewRequest(
            target="https://fixture.invalid/creator",
            platform="douyin",
            outputs=("episodes", "skill"),
        )
    )

    assert preview.platform == "douyin"
    assert preview.creator_id == "creator"
    assert preview.total_items == 2
    assert preview.processable_items == 1
    assert preview.unsupported_items == 1
    assert runner.job_id_for_preview(preview) == runner._job_id(
        SourceCreator("douyin", "creator", "Creator", "https://fixture.invalid/creator")
    )
    assert not list((tmp_path / "data").rglob("job_state.json"))


def test_request_pause_forwards_only_to_the_matching_active_engine(tmp_path):
    runner = SourceDistillationRunner(
        config=make_config(tmp_path),
        platform_manager=SimpleNamespace(),
    )
    engine = SimpleNamespace(request_pause=Mock())
    runner._active_engines["job-1"] = engine

    assert runner.request_pause("job-1") is True
    assert runner.request_pause("other-job") is False
    engine.request_pause.assert_called_once_with()


def test_service_delegates_source_execution(tmp_path):
    from src.application.queries import JobRepository
    from src.application.service import DistillationService

    expected = SourceRunResult(
        job_id="job",
        platform="douyin",
        creator_name="Creator",
        total=0,
        unsupported=0,
        dry_run=True,
    )
    source_runner = SimpleNamespace(run=lambda request: expected)
    service = DistillationService(
        repository=JobRepository(tmp_path),
        source_runner=source_runner,
    )

    assert service.run_source(SourceCreatorRequest("target", dry_run=True)) is expected


def test_existing_lease_blocks_no_resume_before_state_mutation(tmp_path):
    config = make_config(tmp_path)
    leases = JobLeaseManager(config.data_dir / "jobs" / "leases")
    adapter = GuardedAdapter(SimpleNamespace(active=True))
    manager = SimpleNamespace(select=lambda target, platform: adapter)
    runner = SourceDistillationRunner(
        config=config,
        platform_manager=manager,
        events=EventHub(),
        lease_manager=leases,
        pipeline_executor=lambda context: None,
    )
    creator = adapter.get_creator(adapter.resolve("https://www.douyin.com/user/creator"))
    job_id = runner._job_id(creator)
    store = runner._state_store(creator)
    before = store.save(JobState(job_id=job_id, status="running"))

    with leases.acquire(job_id, owner="first-run"):
        try:
            runner.run(
                SourceCreatorRequest(
                    "https://www.douyin.com/user/creator",
                    resume=False,
                )
            )
        except JobLeaseConflict:
            pass
        else:
            raise AssertionError("the second run must not enter the stateful pipeline")

    assert store.load() == before


def test_runner_accepts_bilibili_sessdata_without_bili_jct(tmp_path):
    config = SimpleNamespace(
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "output",
        llm_provider="test",
        bilibili=SimpleNamespace(
            sessdata="session-cookie",
            bili_jct="",
            buvid3="device",
        ),
        credentials_cache=tmp_path / "credentials.json",
    )
    adapter = BilibiliAdapter(
        config,
        credential_provider=Mock(return_value=(SimpleNamespace(), "device")),
        video_fetcher=Mock(
            return_value=[{"bvid": "BV1", "title": "One", "duration": "00:10"}]
        ),
    )
    manager = SimpleNamespace(select=Mock(return_value=adapter))
    runner = SourceDistillationRunner(config=config, platform_manager=manager)

    result = runner.run(SourceCreatorRequest("bilibili:42", dry_run=True))

    assert result.platform == "bilibili"
    assert result.total == 1
