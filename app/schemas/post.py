"""Pydantic schemas for content-related requests and responses."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ContentTypeEnum(str, Enum):
    QURAN_VERSE = "quran_verse"
    HADITH = "hadith"
    DUA = "dua"
    DAILY_REMINDER = "daily_reminder"
    SEERAH = "seerah"
    FIQH_BASIC = "fiqh_basic"
    NAMES_OF_ALLAH = "names_of_allah"
    ISLAMIC_HISTORY = "islamic_history"
    AKHLAQ = "akhlaq"
    TAWHEED = "tawheed"


class MediaFormatEnum(str, Enum):
    QUOTE_CARD = "quote_card"
    CAROUSEL = "carousel"
    REEL = "reel"


class PostStatusEnum(str, Enum):
    DRAFT = "draft"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    REJECTED = "rejected"
    RENDERING = "rendering"
    READY = "ready"
    PUBLISHED = "published"
    FAILED = "failed"


class HadithGradeEnum(str, Enum):
    SAHIH = "sahih"
    HASAN = "hasan"
    DAIF = "daif"
    MAWDU = "mawdu"
    UNKNOWN = "unknown"


# ── Request Schemas ──────────────────────────────────────────────────────────


class GenerateContentRequest(BaseModel):
    """Request to generate new Islamic content."""

    content_type: ContentTypeEnum | None = None
    media_format: MediaFormatEnum = MediaFormatEnum.QUOTE_CARD
    topic_id: str | None = None
    custom_prompt: str | None = Field(
        None, description="Optional custom guidance for generation"
    )


class ReviewPostRequest(BaseModel):
    """Request to approve or reject a post from the review queue."""

    approved: bool
    rejection_reason: str | None = None


# ── Response Schemas ─────────────────────────────────────────────────────────


class PostResponse(BaseModel):
    """Response containing a generated post."""

    post_id: str
    topic_id: str
    content_type: ContentTypeEnum
    media_format: MediaFormatEnum
    arabic_text: str
    english_text: str
    caption: str | None = None
    hashtags: str | None = None
    source_ref: str | None = None
    hadith_grade: HadithGradeEnum | None = None
    confidence_score: float | None = None
    status: PostStatusEnum
    created_at: datetime
    verified_at: datetime | None = None
    published_at: datetime | None = None

    model_config = {"from_attributes": True}


class PostListResponse(BaseModel):
    """Paginated list of posts."""

    posts: list[PostResponse]
    total: int
    page: int
    per_page: int


class GenerationResult(BaseModel):
    """Result of the content generation pipeline."""

    post_id: str
    status: PostStatusEnum
    confidence_score: float | None = None
    message: str


class PipelineStatusResponse(BaseModel):
    """Status of the content pipeline."""

    is_running: bool
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    posts_generated_today: int
    posts_published_today: int
    posts_in_review: int
