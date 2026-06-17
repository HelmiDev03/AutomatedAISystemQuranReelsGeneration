"""Pydantic schemas for topic-related requests and responses."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class TopicCategoryEnum(str, Enum):
    QURAN_TAFSIR = "quran_tafsir"
    HADITH = "hadith"
    SEERAH = "seerah"
    FIQH_BASIC = "fiqh_basic"
    DUA = "dua"
    ISLAMIC_HISTORY = "islamic_history"
    DAILY_REMINDER = "daily_reminder"
    NAMES_OF_ALLAH = "names_of_allah"
    AKHLAQ = "akhlaq"
    SALAH = "salah"
    TAWHEED = "tawheed"
    QURAN_RECITATION = "quran_recitation"


class TopicResponse(BaseModel):
    """Response containing a content topic."""

    topic_id: str
    name: str
    category: TopicCategoryEnum
    description: str | None = None
    weight_score: float
    last_used_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class TopicListResponse(BaseModel):
    """List of content topics."""

    topics: list[TopicResponse]
    total: int


class TopicPerformanceResponse(BaseModel):
    """Performance metrics for a topic."""

    topic_id: str
    topic_name: str
    category: TopicCategoryEnum
    avg_engagement_rate: float
    best_posting_hour: int | None = None
    post_count: int
    total_reach: int
    total_saves: int

    model_config = {"from_attributes": True}
