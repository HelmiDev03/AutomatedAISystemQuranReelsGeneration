"""Pydantic schemas for media assets."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class MediaTypeEnum(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    CAROUSEL_SLIDE = "carousel_slide"
    AUDIO = "audio"


class MediaAssetResponse(BaseModel):
    """Response containing a media asset."""

    asset_id: str
    post_id: str
    media_type: MediaTypeEnum
    cdn_url: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: int | None = None
    slide_order: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
