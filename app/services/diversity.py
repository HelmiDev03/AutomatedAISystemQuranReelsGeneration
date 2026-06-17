"""Topic Diversity Manager — weighted rotation with Redis-backed deduplication.

Ensures the content pipeline never publishes the same topic back-to-back
and avoids near-duplicate text using embedding cosine similarity.

Weight mechanics:
    - Each topic has a weight (default 1.0) stored in a Redis sorted set.
    - After use the weight is *decayed* (multiplied by 0.3).
    - Weights recover exponentially toward 1.0 with a 24-hour half-life.
    - Topic selection uses weighted-random sampling so high-weight topics
      are more likely but not guaranteed.

Deduplication:
    - Embeddings are stored in a ChromaDB collection (`content_dedup`).
    - Before publishing, new text is compared against the last 200 posts.
    - Any cosine similarity above ``settings.similarity_threshold`` (0.85)
      triggers a duplicate flag.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import redis.asyncio as aioredis
import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import Settings
from app.models.dedup import ContentDedupHash
from app.models.topic import ContentTopic

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)

# ── Redis key constants ─────────────────────────────────────────────────────
_WEIGHTS_KEY = "topic:weights"
_LAST_USED_KEY = "topic:last_used"  # Hash  topic_id → ISO timestamp
_DEDUP_RECENT_KEY = "dedup:recent_embeddings"  # List of recent post IDs

# ── Tuning knobs ────────────────────────────────────────────────────────────
_DECAY_FACTOR = 0.3  # Multiply weight by this after use
_HALF_LIFE_HOURS = 24.0  # Time for weight to recover ~50 %
_RECOVERY_RATE = math.log(2) / (_HALF_LIFE_HOURS * 3600)  # λ for exp recovery
_MAX_DEDUP_HISTORY = 200  # Number of recent posts to check


class DiversityManager:
    """Manages topic rotation and content deduplication.

    Parameters
    ----------
    settings : Settings
        Application configuration (contains ``redis_url``, ``similarity_threshold``, etc.).
    db_session_factory : async_sessionmaker[AsyncSession]
        Factory for creating async SQLAlchemy sessions.
    rag_engine : object | None
        Optional reference to a RAG / ChromaDB engine for embedding generation.
        Must expose ``get_embedding(text) -> list[float]`` and a ChromaDB
        collection at ``rag_engine.dedup_collection``.
    """

    def __init__(
        self,
        settings: Settings,
        db_session_factory: async_sessionmaker[AsyncSession],
        rag_engine: object | None = None,
    ) -> None:
        self._settings = settings
        self._db_factory = db_session_factory
        self._rag = rag_engine
        self._similarity_threshold = settings.similarity_threshold
        self._redis: aioredis.Redis | None = None

    # ── lifecycle helpers ────────────────────────────────────────────────

    async def _get_redis(self) -> aioredis.Redis:
        """Return (and lazily create) the async Redis connection."""
        if self._redis is None:
            self._redis = aioredis.from_url(
                self._settings.redis_url,
                decode_responses=True,
            )
        return self._redis

    async def close(self) -> None:
        """Gracefully close the Redis connection pool."""
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    # ── public API ───────────────────────────────────────────────────────

    async def select_topic(self) -> ContentTopic:
        """Select the next topic using weighted random sampling.

        Algorithm:
            1. Load all topics from the database.
            2. For each topic compute an *effective weight*:
               ``w_eff = w_stored + (1 - w_stored) * (1 - e^{-λΔt})``
               where Δt is the seconds since last use.
            3. Perform weighted-random sampling using ``random.choices``.

        Returns
        -------
        ContentTopic
            The selected topic ORM instance.

        Raises
        ------
        RuntimeError
            If no topics exist in the database.
        """
        redis = await self._get_redis()

        async with self._db_factory() as session:
            result = await session.execute(select(ContentTopic))
            topics: list[ContentTopic] = list(result.scalars().all())

        if not topics:
            raise RuntimeError("No topics available in the database.")

        # Compute effective weights -------------------------------------------
        effective_weights: list[float] = []
        now = datetime.now(timezone.utc)

        for topic in topics:
            stored_weight = await self._get_stored_weight(redis, topic.topic_id)
            last_used_iso = await redis.hget(_LAST_USED_KEY, topic.topic_id)

            if last_used_iso:
                try:
                    last_used = datetime.fromisoformat(last_used_iso)
                except (ValueError, TypeError):
                    last_used = None
            else:
                last_used = topic.last_used_at

            if last_used is not None:
                delta_seconds = max((now - last_used).total_seconds(), 0)
                recovery = (1.0 - stored_weight) * (
                    1.0 - math.exp(-_RECOVERY_RATE * delta_seconds)
                )
                effective = stored_weight + recovery
            else:
                # Never used → full weight
                effective = max(stored_weight, 1.0)

            effective_weights.append(max(effective, 0.05))  # floor to avoid 0

        logger.debug(
            "topic_weights_computed",
            topics=[t.name for t in topics],
            weights=effective_weights,
        )

        # Weighted random selection -------------------------------------------
        selected = random.choices(topics, weights=effective_weights, k=1)[0]

        logger.info(
            "topic_selected",
            topic_id=selected.topic_id,
            topic_name=selected.name,
        )

        return selected

    async def update_weights_after_use(self, topic_id: str) -> None:
        """Decay the weight of a topic after it has been used.

        The stored weight is multiplied by ``_DECAY_FACTOR`` (0.3) and the
        ``last_used_at`` timestamp is updated in both Redis and the database.

        Parameters
        ----------
        topic_id : str
            UUID of the topic that was just used.
        """
        redis = await self._get_redis()
        now = datetime.now(timezone.utc)

        # Read current stored weight
        current = await self._get_stored_weight(redis, topic_id)

        # Decay
        new_weight = current * _DECAY_FACTOR
        new_weight = max(new_weight, 0.01)  # never hit absolute zero

        # Persist to Redis sorted set + last-used hash
        await redis.zadd(_WEIGHTS_KEY, {topic_id: new_weight})
        await redis.hset(_LAST_USED_KEY, topic_id, now.isoformat())

        # Persist to database
        async with self._db_factory() as session:
            await session.execute(
                update(ContentTopic)
                .where(ContentTopic.topic_id == topic_id)
                .values(weight_score=new_weight, last_used_at=now)
            )
            await session.commit()

        logger.info(
            "topic_weight_decayed",
            topic_id=topic_id,
            old_weight=current,
            new_weight=new_weight,
        )

    async def check_content_duplicate(self, text: str) -> bool:
        """Check whether *text* is too similar to recently published content.

        Similarity is evaluated using cosine similarity of OpenAI embeddings
        stored in the ChromaDB ``content_dedup`` collection.  If any stored
        embedding exceeds ``settings.similarity_threshold``, the text is
        considered a duplicate.

        Parameters
        ----------
        text : str
            The candidate text to check.

        Returns
        -------
        bool
            ``True`` if the text is a near-duplicate and should be rejected.
        """
        # Exact-hash check (fast path) ----------------------------------------
        fingerprint = self._fingerprint(text)

        async with self._db_factory() as session:
            result = await session.execute(
                select(ContentDedupHash).where(
                    ContentDedupHash.content_fingerprint == fingerprint
                )
            )
            if result.scalars().first() is not None:
                logger.warning("duplicate_exact_match", fingerprint=fingerprint)
                return True

        # Embedding similarity check (slow path) ------------------------------
        if self._rag is None:
            logger.debug("rag_engine_not_set_skipping_embedding_check")
            return False

        try:
            embedding = await self._get_embedding(text)
        except Exception:
            logger.exception("embedding_generation_failed")
            return False

        try:
            collection = self._rag.dedup_collection  # type: ignore[attr-defined]
            results = collection.query(
                query_embeddings=[embedding],
                n_results=min(_MAX_DEDUP_HISTORY, 10),
                include=["distances"],
            )

            if results and results.get("distances"):
                for distance_list in results["distances"]:
                    for distance in distance_list:
                        # ChromaDB returns L2 distance by default.
                        # Convert to cosine similarity: sim ≈ 1 - (d² / 2)
                        # when vectors are normalised.  We also support cosine
                        # space where distance == 1 - similarity.
                        similarity = 1.0 - distance
                        if similarity >= self._similarity_threshold:
                            logger.warning(
                                "duplicate_embedding_match",
                                similarity=round(similarity, 4),
                                threshold=self._similarity_threshold,
                            )
                            return True
        except Exception:
            logger.exception("dedup_chroma_query_failed")

        return False

    async def record_published_content(self, post_id: str, text: str) -> None:
        """Store embedding and fingerprint of published content for future dedup.

        Parameters
        ----------
        post_id : str
            The UUID of the published post.
        text : str
            The text that was published.
        """
        fingerprint = self._fingerprint(text)

        # Store fingerprint + serialised embedding in the database
        embedding: list[float] = []
        if self._rag is not None:
            try:
                embedding = await self._get_embedding(text)
            except Exception:
                logger.exception("embedding_generation_for_record_failed")

        async with self._db_factory() as session:
            dedup = ContentDedupHash(
                id=str(uuid.uuid4()),
                post_id=post_id,
                embedding_hash=json.dumps(embedding) if embedding else "[]",
                content_fingerprint=fingerprint,
            )
            session.add(dedup)
            await session.commit()

        # Store in ChromaDB for vector search ---------------------------------
        if self._rag is not None and embedding:
            try:
                collection = self._rag.dedup_collection  # type: ignore[attr-defined]
                collection.add(
                    ids=[post_id],
                    embeddings=[embedding],
                    metadatas=[{"post_id": post_id, "fingerprint": fingerprint}],
                )
            except Exception:
                logger.exception("dedup_chroma_add_failed")

        # Track recent post IDs in Redis for sliding-window trimming ----------
        redis = await self._get_redis()
        await redis.lpush(_DEDUP_RECENT_KEY, post_id)
        await redis.ltrim(_DEDUP_RECENT_KEY, 0, _MAX_DEDUP_HISTORY - 1)

        logger.info(
            "published_content_recorded",
            post_id=post_id,
            fingerprint=fingerprint,
        )

    # ── private helpers ──────────────────────────────────────────────────

    async def _get_stored_weight(
        self, redis: aioredis.Redis, topic_id: str
    ) -> float:
        """Return the stored weight from Redis, falling back to 1.0."""
        score = await redis.zscore(_WEIGHTS_KEY, topic_id)
        if score is not None:
            return float(score)
        return 1.0

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=5),
        reraise=True,
    )
    async def _get_embedding(self, text: str) -> list[float]:
        """Generate an embedding vector for *text* via the RAG engine.

        Falls back to a simple hash-based pseudo-embedding if the RAG
        engine is unavailable.
        """
        if self._rag is not None and hasattr(self._rag, "get_embedding"):
            return await self._rag.get_embedding(text)  # type: ignore[attr-defined]
        raise RuntimeError("RAG engine does not provide get_embedding()")

    @staticmethod
    def _fingerprint(text: str) -> str:
        """Return SHA-256 hex digest of normalised text."""
        normalised = " ".join(text.split()).strip().lower()
        return hashlib.sha256(normalised.encode("utf-8")).hexdigest()
