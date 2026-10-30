"""Presentation-neutral creator enumeration and distillation orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Callable
from urllib.parse import quote

from src.application.commands import PreviewRequest, PreviewResult
from src.application.events import EventHub
from src.application.leases import JobLeaseManager
from src.distillation.state import JobState
from src.distillation.store import JobStateStore
from src.platforms.errors import PlatformAuthenticationError
from src.platforms.models import (
    EnumerationCheckpoint,
    ItemType,
    SourceAsset,
    SourceCreator,
    SourceItem,
)


@dataclass(frozen=True)
class SourceCreatorRequest:
    target: str
    platform: str = "auto"
    emit: tuple[str, ...] = ("episodes", "skill")
    rag_chunks: bool = False
    download_workers: int = 3
    asr_workers: int = 1
    llm_workers: int = 3
    max_active_items: int = 3
    retry_limit: int = 2
    resume: bool = True
    retry_failed: bool = False
    keep_media: bool = False
    headful: bool = False
    dry_run: bool = False
    llm_provider: str | None = None
    output_directory: Path | None = None


@dataclass(frozen=True)
class SourceRunResult:
    job_id: str
    platform: str
    creator_name: str
    total: int
    unsupported: int
    completed: int = 0
    failed: int = 0
    paused: bool = False
    dry_run: bool = False
    enumeration_complete: bool = False

    @property
    def exit_code(self) -> int:
        return 0 if self.failed == 0 and self.unsupported == 0 else 1


@dataclass(frozen=True)
class SourcePipelineContext:
    request: SourceCreatorRequest
    creator: SourceCreator
    items: tuple[SourceItem, ...]
    adapter: Any
    state_store: JobStateStore
    events: EventHub
    job_id: str


def source_item_to_dict(item: SourceItem) -> dict[str, Any]:
    return {
        "platform": item.platform,
        "item_id": item.item_id,
        "creator_id": item.creator_id,
        "item_type": item.item_type.value,
        "title": item.title,
        "description": item.description,
        "canonical_url": item.canonical_url,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "duration_seconds": item.duration_seconds,
        "statistics": dict(item.statistics),
        "cover_url": item.cover_url,
        "tags": list(item.tags),
        "assets": [
            {
                "kind": asset.kind,
                "url": asset.url,
                "index": asset.index,
                "expected_bytes": asset.expected_bytes,
            }
            for asset in item.assets
        ],
        "raw_metadata": json.loads(
            json.dumps(dict(item.raw_metadata), ensure_ascii=False, default=str)
        ),
    }


def source_item_from_dict(value: dict[str, Any]) -> SourceItem:
    return SourceItem(
        platform=value["platform"],
        item_id=value["item_id"],
        creator_id=value["creator_id"],
        item_type=ItemType(value["item_type"]),
        title=value.get("title", ""),
        description=value.get("description", ""),
        canonical_url=value["canonical_url"],
        published_at=(
            datetime.fromisoformat(value["published_at"])
            if value.get("published_at")
            else None
        ),
        duration_seconds=value.get("duration_seconds"),
        statistics=value.get("statistics", {}),
        cover_url=value.get("cover_url"),
        tags=tuple(value.get("tags", ())),
        assets=tuple(SourceAsset(**asset) for asset in value.get("assets", ())),
        raw_metadata=value.get("raw_metadata", {}),
    )


class SourceDistillationRunner:
    """Own the complete stateful source run behind one creator-scoped lease."""

    def __init__(
        self,
        *,
        config: Any,
        platform_manager: Any,
        events: EventHub | None = None,
        lease_manager: JobLeaseManager | None = None,
        pipeline_executor: Callable[[SourcePipelineContext], Any] | None = None,
        engine_executor: Callable[[Any, Any, EventHub], Any] | None = None,
        owner: str = "source-runner",
    ) -> None:
        self.config = config
        self.platform_manager = platform_manager
        self.events = events or EventHub()
        self.lease_manager = lease_manager or JobLeaseManager(
            config.data_dir / "jobs" / "leases"
        )
        self.pipeline_executor = pipeline_executor or self._execute_pipeline
        self.engine_executor = engine_executor or self._execute_engine
        self.owner = owner
        self._preview_job_ids: dict[str, str] = {}
        self._active_engines: dict[str, Any] = {}
        self._active_engines_lock = Lock()

    @staticmethod
    def _execute_engine(engine, request, events: EventHub):
        del events
        return asyncio.run(engine.run(request))

    @staticmethod
    def _job_id(creator: SourceCreator) -> str:
        digest = hashlib.sha256(creator.creator_id.encode()).hexdigest()[:16]
        return f"{creator.platform}-{digest}"

    def _state_store(self, creator: SourceCreator) -> JobStateStore:
        path = (
            self.config.data_dir
            / "jobs"
            / creator.platform
            / quote(creator.creator_id, safe="-_.")
            / "job_state.json"
        )
        return JobStateStore(path)

    def _resolve_creator(
        self,
        target_value: str,
        *,
        platform: str,
        headful: bool,
    ) -> tuple[Any, SourceCreator]:
        """Resolve one creator after checking the adapter's safe auth status."""

        adapter = self.platform_manager.select(target_value, platform=platform)
        session = getattr(adapter, "session", None)
        if session is not None:
            session.acquisition_headless = not headful
        auth = adapter.auth_status()
        # ``not_required`` is an explicit adapter claim (e.g. YouTube public pages):
        # it must not be treated as a missing credential.
        if auth.status not in {"configured", "authenticated", "ready", "not_required"}:
            raise PlatformAuthenticationError(
                auth.message
                or f"{adapter.descriptor.name} authentication is required"
            )
        target = adapter.resolve(target_value)
        return adapter, adapter.get_creator(target)

    def preview(self, request: PreviewRequest) -> PreviewResult:
        """Enumerate a creator without writing state or starting the pipeline."""

        adapter, creator = self._resolve_creator(
            request.target,
            platform=request.platform,
            headful=False,
        )
        items: list[SourceItem] = []
        for page in adapter.iter_items(creator, checkpoint=None):
            items.extend(page.items)
        unsupported = sum(item.item_type is not ItemType.VIDEO for item in items)
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "platform": creator.platform,
                    "creator_id": creator.creator_id,
                    "target": creator.canonical_url,
                    "outputs": request.outputs,
                    "rag_chunks": request.rag_chunks,
                    "items": [
                        (item.source_id, item.item_type.value)
                        for item in items
                    ],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self._preview_job_ids[fingerprint] = self._job_id(creator)
        return PreviewResult(
            fingerprint=fingerprint,
            platform=creator.platform,
            creator_id=creator.creator_id,
            creator_name=creator.display_name,
            total_items=len(items),
            processable_items=len(items) - unsupported,
            unsupported_items=unsupported,
            auth_status=adapter.auth_status().status,
        )

    def job_id_for_preview(self, preview: PreviewResult) -> str:
        """Return the state-store job ID associated with a local preview."""

        try:
            return self._preview_job_ids[preview.fingerprint]
        except KeyError as exc:
            raise RuntimeError("The requested preview is no longer available") from exc

    def request_pause(self, job_id: str) -> bool:
        """Ask an active local engine to stop after its current safe unit of work."""

        with self._active_engines_lock:
            engine = self._active_engines.get(job_id)
        if engine is None:
            return False
        engine.request_pause()
        return True

    @staticmethod
    def _checkpoint(state: JobState | None) -> EnumerationCheckpoint | None:
        if state is None or not state.enumeration_checkpoint:
            return None
        value = state.enumeration_checkpoint
        return EnumerationCheckpoint(
            cursor=value.get("cursor"),
            seen_ids=frozenset(value.get("seen_ids", ())),
            complete=bool(value.get("complete", False)),
            expected_count=value.get("expected_count"),
        )

    @staticmethod
    def _enumerating_state(
        state: JobState,
        creator: SourceCreator,
        checkpoint: EnumerationCheckpoint | None,
        catalog: dict[str, Any],
    ) -> JobState:
        checkpoint_value = (
            {
                "cursor": checkpoint.cursor,
                "seen_ids": sorted(checkpoint.seen_ids),
                "complete": checkpoint.complete,
                "expected_count": checkpoint.expected_count,
            }
            if checkpoint is not None
            else {}
        )
        return replace(
            state,
            status="enumerating",
            creator={
                "platform": creator.platform,
                "creator_id": creator.creator_id,
                "display_name": creator.display_name,
                "canonical_url": creator.canonical_url,
            },
            enumeration_checkpoint=checkpoint_value,
            catalog=catalog,
        )

    def run(self, request: SourceCreatorRequest) -> SourceRunResult:
        adapter, creator = self._resolve_creator(
            request.target,
            platform=request.platform,
            headful=request.headful,
        )
        job_id = self._job_id(creator)
        store = self._state_store(creator)

        with self.lease_manager.acquire(job_id, owner=self.owner):
            existing = (
                store.load()
                if request.resume and store.path.exists()
                else None
            )
            checkpoint = self._checkpoint(existing)
            catalog = dict(existing.catalog) if existing else {}
            latest_checkpoint = checkpoint
            persisted = existing

            if not request.dry_run:
                base = existing or JobState(job_id=job_id)
                persisted = store.save(
                    self._enumerating_state(base, creator, checkpoint, catalog),
                    expected_revision=existing.revision if existing else None,
                )

            for page in adapter.iter_items(creator, checkpoint=checkpoint):
                latest_checkpoint = page.checkpoint
                for item in page.items:
                    catalog[item.source_id] = source_item_to_dict(item)
                if not request.dry_run:
                    persisted = store.save(
                        self._enumerating_state(
                            persisted,
                            creator,
                            page.checkpoint,
                            catalog,
                        ),
                        expected_revision=persisted.revision,
                    )

            items = tuple(source_item_from_dict(value) for value in catalog.values())
            unsupported = sum(item.item_type is not ItemType.VIDEO for item in items)
            complete = bool(latest_checkpoint and latest_checkpoint.complete)
            if request.dry_run:
                return SourceRunResult(
                    job_id=job_id,
                    platform=creator.platform,
                    creator_name=creator.display_name,
                    total=len(items),
                    unsupported=unsupported,
                    dry_run=True,
                    enumeration_complete=complete,
                )

            context = SourcePipelineContext(
                request=request,
                creator=creator,
                items=items,
                adapter=adapter,
                state_store=store,
                events=self.events,
                job_id=job_id,
            )
            result = self.pipeline_executor(context)
            return SourceRunResult(
                job_id=job_id,
                platform=creator.platform,
                creator_name=creator.display_name,
                total=len(items),
                unsupported=result.unsupported,
                completed=result.completed,
                failed=result.failed,
                paused=result.paused,
                enumeration_complete=complete,
            )

    def _execute_pipeline(self, context: SourcePipelineContext):
        from src.asr.funasr_engine import FunASREngine
        from src.clean.text_processor import TextProcessor, create_llm_client
        from src.distillation.engine import DistillationEngine
        from src.distillation.processors import VideoContentProcessor
        from src.distillation.request import DistillationRequest
        from src.generate.skill_generator import SkillGenerator
        from src.model.knowledge_extractor import KnowledgeExtractor
        from src.outputs import (
            EpisodeMarkdownTarget,
            OutputManager,
            RagTarget,
            SkillTarget,
        )

        request = context.request
        provider = request.llm_provider or self.config.llm_provider
        llm_client = create_llm_client(provider, self.config)
        if llm_client is None:
            raise RuntimeError("The selected output requires a configured LLM provider")
        knowledge_extractor = KnowledgeExtractor(llm_client)

        def processor_factory():
            return VideoContentProcessor(
                output_root=self.config.data_dir,
                asr=FunASREngine(
                    model_name=self.config.funasr.model,
                    vad_model=self.config.funasr.vad_model,
                    punc_model=self.config.funasr.punc_model,
                    model_dir=self.config.model_cache_dir,
                ),
                cleaner=TextProcessor(llm_client=llm_client),
                knowledge_extractor=knowledge_extractor,
            )

        output_directory = request.output_directory or self.config.output_dir
        targets = []
        if "episodes" in request.emit:
            targets.append(EpisodeMarkdownTarget(output_directory))
        if "skill" in request.emit:
            targets.append(
                SkillTarget(
                    output_directory,
                    merge_fn=knowledge_extractor.merge_knowledge,
                    generator=SkillGenerator(template_dir="templates"),
                )
            )
        if request.rag_chunks:
            targets.append(RagTarget(output_directory))

        engine = DistillationEngine(
            adapter=context.adapter,
            processor_factory=processor_factory,
            state_store=context.state_store,
            output_manager=OutputManager(targets),
            events=context.events,
        )
        engine_request = DistillationRequest(
            job_id=context.job_id,
            creator=context.creator,
            items=context.items,
            output_root=output_directory,
            download_workers=request.download_workers,
            asr_workers=request.asr_workers,
            llm_workers=request.llm_workers,
            max_active_items=request.max_active_items,
            retry_limit=request.retry_limit,
            cleanup_media=not request.keep_media,
            resume=request.resume,
            retry_failed=request.retry_failed,
        )
        with self._active_engines_lock:
            self._active_engines[context.job_id] = engine
        try:
            return self.engine_executor(engine, engine_request, context.events)
        finally:
            with self._active_engines_lock:
                self._active_engines.pop(context.job_id, None)
