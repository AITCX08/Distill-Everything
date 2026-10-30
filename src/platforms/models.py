"""Immutable data exchanged between platform adapters and the pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


_PLATFORM_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _validate_platform(value: str) -> None:
    if not _PLATFORM_RE.fullmatch(value):
        raise ValueError(f"Invalid platform identifier: {value!r}")


def _validate_stable_id(field_name: str, value: str) -> None:
    if not value or value.strip() != value or any(char in value for char in ("/", "\\", "\x00")):
        raise ValueError(f"Invalid {field_name}: {value!r}")


class ItemType(str, Enum):
    """Content shapes understood by the shared pipeline."""

    VIDEO = "video"
    GALLERY = "gallery"
    ARTICLE = "article"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SourceAsset:
    """A remotely hosted asset belonging to one source item."""

    kind: str
    url: str
    index: int = 0
    expected_bytes: int | None = None

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValueError("Asset kind cannot be empty")
        if not self.url:
            raise ValueError("Asset URL cannot be empty")
        if self.index < 0:
            raise ValueError("Asset index cannot be negative")
        if self.expected_bytes is not None and self.expected_bytes < 0:
            raise ValueError("Expected bytes cannot be negative")


@dataclass(frozen=True)
class SourceCreator:
    """Platform-neutral creator identity."""

    platform: str
    creator_id: str
    display_name: str
    canonical_url: str
    avatar_url: str | None = None
    raw_metadata: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _validate_platform(self.platform)
        _validate_stable_id("creator_id", self.creator_id)
        if not self.display_name:
            raise ValueError("Creator display name cannot be empty")
        if not self.canonical_url:
            raise ValueError("Creator canonical URL cannot be empty")
        object.__setattr__(self, "raw_metadata", MappingProxyType(dict(self.raw_metadata)))


_CREATOR_RECORD_FIELDS = ("platform", "creator_id", "display_name", "canonical_url")


def creator_record(creator: SourceCreator) -> dict[str, str]:
    """Serialize creator identity for persisted job state.

    ``raw_metadata`` is a ``MappingProxyType`` and therefore neither deep-copyable
    nor picklable, so ``dataclasses.asdict`` raises ``TypeError: cannot pickle
    'mappingproxy' object``. It is also adapter-internal data that must not land in
    ``job_state.json``. Only the stable public identity is persisted.
    """

    return {name: getattr(creator, name) for name in _CREATOR_RECORD_FIELDS}


@dataclass(frozen=True)
class SourceItem:
    """One platform item normalized for downstream processing."""

    platform: str
    item_id: str
    creator_id: str
    item_type: ItemType
    title: str
    description: str
    canonical_url: str
    published_at: datetime | None = None
    duration_seconds: float | None = None
    statistics: Mapping[str, int] = field(default_factory=dict)
    cover_url: str | None = None
    tags: tuple[str, ...] = ()
    assets: tuple[SourceAsset, ...] = ()
    raw_metadata: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _validate_platform(self.platform)
        _validate_stable_id("item_id", self.item_id)
        _validate_stable_id("creator_id", self.creator_id)
        if not self.canonical_url:
            raise ValueError("Item canonical URL cannot be empty")
        if self.duration_seconds is not None and self.duration_seconds < 0:
            raise ValueError("Duration cannot be negative")
        object.__setattr__(self, "statistics", MappingProxyType(dict(self.statistics)))
        object.__setattr__(self, "tags", tuple(self.tags))
        object.__setattr__(self, "assets", tuple(self.assets))
        object.__setattr__(self, "raw_metadata", MappingProxyType(dict(self.raw_metadata)))

    @property
    def source_id(self) -> str:
        """Return the cross-platform stable identifier."""

        return f"{self.platform}_{self.item_id}"


@dataclass(frozen=True)
class PlatformDescriptor:
    """Capabilities shown by CLI and Dashboard platform listings."""

    name: str
    url_patterns: tuple[str, ...]
    item_types: frozenset[ItemType]
    requires_browser: bool = False
    requires_auth: bool = False
    commands: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_platform(self.name)
        object.__setattr__(self, "url_patterns", tuple(self.url_patterns))
        object.__setattr__(self, "item_types", frozenset(self.item_types))
        object.__setattr__(self, "commands", tuple(self.commands))


@dataclass(frozen=True)
class AuthStatus:
    """Authentication state without any credential material."""

    status: str
    message: str = ""


@dataclass(frozen=True)
class ResolvedTarget:
    """A user target resolved to one platform creator."""

    platform: str
    creator_id: str
    canonical_url: str
    original_target: str
    target_type: str = "creator"
    metadata: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.platform:
            raise ValueError("Resolved target platform cannot be empty")
        if not self.creator_id:
            raise ValueError("Resolved target creator ID cannot be empty")
        if not self.canonical_url:
            raise ValueError("Resolved target canonical URL cannot be empty")
        if self.target_type not in {"creator", "video"}:
            raise ValueError(f"Unsupported resolved target type: {self.target_type!r}")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class EnumerationCheckpoint:
    """Serializable cursor used to resume platform enumeration."""

    cursor: str | None = None
    seen_ids: frozenset[str] = frozenset()
    complete: bool = False
    expected_count: int | None = None


@dataclass(frozen=True)
class EnumerationPage:
    """One normalized page of creator items and its next checkpoint."""

    items: tuple[SourceItem, ...]
    checkpoint: EnumerationCheckpoint
    has_more: bool


@dataclass(frozen=True)
class DownloadedAssets:
    """Local assets produced by an adapter download."""

    video_path: Path | None = None
    audio_path: Path | None = None
    image_paths: tuple[Path, ...] = ()
    temporary_paths: tuple[Path, ...] = ()
