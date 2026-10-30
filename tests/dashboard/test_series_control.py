import os
from unittest.mock import Mock

from src.dashboard.series_control import SeriesController, _worker_is_alive
from src.series.runtime import SeriesRuntimeStore


def test_pause_records_a_cooperative_request(tmp_path):
    state = tmp_path / "series" / "BV1test" / "state.json"
    state.parent.mkdir(parents=True)
    state.write_text("{}", encoding="utf-8")
    runtime = SeriesRuntimeStore(tmp_path / "series" / "BV1test")
    runtime.update(status="running")

    paused = SeriesController(tmp_path, launcher=Mock()).pause("BV1test")

    assert paused["status"] == "pause_requested"


def test_resume_launches_one_checkpointed_worker(tmp_path):
    state = tmp_path / "series" / "BV1test" / "state.json"
    state.parent.mkdir(parents=True)
    state.write_text("{}", encoding="utf-8")
    runtime = SeriesRuntimeStore(tmp_path / "series" / "BV1test")
    runtime.update(status="paused")
    launcher = Mock()

    resumed = SeriesController(tmp_path, launcher=launcher).resume("BV1test")

    assert resumed["status"] == "running"
    launcher.assert_called_once_with("BV1test")


def test_resume_relaunches_when_running_state_has_no_live_worker(tmp_path):
    state = tmp_path / "series" / "BV1test" / "state.json"
    state.parent.mkdir(parents=True)
    state.write_text("{}", encoding="utf-8")
    runtime = SeriesRuntimeStore(tmp_path / "series" / "BV1test")
    runtime.update(status="running", worker_pid=12345)
    launcher = Mock(return_value=67890)
    worker_is_alive = Mock(return_value=False)

    resumed = SeriesController(
        tmp_path,
        launcher=launcher,
        worker_is_alive=worker_is_alive,
    ).resume("BV1test")

    assert resumed["status"] == "running"
    assert resumed["worker_pid"] == 67890
    worker_is_alive.assert_called_once_with(12345)
    launcher.assert_called_once_with("BV1test")


def test_reconcile_marks_an_orphaned_running_series_as_paused(tmp_path):
    state = tmp_path / "series" / "BV1test" / "state.json"
    state.parent.mkdir(parents=True)
    state.write_text("{}", encoding="utf-8")
    runtime = SeriesRuntimeStore(tmp_path / "series" / "BV1test")
    runtime.update(status="running", worker_pid=12345)

    reconciled = SeriesController(
        tmp_path,
        launcher=Mock(),
        worker_is_alive=Mock(return_value=False),
    ).reconcile()

    assert reconciled == 1
    current = runtime.load()
    assert current["status"] == "paused"
    assert current["last_error"] == "执行器已停止，可继续任务。"


def test_worker_liveness_treats_a_failing_probe_as_not_running(monkeypatch):
    """A probe that raises (Windows invalid handle, exotic OSError) means "dead"."""

    def invalid_handle(_pid):
        raise SystemError("invalid handle")

    monkeypatch.setattr("src.dashboard.series_control.process_is_alive", invalid_handle)

    assert _worker_is_alive(99999) is False


def test_worker_liveness_recognizes_the_current_process():
    assert _worker_is_alive(os.getpid()) is True
