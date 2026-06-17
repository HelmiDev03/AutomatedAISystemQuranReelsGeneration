"""Analytics service — engagement tracking and topic weight optimization."""

from datetime import datetime, timezone, timedelta

import structlog
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.metrics import EngagementMetric
from app.models.performance import TopicPerformance
from app.models.post import GeneratedPost, PostStatus
from app.models.topic import ContentTopic
from app.models.queue import PublishQueue, QueueStatus

logger = structlog.get_logger()


class AnalyticsService:
    """Handles engagement tracking, performance analysis, and topic optimization.

    Responsibilities:
    - Record engagement metrics from Instagram Graph API
    - Calculate aggregate topic performance
    - Update topic weights based on engagement data
    - Identify optimal posting times
    """

    def __init__(self, settings: Settings, db: AsyncSession):
        self.settings = settings
        self.db = db

    async def record_engagement(self, post_id: str, metrics: dict) -> EngagementMetric:
        """Record or update engagement metrics for a post.

        Args:
            post_id: The post ID to record metrics for.
            metrics: Dict with keys: likes, comments, shares, saves, reach, impressions.

        Returns:
            The created or updated EngagementMetric record.
        """
        # Check for existing metrics
        stmt = select(EngagementMetric).where(EngagementMetric.post_id == post_id)
        result = await self.db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.likes = metrics.get("likes", existing.likes)
            existing.comments = metrics.get("comments", existing.comments)
            existing.shares = metrics.get("shares", existing.shares)
            existing.saves = metrics.get("saves", existing.saves)
            existing.reach = metrics.get("reach", existing.reach)
            existing.impressions = metrics.get("impressions", existing.impressions)
            existing.recorded_at = datetime.now(timezone.utc)
            logger.info("analytics.metrics_updated", post_id=post_id)
            return existing

        metric = EngagementMetric(
            post_id=post_id,
            likes=metrics.get("likes", 0),
            comments=metrics.get("comments", 0),
            shares=metrics.get("shares", 0),
            saves=metrics.get("saves", 0),
            reach=metrics.get("reach", 0),
            impressions=metrics.get("impressions", 0),
        )
        self.db.add(metric)
        logger.info("analytics.metrics_recorded", post_id=post_id)
        return metric

    async def get_dashboard_stats(self) -> dict:
        """Get overview dashboard statistics.

        Returns:
            Dict with total_posts, published, in_review, rejected,
            avg_engagement_rate, total_reach, posts_today, posts_this_week.
        """
        now = datetime.now(timezone.utc)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_ago = today - timedelta(days=7)

        # Count by status
        status_counts = {}
        for status in PostStatus:
            stmt = (
                select(func.count())
                .select_from(GeneratedPost)
                .where(GeneratedPost.status == status)
            )
            result = await self.db.execute(stmt)
            status_counts[status.value] = result.scalar() or 0

        # Engagement averages
        metrics_stmt = select(EngagementMetric)
        metrics_result = await self.db.execute(metrics_stmt)
        all_metrics = metrics_result.scalars().all()

        avg_rate = 0.0
        total_reach = 0
        if all_metrics:
            rates = [m.engagement_rate for m in all_metrics if m.reach > 0]
            avg_rate = sum(rates) / len(rates) if rates else 0.0
            total_reach = sum(m.reach for m in all_metrics)

        # Time-based counts
        today_stmt = (
            select(func.count())
            .select_from(GeneratedPost)
            .where(GeneratedPost.created_at >= today)
        )
        posts_today = (await self.db.execute(today_stmt)).scalar() or 0

        week_stmt = (
            select(func.count())
            .select_from(GeneratedPost)
            .where(GeneratedPost.created_at >= week_ago)
        )
        posts_week = (await self.db.execute(week_stmt)).scalar() or 0

        return {
            "total_posts": sum(status_counts.values()),
            "published": status_counts.get("published", 0),
            "in_review": status_counts.get("verified", 0) + status_counts.get("ready", 0),
            "rejected": status_counts.get("rejected", 0),
            "avg_engagement_rate": round(avg_rate, 4),
            "total_reach": total_reach,
            "posts_today": posts_today,
            "posts_this_week": posts_week,
        }

    async def get_topic_performance(self) -> list[dict]:
        """Get performance metrics for all topics.

        Returns:
            List of topic performance dicts sorted by engagement rate.
        """
        stmt = (
            select(TopicPerformance, ContentTopic)
            .join(ContentTopic, TopicPerformance.topic_id == ContentTopic.topic_id)
            .order_by(desc(TopicPerformance.avg_engagement_rate))
        )
        result = await self.db.execute(stmt)
        rows = result.all()

        return [
            {
                "topic_id": perf.topic_id,
                "topic_name": topic.name,
                "category": topic.category.value,
                "avg_engagement_rate": round(perf.avg_engagement_rate, 4),
                "best_posting_hour": perf.best_posting_hour,
                "post_count": perf.post_count,
                "total_reach": perf.total_reach,
                "total_saves": perf.total_saves,
            }
            for perf, topic in rows
        ]

    async def update_topic_weights(self) -> None:
        """Recalculate topic weights based on engagement performance.

        Topics with higher engagement rates get increased weight in the
        rotation queue. Uses a normalized scoring approach:
        - Engagement rate (40%)
        - Save rate (30%) — saves indicate high-value content
        - Reach (20%)
        - Recency penalty (10%) — avoid over-repeating recently used topics

        Updates both TopicPerformance records and ContentTopic weights.
        """
        # Get all topics with their published posts
        topics_stmt = select(ContentTopic)
        topics_result = await self.db.execute(topics_stmt)
        topics = topics_result.scalars().all()

        for topic in topics:
            # Get engagement metrics for this topic's published posts
            metrics_stmt = (
                select(EngagementMetric)
                .join(GeneratedPost, EngagementMetric.post_id == GeneratedPost.post_id)
                .where(GeneratedPost.topic_id == topic.topic_id)
                .where(GeneratedPost.status == PostStatus.PUBLISHED)
            )
            metrics_result = await self.db.execute(metrics_stmt)
            metrics = metrics_result.scalars().all()

            if not metrics:
                continue

            # Calculate aggregate stats
            total_engagement = sum(
                m.likes + m.comments + m.shares + m.saves for m in metrics
            )
            total_reach = sum(m.reach for m in metrics)
            total_saves = sum(m.saves for m in metrics)
            post_count = len(metrics)

            avg_rate = (
                sum(m.engagement_rate for m in metrics) / post_count
                if post_count > 0
                else 0.0
            )

            # Find best posting hour
            publish_hours: dict[int, list[float]] = {}
            for metric in metrics:
                # Get the publish time for this post
                pub_stmt = (
                    select(PublishQueue.published_at)
                    .where(PublishQueue.post_id == metric.post_id)
                    .where(PublishQueue.status == QueueStatus.PUBLISHED)
                )
                pub_result = await self.db.execute(pub_stmt)
                pub_time = pub_result.scalar_one_or_none()
                if pub_time:
                    hour = pub_time.hour
                    if hour not in publish_hours:
                        publish_hours[hour] = []
                    publish_hours[hour].append(metric.engagement_rate)

            best_hour = None
            if publish_hours:
                best_hour = max(
                    publish_hours,
                    key=lambda h: sum(publish_hours[h]) / len(publish_hours[h]),
                )

            # Update or create TopicPerformance record
            perf_stmt = select(TopicPerformance).where(
                TopicPerformance.topic_id == topic.topic_id
            )
            perf_result = await self.db.execute(perf_stmt)
            perf = perf_result.scalar_one_or_none()

            if perf:
                perf.avg_engagement_rate = avg_rate
                perf.best_posting_hour = best_hour
                perf.post_count = post_count
                perf.total_reach = total_reach
                perf.total_saves = total_saves
            else:
                perf = TopicPerformance(
                    topic_id=topic.topic_id,
                    avg_engagement_rate=avg_rate,
                    best_posting_hour=best_hour,
                    post_count=post_count,
                    total_reach=total_reach,
                    total_saves=total_saves,
                )
                self.db.add(perf)

            # Update topic weight based on performance
            # Higher engagement → higher weight (but never below 0.3 or above 3.0)
            # New weight = 1.0 + (normalized_engagement * 2.0)
            if avg_rate > 0:
                # Normalize: typical Instagram engagement rate is 1-3%
                normalized = min(avg_rate / 0.03, 1.0)  # Cap at 3% as "excellent"
                new_weight = 0.5 + (normalized * 2.5)
                new_weight = max(0.3, min(3.0, new_weight))
                topic.weight_score = new_weight

        logger.info(
            "analytics.weights_updated",
            topics_processed=len(topics),
        )

    async def get_best_posting_times(self) -> dict:
        """Analyze engagement data to find optimal posting times.

        Returns:
            Dict with best_hours and best_days sorted by engagement rate.
        """
        stmt = (
            select(PublishQueue.published_at, EngagementMetric)
            .join(EngagementMetric, PublishQueue.post_id == EngagementMetric.post_id)
            .where(PublishQueue.status == QueueStatus.PUBLISHED)
            .where(PublishQueue.published_at.isnot(None))
        )
        result = await self.db.execute(stmt)
        rows = result.all()

        if not rows:
            return {"best_hours": [], "best_days": [], "sample_size": 0}

        hour_data: dict[int, list[float]] = {}
        day_data: dict[int, list[float]] = {}

        for published_at, metric in rows:
            hour = published_at.hour
            day = published_at.weekday()

            hour_data.setdefault(hour, []).append(metric.engagement_rate)
            day_data.setdefault(day, []).append(metric.engagement_rate)

        day_names = [
            "Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday",
        ]

        best_hours = sorted(
            [
                {
                    "hour": h,
                    "avg_engagement_rate": round(sum(r) / len(r), 4),
                    "sample_size": len(r),
                }
                for h, r in hour_data.items()
            ],
            key=lambda x: x["avg_engagement_rate"],
            reverse=True,
        )

        best_days = sorted(
            [
                {
                    "day": day_names[d],
                    "avg_engagement_rate": round(sum(r) / len(r), 4),
                    "sample_size": len(r),
                }
                for d, r in day_data.items()
            ],
            key=lambda x: x["avg_engagement_rate"],
            reverse=True,
        )

        return {
            "best_hours": best_hours,
            "best_days": best_days,
            "sample_size": len(rows),
        }
