import asyncio
from types import SimpleNamespace

from src.application.events import EventHub
from src.dashboard.sse import event_stream
from src.orchestration.store import OrchestrationStore
from src.distillation.progress import ProgressCounts, ProgressSnapshot


def test_event_stream_replays_last_event_id_and_uses_named_events():
    hub = EventHub()
    hub.publish("job.updated", {"job_id": "job-1", "revision": 2})
    service = SimpleNamespace(events=hub, list_jobs=lambda: ())

    async def next_message():
        stream = event_stream(service, last_event_id=0, job_id="job-1", heartbeat_seconds=0.01)
        return await anext(stream)

    message = asyncio.run(next_message())

    assert "event: job.updated" in message
    assert "id: 1" in message


def test_sse_serializes_real_progress_snapshot_payloads():
    hub = EventHub()
    snapshot = ProgressSnapshot(
        job_id="job-1", revision=2, overall_progress=0.5, coverage=0.25,
        active_items=(), counts=ProgressCounts(total=4, active=1),
        eta_total_seconds=60, eta_active_slowest_seconds=30, provisional_eta=False,
    )
    hub.publish("progress.snapshot", {"job_id": "job-1", "snapshot": snapshot})
    service = SimpleNamespace(events=hub, list_jobs=lambda: ())

    async def next_message():
        return await anext(event_stream(service, last_event_id=0, job_id="job-1", heartbeat_seconds=0.01))

    message = asyncio.run(next_message())
    assert '"overall_progress": 0.5' in message


def test_initial_sse_snapshot_includes_latest_progress_for_reconnecting_browser():
    hub = EventHub()
    progress = ProgressSnapshot(
        job_id="job-1", revision=2, overall_progress=0.5, coverage=0.25,
        active_items=(), counts=ProgressCounts(total=4, active=1),
        eta_total_seconds=60, eta_active_slowest_seconds=30, provisional_eta=False,
    )
    hub.publish("progress.snapshot", {"job_id": "job-1", "snapshot": progress})
    service = SimpleNamespace(events=hub, list_jobs=lambda: ())

    async def next_message():
        return await anext(event_stream(service, last_event_id=None, job_id="job-1"))

    message = asyncio.run(next_message())

    assert "event: snapshot" in message
    assert '"overall_progress": 0.5' in message


def test_initial_sse_snapshot_includes_existing_trace_lines_for_reconnecting_browser():
    hub = EventHub()
    progress = ProgressSnapshot(
        job_id="job-1", revision=2, overall_progress=0.5, coverage=0.25,
        active_items=(), counts=ProgressCounts(total=4, active=0),
        eta_total_seconds=None, eta_active_slowest_seconds=None, provisional_eta=True,
    )
    hub.publish("trace.appended", {"job_id": "job-1", "line": "Paused at checkpoint."})
    hub.publish("progress.snapshot", {"job_id": "job-1", "snapshot": progress})
    service = SimpleNamespace(events=hub, list_jobs=lambda: ())

    async def next_message():
        return await anext(event_stream(service, last_event_id=None, job_id="job-1"))

    message = asyncio.run(next_message())

    assert '"traces": {"job-1": ["Paused at checkpoint."]}' in message


def test_initial_sse_snapshot_contains_worker_tasks_and_traces(tmp_path):
    hub = EventHub()
    store = OrchestrationStore(tmp_path / "orchestration.sqlite3")
    job = store.create_job(platform="bilibili", target="https://example.invalid/creator")
    task = store.create_tasks(job.job_id, ["p01"])[0]
    store.transition_task(task.task_id, task.revision, status="running", stage="downloading")
    store.append_event(task.task_id, kind="log", payload={"line": "worker ready"})
    service = SimpleNamespace(events=hub, list_jobs=lambda: ())
    manager = SimpleNamespace(store=store)

    async def next_message():
        return await anext(event_stream(service, last_event_id=None, job_id=None, task_manager=manager))

    message = asyncio.run(next_message())

    assert '"tasks"' in message
    assert '"traces"' in message
    assert "worker ready" in message


