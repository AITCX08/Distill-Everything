"""
配置管理模块

从 .env 文件和环境变量加载项目配置，使用 Pydantic 进行校验。
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent

# 加载 .env 文件
load_dotenv(PROJECT_ROOT / ".env")

# 支持的 LLM 提供商列表
LLM_PROVIDERS = ("claude", "openai", "qwen", "deepseek", "ollama")


class BilibiliConfig(BaseModel):
    """B站相关配置"""
    sessdata: str = Field(default="", description="B站 SESSDATA Cookie")
    bili_jct: str = Field(default="", description="B站 bili_jct Cookie")
    buvid3: str = Field(default="", description="B站 buvid3 Cookie")


class AnthropicConfig(BaseModel):
    """Claude API 配置"""
    api_key: str = Field(default="", description="Anthropic API Key")
    model: str = Field(default="claude-sonnet-4-20250514", description="使用的模型名称")


class OpenAIConfig(BaseModel):
    """OpenAI API 配置"""
    api_key: str = Field(default="", description="OpenAI API Key")
    base_url: str = Field(default="https://api.openai.com/v1", description="API Base URL")
    model: str = Field(default="gpt-4o", description="使用的模型名称")


class QwenConfig(BaseModel):
    """通义千问 Qwen API 配置（阿里云 DashScope，OpenAI 兼容接口）"""
    api_key: str = Field(default="", description="DashScope API Key")
    base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        description="DashScope OpenAI 兼容接口地址",
    )
    model: str = Field(default="qwen3-235b-a22b", description="Qwen 模型名称")


class DeepSeekConfig(BaseModel):
    """DeepSeek API 配置（OpenAI 兼容接口）"""
    api_key: str = Field(default="", description="DeepSeek API Key")
    base_url: str = Field(
        default="https://api.deepseek.com",
        description="DeepSeek API 地址",
    )
    model: str = Field(default="deepseek-reasoner", description="DeepSeek 模型名称")


class OllamaConfig(BaseModel):
    """Ollama 本地模型配置"""
    base_url: str = Field(default="http://localhost:11434/v1", description="Ollama API 地址")
    model: str = Field(default="qwen2.5:3b", description="本地模型名称")


class FunASRConfig(BaseModel):
    """FunASR 语音识别配置"""
    model: str = Field(default="paraformer-zh", description="ASR模型名称")
    vad_model: str = Field(default="fsmn-vad", description="VAD模型名称")
    punc_model: str = Field(default="ct-punc", description="标点恢复模型名称")


class FeishuConfig(BaseModel):
    """飞书开放平台自建应用配置"""
    app_id: str = Field(default="", description="飞书自建应用 App ID")
    app_secret: str = Field(default="", description="飞书自建应用 App Secret")

class DouyinConfig(BaseModel):
    """抖音持久浏览器会话配置。"""
    profile_dir: Path = Field(
        default=PROJECT_ROOT / "data" / "browser" / "douyin",
        description="Playwright persistent profile directory",
    )
    login_timeout: int = Field(default=180, ge=30)
    source: str = Field(default="auto", description="auto | sidecar | browser")
    sidecar_url: str = Field(default="http://127.0.0.1:8000")
    sidecar_timeout: float = Field(default=30.0, gt=0)
    sidecar_dir: Path = Field(default=PROJECT_ROOT / ".local-artifacts" / "douyin-sidecar")


class XhsConfig(BaseModel):
    """小红书 sidecar 配置。"""
    source: str = Field(default="sidecar", description="sidecar | off")
    sidecar_url: str = Field(default="http://127.0.0.1:8001")
    timeout: float = Field(default=30.0, gt=0)


class DistillationConfig(BaseModel):
    """跨平台蒸馏的发布默认值。"""
    emit: tuple[str, ...] = ("episodes", "skill")
    rag_chunks: bool = False
    download_workers: int = Field(default=3, ge=1)
    asr_workers: int = Field(default=1, ge=1, le=1)
    llm_workers: int = Field(default=3, ge=1)
    max_active_items: int = Field(default=3, ge=1)
    retry_limit: int = Field(default=2, ge=0)
    keep_media: bool = False


class AppConfig(BaseModel):
    """应用全局配置"""
    up_uid: int = Field(default=0, description="UP主UID")
    llm_provider: str = Field(default="claude", description="LLM提供商")
    data_dir: Path = Field(default=PROJECT_ROOT / "data", description="数据存储目录")
    output_dir: Path = Field(default=PROJECT_ROOT / "output", description="输出目录")
    ffmpeg_bin: str = Field(default="ffmpeg", description="ffmpeg 可执行文件路径（音频转码）")
    bilibili: BilibiliConfig = Field(default_factory=BilibiliConfig)
    anthropic: AnthropicConfig = Field(default_factory=AnthropicConfig)
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    qwen: QwenConfig = Field(default_factory=QwenConfig)
    deepseek: DeepSeekConfig = Field(default_factory=DeepSeekConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    funasr: FunASRConfig = Field(default_factory=FunASRConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    xhs: XhsConfig = Field(default_factory=XhsConfig)
    insights_enabled: bool = Field(default=False, description="创作者中心采集开关")
    douyin: DouyinConfig = Field(default_factory=DouyinConfig)
    distillation: DistillationConfig = Field(default_factory=DistillationConfig)

    def model_post_init(self, __context) -> None:
        default_profile = PROJECT_ROOT / "data" / "browser" / "douyin"
        if self.douyin.profile_dir == default_profile:
            self.douyin.profile_dir = self.data_dir / "browser" / "douyin"

    @property
    def credentials_cache(self) -> Path:
        """B站凭证缓存文件"""
        return self.data_dir / ".credentials.json"

    @property
    def model_cache_dir(self) -> Path:
        """FunASR/ModelScope 模型缓存目录"""
        return self.data_dir / ".cache" / "modelscope"

    @property
    def audio_dir(self) -> Path:
        """音频文件目录"""
        return self.data_dir / "audio"

    @property
    def transcripts_dir(self) -> Path:
        """转写结果目录"""
        return self.data_dir / "transcripts"

    @property
    def cleaned_dir(self) -> Path:
        """清洗结果目录"""
        return self.data_dir / "cleaned"

    @property
    def knowledge_dir(self) -> Path:
        """知识模型目录"""
        return self.data_dir / "knowledge"

    @property
    def rag_chunks_dir(self) -> Path:
        """RAG 知识块目录"""
        return self.data_dir / "rag_chunks"

    def ensure_dirs(self):
        """确保所有数据目录存在"""
        for d in [self.audio_dir, self.transcripts_dir, self.cleaned_dir,
                  self.knowledge_dir, self.rag_chunks_dir, self.output_dir,
                  self.model_cache_dir]:
            d.mkdir(parents=True, exist_ok=True)


def load_config() -> AppConfig:
    """从环境变量加载配置"""
    data_dir = Path(os.getenv("DATA_DIR", str(PROJECT_ROOT / "data")))
    config = AppConfig(
        up_uid=int(os.getenv("UP_UID", "0")),
        llm_provider=os.getenv("LLM_PROVIDER", "claude"),
        data_dir=data_dir,
        output_dir=Path(os.getenv("OUTPUT_DIR", str(PROJECT_ROOT / "output"))),
        ffmpeg_bin=os.getenv("FFMPEG_BIN", "ffmpeg"),
        bilibili=BilibiliConfig(
            sessdata=os.getenv("BILIBILI_SESSDATA", ""),
            bili_jct=os.getenv("BILIBILI_BILI_JCT", ""),
            buvid3=os.getenv("BILIBILI_BUVID3", ""),
        ),
        anthropic=AnthropicConfig(
            api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
        ),
        openai=OpenAIConfig(
            api_key=os.getenv("OPENAI_API_KEY", ""),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            model=os.getenv("OPENAI_MODEL", "gpt-4o"),
        ),
        qwen=QwenConfig(
            api_key=os.getenv("QWEN_API_KEY", ""),
            base_url=os.getenv("QWEN_BASE_URL",
                               "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            model=os.getenv("QWEN_MODEL", "qwen3-235b-a22b"),
        ),
        deepseek=DeepSeekConfig(
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-reasoner"),
        ),
        ollama=OllamaConfig(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            model=os.getenv("OLLAMA_MODEL", "qwen2.5:7b"),
        ),
        funasr=FunASRConfig(
            model=os.getenv("FUNASR_MODEL", "paraformer-zh"),
            vad_model=os.getenv("FUNASR_VAD_MODEL", "fsmn-vad"),
            punc_model=os.getenv("FUNASR_PUNC_MODEL", "ct-punc"),
        ),
        feishu=FeishuConfig(
            app_id=os.getenv("FEISHU_APP_ID", ""),
            app_secret=os.getenv("FEISHU_APP_SECRET", ""),
        ),
        douyin=DouyinConfig(
            profile_dir=Path(os.getenv("DOUYIN_PROFILE_DIR", str(data_dir / "browser" / "douyin"))),
            login_timeout=int(os.getenv("DOUYIN_LOGIN_TIMEOUT", "180")),
            source=os.getenv("DOUYIN_SOURCE", "auto").strip().lower() or "auto",
            sidecar_url=os.getenv("DOUYIN_SIDECAR_URL", "http://127.0.0.1:8000"),
            sidecar_timeout=float(os.getenv("DOUYIN_SIDECAR_TIMEOUT", "30")),
            sidecar_dir=Path(
                os.getenv("DOUYIN_SIDECAR_DIR", str(PROJECT_ROOT / ".local-artifacts" / "douyin-sidecar"))
            ),
        ),
        insights_enabled=os.getenv("INSIGHTS_ENABLED", "0").lower() in {"1", "true", "yes"},
        xhs=XhsConfig(
            source=os.getenv("XHS_SOURCE", "sidecar").strip().lower() or "sidecar",
            sidecar_url=os.getenv("XHS_SIDECAR_URL", "http://127.0.0.1:8001"),
            timeout=float(os.getenv("XHS_SIDECAR_TIMEOUT", "30")),
        ),
        distillation=DistillationConfig(
            download_workers=int(os.getenv("DISTILL_DOWNLOAD_WORKERS", "3")),
            asr_workers=int(os.getenv("DISTILL_ASR_WORKERS", "1")),
            llm_workers=int(os.getenv("DISTILL_LLM_WORKERS", "3")),
            max_active_items=int(os.getenv("DISTILL_MAX_ACTIVE", "3")),
            retry_limit=int(os.getenv("DISTILL_RETRY_LIMIT", "2")),
            keep_media=os.getenv("DISTILL_KEEP_MEDIA", "0").lower() in {"1", "true", "yes"},
        ),
    )
    config.ensure_dirs()
    return config
