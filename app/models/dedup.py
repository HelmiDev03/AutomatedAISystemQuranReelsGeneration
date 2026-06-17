"""Content deduplication hashes table — prevents duplicate content via embedding similarity."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ContentDedupHash(Base):
    """Stores embedding hashes for published content to detect near-duplicates.

    Before publishing, new content embeddings are compared against this table
    using cosine similarity. Anything above the similarity threshold (0.85)
    is rejected and regenerated.
    """

    __tablename__ = "content_dedup_hashes"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    post_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("generated_posts.post_id"), nullable=False, index=True
    )
    embedding_hash: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # Serialized embedding vector
    content_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )  # SHA-256 of normalized text

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    post = relationship("GeneratedPost", back_populates="dedup_hash", lazy="selectin")

    def __repr__(self) -> str:
        return f"<ContentDedupHash(post={self.post_id!r})>"
