
from src.asr.funasr_engine import TranscriptResult, TranscriptSegment
from src.distillation.processors import (
    UnsupportedProcessor,
    VideoContentProcessor,
)
from src.distillation.state import ProcessingStatus
from src.platforms.models import DownloadedAssets, ItemType, SourceItem


def make_item(item_type=ItemType.VIDEO):
    return SourceItem(
        platform="douyin",
        item_id="123",
        creator_id="creator-1",
        item_type=item_type,
        title="Video",
        description="Description",
        canonical_url="https://www.douyin.com/video/123",
    )


class FakeAudioExtractor:
    def extract(self, video_path, destination):
        destination.write_bytes(b"wav")
        return destination


class FakeAsr:
    def transcribe(self, audio_path, source_id=""):
        return TranscriptResult(
            source_id=source_id,
            audio_path=str(audio_path),
            full_text="Useful transcript",
            segments=[TranscriptSegment("s1", "Useful transcript", 0, 2)],
            model_name="fake",
        )


class FakeCleaner:
    def process_transcript(self, transcript):
        return {
            "source_id": transcript["source_id"],
            "bvid": transcript["bvid"],
            "title": transcript["title"],
            "full_text": transcript["full_text"],
            "topics": [{"id": "t1", "title": "Topic", "content": transcript["full_text"], "tags": []}],
            "segments": transcript["segments"],
            "metadata": transcript["metadata"],
        }


class FakeKnowledgeExtractor:
    def extract_from_video(self, cleaned):
        from src.model.knowledge_extractor import VideoKnowledge

        return VideoKnowledge(
            source_id=cleaned["source_id"],
            title=cleaned["title"],
            summary="Summary",
            core_views=["View"],
        )


def test_video_processor_keeps_stages_separate_and_platform_neutral(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    processor = VideoContentProcessor(
        output_root=tmp_path / "artifacts",
        asr=FakeAsr(),
        cleaner=FakeCleaner(),
        knowledge_extractor=FakeKnowledgeExtractor(),
        audio_extractor=FakeAudioExtractor(),
    )
    item = make_item()

    prepared = processor.prepare(item, DownloadedAssets(video_path=video))
    transcript = processor.transcribe(prepared)
    enriched = processor.enrich(transcript)

    assert prepared.audio_path.exists()
    assert transcript.path.name == "douyin_123.json"
    assert transcript.document["source_id"] == "douyin_123"
    assert enriched.cleaned_path.exists()
    assert enriched.knowledge_path.exists()
    assert enriched.knowledge.source_id == "douyin_123"


def test_gallery_is_explicitly_unsupported():
    result = UnsupportedProcessor().process(make_item(ItemType.GALLERY))

    assert result.status is ProcessingStatus.UNSUPPORTED
    assert result.error_code == "unsupported_note"
    assert "OCR" in result.reason
