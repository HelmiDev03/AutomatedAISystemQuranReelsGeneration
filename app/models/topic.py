"""Content topics table — tracks topic categories and rotation weights."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TopicCategory(str, enum.Enum):
    """Categories of Islamic content topics."""

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


class ContentTopic(Base):
    """Represents a content topic for the rotation queue."""

    __tablename__ = "content_topics"

    topic_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    category: Mapped[TopicCategory] = mapped_column(
        Enum(TopicCategory), nullable=False, index=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    weight_score: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    posts = relationship("GeneratedPost", back_populates="topic", lazy="selectin")
    performance = relationship(
        "TopicPerformance", back_populates="topic", uselist=False, lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<ContentTopic(name={self.name!r}, category={self.category.value!r})>"
