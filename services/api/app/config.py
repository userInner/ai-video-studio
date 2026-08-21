from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    app_name: str = "传播引擎"
    environment: str = "development"
    database_url: str = f"sqlite+aiosqlite:///{PROJECT_ROOT / 'data' / 'app.db'}"
    asset_root: Path = PROJECT_ROOT / "data" / "assets"
    allowed_origins: str = "http://127.0.0.1:3000,http://localhost:3000"

    sub2api_base_url: str = "https://sub2api.aibro.vip/v1"
    sub2api_api_key: str | None = Field(default=None, repr=False)
    text_model: str = "gpt-5.6-luna"
    image_model: str = "gpt-image-2"
    image_quality: str = "medium"
    image_timeout_seconds: int = 360
    media_quality_max_retries: int = 1
    minimax_base_url: str = "https://api.minimax.io"
    minimax_api_key: str | None = Field(default=None, repr=False)
    tts_model: str = "speech-2.8-hd"
    tts_voice_id: str = "Chinese (Mandarin)_Reliable_Executive"
    prefer_local_qwen_tts: bool = True
    qwen_tts_python: Path = Path.home() / "miniconda3" / "envs" / "qwen3tts" / "bin" / "python"
    qwen_tts_checkpoint: Path = Path.home() / ".cache" / "modelscope" / "models" / "Qwen--Qwen3-TTS-12Hz-1.7B-VoiceDesign" / "snapshots" / "master"
    qwen_tts_base_checkpoint: Path = Path.home() / ".cache" / "modelscope" / "models" / "Qwen--Qwen3-TTS-12Hz-1.7B-Base" / "snapshots" / "master"
    qwen_tts_voice_reference: Path = PROJECT_ROOT / "data" / "voices" / "qwen-finance-narrator-v1.wav"
    qwen_tts_voice_design: str = "成熟中性的中文女声，中低音，理性、干净、专业，像财经纪录片解说。语速自然，重点准确，克制但有判断力。"
    whiteboard_renderer_root: Path = PROJECT_ROOT / "vendor" / "srt-whiteboard-animation"
    allow_native_tts_fallback: bool = True
    use_codex_runtime: bool = True
    allow_demo_fallback: bool = True
    director_timeout_seconds: int = 45
    research_timeout_seconds: int = 120
    research_max_attempts: int = 3

    @property
    def origins(self) -> list[str]:
        return [value.strip() for value in self.allowed_origins.split(",") if value.strip()]

    def resolved_sub2api_key(self) -> str | None:
        return self.sub2api_api_key


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.asset_root.mkdir(parents=True, exist_ok=True)
    if settings.database_url.startswith("sqlite"):
        (PROJECT_ROOT / "data").mkdir(parents=True, exist_ok=True)
    return settings
