"""Analytics API endpoints."""

from datetime import datetime, timezone, timedelta

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.models.post import GeneratedPost, PostStatus
from app.models.metrics import EngagementMetric
from app.models.performance import TopicPerformance
from app.models.topic import ContentTopic
from app.schemas.metrics import (
    DashboardResponse,
    EngagementResponse,
    ContentTypeStats,
)
from app.schemas.topic import TopicPerformanceResponse

logger = structlog.get_logger()

router = APIRouter()


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(db: AsyncSession = Depends(get_db)):
    """Dashboard overview with key metrics."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=7)

    # Total posts
    total_stmt = select(func.count()).select_from(GeneratedPost)
    total = (await db.execute(total_stmt)).scalar() or 0

    # Published posts
    published_stmt = (
        select(func.count())
        .select_from(GeneratedPost)
        .where(GeneratedPost.status == PostStatus.PUBLISHED)
    )
    published = (await db.execute(published_stmt)).scalar() or 0

    # Posts in review
    review_stmt = (
        select(func.count())
        .select_from(GeneratedPost)
        .where(GeneratedPost.status.in_([PostStatus.VERIFIED, PostStatus.READY]))
    )
    in_review = (await db.execute(review_stmt)).scalar() or 0

    # Rejected posts
    rejected_stmt = (
        select(func.count())
        .select_from(GeneratedPost)
        .where(GeneratedPost.status == PostStatus.REJECTED)
    )
    rejected = (await db.execute(rejected_stmt)).scalar() or 0

    # Average engagement rate
    avg_rate_stmt = select(
        func.avg(
            (EngagementMetric.likes + EngagementMetric.comments +
             EngagementMetric.shares + EngagementMetric.saves).cast(db.bind.dialect.name == 'postgresql' and 'FLOAT' or 'REAL')
        )
    ).select_from(EngagementMetric)
    # Simplified: just compute from available data
    metrics_stmt = select(EngagementMetric)
    metrics_result = await db.execute(metrics_stmt)
    all_metrics = metrics_result.scalars().all()

    if all_metrics:
        rates = [m.engagement_rate for m in all_metrics]
        avg_rate = sum(rates) / len(rates) if rates else 0.0
        total_reach = sum(m.reach for m in all_metrics)
    else:
        avg_rate = 0.0
        total_reach = 0

    # Posts today
    today_stmt = (
        select(func.count())
        .select_from(GeneratedPost)
        .where(GeneratedPost.created_at >= today_start)
    )
    posts_today = (await db.execute(today_stmt)).scalar() or 0

    # Posts this week
    week_stmt = (
        select(func.count())
        .select_from(GeneratedPost)
        .where(GeneratedPost.created_at >= week_start)
    )
    posts_week = (await db.execute(week_stmt)).scalar() or 0

    # Top content type
    top_type_stmt = (
        select(GeneratedPost.content_type, func.count().label("cnt"))
        .where(GeneratedPost.status == PostStatus.PUBLISHED)
        .group_by(GeneratedPost.content_type)
        .order_by(desc("cnt"))
        .limit(1)
    )
    top_type_result = await db.execute(top_type_stmt)
    top_type_row = top_type_result.first()
    top_content_type = top_type_row[0].value if top_type_row else None

    # Best posting hour
    best_hour_stmt = (
        select(TopicPerformance.best_posting_hour)
        .where(TopicPerformance.best_posting_hour.isnot(None))
        .order_by(desc(TopicPerformance.avg_engagement_rate))
        .limit(1)
    )
    best_hour_result = await db.execute(best_hour_stmt)
    best_hour = best_hour_result.scalar_one_or_none()

    return DashboardResponse(
        total_posts=total,
        published_posts=published,
        posts_in_review=in_review,
        rejected_posts=rejected,
        avg_engagement_rate=avg_rate,
        total_reach=total_reach,
        top_content_type=top_content_type,
        best_posting_hour=best_hour,
        posts_today=posts_today,
        posts_this_week=posts_week,
    )


@router.get("/posts/{post_id}/metrics", response_model=EngagementResponse)
async def get_post_metrics(post_id: str, db: AsyncSession = Depends(get_db)):
    """Get engagement metrics for a specific post."""
    stmt = select(EngagementMetric).where(EngagementMetric.post_id == post_id)
    result = await db.execute(stmt)
    metric = result.scalar_one_or_none()

    if not metric:
        raise HTTPException(status_code=404, detail="Metrics not found for this post")

    return EngagementResponse(
        metric_id=metric.metric_id,
        post_id=metric.post_id,
        likes=metric.likes,
        comments=metric.comments,
        shares=metric.shares,
        saves=metric.saves,
        reach=metric.reach,
        impressions=metric.impressions,
        engagement_rate=metric.engagement_rate,
        recorded_at=metric.recorded_at,
    )


@router.get("/topics/performance")
async def get_topics_performance(db: AsyncSession = Depends(get_db)):
    """Get performance metrics for all topics."""
    stmt = (
        select(TopicPerformance, ContentTopic)
        .join(ContentTopic, TopicPerformance.topic_id == ContentTopic.topic_id)
        .order_by(desc(TopicPerformance.avg_engagement_rate))
    )
    result = await db.execute(stmt)
    rows = result.all()

    return {
        "topics": [
            {
                "topic_id": perf.topic_id,
                "topic_name": topic.name,
                "category": topic.category.value,
                "avg_engagement_rate": perf.avg_engagement_rate,
                "best_posting_hour": perf.best_posting_hour,
                "post_count": perf.post_count,
                "total_reach": perf.total_reach,
                "total_saves": perf.total_saves,
            }
            for perf, topic in rows
        ],
        "total": len(rows),
    }


@router.get("/content-types")
async def get_content_type_stats(db: AsyncSession = Depends(get_db)):
    """Performance breakdown by content type."""
    # Get published posts grouped by content type
    stmt = (
        select(
            GeneratedPost.content_type,
            func.count().label("post_count"),
        )
        .where(GeneratedPost.status == PostStatus.PUBLISHED)
        .group_by(GeneratedPost.content_type)
        .order_by(desc("post_count"))
    )
    result = await db.execute(stmt)
    rows = result.all()

    stats = []
    for content_type, count in rows:
        # Get metrics for this content type
        metrics_stmt = (
            select(EngagementMetric)
            .join(GeneratedPost, EngagementMetric.post_id == GeneratedPost.post_id)
            .where(GeneratedPost.content_type == content_type)
        )
        metrics_result = await db.execute(metrics_stmt)
        metrics = metrics_result.scalars().all()

        avg_rate = 0.0
        total_reach = 0
        total_saves = 0
        if metrics:
            rates = [m.engagement_rate for m in metrics]
            avg_rate = sum(rates) / len(rates)
            total_reach = sum(m.reach for m in metrics)
            total_saves = sum(m.saves for m in metrics)

        stats.append(ContentTypeStats(
            content_type=content_type.value if hasattr(content_type, 'value') else content_type,
            post_count=count,
            avg_engagement_rate=avg_rate,
            total_reach=total_reach,
            total_saves=total_saves,
        ))

    return {"content_types": stats}


@router.get("/best-times")
async def get_best_posting_times(db: AsyncSession = Depends(get_db)):
    """Analyze engagement data to find optimal posting times."""
    # Get engagement metrics with publish times
    from app.models.queue import PublishQueue, QueueStatus

    stmt = (
        select(PublishQueue.published_at, EngagementMetric)
        .join(EngagementMetric, PublishQueue.post_id == EngagementMetric.post_id)
        .where(PublishQueue.status == QueueStatus.PUBLISHED)
        .where(PublishQueue.published_at.isnot(None))
    )
    result = await db.execute(stmt)
    rows = result.all()

    if not rows:
        return {
            "best_hours": [],
            "best_days": [],
            "message": "Not enough data yet. Publish more posts to see patterns.",
        }

    # Group by hour
    hour_data: dict[int, list[float]] = {}
    day_data: dict[int, list[float]] = {}

    for published_at, metric in rows:
        hour = published_at.hour
        day = published_at.weekday()  # 0=Monday

        if hour not in hour_data:
            hour_data[hour] = []
        hour_data[hour].append(metric.engagement_rate)

        if day not in day_data:
            day_data[day] = []
        day_data[day].append(metric.engagement_rate)

    # Calculate averages
    best_hours = sorted(
        [
            {"hour": h, "avg_engagement_rate": sum(rates) / len(rates), "post_count": len(rates)}
            for h, rates in hour_data.items()
        ],
        key=lambda x: x["avg_engagement_rate"],
        reverse=True,
    )

    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    best_days = sorted(
        [
            {"day": day_names[d], "avg_engagement_rate": sum(rates) / len(rates), "post_count": len(rates)}
            for d, rates in day_data.items()
        ],
        key=lambda x: x["avg_engagement_rate"],
        reverse=True,
    )

    return {"best_hours": best_hours, "best_days": best_days}
