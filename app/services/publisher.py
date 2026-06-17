"""Instagram Publisher — Graph API v22.0 client for image, carousel, and reel publishing.

Implements the two-step Instagram Content Publishing flow:
    1. **Create container** — ``POST /{ig-user-id}/media`` with the media payload.
    2. **Poll status** — ``GET /{container-id}?fields=status_code`` until ``FINISHED``.
    3. **Publish** — ``POST /{ig-user-id}/media_publish`` with the container ID.

Error handling:
    - Container status ``ERROR`` raises immediately.
    - Error code ``9007`` (container not finished) is retried via polling.
    - All API calls use ``tenacity`` exponential backoff.
    - ``httpx.AsyncClient`` is used for non-blocking HTTP.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import Settings

logger = structlog.get_logger(__name__)


class InstagramPublishError(Exception):
    """Raised when the Instagram API returns an unrecoverable error."""

    def __init__(self, message: str, error_code: int | None = None) -> None:
        self.error_code = error_code
        super().__init__(message)


class ContainerNotFinishedError(Exception):
    """Raised when the container is still processing (status != FINISHED)."""


class InstagramPublisher:
    """Async client for the Instagram Graph API Content Publishing endpoints.

    Parameters
    ----------
    settings : Settings
        Must contain ``instagram_access_token`` and ``instagram_business_id``.
    """

    BASE_URL = "https://graph.instagram.com/v22.0"

    def __init__(self, settings: Settings) -> None:
        self._access_token = settings.instagram_access_token
        self._ig_user_id = settings.instagram_business_id
        self._client = httpx.AsyncClient(
            timeout=60.0,
            headers={"User-Agent": "IslamicContentAutomation/1.0"},
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    # ── public publishing methods ────────────────────────────────────────

    async def publish_image(self, image_url: str, caption: str) -> str:
        """Publish a single image post to Instagram.

        Parameters
        ----------
        image_url : str
            Publicly accessible URL of the image (Cloudinary CDN URL).
        caption : str
            Post caption including hashtags.

        Returns
        -------
        str
            The ``ig_media_id`` of the published post.
        """
        logger.info("ig_publish_image_start", image_url=image_url)

        container_id = await self._create_container(
            image_url=image_url,
            caption=caption,
        )

        await self._poll_container_status(container_id)
        ig_media_id = await self._publish_container(container_id)

        logger.info(
            "ig_image_published",
            ig_media_id=ig_media_id,
            container_id=container_id,
        )
        return ig_media_id

    async def publish_carousel(
        self, image_urls: list[str], caption: str
    ) -> str:
        """Publish a carousel post (2–10 images) to Instagram.

        Parameters
        ----------
        image_urls : list[str]
            List of publicly accessible image URLs (2-10 items).
        caption : str
            Caption for the carousel post.

        Returns
        -------
        str
            The ``ig_media_id`` of the published carousel.

        Raises
        ------
        ValueError
            If fewer than 2 or more than 10 images are provided.
        """
        if not 2 <= len(image_urls) <= 10:
            raise ValueError(
                f"Carousel requires 2-10 images, got {len(image_urls)}"
            )

        logger.info("ig_publish_carousel_start", slide_count=len(image_urls))

        # Step 1: Create child containers (can be done concurrently) ----------
        child_tasks = [
            self._create_container(image_url=url, is_carousel_item=True)
            for url in image_urls
        ]
        child_ids = await asyncio.gather(*child_tasks)

        logger.debug("ig_carousel_children_created", children=list(child_ids))

        # Step 2: Create parent carousel container ----------------------------
        parent_id = await self._create_container(
            media_type="CAROUSEL",
            caption=caption,
            children=list(child_ids),
        )

        # Step 3: Poll + publish parent ---------------------------------------
        await self._poll_container_status(parent_id)
        ig_media_id = await self._publish_container(parent_id)

        logger.info(
            "ig_carousel_published",
            ig_media_id=ig_media_id,
            slide_count=len(image_urls),
        )
        return ig_media_id

    async def publish_reel(
        self,
        video_url: str,
        caption: str,
        *,
        cover_url: str | None = None,
        share_to_feed: bool = True,
    ) -> str:
        """Publish a reel (short video) to Instagram.

        Parameters
        ----------
        video_url : str
            Publicly accessible URL of the video (Cloudinary CDN URL).
        caption : str
            Reel caption.
        cover_url : str | None
            Optional cover image URL.
        share_to_feed : bool
            Whether to also share the reel to the feed grid.

        Returns
        -------
        str
            The ``ig_media_id`` of the published reel.
        """
        logger.info("ig_publish_reel_start", video_url=video_url)

        params: dict[str, Any] = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": str(share_to_feed).lower(),
        }
        if cover_url:
            params["cover_url"] = cover_url

        container_id = await self._create_container(**params)

        # Reels take longer to process — use extended timeout
        await self._poll_container_status(container_id, timeout=300)
        ig_media_id = await self._publish_container(container_id)

        logger.info(
            "ig_reel_published",
            ig_media_id=ig_media_id,
            container_id=container_id,
        )
        return ig_media_id

    # ── insights ─────────────────────────────────────────────────────────

    async def get_post_insights(self, ig_media_id: str) -> dict[str, Any]:
        """Fetch engagement insights for a published post.

        Parameters
        ----------
        ig_media_id : str
            The Instagram media ID returned by publish methods.

        Returns
        -------
        dict
            Engagement metrics keyed by metric name. Example::

                {
                    "impressions": 1200,
                    "reach": 800,
                    "likes": 150,
                    "comments": 12,
                    "saved": 30,
                    "shares": 5,
                    "engagement": 197,
                }
        """
        metrics = "impressions,reach,likes,comments,saved,shares"

        data = await self._api_get(
            f"/{ig_media_id}/insights",
            params={"metric": metrics},
        )

        result: dict[str, Any] = {}
        for entry in data.get("data", []):
            name = entry.get("name", "")
            values = entry.get("values", [{}])
            result[name] = values[0].get("value", 0) if values else 0

        logger.info("ig_insights_fetched", ig_media_id=ig_media_id, metrics=result)
        return result

    # ── container lifecycle ──────────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type(
            (httpx.HTTPError, httpx.TimeoutException)
        ),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=15),
        reraise=True,
    )
    async def _create_container(self, **params: Any) -> str:
        """Create a media container via the Instagram API.

        Parameters
        ----------
        **params
            Key-value pairs passed directly to the API. Common keys:
            ``image_url``, ``video_url``, ``caption``, ``media_type``,
            ``children``, ``is_carousel_item``.

        Returns
        -------
        str
            The container (creation) ID.
        """
        payload = {
            "access_token": self._access_token,
            **params,
        }

        # Convert list of children to comma-separated string if present
        if "children" in payload and isinstance(payload["children"], list):
            payload["children"] = ",".join(payload["children"])

        response = await self._client.post(
            f"{self.BASE_URL}/{self._ig_user_id}/media",
            data=payload,
        )
        response.raise_for_status()
        data = response.json()

        if "id" not in data:
            error = data.get("error", {})
            raise InstagramPublishError(
                f"Container creation failed: {error.get('message', data)}",
                error_code=error.get("code"),
            )

        container_id = data["id"]
        logger.debug("ig_container_created", container_id=container_id)
        return container_id

    async def _poll_container_status(
        self, container_id: str, *, timeout: int = 60
    ) -> bool:
        """Poll a container until its status is ``FINISHED``.

        Parameters
        ----------
        container_id : str
            The container ID to poll.
        timeout : int
            Maximum seconds to wait before raising.

        Returns
        -------
        bool
            ``True`` when the container is finished.

        Raises
        ------
        InstagramPublishError
            If the container enters ``ERROR`` status.
        ContainerNotFinishedError
            If the timeout is exceeded.
        """
        poll_interval = 2  # seconds
        elapsed = 0

        while elapsed < timeout:
            data = await self._api_get(
                f"/{container_id}",
                params={"fields": "status_code,status"},
            )

            status_code = data.get("status_code", "").upper()
            logger.debug(
                "ig_container_poll",
                container_id=container_id,
                status=status_code,
                elapsed=elapsed,
            )

            if status_code == "FINISHED":
                return True

            if status_code == "ERROR":
                error_status = data.get("status", "Unknown error")
                raise InstagramPublishError(
                    f"Container {container_id} failed: {error_status}"
                )

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            # Gradually increase interval for long-running containers
            if elapsed > 30:
                poll_interval = min(poll_interval + 1, 10)

        raise ContainerNotFinishedError(
            f"Container {container_id} not finished after {timeout}s"
        )

    @retry(
        retry=retry_if_exception_type(
            (httpx.HTTPError, httpx.TimeoutException, ContainerNotFinishedError)
        ),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, max=20),
        reraise=True,
    )
    async def _publish_container(self, container_id: str) -> str:
        """Publish a finished container.

        Parameters
        ----------
        container_id : str
            The container ID to publish.

        Returns
        -------
        str
            The published ``ig_media_id``.
        """
        response = await self._client.post(
            f"{self.BASE_URL}/{self._ig_user_id}/media_publish",
            data={
                "creation_id": container_id,
                "access_token": self._access_token,
            },
        )
        response.raise_for_status()
        data = response.json()

        if "id" not in data:
            error = data.get("error", {})
            error_code = error.get("code")

            # Error 9007 = container not finished yet — let tenacity retry
            if error_code == 9007:
                raise ContainerNotFinishedError(
                    f"Container {container_id} not ready (code 9007)"
                )

            raise InstagramPublishError(
                f"Publish failed: {error.get('message', data)}",
                error_code=error_code,
            )

        ig_media_id = data["id"]
        logger.info("ig_container_published", ig_media_id=ig_media_id)
        return ig_media_id

    # ── low-level HTTP helpers ───────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type(
            (httpx.HTTPError, httpx.TimeoutException)
        ),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=10),
        reraise=True,
    )
    async def _api_get(
        self, endpoint: str, *, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """Perform a GET request against the Instagram Graph API.

        Parameters
        ----------
        endpoint : str
            Path relative to ``BASE_URL`` (e.g. ``"/{id}/insights"``).
        params : dict | None
            Additional query parameters.

        Returns
        -------
        dict
            Parsed JSON response.
        """
        query: dict[str, str] = {"access_token": self._access_token}
        if params:
            query.update(params)

        response = await self._client.get(
            f"{self.BASE_URL}{endpoint}",
            params=query,
        )
        response.raise_for_status()
        return response.json()
