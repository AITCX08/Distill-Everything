"""Tests for one-work, checkpointed worker execution."""

import json
import threading
import time

from src.orchestration.manager import TaskManager
from src.orchestration.store import OrchestrationStore
from src.orchestration.worker import run_worker


def _worker_events(work_dir) -> str:
    """Surface the worker's own failure reason when an assertion trips."""

    path = work_dir / "events.jsonl"
    if not path.exists():
        return "worker wrote no events"
    return path.read_text("utf-8")


class FakePipeline:
    def __init__(self):
        self.download_calls = 0
        self.clean_calls = 0
        self.summary_calls = 0
        self.write_calls = 0

    def download(self, context):
        self.download_calls += 1
        return {"audio": "media/audio.m4a"}

    def clean(self, context):
        self.clean_calls += 1
        return {"cleaned": "artifacts/cleaned.json"}

    def summarize(self, context):
        self.summary_calls += 1
        return {"knowledge": "artifacts/knowledge.json"}

    def write(self, context):
        self.write_calls += 1
        return {"episode": "artifacts/episode.md"}


def _write_payload(tmp_path, *, task_id):
    work_dir = tmp_path / "worker"
    payload = tmp_path / "payload.json"
    payload.write_text(
        json.dumps({"task_id": task_id, "work_dir": str(work_dir), "source": {"id": "p01"}}),
        "utf-8",
    )
    return payload, work_dir


def _write_checkpoint(work_dir, *, task_id, stage, transcript_verified=False):
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "checkpoint.json").write_text(
        json.dumps(
            {
                "task_id": task_id,
                "stage": stage,
                "checkpoint_revision": 3,
                "artifacts": {"transcript": "artifacts/transcript.json"},
                "transcript_verified": transcript_verified,
            }
        ),
        "utf-8",
    )


def test_worker_resumes_after_valid_transcript_without_redownloading(tmp_path):
    payload, work_dir = _write_payload(tmp_path, task_id="tsk_1")
    _write_checkpoint(work_dir, task_id="tsk_1", stage="cleaning", transcript_verified=True)
    pipeline = FakePipeline()

    assert run_worker("tsk_1", payload, pipeline=pipeline) == 0
    assert pipeline.download_calls == 0
    assert pipeline.clean_calls == 1
    assert pipeline.summary_calls == 1
    assert pipeline.write_calls == 1

    checkpoint = json.loads((work_dir / "checkpoint.json").read_text("utf-8"))
    assert checkpoint["stage"] == "completed"
    assert checkpoint["checkpoint_revision"] == 6


def test_worker_honors_parent_pause_between_stages(tmp_path):
    payload, work_dir = _write_payload(tmp_path, task_id="tsk_2")
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "control.json").write_text('{"action":"pause"}', "utf-8")
    pipeline = FakePipeline()

    assert run_worker("tsk_2", payload, pipeline=pipeline) == 0
    assert pipeline.download_calls == 0
    checkpoint = json.loads((work_dir / "checkpoint.json").read_text("utf-8"))
    assert checkpoint["stage"] == "paused"
    assert checkpoint["resume_stage"] == "pending"
    events = (work_dir / "events.jsonl").read_text("utf-8")
    assert '"status": "paused"' in events


def test_worker_records_unexpected_pipeline_errors_as_failed_terminal_events(tmp_path):
    payload, work_dir = _write_payload(tmp_path, task_id="tsk_unexpected")
    _write_checkpoint(work_dir, task_id="tsk_unexpected", stage="cleaning", transcript_verified=True)

    class BrokenPipeline:
        def clean(self, context):
            raise TypeError("unexpected local pipeline failure")

    assert run_worker("tsk_unexpected", payload, pipeline=BrokenPipeline()) == 1
    events = (work_dir / "events.jsonl").read_text("utf-8")
    assert '"status": "failed"' in events
    assert "unexpected local pipeline failure" in events


def test_worker_builds_one_pipeline_from_its_payload_when_not_injected(tmp_path):
    payload, _ = _write_payload(tmp_path, task_id="tsk_factory")
    class FullPipeline(FakePipeline):
        def extract_audio(self, context):
            return {}

        def transcribe(self, context):
            return {"transcript": "artifacts/transcript.json"}

    pipeline = FullPipeline()
    built_for = []

    assert run_worker(
        "tsk_factory",
        payload,
        pipeline_factory=lambda value: built_for.append(value["task_id"]) or pipeline,
    ) == 0

    assert built_for == ["tsk_factory"]
    assert pipeline.download_calls == 1


def test_resource_controlled_worker_runs_all_stages_after_manager_grants(tmp_path):
    class FullPipeline(FakePipeline):
        def __init__(self):
            super().__init__()
            self.extract_calls = 0
            self.transcribe_calls = 0

        def extract_audio(self, context):
            self.extract_calls += 1
            return {}

        def transcribe(self, context):
            self.transcribe_calls += 1
            return {"transcript": "artifacts/transcript.json"}

    store = OrchestrationStore(tmp_path / "orchestration.sqlite3")
    job = store.create_job(platform="bilibili", target="https://example.invalid")
    task = store.create_tasks(job.job_id, ["p01"])[0]
    task = store.transition_task(task.task_id, task.revision, status="running")
    manager = TaskManager(store=store, worker_root=tmp_path / "workers")
    payload = tmp_path / "payload.json"
    work_dir = tmp_path / "workers" / task.task_id
    payload.write_text(
        json.dumps({
            "task_id": task.task_id,
            "work_dir": str(work_dir),
            "source": {"id": "p01"},
            "resource_control": True,
        }),
        "utf-8",
    )
    pipeline = FullPipeline()
    result = []
    worker = threading.Thread(target=lambda: result.append(run_worker(task.task_id, payload, pipeline=pipeline)))
    worker.start()
    deadline = time.monotonic() + 10
    while worker.is_alive() and time.monotonic() < deadline:
        manager.tick()
        time.sleep(0.01)
    worker.join(timeout=5)
    assert not worker.is_alive(), "worker did not finish before the deadline"

    assert result == [0], _worker_events(work_dir)
    assert (pipeline.download_calls, pipeline.extract_calls, pipeline.transcribe_calls) == (1, 1, 1)
    assert (pipeline.clean_calls, pipeline.summary_calls, pipeline.write_calls) == (1, 1, 1)


def test_manager_marks_completed_worker_stage_as_completed(tmp_path):
    """A terminal completion must not retain the previous `writing` stage in the UI."""

    store = OrchestrationStore(tmp_path / "orchestration.sqlite3")
    job = store.create_job(platform="bilibili", target="https://example.invalid")
    (task,) = store.create_tasks(job.job_id, ["p01"])
    task = store.transition_task(
        task.task_id,
        task.revision,
        status="running",
        stage="writing",
    )
    worker_root = tmp_path / "workers"
    task_dir = worker_root / task.task_id
    task_dir.mkdir(parents=True)
    (task_dir / "events.jsonl").write_text(
        json.dumps({"v": 1, "type": "terminal", "task_id": task.task_id, "status": "completed"}) + "\n",
        "utf-8",
    )

    TaskManager(store=store, worker_root=worker_root)._read_worker_events(task.task_id)

    updated = store.get_task(task.task_id)
    assert updated.status == "completed"
    assert updated.stage == "completed"
