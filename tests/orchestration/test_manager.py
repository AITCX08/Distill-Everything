"""Lifecycle tests for the process-owning task manager."""

from src.orchestration.manager import (
    TaskManager,
    TaskManagerOwnershipError,
    _default_pid_probe,
)
from src.orchestration.store import OrchestrationStore
import json


class FakeProcess:
    pid = 4242

    def poll(self):
        return None


class FakeProcessFactory:
    def __call__(self, task, payload_path):
        self.pid = FakeProcess.pid
        self.task_id = task.task_id
        self.payload_path = payload_path
        return FakeProcess()


def test_only_one_task_manager_can_claim_one_data_root(tmp_path):
    store = OrchestrationStore(tmp_path / "orchestration.sqlite3")
    first = TaskManager(store=store, worker_root=tmp_path / "workers")
    second = TaskManager(store=store, worker_root=tmp_path / "workers")

    first.claim_ownership()
    try:
        second.claim_ownership()
    except TaskManagerOwnershipError:
        pass
    else:
        raise AssertionError("a second task manager claimed the same data root")


def test_start_records_one_lease_and_reads_worker_events(tmp_path):
    factory = FakeProcessFactory()
    store = OrchestrationStore(tmp_path / "orchestration.sqlite3")
    job = store.create_job(platform="bilibili", target="https://example.invalid/creator")
    manager = TaskManager(store=store, worker_root=tmp_path / "workers", process_factory=factory)
    task = manager.enqueue(job.job_id, "p01")

    manager.tick()

    assert store.get_lease(task.task_id).pid == factory.pid
    assert store.get_task(task.task_id).status == "running"
    assert store.get_task(task.task_id).attempt == 1
    assert factory.payload_path.exists()


def test_tick_persists_valid_worker_jsonl_events(tmp_path):
    class EventProcessFactory(FakeProcessFactory):
        def __call__(self, task, payload_path):
            result = super().__call__(task, payload_path)
            (payload_path.parent / "events.jsonl").write_text(
                '{"v":1,"type":"stage","task_id":"' + task.task_id + '","stage":"downloading"}\n',
                "utf-8",
            )
            return result

    factory = EventProcessFactory()
    store = OrchestrationStore(tmp_path / "orchestration.sqlite3")
    job = store.create_job(platform="bilibili", target="https://example.invalid/creator")
    manager = TaskManager(store=store, worker_root=tmp_path / "workers", process_factory=factory)
    task = manager.enqueue(job.job_id, "p01")

    manager.tick()

    event = store.list_events(task.task_id)[0]
    assert event.kind == "stage"
    assert event.payload["stage"] == "downloading"


def test_tick_reads_events_appended_after_worker_launch(tmp_path):
    factory = FakeProcessFactory()
    store = OrchestrationStore(tmp_path / "orchestration.sqlite3")
    job = store.create_job(platform="bilibili", target="https://example.invalid/creator")
    manager = TaskManager(store=store, worker_root=tmp_path / "workers", process_factory=factory)
    task = manager.enqueue(job.job_id, "p01")
    manager.tick()
    events_path = tmp_path / "workers" / task.task_id / "events.jsonl"
    events_path.write_text(
        '{"v":1,"type":"stage","task_id":"' + task.task_id + '","stage":"transcribing"}\n',
        "utf-8",
    )

    manager.tick()

    assert store.get_task(task.task_id).stage == "transcribing"


def test_bilibili_task_payload_keeps_only_stable_video_and_part_identifiers(tmp_path):
    factory = FakeProcessFactory()
    store = OrchestrationStore(tmp_path / "orchestration.sqlite3")
    delivery = tmp_path / "delivery"
    job = store.create_job(
        platform="bilibili",
        target="https://www.bilibili.com/video/BV18bLkztE7R",
        output_directory=str(delivery),
    )
    task = store.create_tasks(job.job_id, ["bilibili_BV18bLkztE7R_p07"])[0]
    manager = TaskManager(store=store, worker_root=tmp_path / "workers", process_factory=factory)

    manager.start(task.task_id)

    payload = json.loads(factory.payload_path.read_text("utf-8"))
    assert payload["source"] == {"platform": "bilibili", "bvid": "BV18bLkztE7R", "part": 7}
    assert payload["output_directory"] == str(delivery)
    assert "command" not in payload
    assert "cookie" not in json.dumps(payload).lower()
    assert str(delivery) not in json.dumps(store.list_events(task.task_id), default=str)


def test_tick_releases_a_completed_worker_slot_after_its_terminal_event(tmp_path):
    class FinishedProcess:
        pid = 4242

        def poll(self):
            return 0

    store = OrchestrationStore(tmp_path / "orchestration.sqlite3")
    job = store.create_job(platform="bilibili", target="https://example.invalid/creator")
    task = store.create_tasks(job.job_id, ["p01"])[0]
    manager = TaskManager(
        store=store,
        worker_root=tmp_path / "workers",
        process_factory=lambda task, payload: FinishedProcess(),
    )
    manager.start(task.task_id)
    (tmp_path / "workers" / task.task_id / "events.jsonl").write_text(
        '{"v":1,"type":"terminal","task_id":"' + task.task_id + '","status":"completed"}\n',
        "utf-8",
    )

    manager.tick()

    assert store.get_task(task.task_id).status == "completed"
    try:
        store.get_lease(task.task_id)
    except KeyError:
        pass
    else:
        raise AssertionError("completed worker lease was not released")


def test_manager_grants_only_one_asr_stage_to_two_worker_processes(tmp_path):
    factory = FakeProcessFactory()
    store = OrchestrationStore(tmp_path / "orchestration.sqlite3")
    job = store.create_job(platform="bilibili", target="https://example.invalid/creator")
    first, second = store.create_tasks(job.job_id, ["p01", "p02"])
    manager = TaskManager(store=store, worker_root=tmp_path / "workers", process_factory=factory)
    manager.start(first.task_id)
    manager.start(second.task_id)
    for task in (first, second):
        (tmp_path / "workers" / task.task_id / "resource-request.json").write_text(
            '{"stage":"transcribing"}', "utf-8"
        )

    manager.tick()

    grants = [
        (tmp_path / "workers" / task.task_id / "resource-grant.json").exists()
        for task in (first, second)
    ]
    assert grants.count(True) == 1


DEAD_PID = 4_000_000


def test_pid_probe_treats_a_missing_process_as_dead():
    """``psutil.NoSuchProcess`` is not an ``OSError``; the probe must not raise."""

    assert _default_pid_probe(DEAD_PID, "stale-marker") is False


def test_claim_ownership_reclaims_a_stale_owner_row(tmp_path):
    """An unclean shutdown leaves a dead PID behind; startup must take it over."""

    store = OrchestrationStore(tmp_path / "orchestration.sqlite3")
    assert store.claim_task_manager(
        owner_id="dead-owner", pid=DEAD_PID, start_marker="stale-marker"
    )

    manager = TaskManager(store=store, worker_root=tmp_path / "workers")
    manager.claim_ownership()

    owner = store.task_manager_owner()
    assert owner is not None and owner[0] != "dead-owner"
