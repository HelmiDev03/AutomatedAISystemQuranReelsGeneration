"""Main content pipeline task — runs every 55 minutes via Celery Beat.

This is the heart of the system: pick topic → generate → verify → render → upload → publish.
"""

import asyncio
import sys
from datetime import datetime, timezone

import structlog

from app.tasks.celery_app import celery_app

# Windows event loop fix
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logger = structlog.get_logger()


async def _run_pipeline() -> dict:
    """Async implementation of the content pipeline.

    Steps:
    1. Select topic from weighted rotation queue
    2. Generate content via RAG-grounded LLM
    3. Verify content (5-step verification)
    4. Render media (quote card / carousel / reel)
    5. Upload to Cloudinary
    6. If review_mode → send to Telegram for approval
    7. Else if verified → publish to Instagram
    8. Log results and update topic weights
    """
    from app.config import get_settings
    from app.database import async_session
    from app.models.post import GeneratedPost, PostStatus, MediaFormat
    from app.models.media import MediaAsset, MediaType
    from app.models.queue import PublishQueue, QueueStatus
    from app.services.rag_engine import RAGEngine
    from app.services.content_generator import ContentGenerator
    from app.services.verifier import ContentVerifier
    from app.services.diversity import DiversityManager
    from app.services.media_renderer import MediaRenderer
    from app.services.cloudinary_storage import CloudinaryStorage
    from app.services.telegram_bot import TelegramReviewBot
    from app.services.publisher import InstagramPublisher

    settings = get_settings()
    result = {"status": "started", "post_id": None, "errors": []}

    try:
        # Initialize services
        rag_engine = RAGEngine(settings)
        content_gen = ContentGenerator(settings, rag_engine)
        verifier = ContentVerifier(settings, rag_engine)
        media_renderer = MediaRenderer(settings)
        storage = CloudinaryStorage(settings)
        telegram = TelegramReviewBot(settings)

        async with async_session() as db:
            # ── Step 1: Select Topic ─────────────────────────────────────
            logger.info("pipeline.step1", action="selecting_topic")
            diversity = DiversityManager(settings, db, rag_engine)
            topic = await diversity.select_topic()

            if not topic:
                logger.warning("pipeline.no_topic", msg="No available topics")
                result["status"] = "no_topic"
                return result

            logger.info(
                "pipeline.topic_selected",
                topic_id=topic.topic_id,
                topic_name=topic.name,
                category=topic.category.value,
            )

            # ── Step 2: Generate Content ─────────────────────────────────
            logger.info("pipeline.step2", action="generating_content")
            generated = await content_gen.generate(
                content_type=topic.category.value,
                topic_name=topic.name,
            )

            # Create post record
            post = GeneratedPost(
                topic_id=topic.topic_id,
                content_type=generated.get("content_type", topic.category.value),
                media_format=generated.get("media_format", "quote_card"),
                arabic_text=generated["arabic_text"],
                english_text=generated["english_text"],
                caption=generated.get("caption", ""),
                hashtags=" ".join(generated.get("hashtags", [])),
                source_ref=generated.get("source_ref", ""),
                hadith_grade=generated.get("hadith_grade"),
                confidence_score=generated.get("confidence", 0.0),
                status=PostStatus.VERIFYING,
            )
            db.add(post)
            await db.flush()
            result["post_id"] = post.post_id

            logger.info(
                "pipeline.content_generated",
                post_id=post.post_id,
                confidence=post.confidence_score,
            )

            # ── Step 3: Verify Content ───────────────────────────────────
            logger.info("pipeline.step3", action="verifying_content")
            verification = await verifier.verify({
                "arabic_text": post.arabic_text,
                "english_text": post.english_text,
                "source_ref": post.source_ref,
                "hadith_grade": post.hadith_grade,
                "content_type": post.content_type.value if hasattr(post.content_type, 'value') else post.content_type,
            })

            post.confidence_score = verification.confidence_score
            post.verified_at = datetime.now(timezone.utc)

            if not verification.passed:
                post.status = PostStatus.REJECTED
                post.rejection_reason = "; ".join(
                    issue for issue in verification.issues
                )
                await db.commit()
                logger.warning(
                    "pipeline.verification_failed",
                    post_id=post.post_id,
                    issues=verification.issues,
                )
                result["status"] = "rejected"
                result["errors"] = verification.issues
                return result

            post.status = PostStatus.VERIFIED
            logger.info(
                "pipeline.verified",
                post_id=post.post_id,
                confidence=verification.confidence_score,
            )

            # ── Step 4: Render Media ─────────────────────────────────────
            logger.info("pipeline.step4", action="rendering_media")
            post.status = PostStatus.RENDERING

            media_format = post.media_format
            if hasattr(media_format, 'value'):
                media_format = media_format.value

            if media_format == "quote_card":
                file_path = await media_renderer.render_quote_card(
                    arabic_text=post.arabic_text,
                    english_text=post.english_text,
                    source_ref=post.source_ref or "",
                )
                media_type = MediaType.IMAGE
            elif media_format == "carousel":
                file_paths = await media_renderer.render_carousel(
                    slides=generated.get("slides", [
                        {"arabic_text": post.arabic_text, "english_text": post.english_text}
                    ])
                )
                # Use first slide as primary, create assets for all
                file_path = file_paths[0] if file_paths else None
                media_type = MediaType.CAROUSEL_SLIDE
            elif media_format == "reel":
                file_path = await media_renderer.render_reel(
                    narration_text=post.english_text,
                    arabic_text=post.arabic_text,
                )
                media_type = MediaType.VIDEO
            else:
                file_path = await media_renderer.render_quote_card(
                    arabic_text=post.arabic_text,
                    english_text=post.english_text,
                    source_ref=post.source_ref or "",
                )
                media_type = MediaType.IMAGE

            if not file_path:
                post.status = PostStatus.FAILED
                await db.commit()
                result["status"] = "render_failed"
                return result

            # ── Step 5: Upload to Cloudinary ─────────────────────────────
            logger.info("pipeline.step5", action="uploading_media")

            if media_type == MediaType.VIDEO:
                upload_result = await storage.upload_video(file_path)
            else:
                upload_result = await storage.upload_image(file_path)

            # Create media asset record
            media_asset = MediaAsset(
                post_id=post.post_id,
                media_type=media_type,
                file_path=file_path,
                cdn_url=upload_result["url"],
                cloudinary_public_id=upload_result["public_id"],
            )
            db.add(media_asset)

            # Handle carousel slides
            if media_format == "carousel" and len(file_paths) > 1:
                for i, slide_path in enumerate(file_paths[1:], start=1):
                    slide_upload = await storage.upload_image(slide_path)
                    slide_asset = MediaAsset(
                        post_id=post.post_id,
                        media_type=MediaType.CAROUSEL_SLIDE,
                        file_path=slide_path,
                        cdn_url=slide_upload["url"],
                        cloudinary_public_id=slide_upload["public_id"],
                        slide_order=i,
                    )
                    db.add(slide_asset)

            # ── Step 6: Review or Publish ────────────────────────────────
            needs_review = (
                settings.review_mode
                or verification.confidence_score < settings.confidence_threshold
                or verification.needs_human_review
            )

            if needs_review:
                logger.info(
                    "pipeline.step6",
                    action="sending_for_review",
                    reason="review_mode" if settings.review_mode else "low_confidence",
                )
                post.status = PostStatus.READY

                # Send to Telegram for review
                try:
                    await telegram.send_for_review(
                        post_data={
                            "post_id": post.post_id,
                            "content_type": post.content_type.value if hasattr(post.content_type, 'value') else post.content_type,
                            "arabic_text": post.arabic_text,
                            "english_text": post.english_text,
                            "source_ref": post.source_ref,
                            "confidence_score": post.confidence_score,
                        },
                        media_path=file_path,
                    )
                except Exception as e:
                    logger.warning("pipeline.telegram_failed", error=str(e))

                await db.commit()
                result["status"] = "awaiting_review"
                return result

            # Auto-publish (review_mode is off and confidence is high)
            logger.info("pipeline.step6", action="publishing")
            publisher = InstagramPublisher(settings)

            try:
                caption_text = post.caption or ""
                if post.hashtags and "#" not in caption_text:
                    caption_text = f"{caption_text}\n\n" + " ".join(f"#{tag.lstrip('#')}" for tag in post.hashtags.split())

                if media_format == "reel":
                    ig_media_id = await publisher.publish_reel(
                        video_url=upload_result["url"],
                        caption=caption_text or "",
                    )
                elif media_format == "carousel":
                    carousel_urls = [upload_result["url"]]
                    for asset in post.media_assets:
                        if asset.cdn_url and asset.cdn_url != upload_result["url"]:
                            carousel_urls.append(asset.cdn_url)
                    ig_media_id = await publisher.publish_carousel(
                        image_urls=carousel_urls,
                        caption=caption_text or "",
                    )
                else:
                    ig_media_id = await publisher.publish_image(
                        image_url=upload_result["url"],
                        caption=caption_text or "",
                    )

                # Create publish queue entry
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

                logger.info(
                    "pipeline.published",
                    post_id=post.post_id,
                    ig_media_id=ig_media_id,
                )
                result["status"] = "published"

            except Exception as e:
                logger.error("pipeline.publish_failed", error=str(e))
                post.status = PostStatus.FAILED

                # Notify via Telegram
                try:
                    await telegram.send_error_alert(
                        error=str(e),
                        context=f"Publishing post {post.post_id}",
                    )
                except Exception:
                    pass

                result["status"] = "publish_failed"
                result["errors"].append(str(e))

            # ── Step 7: Update weights ───────────────────────────────────
            await diversity.update_weights_after_use(topic.topic_id)
            await db.commit()

            # Record content for dedup
            try:
                await diversity.record_published_content(
                    post.post_id,
                    f"{post.arabic_text} {post.english_text}",
                )
            except Exception as e:
                logger.warning("pipeline.dedup_record_failed", error=str(e))

    except Exception as e:
        logger.exception("pipeline.unhandled_error", error=str(e))
        result["status"] = "error"
        result["errors"].append(str(e))

        # Try to send error notification
        try:
            settings = get_settings()
            telegram = TelegramReviewBot(settings)
            await telegram.send_error_alert(
                error=str(e),
                context="Content pipeline unhandled error",
            )
        except Exception:
            pass

    return result


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=300,  # 5 minutes between retries
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=1800,  # Max 30 min backoff
)
def run_content_pipeline(self) -> dict:
    """Main content pipeline — Celery task entry point.

    Runs every 55 minutes via Celery Beat.
    Wraps the async pipeline in asyncio.run().
    """
    logger.info(
        "pipeline.task_started",
        task_id=self.request.id,
        retry=self.request.retries,
    )

    try:
        result = asyncio.run(_run_pipeline())
        logger.info("pipeline.task_completed", result=result)
        return result
    except Exception as exc:
        logger.exception("pipeline.task_error", error=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=2)
