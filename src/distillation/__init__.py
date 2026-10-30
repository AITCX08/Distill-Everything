"""Recoverable distillation state and artifact primitives.

Exports are resolved lazily (PEP 562). ``engine`` depends on
``src.outputs.base`` and ``src.platforms.models``; eager re-exports would make
``import src.distillation.progress`` pull the whole pipeline and close the
``outputs -> platforms -> distillation -> outputs`` import cycle.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, str] = {
    "ArtifactRecord": "src.distillation.artifacts",
    "sha256_file": "src.distillation.artifacts",
    "verify_artifact": "src.distillation.artifacts",
    "EtaEstimate": "src.distillation.eta",
    "EtaEstimator": "src.distillation.eta",
    "DistillationEngine": "src.distillation.engine",
    "JobResult": "src.distillation.engine",
    "TransferProgress": "src.distillation.progress",
    "ItemProgress": "src.distillation.progress",
    "ProgressSnapshot": "src.distillation.progress",
    "ProgressTracker": "src.distillation.progress",
    "DistillationRequest": "src.distillation.request",
    "ItemState": "src.distillation.state",
    "JobState": "src.distillation.state",
    "ProcessingStatus": "src.distillation.state",
    "recover_item": "src.distillation.state",
    "JobStateStore": "src.distillation.store",
    "atomic_write_bytes": "src.distillation.store",
    "atomic_write_json": "src.distillation.store",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_path = _EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_path), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
