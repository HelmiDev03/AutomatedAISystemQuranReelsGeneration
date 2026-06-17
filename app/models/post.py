"""Generated posts table — stores all AI-generated content with verification status."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ContentType(str, enum.Enum):
    """Types of Islamic content that can be generated."""

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


class PostStatus(str, enum.Enum):
    """Lifecycle status of a generated post."""

    DRAFT = "draft"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    REJECTED = "rejected"
    RENDERING = "rendering"
    READY = "ready"
    PUBLISHED = "published"
    FAILED = "failed"


class HadithGrade(str, enum.Enum):
    """Muhaddith-assigned hadith authentication grades."""

    SAHIH = "sahih"
    HASAN = "hasan"
    DAIF = "daif"
    MAWDU = "mawdu"
    UNKNOWN = "unknown"


class MediaFormat(str, enum.Enum):
    """Instagram media format for the post."""

    QUOTE_CARD = "quote_card"
    CAROUSEL = "carousel"
    REEL = "reel"


class GeneratedPost(Base):
    """A single piece of AI-generated Islamic content."""

    __tablename__ = "generated_posts"

    post_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    topic_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("content_topics.topic_id"), nullable=False, index=True
    )
    content_type: Mapped[ContentType] = mapped_column(
        Enum(ContentType), nullable=False, index=True
    )
    media_format: Mapped[MediaFormat] = mapped_column(
        Enum(MediaFormat), nullable=False, default=MediaFormat.QUOTE_CARD
    )

    # Content fields
    arabic_text: Mapped[str] = mapped_column(Text, nullable=False)
    english_text: Mapped[str] = mapped_column(Text, nullable=False)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    hashtags: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Source verification
    source_ref: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )  # e.g., "Sahih al-Bukhari 6018"
    hadith_grade: Mapped[HadithGrade | None] = mapped_column(
        Enum(HadithGrade), nullable=True
    )
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Status tracking
    status: Mapped[PostStatus] = mapped_column(
        Enum(PostStatus), nullable=False, default=PostStatus.DRAFT, index=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    topic = relationship("ContentTopic", back_populates="posts", lazy="selectin")
    media_assets = relationship(
        "MediaAsset", back_populates="post", lazy="selectin", cascade="all, delete-orphan"
    )
    publish_queue_entry = relationship(
        "PublishQueue", back_populates="post", uselist=False, lazy="selectin"
    )
    engagement = relationship(
        "EngagementMetric", back_populates="post", uselist=False, lazy="selectin"
    )
    dedup_hash = relationship(
        "ContentDedupHash", back_populates="post", uselist=False, lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<GeneratedPost(type={self.content_type.value!r}, status={self.status.value!r})>"