def publish_approved_post(self, post_id: str) -> dict:
    """Publish a post that was approved via Telegram review.

    Called when a reviewer approves a post.
    """

    async def _publish(post_id: str) -> dict:
        from app.config import get_settings
        from app.database import async_session
        from app.models.post import GeneratedPost, PostStatus
        from app.models.queue import PublishQueue, QueueStatus
        from app.services.publisher import InstagramPublisher
        from app.services.telegram_bot import TelegramReviewBot

        settings = get_settings()
        publisher = InstagramPublisher(settings)
        telegram = TelegramReviewBot(settings)

        async with async_session() as db:
            from sqlalchemy import select

            stmt = select(GeneratedPost).where(GeneratedPost.post_id == post_id)
            result = await db.execute(stmt)
            post = result.scalar_one_or_none()

            if not post:
                return {"status": "not_found"}

            if not post.media_assets:
                return {"status": "no_media"}

            primary_asset = post.media_assets[0]
            caption_text = post.caption or ""
            if post.hashtags and "#" not in caption_text:
                caption_text = f"{caption_text}\n\n" + " ".join(f"#{tag.lstrip('#')}" for tag in post.hashtags.split())

            try:
                media_format = post.media_format
                if hasattr(media_format, 'value'):
                    media_format = media_format.value

                if media_format == "reel":
                    ig_media_id = await publisher.publish_reel(
                        video_url=primary_asset.cdn_url,
                        caption=caption_text,
                    )
                elif media_format == "carousel":
                    urls = [a.cdn_url for a in sorted(post.media_assets, key=lambda a: a.slide_order or 0)]
                    ig_media_id = await publisher.publish_carousel(
                        image_urls=urls,
                        caption=caption_text,
                    )
                else:
                    ig_media_id = await publisher.publish_image(
                        image_url=primary_asset.cdn_url,
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
                await db.commit()

                await telegram.send_notification(
                    f"✅ Post {post_id} published successfully!\nIG Media ID: {ig_media_id}"
                )

                return {"status": "published", "ig_media_id": ig_media_id}

            except Exception as e:
                await telegram.send_error_alert(str(e), f"Publishing approved post {post_id}")
                return {"status": "failed", "error": str(e)}

    return asyncio.run(_publish(post_id))
