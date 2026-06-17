"""Application settings loaded from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for the Islamic Content Automation system.

    All values are loaded from the .env file or environment variables.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── LLM API (Groq — OpenAI-compatible) ─────────────────────────────
    openai_api_key: str = ""
    openai_base_url: str = "https://api.groq.com/openai/v1"
    openai_model_primary: str = "llama-3.3-70b-versatile"
    openai_model_complex: str = "llama-3.3-70b-versatile"
    openai_image_model: str = ""  # Groq doesn't support image generation

    # ── Database ─────────────────────────────────────────────────────────
    # SQLite for local dev (no Docker needed), PostgreSQL for production
    database_url: str = "sqlite+aiosqlite:///./automate.db"

    # ── Redis (optional for local dev — Celery needs it for scheduling)
    redis_url: str = ""

    # ── ChromaDB ─────────────────────────────────────────────────────────
    # Embedded mode (persist_dir) — no server needed for local dev
    # Set chroma_host to use client/server mode instead
    chroma_host: str = ""
    chroma_port: int = 8100
    chroma_persist_dir: str = "./chroma_data"
    chroma_quran_collection: str = "quran_verses"
    chroma_hadith_collection: str = "hadith_collection"

    # ── Cloudinary ───────────────────────────────────────────────────────
    cloudinary_cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: str = ""

    # ── Pixabay (Background Videos) ─────────────────────────────────────
    pixabay_api_key: str = ""

    # ── Instagram Graph API ──────────────────────────────────────────────
    instagram_access_token: str = ""
    instagram_business_id: str = ""

    # ── Telegram ─────────────────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ── Pipeline Settings ────────────────────────────────────────────────
    confidence_threshold: float = 0.85
    similarity_threshold: float = 0.85
    posting_interval_minutes: int = 55
    review_mode: bool = True  # Require manual approval for all posts

    # ── Edge TTS ─────────────────────────────────────────────────────────
    edge_tts_voice_male: str = "ar-SA-HamedNeural"
    edge_tts_voice_female: str = "ar-SA-ZariyahNeural"

    @property
    def sync_database_url(self) -> str:
        """Return synchronous database URL for Alembic migrations."""
        return self.database_url.replace("+asyncpg", "+psycopg2").replace(
            "postgresql+psycopg2", "postgresql"
        )


def get_settings() -> Settings:
    """Create and return a cached Settings instance."""
    return Settings()
