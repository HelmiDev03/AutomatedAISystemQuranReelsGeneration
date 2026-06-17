"""Cloudinary Storage — async wrapper around the synchronous Cloudinary SDK.

All blocking ``cloudinary.uploader`` calls are dispatched to a thread-pool
via ``asyncio.get_running_loop().run_in_executor()`` so they never block the
event loop.

Usage::

    storage = CloudinaryStorage(settings)
    result = await storage.upload_image("media_output/card.png")
    print(result["url"], result["public_id"])
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import cloudinary
import cloudinary.api
import cloudinary.uploader
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import Settings

logger = structlog.get_logger(__name__)


class CloudinaryStorage:
    """Async facade over the Cloudinary upload / management SDK.

    Parameters
    ----------
    settings : Settings
        Must contain ``cloudinary_cloud_name``, ``cloudinary_api_key``,
        and ``cloudinary_api_secret``.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        cloudinary.config(
            cloud_name=settings.cloudinary_cloud_name,
            api_key=settings.cloudinary_api_key,
            api_secret=settings.cloudinary_api_secret,
            secure=True,
        )
        logger.info(
            "cloudinary_configured",
            cloud_name=settings.cloudinary_cloud_name,
        )

    # ── public API ───────────────────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type(
            (cloudinary.exceptions.Error, ConnectionError, TimeoutError)
        ),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=15),
        reraise=True,
    )
    async def upload_image(
        self,
        file_path: str,
        folder: str = "islamic-content",
        *,
        transformation: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Upload an image to Cloudinary.

        Parameters
        ----------
        file_path : str
            Absolute or relative path to the local image file.
        folder : str
            Cloudinary folder to organise assets into.
        transformation : dict | None
            Optional Cloudinary transformation to apply on upload.

        Returns
        -------
        dict
            ``{"url": <secure_url>, "public_id": <public_id>, "width": ..., "height": ...}``

        Raises
        ------
        FileNotFoundError
            If *file_path* does not exist on disk.
        cloudinary.exceptions.Error
            On upload failure after retries.
        """
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"Image file not found: {file_path}")

        upload_options: dict[str, Any] = {
            "folder": folder,
            "resource_type": "image",
            "overwrite": False,
            "unique_filename": True,
        }
        if transformation:
            upload_options["transformation"] = transformation

        logger.info("cloudinary_uploading_image", file_path=file_path, folder=folder)

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: cloudinary.uploader.upload(file_path, **upload_options),
        )

        output = {
            "url": result.get("secure_url", result.get("url", "")),
            "public_id": result.get("public_id", ""),
            "width": result.get("width"),
            "height": result.get("height"),
            "format": result.get("format"),
            "bytes": result.get("bytes"),
        }

        logger.info(
            "cloudinary_image_uploaded",
            public_id=output["public_id"],
            url=output["url"],
        )
        return output

    @retry(
        retry=retry_if_exception_type(
            (cloudinary.exceptions.Error, ConnectionError, TimeoutError)
        ),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=30),
        reraise=True,
    )
    async def upload_video(
        self,
        file_path: str,
        folder: str = "islamic-content",
        *,
        eager: list[dict[str, Any]] | None = None,
    ) -> dict[str, str]:
        """Upload a video to Cloudinary.

        Parameters
        ----------
        file_path : str
            Absolute or relative path to the local video file.
        folder : str
            Cloudinary folder to organise assets into.
        eager : list[dict] | None
            Optional eager transformations (e.g. format conversion).

        Returns
        -------
        dict
            ``{"url": <secure_url>, "public_id": <public_id>, "duration": ...}``
        """
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"Video file not found: {file_path}")

        upload_options: dict[str, Any] = {
            "folder": folder,
            "resource_type": "video",
            "overwrite": False,
            "unique_filename": True,
            "chunk_size": 20_000_000,  # 20 MB chunks for large videos
        }
        if eager:
            upload_options["eager"] = eager
            upload_options["eager_async"] = True

        logger.info("cloudinary_uploading_video", file_path=file_path, folder=folder)

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: cloudinary.uploader.upload(file_path, **upload_options),
        )

        output = {
            "url": result.get("secure_url", result.get("url", "")),
            "public_id": result.get("public_id", ""),
            "duration": result.get("duration"),
            "width": result.get("width"),
            "height": result.get("height"),
            "format": result.get("format"),
            "bytes": result.get("bytes"),
        }

        logger.info(
            "cloudinary_video_uploaded",
            public_id=output["public_id"],
            url=output["url"],
        )
        return output

    @retry(
        retry=retry_if_exception_type(
            (cloudinary.exceptions.Error, ConnectionError, TimeoutError)
        ),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=10),
        reraise=True,
    )
    async def delete_asset(
        self,
        public_id: str,
        resource_type: str = "image",
    ) -> dict[str, Any]:
        """Delete an asset from Cloudinary by its public ID.

        Parameters
        ----------
        public_id : str
            The Cloudinary public ID of the asset to delete.
        resource_type : str
            ``"image"`` or ``"video"``.

        Returns
        -------
        dict
            Cloudinary API response (contains ``"result": "ok"`` on success).
        """
        logger.info(
            "cloudinary_deleting_asset",
            public_id=public_id,
            resource_type=resource_type,
        )

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: cloudinary.uploader.destroy(
                public_id, resource_type=resource_type
            ),
        )

        logger.info(
            "cloudinary_asset_deleted",
            public_id=public_id,
            result=result.get("result"),
        )
        return result

    async def get_asset_info(self, public_id: str) -> dict[str, Any]:
        """Fetch metadata for an existing Cloudinary asset.

        Parameters
        ----------
        public_id : str
            The Cloudinary public ID.

        Returns
        -------
        dict
            Full Cloudinary resource metadata.
        """
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: cloudinary.api.resource(public_id),
        )
        return result
