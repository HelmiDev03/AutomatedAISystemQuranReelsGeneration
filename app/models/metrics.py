"""Engagement metrics table — stores Instagram analytics per post."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class EngagementMetric(Base):
    """Instagram engagement metrics for a published post."""

    __tablename__ = "engagement_metrics"

    metric_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    post_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("generated_posts.post_id"), nullable=False, index=True
    )

    # Engagement data
    likes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    comments: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    shares: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    saves: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reach: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    impressions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Timestamps
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    post = relationship("GeneratedPost", back_populates="engagement", lazy="selectin")

    @property
    def engagement_rate(self) -> float:
        """Calculate engagement rate as (likes+comments+shares+saves) / reach."""
        if self.reach == 0:
            return 0.0
        return (self.likes + self.comments + self.shares + self.saves) / self.reach

    def __repr__(self) -> str:
        return f"<EngagementMetric(post={self.post_id!r}, likes={self.likes})>"
