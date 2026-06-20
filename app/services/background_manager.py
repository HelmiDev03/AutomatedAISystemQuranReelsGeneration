"""Service for fetching Islamically-appropriate background videos from Pixabay.

All videos are filtered through strict rules:
- Nature videos: pure landscapes with NO visible humans
- Islamic videos: mosques, prayer, Quran — nothing outside Islamic guidelines
- SafeSearch is always enabled
"""

import os
import random
import uuid
from pathlib import Path

import httpx
import structlog

from app.config import Settings

logger = structlog.get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_BACKGROUNDS_DIR = _PROJECT_ROOT / "backgrounds"

# ── Safe search queries ──────────────────────────────────────────────────────
# These are pre-approved queries that produce Islamically-appropriate results.

NATURE_QUERIES = [
    "dark nature",
    "blue nature",
    "stars night sky",
    "dark forest night",
    "dark ocean waves",
    "dark mountains night",
    "blue forest mist",
    "night rain",
    "deep space nebula",
    "northern lights aurora",
    "blue waterfall night",
    "wave, night",
    "ocean, night",
    "sky, night",
]

ISLAMIC_QUERIES = [
    "mosque night",
    "minaret night",
    "lantern ramadan night",
    "islamic calligraphy",
    "tasbih night",
]


class BackgroundVideoManager:
    """Manages downloading background videos from Pixabay with Islamic filtering."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pixabay_key = getattr(settings, "pixabay_api_key", "")
        _BACKGROUNDS_DIR.mkdir(parents=True, exist_ok=True)

    async def get_background_video(self, force_new: bool = False, theme: str | None = None) -> str | None:
        """Get a background video, downloading a new one if necessary.

        Parameters
        ----------
        force_new : bool
            If True, always download a fresh video instead of using cache.
        theme : str | None
            AI-selected visual theme to search for.

        Returns
        -------
        str
            Absolute path to the downloaded MP4 file.

        Raises
        ------
        RuntimeError
            If all download attempts from Pixabay fail.
        """
        # 1. Try to use an existing cached video if we don't force a new one
        if not force_new and not theme:
            existing = [
                f for f in _BACKGROUNDS_DIR.iterdir()
                if f.suffix in {".mp4", ".webm", ".mkv"} and f.name.startswith("pixabay_")
            ]
            if existing:
                chosen = random.choice(existing)
                logger.info("background_manager.using_cached", path=str(chosen))
                return str(chosen)

        # 2. Try Pixabay API with retries
        if not self._pixabay_key:
            raise RuntimeError("Pixabay API key is missing. Cannot fetch background videos.")

        max_attempts = 5
        last_error = None

        for attempt in range(1, max_attempts + 1):
            logger.info("background_manager.pixabay_attempt", attempt=attempt, max_attempts=max_attempts)
            try:
                # If theme query keeps failing (e.g. no results or download error),
                # try fallback random nature query for attempts after the first two.
                query_theme = theme if attempt <= 2 else None
                return await self._download_from_pixabay(query_theme)
            except Exception as e:
                last_error = e
                logger.warning("background_manager.pixabay_attempt_failed", attempt=attempt, error=str(e))
                if attempt < max_attempts:
                    import asyncio
                    await asyncio.sleep(2)

        raise RuntimeError(
            f"Failed to download background video from Pixabay after {max_attempts} attempts. "
            f"Last error: {last_error}"
        )

    def _build_query(self, theme: str | None) -> str:
        """Build a safe, filtered search query.

        Maps the AI's theme suggestion to a pre-approved query list
        to prevent inappropriate content from appearing.
        """
        negative_exclusions = " -person -people -human -moon -dog -cat -pet "

        if theme:
            # If the theme matches exactly, use it directly
            if theme in NATURE_QUERIES or theme in ISLAMIC_QUERIES:
                base_query = theme
            else:
                theme_lower = theme.lower()

                # Check if the theme is Islamic/Deen-related
                islamic_keywords = ["mosque", "prayer", "quran", "islamic", "minaret",
                                    "ramadan", "eid", "masjid", "deen", "muslim",
                                    "calligraphy", "lantern", "crescent", "tasbih"]
                is_islamic = any(kw in theme_lower for kw in islamic_keywords)

                if is_islamic:
                    # Use a matching Islamic query or pick a random one
                    matching = [q for q in ISLAMIC_QUERIES if any(w in q for w in theme_lower.split())]
                    base_query = random.choice(matching) if matching else random.choice(ISLAMIC_QUERIES)
                else:
                    # For nature themes, map to safe nature queries
                    matching = [q for q in NATURE_QUERIES if any(w in q for w in theme_lower.split())]
                    base_query = random.choice(matching) if matching else random.choice(NATURE_QUERIES)
        else:
            base_query = random.choice(NATURE_QUERIES)

        return base_query + negative_exclusions

    async def _download_from_pixabay(self, theme: str | None) -> str:
        """Search and download a video from Pixabay with strict filtering."""
        query = self._build_query(theme)

        # Determine the category filter dynamically to avoid non-nature categories (like animals, fashion)
        category = None
        if theme:
            theme_lower = theme.lower()
            islamic_keywords = ["mosque", "prayer", "quran", "islamic", "minaret",
                                "ramadan", "eid", "masjid", "deen", "muslim",
                                "calligraphy", "lantern", "crescent", "tasbih"]
            is_islamic = any(kw in theme_lower for kw in islamic_keywords)
            if not is_islamic:
                category = "nature"
        else:
            category = "nature"

        logger.info("background_manager.pixabay_search", query=query, category=category)

        async with httpx.AsyncClient(timeout=30) as client:
            page = random.randint(1, 3)
            params = {
                "key": self._pixabay_key,
                "q": query,
                "video_type": "film",
                "per_page": 20,
                "page": page,
                "safesearch": "true",
                "order": "popular",
            }
            if category:
                params["category"] = category

            response = await client.get(
                "https://pixabay.com/api/videos/",
                params=params,
            )
            response.raise_for_status()
            data = response.json()

            if not data.get("hits"):
                raise ValueError(f"No Pixabay videos found for query: {query}")

            # Filter hits to ensure duration is at least 10 seconds
            hits = data.get("hits", [])
            suitable_hits = [v for v in hits if v.get("duration", 0) >= 10]
            if not suitable_hits:
                # Fallback to any hits if none match the duration filter
                suitable_hits = hits

            # Pick a random video from the suitable results
            video = random.choice(suitable_hits)
            logger.info("background_manager.selected_video", id=video.get("id"), duration=video.get("duration"))

            # Get the best quality video file
            videos = video.get("videos", {})

            # Prefer large > medium > small
            best = (
                videos.get("large", {})
                or videos.get("medium", {})
                or videos.get("small", {})
            )

            download_url = best.get("url", "") if best else ""
            if not download_url:
                raise ValueError("No downloadable video URL found in Pixabay response")

            return await self._download_url(download_url, "pixabay")

    async def _download_url(self, url: str, source_name: str) -> str:
        """Download video from URL and save to backgrounds folder."""
        uid = uuid.uuid4().hex[:8]
        ext = url.split("?")[0].split(".")[-1]
        if len(ext) > 4:
            ext = "mp4"
        output_path = _BACKGROUNDS_DIR / f"{source_name}_{uid}.{ext}"

        logger.info("background_manager.downloading", url=url[:70] + "...")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        async with httpx.AsyncClient(timeout=120, follow_redirects=True, headers=headers) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                with open(output_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        f.write(chunk)

        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        logger.info("background_manager.downloaded", path=str(output_path), size_mb=f"{size_mb:.1f}")
        return str(output_path)
