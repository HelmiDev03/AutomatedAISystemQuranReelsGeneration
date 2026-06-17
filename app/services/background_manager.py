"""Service for automatically fetching nature background videos."""

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

class BackgroundVideoManager:
    """Manages downloading and caching of nature background videos."""

    # Fallback public domain videos (ultra-reliable test CDN)
    FALLBACK_VIDEOS = [
        "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerEscapes.mp4",
        "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerJoyrides.mp4",
    ]

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pexels_key = getattr(settings, "pexels_api_key", "")
        _BACKGROUNDS_DIR.mkdir(parents=True, exist_ok=True)

    async def get_background_video(self, force_new: bool = False, theme: str | None = None) -> str | None:
        """Get a background video, downloading a new one if necessary.
        
        Returns
        -------
        str | None
            Absolute path to the downloaded MP4 file, or None if all downloads fail.
        """
        # 1. Try to use an existing cached video if we don't force a new one
        if not force_new and not theme:
            existing = [f for f in _BACKGROUNDS_DIR.iterdir() if f.suffix in {".mp4", ".webm", ".mkv"}]
            if existing:
                chosen = random.choice(existing)
                logger.info("background_manager.using_cached", path=str(chosen))
                return str(chosen)

        # 2. Try Pexels API if configured
        if self._pexels_key:
            try:
                return await self._download_from_pexels(theme)
            except Exception as e:
                logger.warning("background_manager.pexels_failed", error=str(e))
                # Fall through to fallback

        # 3. Use fallback Pixabay/Public URLs
        try:
            return await self._download_fallback()
        except Exception as e:
            logger.warning("background_manager.fallback_failed", error=str(e))
            return None

    async def _download_fallback(self) -> str:
        """Download a random fallback video."""
        url = random.choice(self.FALLBACK_VIDEOS)
        return await self._download_url(url, "fallback_nature")

    async def _download_from_pexels(self, theme: str | None = None) -> str:
        """Search and download a nature video from Pexels, matching the theme if provided."""
        if theme:
            query = f"{theme} nature"
        else:
            queries = ["nature landscape", "mountains drone", "forest drone", "ocean waves drone", "waterfall"]
            query = random.choice(queries)
        
        logger.info("background_manager.pexels_search", query=query)
        
        async with httpx.AsyncClient(timeout=30) as client:
            headers = {"Authorization": self._pexels_key}
            # Get a random page of results
            page = random.randint(1, 5)
            response = await client.get(
                f"https://api.pexels.com/videos/search?query={query}&per_page=15&page={page}&orientation=portrait",
                headers=headers
            )
            response.raise_for_status()
            data = response.json()
            
            if not data.get("videos"):
                raise ValueError(f"No videos found for query: {query}")
                
            video = random.choice(data["videos"])
            
            # Find the best quality HD link
            files = video.get("video_files", [])
            hd_files = [f for f in files if f.get("quality") == "hd" and f.get("width", 0) >= 1080]
            
            if not hd_files:
                hd_files = [f for f in files if f.get("quality") == "hd"]
                
            best_file = hd_files[0] if hd_files else files[0]
            download_url = best_file["link"]
            
            return await self._download_url(download_url, "pexels")

    async def _download_url(self, url: str, source_name: str) -> str:
        """Download video from URL and save to backgrounds folder."""
        uid = uuid.uuid4().hex[:8]
        # preserve extension
        ext = url.split("?")[0].split(".")[-1]
        if len(ext) > 4: ext = "mp4"
        output_path = _BACKGROUNDS_DIR / f"{source_name}_{uid}.{ext}"
        
        logger.info("background_manager.downloading", url=url[:50] + "...")
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        async with httpx.AsyncClient(timeout=120, follow_redirects=True, headers=headers) as client:
            # Stream the download to handle large files better
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                with open(output_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        f.write(chunk)
                        
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        logger.info("background_manager.downloaded", path=str(output_path), size_mb=f"{size_mb:.1f}")
        return str(output_path)
