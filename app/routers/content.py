"""Content management API endpoints."""

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.models.post import GeneratedPost, PostStatus
from app.models.topic import ContentTopic
from app.schemas.post import (
    GenerateContentRequest,
    GenerationResult,
    PostListResponse,
    PostResponse,
    PostStatusEnum,
    ReviewPostRequest,
)
from app.schemas.topic import TopicListResponse, TopicResponse

logger = structlog.get_logger()

router = APIRouter()


@router.post("/generate", response_model=GenerationResult)
async def generate_content(
    request: GenerateContentRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Trigger content generation for a specific type or random topic."""
    from app.services.rag_engine import RAGEngine
    from app.services.content_generator import ContentGenerator
    from app.services.verifier import ContentVerifier
    from app.services.diversity import DiversityManager

    try:
        rag_engine = RAGEngine(settings)
        content_gen = ContentGenerator(settings, rag_engine)
        verifier = ContentVerifier(settings, rag_engine)
        diversity = DiversityManager(settings, db, rag_engine)

        # Select topic
        if request.topic_id:
            stmt = select(ContentTopic).where(ContentTopic.topic_id == request.topic_id)
            result = await db.execute(stmt)
            topic = result.scalar_one_or_none()
            if not topic:
                raise HTTPException(status_code=404, detail="Topic not found")
        else:
            topic = await diversity.select_topic()
            if not topic:
                raise HTTPException(status_code=404, detail="No available topics")

        # Generate
        content_type = request.content_type.value if request.content_type else topic.category.value
        generated = await content_gen.generate(
            content_type=content_type,
            topic_name=topic.name,
            media_format=request.media_format.value,
        )

        # Create post
        post = GeneratedPost(
            topic_id=topic.topic_id,
            content_type=generated.get("content_type", content_type),
            media_format=generated.get("media_format", request.media_format.value),
            arabic_text=generated["arabic_text"],
            english_text=generated["english_text"],
            caption=generated.get("caption", ""),
            hashtags=" ".join(generated.get("hashtags", [])),
            source_ref=generated.get("source_ref", ""),
            hadith_grade=generated.get("hadith_grade"),
            confidence_score=generated.get("confidence", 0.0),
            status=PostStatus.DRAFT,
        )
        db.add(post)

        # Verify
        verification = await verifier.verify({
            "arabic_text": post.arabic_text,
            "english_text": post.english_text,
            "source_ref": post.source_ref,
            "hadith_grade": post.hadith_grade,
            "content_type": content_type,
        })

        post.confidence_score = verification.confidence_score
        post.verified_at = datetime.now(timezone.utc)

        if verification.passed:
            post.status = PostStatus.VERIFIED
            message = "Content generated and verified successfully"
        else:
            post.status = PostStatus.REJECTED
            post.rejection_reason = "; ".join(verification.issues)
            message = f"Content rejected: {post.rejection_reason}"

        await db.flush()

        return GenerationResult(
            post_id=post.post_id,
            status=PostStatusEnum(post.status.value),
            confidence_score=post.confidence_score,
            message=message,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("content.generate_error", error=str(e))
        raise HTTPException(status_code=500, detail=f"Generation failed: {str(e)}")


@router.get("/posts", response_model=PostListResponse)
async def list_posts(
    status: PostStatusEnum | None = None,
    content_type: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List generated posts with optional filtering."""
    stmt = select(GeneratedPost).order_by(desc(GeneratedPost.created_at))

    if status:
        stmt = stmt.where(GeneratedPost.status == status.value)
    if content_type:
        stmt = stmt.where(GeneratedPost.content_type == content_type)

    # Count total
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    # Paginate
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(stmt)
    posts = result.scalars().all()

    return PostListResponse(
        posts=[PostResponse.model_validate(p) for p in posts],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/posts/{post_id}", response_model=PostResponse)
async def get_post(post_id: str, db: AsyncSession = Depends(get_db)):
    """Get a single post by ID."""
    stmt = select(GeneratedPost).where(GeneratedPost.post_id == post_id)
    result = await db.execute(stmt)
    post = result.scalar_one_or_none()

    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    return PostResponse.model_validate(post)


@router.post("/posts/{post_id}/review", response_model=PostResponse)
async def review_post(
    post_id: str,
    request: ReviewPostRequest,
    db: AsyncSession = Depends(get_db),
):
    """Approve or reject a post from the review queue."""
    stmt = select(GeneratedPost).where(GeneratedPost.post_id == post_id)
    result = await db.execute(stmt)
    post = result.scalar_one_or_none()

    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if request.approved:
        post.status = PostStatus.READY
        post.verified_at = datetime.now(timezone.utc)
        logger.info("content.post_approved", post_id=post_id)
    else:
        post.status = PostStatus.REJECTED
        post.rejection_reason = request.rejection_reason or "Rejected by reviewer"
        logger.info("content.post_rejected", post_id=post_id, reason=request.rejection_reason)

    return PostResponse.model_validate(post)


@router.get("/review-queue", response_model=PostListResponse)
async def get_review_queue(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Get posts awaiting human review."""
    stmt = (
        select(GeneratedPost)
        .where(
            GeneratedPost.status.in_([PostStatus.VERIFIED, PostStatus.READY])
        )
        .order_by(GeneratedPost.created_at)
    )

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = stmt.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(stmt)
    posts = result.scalars().all()

    return PostListResponse(
        posts=[PostResponse.model_validate(p) for p in posts],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/topics", response_model=TopicListResponse)
async def list_topics(db: AsyncSession = Depends(get_db)):
    """List all content topics."""
    stmt = select(ContentTopic).order_by(ContentTopic.category, ContentTopic.name)
    result = await db.execute(stmt)
    topics = result.scalars().all()

    return TopicListResponse(
        topics=[TopicResponse.model_validate(t) for t in topics],
        total=len(topics),
    )
