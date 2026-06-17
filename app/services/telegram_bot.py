"""Telegram Review Bot — sends posts for human review via Telegram Bot API.

Uses raw HTTPS calls via ``httpx`` rather than the full ``python-telegram-bot``
library because we only need one-way messaging (bot → reviewer).  Inline
keyboard buttons are included for approve / reject workflows; the callback
is handled separately by a webhook endpoint.

Message formatting:
    - Uses Telegram MarkdownV2 for rich text.
    - Media posts include the rendered image/video as a photo attachment.
    - Error alerts are sent with 🚨 prefix for visibility.
"""

from __future__ import annotations

import os
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

_TELEGRAM_API = "https://api.telegram.org"


def _escape_markdown_v2(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2 format.

    Telegram requires these characters to be escaped with a preceding ``\\``:
    ``_ * [ ] ( ) ~ ` > # + - = | { } . !``
    """
    special = r"_*[]()~`>#+-=|{}.!"
    escaped = []
    for ch in text:
        if ch in special:
            escaped.append(f"\\{ch}")
        else:
            escaped.append(ch)
    return "".join(escaped)


class TelegramReviewBot:
    """Send posts, notifications, and error alerts to a Telegram chat.

    Parameters
    ----------
    settings : Settings
        Must provide ``telegram_bot_token`` and ``telegram_chat_id``.
    """

    def __init__(self, settings: Settings) -> None:
        self._token = settings.telegram_bot_token
        self._chat_id = settings.telegram_chat_id
        self._base_url = f"{_TELEGRAM_API}/bot{self._token}"
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        """Cleanly close the underlying HTTP client."""
        await self._client.aclose()

    # ── public API ───────────────────────────────────────────────────────

    async def send_for_review(
        self,
        post_data: dict[str, Any],
        media_path: str | None = None,
    ) -> bool:
        """Send a post to the reviewer's Telegram chat for approval.

        Parameters
        ----------
        post_data : dict
            Expected keys: ``post_id``, ``content_type``, ``arabic_text``,
            ``english_text``, ``source_ref``, ``confidence_score``,
            ``hadith_grade`` (optional).
        media_path : str | None
            Local file path to the rendered image or video. If provided the
            message is sent as a photo with caption.

        Returns
        -------
        bool
            ``True`` if the message was sent successfully.
        """
        post_id = post_data.get("post_id", "unknown")
        content_type = post_data.get("content_type", "—")
        arabic_text = post_data.get("arabic_text", "")
        english_text = post_data.get("english_text", "")
        source_ref = post_data.get("source_ref", "—")
        confidence = post_data.get("confidence_score", 0.0)
        hadith_grade = post_data.get("hadith_grade")

        # Build plain-text message (no MarkdownV2 for reliability) -----------
        lines = [
            "🔍 *New Post for Review*",
            "",
            f"📝 Type: {content_type}",
        ]
        if hadith_grade:
            lines.append(f"📜 Hadith Grade: {hadith_grade}")
        lines += [
            "",
            f"🕌 Arabic:\n{arabic_text[:800]}",
            "",
            f"🌐 English:\n{english_text[:800]}",
            "",
            f"📖 Source: {source_ref}",
            f"📊 Confidence: {confidence:.2%}",
        ]
        text = "\n".join(lines)

        # Inline keyboard: Approve / Reject -----------------------------------
        inline_keyboard = {
            "inline_keyboard": [
                [
                    {
                        "text": "✅ Approve",
                        "callback_data": f"approve:{post_id}",
                    },
                    {
                        "text": "❌ Reject",
                        "callback_data": f"reject:{post_id}",
                    },
                ]
            ]
        }

        try:
            if media_path and os.path.isfile(media_path):
                success = await self._send_photo(
                    caption=text,
                    photo_path=media_path,
                    reply_markup=inline_keyboard,
                )
            else:
                success = await self._send_message(
                    text=text,
                    reply_markup=inline_keyboard,
                )

            if success:
                logger.info(
                    "review_sent",
                    post_id=post_id,
                    content_type=content_type,
                )
            return success

        except Exception:
            logger.exception("review_send_failed", post_id=post_id)
            return False

    async def send_notification(self, message: str) -> bool:
        """Send a plain-text informational notification.

        Parameters
        ----------
        message : str
            The notification text (plain text, no markdown).

        Returns
        -------
        bool
            ``True`` on success.
        """
        return await self._send_message(text=f"ℹ️ {message}")

    async def send_error_alert(self, error: str, context: str) -> bool:
        """Send a high-visibility error alert to the admin chat.

        Parameters
        ----------
        error : str
            Short description of the error.
        context : str
            What the system was doing when the error occurred.

        Returns
        -------
        bool
            ``True`` on success.
        """
        text = (
            "🚨 *Error Alert*\n\n"
            f"Context: {context}\n\n"
            f"Error: {error}\n\n"
            f"⏰ Please investigate immediately."
        )
        return await self._send_message(text=text, parse_mode="Markdown")

    # ── private helpers ──────────────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=10),
        reraise=True,
    )
    async def _send_message(
        self,
        text: str,
        *,
        parse_mode: str | None = "Markdown",
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        """POST sendMessage to the Telegram Bot API.

        Parameters
        ----------
        text : str
            Message body.
        parse_mode : str | None
            ``"Markdown"``, ``"MarkdownV2"``, ``"HTML"``, or ``None``.
        reply_markup : dict | None
            Inline keyboard markup dict.

        Returns
        -------
        bool
            ``True`` if the API returned ``ok: true``.
        """
        payload: dict[str, Any] = {
            "chat_id": self._chat_id,
            "text": text,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup

        response = await self._client.post(
            f"{self._base_url}/sendMessage",
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

        if not data.get("ok"):
            logger.error(
                "telegram_send_message_failed",
                description=data.get("description"),
                error_code=data.get("error_code"),
            )
            # Retry without parse_mode if markdown caused the error
            if parse_mode and data.get("error_code") == 400:
                logger.warning("retrying_without_parse_mode")
                return await self._send_message(
                    text=text, parse_mode=None, reply_markup=reply_markup
                )
            return False

        return True

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=10),
        reraise=True,
    )
    async def _send_photo(
        self,
        caption: str,
        photo_path: str,
        *,
        parse_mode: str | None = "Markdown",
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        """POST sendPhoto to the Telegram Bot API (multipart upload).

        Parameters
        ----------
        caption : str
            Caption text (max 1024 characters for photos).
        photo_path : str
            Local path to the image file.
        parse_mode : str | None
            Markdown formatting mode.
        reply_markup : dict | None
            Inline keyboard markup.

        Returns
        -------
        bool
            ``True`` on success.
        """
        # Telegram photo captions are limited to 1024 characters
        truncated_caption = caption[:1024]

        data: dict[str, Any] = {
            "chat_id": self._chat_id,
            "caption": truncated_caption,
        }
        if parse_mode:
            data["parse_mode"] = parse_mode
        if reply_markup:
            import json as _json

            data["reply_markup"] = _json.dumps(reply_markup)

        with open(photo_path, "rb") as photo_file:
            files = {"photo": (os.path.basename(photo_path), photo_file, "image/png")}
            response = await self._client.post(
                f"{self._base_url}/sendPhoto",
                data=data,
                files=files,
            )

        response.raise_for_status()
        resp_data = response.json()

        if not resp_data.get("ok"):
            logger.error(
                "telegram_send_photo_failed",
                description=resp_data.get("description"),
            )
            # Fallback: send as message without photo
            if resp_data.get("error_code") == 400:
                logger.warning("photo_send_failed_falling_back_to_text")
                return await self._send_message(
                    text=f"{truncated_caption}\n\n📎 (media attachment failed)",
                    reply_markup=reply_markup,
                )
            return False

        return True
