"""Media assets table — tracks generated images, videos, and carousel slides."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class MediaType(str, enum.Enum):
    """Type of media asset."""

    IMAGE = "image"
    VIDEO = "video"
    CAROUSEL_SLIDE = "carousel_slide"
    AUDIO = "audio"


class MediaAsset(Base):
    """A media file (image, video, audio) associated with a post."""

    __tablename__ = "media_assets"

    asset_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    post_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("generated_posts.post_id"), nullable=False, index=True
    )
    media_type: Mapped[MediaType] = mapped_column(Enum(MediaType), nullable=False)

    # File paths
    file_path: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )  # Local path
    cdn_url: Mapped[str | None] = mapped_column(
        String(1000), nullable=True
    )  # Cloudinary URL
    cloudinary_public_id: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )  # For deletion/management

    # Metadata
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # For video/audio
    slide_order: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # For carousel ordering
    alt_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    post = relationship("GeneratedPost", back_populates="media_assets", lazy="selectin")

    def __repr__(self) -> str:
        return f"<MediaAsset(type={self.media_type.value!r}, post={self.post_id!r})>"