def test_initial_sse_snapshot_exposes_safe_task_metadata_without_output_directory(tmp_path):
    hub = EventHub()
    store = OrchestrationStore(tmp_path / "orchestration.sqlite3")
    job = store.create_job(
        platform="bilibili",
        target="https://example.invalid/creator",
        output_directory=str(tmp_path / "private-delivery"),
    )
    store.create_tasks(job.job_id, ["bilibili_BV18bLkztE7R_p02"])
    service = SimpleNamespace(events=hub, list_jobs=lambda: ())
    manager = SimpleNamespace(store=store)

    async def next_message():
        return await anext(event_stream(service, last_event_id=None, job_id=None, task_manager=manager))

    message = asyncio.run(next_message())

    assert '"display_title": "第 2 集"' in message
    assert '"part_number": 2' in message
    assert '"delivery_state": "pending"' in message
    assert "private-delivery" not in message


def test_worker_sse_snapshot_enriches_task_with_series_title_and_completion_time(tmp_path):
    hub = EventHub()
    store = OrchestrationStore(tmp_path / "orchestration.sqlite3")
    job = store.create_job(platform="bilibili", target="https://example.invalid/creator")
    task = store.create_tasks(job.job_id, ["bilibili_BV18bLkztE7R_p01"])[0]
    store.transition_task(task.task_id, task.revision, status="completed", stage="completed")
    source_id = "bilibili_BV18bLkztE7R_p01"
    source_state = SimpleNamespace(
        catalog={source_id: {"title": "四柱命卦 1"}},
        items={source_id: SimpleNamespace(completed_at="2026-08-10T16:44:12+00:00")},
    )
    service = SimpleNamespace(
        events=hub,
        list_jobs=lambda: (),
        queries=SimpleNamespace(list=lambda: (source_state,)),
    )
    manager = SimpleNamespace(store=store)

    async def next_message():
        return await anext(event_stream(service, last_event_id=None, job_id=None, task_manager=manager))

    message = asyncio.run(next_message())

    assert '"display_title": "四柱命卦 1"' in message
    assert '"completed_at": "2026-08-10T16:44:12+00:00"' in message


def test_initial_sse_snapshot_projects_latest_worker_transfer_to_its_task(tmp_path):
    hub = EventHub()
    store = OrchestrationStore(tmp_path / "orchestration.sqlite3")
    job = store.create_job(platform="bilibili", target="https://example.invalid/creator")
    task = store.create_tasks(job.job_id, ["p01"])[0]
    store.transition_task(task.task_id, task.revision, status="running", stage="downloading")
    store.append_event(
        task.task_id,
        kind="transfer",
        payload={"completed_bytes": 25, "total_bytes": 100, "bytes_per_second": 5},
    )
    service = SimpleNamespace(events=hub, list_jobs=lambda: ())
    manager = SimpleNamespace(store=store)

    async def next_message():
        return await anext(event_stream(service, last_event_id=None, job_id=None, task_manager=manager))

    message = asyncio.run(next_message())

    assert '"completed_bytes": 25' in message
    assert '"bytes_per_second": 5' in message


def test_worker_sse_stream_refreshes_task_snapshot_after_manager_state_changes(tmp_path):
    hub = EventHub()
    store = OrchestrationStore(tmp_path / "orchestration.sqlite3")
    job = store.create_job(platform="bilibili", target="https://example.invalid/creator")
    task = store.create_tasks(job.job_id, ["p01"])[0]
    service = SimpleNamespace(events=hub, list_jobs=lambda: ())
    manager = SimpleNamespace(store=store)

    async def changed_snapshot():
        stream = event_stream(
            service,
            last_event_id=None,
            job_id=None,
            heartbeat_seconds=0.01,
            task_manager=manager,
        )
        await anext(stream)
        store.transition_task(task.task_id, task.revision, status="running", stage="downloading")
        return await anext(stream)

    message = asyncio.run(changed_snapshot())

    assert '"status": "running"' in message
    assert '"stage": "downloading"' in message


def test_worker_sse_snapshot_projects_a_redacted_terminal_failure_reason(tmp_path):
    hub = EventHub()
    store = OrchestrationStore(tmp_path / "orchestration.sqlite3")
    job = store.create_job(platform="bilibili", target="https://example.invalid/creator")
    task = store.create_tasks(job.job_id, ["p01"])[0]
    task = store.transition_task(task.task_id, task.revision, status="failed", stage="downloading")
    store.append_event(task.task_id, kind="terminal", payload={"status": "failed", "reason": "Dashboard login required"})
    service = SimpleNamespace(events=hub, list_jobs=lambda: ())
    manager = SimpleNamespace(store=store)

    async def next_message():
        return await anext(event_stream(service, last_event_id=None, job_id=None, task_manager=manager))

    message = asyncio.run(next_message())

    assert '"error": "Dashboard login required"' in message
