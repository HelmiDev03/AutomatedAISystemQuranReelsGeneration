"""Topic performance table — aggregate engagement stats per topic for optimization."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TopicPerformance(Base):
    """Aggregate performance metrics for a content topic.

    Used by the diversity manager to adjust topic weights based on
    historical engagement data.
    """

    __tablename__ = "topic_performance"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    topic_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("content_topics.topic_id"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Aggregate metrics
    avg_engagement_rate: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    best_posting_hour: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # 0-23 UTC
    post_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_reach: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_saves: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Timestamps
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    topic = relationship("ContentTopic", back_populates="performance", lazy="selectin")

    def __repr__(self) -> str:
        return (
            f"<TopicPerformance(topic={self.topic_id!r}, "
            f"avg_rate={self.avg_engagement_rate:.2%})>"
        )
