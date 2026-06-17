"""Publish queue table — manages the Instagram posting schedule."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class QueueStatus(str, enum.Enum):
    """Status of a publish queue entry."""

    PENDING = "pending"
    UPLOADING = "uploading"
    PROCESSING = "processing"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PublishQueue(Base):
    """A scheduled post in the Instagram publishing queue."""

    __tablename__ = "publish_queue"

    queue_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    post_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("generated_posts.post_id"), nullable=False, index=True
    )

    # Scheduling
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Instagram tracking
    ig_media_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ig_container_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Status
    status: Mapped[QueueStatus] = mapped_column(
        Enum(QueueStatus), nullable=False, default=QueueStatus.PENDING, index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(default=0, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    post = relationship(
        "GeneratedPost", back_populates="publish_queue_entry", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<PublishQueue(status={self.status.value!r}, scheduled={self.scheduled_at})>"
