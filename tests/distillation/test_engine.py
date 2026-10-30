import asyncio
import json
import threading
import time
from dataclasses import dataclass

from src.distillation.engine import DistillationEngine
from src.distillation.request import DistillationRequest
from src.distillation.store import JobStateStore
from src.distillation.artifacts import ArtifactRecord, sha256_file
from src.distillation.state import ItemState, JobState, ProcessingStatus
from src.platforms.models import DownloadedAssets, ItemType, SourceCreator, SourceItem


class StageProbes:
    def __init__(self):
        self.lock = threading.Lock()
        self.current = {"download": 0, "asr": 0, "llm": 0, "active": 0}
        self.maximum = dict(self.current)
        self.asr_initializations = 0

    def enter(self, stage):
        with self.lock:
            self.current[stage] += 1
            self.maximum[stage] = max(self.maximum[stage], self.current[stage])
            if stage == "download":
                self.current["active"] += 1
                self.maximum["active"] = max(
                    self.maximum["active"], self.current["active"]
                )

    def leave(self, stage):
        with self.lock:
            self.current[stage] -= 1
            if stage == "llm":
                self.current["active"] -= 1


@dataclass
class Prepared:
    item: SourceItem
    assets: DownloadedAssets
    audio_path: object = None


@dataclass
class Transcript:
    item: SourceItem
    path: object = None
    document: dict = None


@dataclass
class Enriched:
    transcript: Transcript
    cleaned: dict
    knowledge: dict
    cleaned_path: object = None
    knowledge_path: object = None


class FakeAdapter:
    def __init__(self, probes, tmp_path, fail_source_id=None):
        self.probes = probes
        self.tmp_path = tmp_path
        self.fail_source_id = fail_source_id

    def download_assets(self, item, destination, *, progress):
        self.probes.enter("download")
        try:
            time.sleep(0.02)
            if item.source_id == self.fail_source_id:
                raise RuntimeError("download failed")
            path = self.tmp_path / f"{item.item_id}.wav"
            path.write_bytes(b"audio")
            return DownloadedAssets(audio_path=path)
        finally:
            if item.source_id == self.fail_source_id:
                with self.probes.lock:
                    self.probes.current["active"] -= 1
            self.probes.leave("download")


class FakeProcessor:
    def __init__(self, probes):
        self.probes = probes
        probes.asr_initializations += 1

    def prepare(self, item, assets):
        return Prepared(item, assets, assets.audio_path)

    def transcribe(self, prepared):
        self.probes.enter("asr")
        try:
            time.sleep(0.01)
            return Transcript(prepared.item, document={"full_text": "text"})
        finally:
            self.probes.leave("asr")

    def enrich(self, transcript):
        self.probes.enter("llm")
        try:
            time.sleep(0.02)
            return Enriched(transcript, {"full_text": "clean"}, {"summary": "knowledge"})
        finally:
            self.probes.leave("llm")

    def load_transcript_artifact(self, item, path):
        return Transcript(item, path=path, document={"full_text": "resumed"})


def make_creator():
    return SourceCreator(
        platform="douyin",
        creator_id="creator-1",
        display_name="Creator",
        canonical_url="https://www.douyin.com/user/creator-1",
    )


def make_items(count):
    return tuple(
        SourceItem(
            platform="douyin",
            item_id=str(index),
            creator_id="creator-1",
            item_type=ItemType.VIDEO,
            title=f"Video {index}",
            description="",
            canonical_url=f"https://www.douyin.com/video/{index}",
        )
        for index in range(count)
    )


def test_stage_limits_and_single_asr_initialization(tmp_path):
    probes = StageProbes()
    request = DistillationRequest(
        job_id="job-1",
        creator=make_creator(),
        items=make_items(8),
        output_root=tmp_path / "output",
        download_workers=3,
        asr_workers=1,
        llm_workers=3,
        max_active_items=3,
        cleanup_media=False,
    )
    engine = DistillationEngine(
        adapter=FakeAdapter(probes, tmp_path),
        processor_factory=lambda: FakeProcessor(probes),
        state_store=JobStateStore(tmp_path / "job_state.json"),
    )

    result = asyncio.run(engine.run(request))

    assert result.completed == 8
    assert probes.maximum["download"] <= 3
    assert probes.maximum["asr"] == 1
    assert probes.maximum["llm"] <= 3
    assert probes.maximum["active"] <= 3
    assert probes.asr_initializations == 1


def test_one_item_failure_does_not_stop_batch(tmp_path):
    probes = StageProbes()
    request = DistillationRequest(
        job_id="job-1",
        creator=make_creator(),
        items=make_items(3),
        output_root=tmp_path / "output",
        cleanup_media=False,
        retry_limit=0,
    )
    engine = DistillationEngine(
        adapter=FakeAdapter(probes, tmp_path, fail_source_id="douyin_1"),
        processor_factory=lambda: FakeProcessor(probes),
        state_store=JobStateStore(tmp_path / "job_state.json"),
    )

    result = asyncio.run(engine.run(request))

    assert result.completed == 2
    assert result.failed == 1
    assert engine.state.items["douyin_1"].last_error == "download failed"


