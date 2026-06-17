"""Pydantic schemas for analytics and metrics."""

from datetime import datetime

from pydantic import BaseModel


class EngagementResponse(BaseModel):
    """Engagement metrics for a single post."""

    metric_id: str
    post_id: str
    likes: int
    comments: int
    shares: int
    saves: int
    reach: int
    impressions: int
    engagement_rate: float
    recorded_at: datetime

    model_config = {"from_attributes": True}


class DashboardResponse(BaseModel):
    """Overview dashboard data."""

    total_posts: int
    published_posts: int
    posts_in_review: int
    rejected_posts: int
    avg_engagement_rate: float
    total_reach: int
    top_content_type: str | None = None
    best_posting_hour: int | None = None
    posts_today: int
    posts_this_week: int


class ContentTypeStats(BaseModel):
    """Performance breakdown by content type."""

    content_type: str
    post_count: int
    avg_engagement_rate: float
    total_reach: int
    total_saves: int
