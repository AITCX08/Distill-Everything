"""Composable distillation output targets.

Exports are resolved lazily (PEP 562). Importing a submodule such as
``src.outputs.base`` must not drag in the whole package graph: ``base`` imports
``src.platforms.models``, which imports ``src.platforms.base``, which imports
``src.distillation.progress``. Eager re-exports here would close that loop and
raise ``ImportError: cannot import name 'ArtifactKind' from partially
initialized module``.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, str] = {
    "ArtifactKind": "src.outputs.base",
    "CorpusOutputContext": "src.outputs.base",
    "ItemOutputContext": "src.outputs.base",
    "OutputReceipt": "src.outputs.base",
    "OutputTarget": "src.outputs.base",
    "EpisodeMarkdownTarget": "src.outputs.episodes",
    "OutputManager": "src.outputs.manager",
    "RagTarget": "src.outputs.rag",
    "OutputRegistry": "src.outputs.registry",
    "SkillTarget": "src.outputs.skill",
    "corpus_fingerprint": "src.outputs.skill",
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
