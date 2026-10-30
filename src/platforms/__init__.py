"""Platform-neutral source adapter contracts.

Exports are resolved lazily (PEP 562). ``src.platforms.models`` is imported by
``src.outputs.base`` and ``src.distillation.request``; an eager package init here
would pull ``PlatformManager`` and the adapter chain into every one of those
imports and reintroduce the circular import described in ``src.outputs``.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, str] = {
    "PlatformAdapter": "src.platforms.base",
    "PlatformManager": "src.platforms.manager",
    "PlatformRegistry": "src.platforms.registry",
    "AuthStatus": "src.platforms.models",
    "DownloadedAssets": "src.platforms.models",
    "EnumerationCheckpoint": "src.platforms.models",
    "EnumerationPage": "src.platforms.models",
    "ItemType": "src.platforms.models",
    "PlatformDescriptor": "src.platforms.models",
    "ResolvedTarget": "src.platforms.models",
    "SourceAsset": "src.platforms.models",
    "SourceCreator": "src.platforms.models",
    "SourceItem": "src.platforms.models",
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
