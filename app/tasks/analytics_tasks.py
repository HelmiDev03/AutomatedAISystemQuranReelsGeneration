"""Analytics Celery tasks — engagement collection and topic weight optimization."""

import asyncio
import sys
from datetime import datetime, timezone, timedelta

import structlog

from app.tasks.celery_app import celery_app

# Windows event loop fix
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logger = structlog.get_logger()


@celery_app.task(bind=True, max_retries=2)
def collect_engagement_metrics(self) -> dict:
    """Fetch engagement metrics from Instagram for recently published posts.

    Runs every 6 hours via Celery Beat.
    Queries Instagram Graph API Insights for posts published in the last 7 days.
    """

    async def _collect() -> dict:
        from app.config import get_settings
        from app.database import async_session
        from app.models.post import GeneratedPost, PostStatus
        from app.models.queue import PublishQueue, QueueStatus
        from app.models.metrics import EngagementMetric
        from app.services.publisher import InstagramPublisher

        settings = get_settings()

        if not settings.instagram_access_token:
            logger.warning("analytics.no_token", msg="Instagram token not configured")
            return {"status": "skipped", "reason": "no_token"}

        publisher = InstagramPublisher(settings)
        collected = 0
        errors = 0

        async with async_session() as db:
            from sqlalchemy import select

            # Get published posts from last 7 days
            cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            stmt = (
                select(PublishQueue)
                .where(PublishQueue.status == QueueStatus.PUBLISHED)
                .where(PublishQueue.published_at >= cutoff)
                .where(PublishQueue.ig_media_id.isnot(None))
            )
            result = await db.execute(stmt)
            queue_entries = result.scalars().all()

            for entry in queue_entries:
                try:
                    insights = await publisher.get_post_insights(entry.ig_media_id)

                    if not insights:
                        continue

                    # Check if we already have metrics for this post
                    existing_stmt = select(EngagementMetric).where(
                        EngagementMetric.post_id == entry.post_id
                    )
                    existing_result = await db.execute(existing_stmt)
                    existing = existing_result.scalar_one_or_none()

                    if existing:
                        # Update existing metrics
                        existing.likes = insights.get("likes", 0)
                        existing.comments = insights.get("comments", 0)
                        existing.shares = insights.get("shares", 0)
                        existing.saves = insights.get("saves", 0)
                        existing.reach = insights.get("reach", 0)
                        existing.impressions = insights.get("impressions", 0)
                        existing.recorded_at = datetime.now(timezone.utc)
                    else:
                        # Create new metrics record
                        metric = EngagementMetric(
                            post_id=entry.post_id,
                            likes=insights.get("likes", 0),
                            comments=insights.get("comments", 0),
                            shares=insights.get("shares", 0),
                            saves=insights.get("saves", 0),
                            reach=insights.get("reach", 0),
                            impressions=insights.get("impressions", 0),
                        )
                        db.add(metric)

                    collected += 1

                except Exception as e:
                    logger.warning(
                        "analytics.collect_error",
                        post_id=entry.post_id,
                        error=str(e),
                    )
                    errors += 1

            await db.commit()

        logger.info(
            "analytics.collection_complete",
            collected=collected,
            errors=errors,
        )
        return {"status": "completed", "collected": collected, "errors": errors}

    return asyncio.run(_collect())


@celery_app.task(bind=True, max_retries=1)
def update_topic_weights(self) -> dict:
    """Recalculate topic weights based on engagement performance.

    Runs daily via Celery Beat.
    Topics with higher engagement rates get increased weight in the rotation queue.
    """

    async def _update() -> dict:
        from app.config import get_settings
        from app.database import async_session
        from app.services.analytics import AnalyticsService

        settings = get_settings()

        async with async_session() as db:
            analytics = AnalyticsService(settings, db)
            await analytics.update_topic_weights()
            await db.commit()

        logger.info("analytics.weights_updated")
        return {"status": "completed"}

    return asyncio.run(_update())
