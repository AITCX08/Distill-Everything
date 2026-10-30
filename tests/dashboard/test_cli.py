from click.testing import CliRunner
from types import SimpleNamespace

import main


def test_source_platforms_lists_every_registered_adapter(monkeypatch):
    """The CLI must enumerate the registry, so a new adapter needs no CLI edit."""

    class Descriptor:
        def __init__(self, name):
            self.name = name
            self.item_types = ()
            self.requires_browser = False
            self.requires_auth = False

    class Manager:
        def list_descriptors(self):
            return (Descriptor("bilibili"), Descriptor("douyin"), Descriptor("youtube"))

    monkeypatch.setattr(main, "build_platform_manager", lambda config: Manager())
    monkeypatch.setattr(main, "load_config", lambda: object(), raising=False)

    result = CliRunner().invoke(main.cli, ["source", "platforms"])

    assert result.exit_code == 0, result.output
    assert "youtube" in result.output


def test_source_status_rejects_unknown_platform_with_the_available_list(monkeypatch):
    class Manager:
        def get(self, platform):
            from src.platforms.errors import UnknownPlatformError

            raise UnknownPlatformError(platform)

        def list_descriptors(self):
            return (SimpleNamespace(name="bilibili"), SimpleNamespace(name="youtube"))

    monkeypatch.setattr(main, "build_platform_manager", lambda config: Manager())
    monkeypatch.setattr(main, "load_config", lambda: object(), raising=False)

    result = CliRunner().invoke(main.cli, ["source", "status", "xiaohongshu"])

    assert result.exit_code != 0
    assert "bilibili, youtube" in result.output


def test_dashboard_cli_uses_no_open_and_exposes_no_host_option(monkeypatch):
    service = object()
    captured = []
    monkeypatch.setattr(main, "build_dashboard_service", lambda config: service)
    monkeypatch.setattr(main, "run_dashboard", lambda value, port, open_browser: captured.append((value, port, open_browser)))

    result = CliRunner().invoke(main.cli, ["dashboard", "--port", "8765", "--no-open"])
    help_result = CliRunner().invoke(main.cli, ["dashboard", "--help"])

    assert result.exit_code == 0, result.output
    assert captured == [(service, 8765, False)]
    assert help_result.exit_code == 0
    assert "--host" not in help_result.output


def test_dashboard_service_uses_runner_preview_without_cli_live_renderer(monkeypatch, tmp_path):
    captured = {}

    class Runner:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.platform_manager = kwargs["platform_manager"]

        def preview(self, request):
            raise AssertionError("not invoked during service construction")

    monkeypatch.setattr(main, "SourceDistillationRunner", Runner)
    monkeypatch.setattr(main, "build_source_event_hub", lambda config: object())
    monkeypatch.setattr(main, "build_platform_manager", lambda config: object())

    service = main.build_dashboard_service(SimpleNamespace(data_dir=tmp_path))

    assert service.source_runner.platform_manager is captured["platform_manager"]
    assert service.commands.previewer == service.source_runner.preview
    assert "engine_executor" not in captured