def test_valid_transcript_resume_skips_download_and_asr(tmp_path):
    probes = StageProbes()
    item = make_items(1)[0]
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(
        '{"full_text":"resumed","segments":[{"text":"resumed","start":0,"end":1}]}',
        "utf-8",
    )
    record = ArtifactRecord(
        path=str(transcript_path),
        sha256=sha256_file(transcript_path),
        size_bytes=transcript_path.stat().st_size,
    )
    store = JobStateStore(tmp_path / "job_state.json")
    store.save(
        JobState(
            job_id="job-1",
            status="running",
            items={
                item.source_id: ItemState(
                    source_id=item.source_id,
                    processing_status=ProcessingStatus.WRITING,
                    artifacts={"transcript": record},
                    transcript_verified=True,
                )
            },
        )
    )
    request = DistillationRequest(
        job_id="job-1",
        creator=make_creator(),
        items=(item,),
        output_root=tmp_path / "output",
        cleanup_media=False,
    )
    engine = DistillationEngine(
        adapter=FakeAdapter(probes, tmp_path),
        processor_factory=lambda: FakeProcessor(probes),
        state_store=store,
    )

    result = asyncio.run(engine.run(request))

    assert result.completed == 1
    assert probes.maximum["download"] == 0
    assert probes.maximum["asr"] == 0
    assert probes.maximum["llm"] == 1


def test_engine_preserves_dashboard_request_fields_when_initializing_state(tmp_path):
    probes = StageProbes()
    store = JobStateStore(tmp_path / "job_state.json")
    store.save(
        JobState(
            job_id="job-1",
            status="queued",
            request={
                "target": "https://fixture.invalid/creator",
                "platform": "douyin",
                "outputs": ("episodes", "skill"),
                "rag_chunks": True,
            },
        )
    )
    request = DistillationRequest(
        job_id="job-1",
        creator=make_creator(),
        items=make_items(1),
        output_root=tmp_path / "output",
        cleanup_media=False,
    )
    engine = DistillationEngine(
        adapter=FakeAdapter(probes, tmp_path),
        processor_factory=lambda: FakeProcessor(probes),
        state_store=store,
    )

    asyncio.run(engine.run(request))

    saved = store.load()
    assert saved.request["target"] == "https://fixture.invalid/creator"
    assert saved.request["outputs"] == ["episodes", "skill"]
    assert saved.request["rag_chunks"] is True


def test_item_update_merges_a_pause_revision_instead_of_failing(tmp_path):
    probes = StageProbes()
    item = make_items(1)[0]
    store = JobStateStore(tmp_path / "job_state.json")
    initial = store.save(
        JobState(
            job_id="job-1",
            status="running",
            items={item.source_id: ItemState(source_id=item.source_id)},
        )
    )
    engine = DistillationEngine(
        adapter=FakeAdapter(probes, tmp_path),
        processor_factory=lambda: FakeProcessor(probes),
        state_store=store,
    )
    engine.state = initial
    paused = store.save(
        JobState(
            job_id=initial.job_id,
            status="pause_requested",
            items=initial.items,
        ),
        expected_revision=initial.revision,
    )

    asyncio.run(engine._update_item(item.source_id, processing_status=ProcessingStatus.DOWNLOADING))

    saved = store.load()
    assert saved.revision == paused.revision + 1
    assert saved.status == "pause_requested"
    assert saved.items[item.source_id].processing_status is ProcessingStatus.DOWNLOADING


def test_engine_honors_a_pause_request_written_before_initialization(tmp_path):
    probes = StageProbes()
    store = JobStateStore(tmp_path / "job_state.json")
    store.save(JobState(job_id="job-1", status="pause_requested"))
    request = DistillationRequest(
        job_id="job-1",
        creator=make_creator(),
        items=make_items(1),
        output_root=tmp_path / "output",
        cleanup_media=False,
    )
    engine = DistillationEngine(
        adapter=FakeAdapter(probes, tmp_path),
        processor_factory=lambda: FakeProcessor(probes),
        state_store=store,
    )

    result = asyncio.run(engine.run(request))

    assert result.paused is True
    assert store.load().status == "paused"
    assert probes.maximum["download"] == 0


def test_job_state_persists_only_public_creator_fields(tmp_path):
    """``raw_metadata`` is a mapping proxy: it cannot be asdict-ed or persisted."""

    probes = StageProbes()
    store = JobStateStore(tmp_path / "job_state.json")
    creator = SourceCreator(
        platform="douyin",
        creator_id="creator-1",
        display_name="Creator",
        canonical_url="https://www.douyin.com/user/creator-1",
        raw_metadata={"sec_uid": "adapter-internal", "nested": {"cursor": 7}},
    )
    request = DistillationRequest(
        job_id="job-1",
        creator=creator,
        items=make_items(1),
        output_root=tmp_path / "output",
        cleanup_media=False,
    )
    engine = DistillationEngine(
        adapter=FakeAdapter(probes, tmp_path),
        processor_factory=lambda: FakeProcessor(probes),
        state_store=store,
    )

    asyncio.run(engine.run(request))

    raw = (tmp_path / "job_state.json").read_text("utf-8")
    assert "adapter-internal" not in raw
    assert json.loads(raw)["creator"] == {
        "platform": "douyin",
        "creator_id": "creator-1",
        "display_name": "Creator",
        "canonical_url": "https://www.douyin.com/user/creator-1",
    }
