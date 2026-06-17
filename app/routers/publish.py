"""Publishing API endpoints."""

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.models.post import GeneratedPost, PostStatus
from app.models.queue import PublishQueue, QueueStatus
from app.schemas.post import PipelineStatusResponse

logger = structlog.get_logger()

router = APIRouter()


@router.post("/now/{post_id}")
async def publish_now(
    post_id: str,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Immediately publish a specific post to Instagram."""
    from app.services.publisher import InstagramPublisher
    from app.services.cloudinary_storage import CloudinaryStorage
    from app.services.media_renderer import MediaRenderer

    stmt = select(GeneratedPost).where(GeneratedPost.post_id == post_id)
    result = await db.execute(stmt)
    post = result.scalar_one_or_none()

    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if post.status not in (PostStatus.VERIFIED, PostStatus.READY):
        raise HTTPException(
            status_code=400,
            detail=f"Post cannot be published — current status: {post.status.value}",
        )

    # Check if media exists
    if not post.media_assets:
        # Render media first
        renderer = MediaRenderer(settings)
        storage = CloudinaryStorage(settings)

        file_path = await renderer.render_quote_card(
            arabic_text=post.arabic_text,
            english_text=post.english_text,
            source_ref=post.source_ref or "",
        )
        upload_result = await storage.upload_image(file_path)
        cdn_url = upload_result["url"]
    else:
        cdn_url = post.media_assets[0].cdn_url

    if not cdn_url:
        raise HTTPException(status_code=400, detail="No media URL available")

    # Publish to Instagram
    if not settings.instagram_access_token:
        raise HTTPException(status_code=400, detail="Instagram not configured")

    caption_text = post.caption or ""
    if post.hashtags and "#" not in caption_text:
        caption_text = f"{caption_text}\n\n" + " ".join(f"#{tag.lstrip('#')}" for tag in post.hashtags.split())

    try:
        ig_media_id = await publisher.publish_image(
            image_url=cdn_url,
            caption=caption_text,
        )

        queue_entry = PublishQueue(
            post_id=post.post_id,
            scheduled_at=datetime.now(timezone.utc),
            published_at=datetime.now(timezone.utc),
            ig_media_id=ig_media_id,
            status=QueueStatus.PUBLISHED,
        )
        db.add(queue_entry)

        post.status = PostStatus.PUBLISHED
        post.published_at = datetime.now(timezone.utc)

        return {
            "status": "published",
            "post_id": post_id,
            "ig_media_id": ig_media_id,
        }

    except Exception as e:
        logger.exception("publish.failed", post_id=post_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Publishing failed: {str(e)}")


@router.get("/queue")
async def get_publish_queue(db: AsyncSession = Depends(get_db)):
    """View the publish queue."""
    stmt = (
        select(PublishQueue)
        .order_by(desc(PublishQueue.created_at))
        .limit(50)
    )
    result = await db.execute(stmt)
    entries = result.scalars().all()

    return {
        "queue": [
            {
                "queue_id": e.queue_id,
                "post_id": e.post_id,
                "scheduled_at": e.scheduled_at.isoformat(),
                "published_at": e.published_at.isoformat() if e.published_at else None,
                "ig_media_id": e.ig_media_id,
                "status": e.status.value,
                "retry_count": e.retry_count,
            }
            for e in entries
        ],
        "total": len(entries),
    }


@router.post("/pipeline/trigger")
async def trigger_pipeline():
    """Manually trigger the content pipeline."""
    from app.tasks.pipeline import run_content_pipeline

    task = run_content_pipeline.delay()

    return {
        "status": "triggered",
        "task_id": task.id,
        "message": "Content pipeline started. Check task status for results.",
    }


@router.get("/pipeline/status", response_model=PipelineStatusResponse)
async def pipeline_status(db: AsyncSession = Depends(get_db)):
    """Get the current pipeline status."""
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Posts generated today
    gen_stmt = (
        select(func.count())
        .select_from(GeneratedPost)
        .where(GeneratedPost.created_at >= today_start)
    )
    gen_result = await db.execute(gen_stmt)
    posts_generated_today = gen_result.scalar() or 0

    # Posts published today
    pub_stmt = (
        select(func.count())
        .select_from(PublishQueue)
        .where(PublishQueue.published_at >= today_start)
        .where(PublishQueue.status == QueueStatus.PUBLISHED)
    )
    pub_result = await db.execute(pub_stmt)
    posts_published_today = pub_result.scalar() or 0

    # Posts in review
    review_stmt = (
        select(func.count())
        .select_from(GeneratedPost)
        .where(
            GeneratedPost.status.in_([PostStatus.VERIFIED, PostStatus.READY])
        )
    )
    review_result = await db.execute(review_stmt)
    posts_in_review = review_result.scalar() or 0

    # Last publish time
    last_pub_stmt = (
        select(PublishQueue.published_at)
        .where(PublishQueue.status == QueueStatus.PUBLISHED)
        .order_by(desc(PublishQueue.published_at))
        .limit(1)
    )
    last_pub_result = await db.execute(last_pub_stmt)
    last_run = last_pub_result.scalar_one_or_none()

    return PipelineStatusResponse(
        is_running=False,  # Would need Celery inspect for real status
        last_run_at=last_run,
        next_run_at=None,
        posts_generated_today=posts_generated_today,
        posts_published_today=posts_published_today,
        posts_in_review=posts_in_review,
    )
