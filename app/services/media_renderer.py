"""Media Renderer — generates Instagram-ready images and video reels.

Image rendering:
    - Uses **Pillow** with ``arabic_reshaper`` and ``python-bidi`` for correct
      right-to-left Arabic text rendering.
    - Produces 1080×1080 (square), 1080×1350 (portrait), and 1080×1920
      (story / reel) formats.
    - Lines are word-wrapped in *logical* order **before** reshaping so that
      line breaks fall on natural boundaries.

Video rendering:
    - **Edge TTS** generates Arabic narration audio.
    - **MoviePy 2.x** composes the video from a background image + audio.
    - **FFmpeg** subprocess burns hardcoded subtitles for reliability
      (much faster and more predictable than MoviePy text rendering).

All rendered files are saved under ``media_output/`` relative to the project root.
"""

from __future__ import annotations

import asyncio
import math
import os
import subprocess
import textwrap
import uuid
from pathlib import Path
from typing import Any

import structlog
from PIL import Image, ImageDraw, ImageFont

from app.config import Settings

logger = structlog.get_logger(__name__)

# ── directory setup ─────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_OUTPUT_DIR = _PROJECT_ROOT / "media_output"
_FONTS_DIR = _PROJECT_ROOT / "fonts"

# ── font paths ──────────────────────────────────────────────────────────────
_ARABIC_FONT = _FONTS_DIR / "Amiri-Regular.ttf"
_ARABIC_FONT_BOLD = _FONTS_DIR / "Amiri-Bold.ttf"
_ENGLISH_FONT = _FONTS_DIR / "Lato-Regular.ttf"

# ── colour palettes ────────────────────────────────────────────────────────
_STYLES: dict[str, dict[str, Any]] = {
    "dark": {
        "bg_start": (18, 18, 35),
        "bg_end": (35, 25, 55),
        "text_arabic": "#FFFFFF",
        "text_english": "#C8C8D0",
        "text_source": "#8888AA",
        "border_color": (180, 150, 100, 80),
        "accent": (200, 170, 110),
    },
    "light": {
        "bg_start": (245, 240, 230),
        "bg_end": (235, 225, 210),
        "text_arabic": "#1A1A2E",
        "text_english": "#3A3A4E",
        "text_source": "#7A7A8E",
        "border_color": (150, 120, 80, 60),
        "accent": (120, 90, 50),
    },
    "green": {
        "bg_start": (10, 40, 30),
        "bg_end": (20, 60, 45),
        "text_arabic": "#F0F0E0",
        "text_english": "#C0D0C0",
        "text_source": "#80A080",
        "border_color": (100, 160, 100, 80),
        "accent": (130, 190, 130),
    },
}

# ── Instagram dimensions ───────────────────────────────────────────────────
_SIZE_SQUARE = (1080, 1080)
_SIZE_PORTRAIT = (1080, 1350)
_SIZE_REEL = (1080, 1920)


def _ensure_output_dir() -> Path:
    """Create and return the output directory."""
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return _OUTPUT_DIR


def _load_font(font_path: Path, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a TrueType font, falling back to system Arabic-capable fonts."""
    if font_path.is_file():
        return ImageFont.truetype(str(font_path), size)

    # Try Windows system fonts that support Arabic
    import platform
    if platform.system() == "Windows":
        win_fonts = Path("C:/Windows/Fonts")
        # Prefer fonts with good Arabic joining/ligature support
        fallbacks = [
            "majalla.ttf",      # Sakkal Majalla — best Arabic font
            "tahoma.ttf",       # Tahoma — good Arabic ligatures
            "tahomabd.ttf",     # Tahoma Bold
            "segoeui.ttf",      # Segoe UI
            "arial.ttf",        # Arial — last resort (weak Arabic joining)
        ]
        for fb in fallbacks:
            fb_path = win_fonts / fb
            if fb_path.is_file():
                logger.info("font_fallback_used", path=str(fb_path))
                return ImageFont.truetype(str(fb_path), size)

    logger.warning("font_not_found_using_default", path=str(font_path))
    return ImageFont.load_default()


def _reshape_arabic_line(text: str) -> str:
    """Reshape and apply bidi algorithm to a single line of Arabic text.

    Uses arabic_reshaper with full ligature and diacritics support,
    then applies the Unicode bidi algorithm for correct RTL display
    in Pillow's LTR rendering engine.
    """
    import arabic_reshaper
    from bidi.algorithm import get_display

    # Configure reshaper for Uthmanic/Quranic Arabic
    configuration = {
        'delete_harakat': False,           # Keep diacritics (tashkeel)
        'support_ligatures': True,         # Enable lam-alef etc.
        'RIAL SIGN': True,
        'use_unshaped_instead_of_isolated': True,  # Better joining
    }
    reshaper = arabic_reshaper.ArabicReshaper(configuration=configuration)
    reshaped = reshaper.reshape(text)
    return get_display(reshaped)


def _draw_gradient(img: Image.Image, start_color: tuple, end_color: tuple) -> None:
    """Draw a vertical linear gradient on *img* in-place."""
    width, height = img.size
    pixels = img.load()
    for y in range(height):
        ratio = y / max(height - 1, 1)
        r = int(start_color[0] + (end_color[0] - start_color[0]) * ratio)
        g = int(start_color[1] + (end_color[1] - start_color[1]) * ratio)
        b = int(start_color[2] + (end_color[2] - start_color[2]) * ratio)
        for x in range(width):
            pixels[x, y] = (r, g, b)


def _draw_islamic_border(
    draw: ImageDraw.ImageDraw,
    size: tuple[int, int],
    color: tuple[int, ...],
    width: int = 3,
) -> None:
    """Draw a decorative double-border with corner ornaments."""
    w, h = size
    margin_outer = 30
    margin_inner = 50

    # Outer border
    draw.rectangle(
        [margin_outer, margin_outer, w - margin_outer, h - margin_outer],
        outline=color[:3],
        width=width,
    )
    # Inner border
    draw.rectangle(
        [margin_inner, margin_inner, w - margin_inner, h - margin_inner],
        outline=color[:3],
        width=max(1, width - 1),
    )

    # Corner decorative dots
    dot_r = 4
    corners = [
        (margin_outer, margin_outer),
        (w - margin_outer, margin_outer),
        (margin_outer, h - margin_outer),
        (w - margin_outer, h - margin_outer),
    ]
    for cx, cy in corners:
        draw.ellipse(
            [cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r],
            fill=color[:3],
        )

    # Midpoint decorative arcs (top & bottom)
    mid_x = w // 2
    arc_w = 60
    # Top
    draw.arc(
        [mid_x - arc_w, margin_outer - 10, mid_x + arc_w, margin_outer + 20],
        start=0,
        end=180,
        fill=color[:3],
        width=width,
    )
    # Bottom
    draw.arc(
        [mid_x - arc_w, h - margin_outer - 20, mid_x + arc_w, h - margin_outer + 10],
        start=180,
        end=360,
        fill=color[:3],
        width=width,
    )


class MediaRenderer:
    """Generates Instagram-ready images and video reels.

    Parameters
    ----------
    settings : Settings
        Application configuration.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._voice_male = settings.edge_tts_voice_male
        self._voice_female = settings.edge_tts_voice_female
        _ensure_output_dir()

    # ── public API ───────────────────────────────────────────────────────

    async def render_quote_card(
        self,
        arabic_text: str,
        english_text: str,
        source_ref: str,
        style: str = "dark",
        *,
        size: tuple[int, int] = _SIZE_SQUARE,
    ) -> str:
        """Generate a 1080×1080 quote card image.

        Steps:
            1. Create background with vertical gradient.
            2. Draw decorative Islamic border pattern.
            3. Render Arabic text (reshaped + bidi) centred.
            4. Render English translation below.
            5. Add source citation at the bottom.
            6. Save as PNG.

        Parameters
        ----------
        arabic_text : str
            Arabic quote or verse.
        english_text : str
            English translation.
        source_ref : str
            Citation (e.g. "Sahih al-Bukhari 6018").
        style : str
            Colour palette: ``"dark"``, ``"light"``, or ``"green"``.
        size : tuple[int, int]
            Canvas size; defaults to 1080×1080.

        Returns
        -------
        str
            Absolute path to the rendered PNG file.
        """
        palette = _STYLES.get(style, _STYLES["dark"])
        width, height = size
        filename = f"quote_{uuid.uuid4().hex[:12]}.png"
        output_path = str(_OUTPUT_DIR / filename)

        # Run the CPU-bound rendering in a thread pool
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            self._render_quote_card_sync,
            arabic_text,
            english_text,
            source_ref,
            palette,
            size,
            output_path,
        )

        logger.info("quote_card_rendered", path=output_path, style=style)
        return output_path

    async def render_carousel(
        self,
        slides: list[dict[str, Any]],
        style: str = "dark",
    ) -> list[str]:
        """Generate multiple carousel slides.

        Each slide dict should contain:
            - ``arabic_text``: Arabic content for this slide.
            - ``english_text``: English translation.
            - ``source_ref`` (optional): Citation.

        Parameters
        ----------
        slides : list[dict]
            List of slide content dictionaries.
        style : str
            Colour palette name.

        Returns
        -------
        list[str]
            List of absolute file paths to the rendered slide PNGs.
        """
        if not slides:
            raise ValueError("At least one slide is required.")

        tasks = []
        for idx, slide in enumerate(slides):
            tasks.append(
                self.render_quote_card(
                    arabic_text=slide.get("arabic_text", ""),
                    english_text=slide.get("english_text", ""),
                    source_ref=slide.get("source_ref", ""),
                    style=style,
                )
            )

        paths = await asyncio.gather(*tasks)
        logger.info("carousel_rendered", slide_count=len(paths))
        return list(paths)

    async def render_reel(
        self,
        narration_text: str,
        arabic_text: str,
        *,
        english_text: str = "",
        source_ref: str = "",
        voice: str | None = None,
        style: str = "dark",
    ) -> str:
        """Generate a video reel with TTS narration and burned subtitles.

        Steps:
            1. Generate Arabic TTS audio via Edge TTS.
            2. Create a static background image (1080×1920).
            3. Compose video with MoviePy 2.x (image + audio).
            4. Generate SRT subtitle file.
            5. Burn subtitles via FFmpeg subprocess.

        Parameters
        ----------
        narration_text : str
            Text for the TTS narration (Arabic).
        arabic_text : str
            Arabic text to display on the background image.
        english_text : str
            Optional English translation.
        source_ref : str
            Optional source citation.
        voice : str | None
            Edge TTS voice name. Defaults to male Arabic voice.
        style : str
            Visual style for the background.

        Returns
        -------
        str
            Absolute path to the rendered MP4 file.
        """
        uid = uuid.uuid4().hex[:12]
        voice = voice or self._voice_male

        # File paths
        audio_path = str(_OUTPUT_DIR / f"tts_{uid}.mp3")
        bg_path = str(_OUTPUT_DIR / f"bg_{uid}.png")
        raw_video_path = str(_OUTPUT_DIR / f"raw_{uid}.mp4")
        srt_path = str(_OUTPUT_DIR / f"sub_{uid}.srt")
        final_path = str(_OUTPUT_DIR / f"reel_{uid}.mp4")

        # Step 1: Generate TTS audio ------------------------------------------
        await self._generate_tts(narration_text, voice, audio_path)

        # Step 2: Create background image (reel dimensions) -------------------
        bg_image_path = await self.render_quote_card(
            arabic_text=arabic_text,
            english_text=english_text,
            source_ref=source_ref,
            style=style,
            size=_SIZE_REEL,
        )
        # Rename to our UID-based path
        os.replace(bg_image_path, bg_path)

        # Step 3: Compose with MoviePy 2.x -----------------------------------
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            self._compose_video_sync,
            bg_path,
            audio_path,
            raw_video_path,
        )

        # Step 4: Generate SRT subtitles --------------------------------------
        await loop.run_in_executor(
            None,
            self._generate_srt,
            narration_text,
            audio_path,
            srt_path,
        )

        # Step 5: Burn subtitles with FFmpeg ----------------------------------
        await self._burn_subtitles(raw_video_path, srt_path, final_path)

        # Clean up intermediate files -----------------------------------------
        for tmp in (audio_path, bg_path, raw_video_path, srt_path):
            try:
                os.remove(tmp)
            except OSError:
                pass

        logger.info("reel_rendered", path=final_path)
        return final_path

    # ── TTS ──────────────────────────────────────────────────────────────

    async def _generate_tts(
        self, text: str, voice: str, output_path: str
    ) -> None:
        """Generate TTS audio using Edge TTS.

        Parameters
        ----------
        text : str
            The text to synthesize.
        voice : str
            Edge TTS voice name (e.g. ``ar-SA-HamedNeural``).
        output_path : str
            Where to save the resulting MP3 file.
        """
        import edge_tts

        logger.debug("tts_generating", voice=voice, text_length=len(text))

        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_path)

        logger.info("tts_generated", output_path=output_path)

    # ── Arabic text rendering ────────────────────────────────────────────

    async def _render_arabic_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        position: tuple[int, int],
        color: str,
        max_width: int,
    ) -> int:
        """Render Arabic text with proper reshaping, bidi, and word wrapping.

        Text is first wrapped in logical order using ``textwrap.wrap()``,
        then each line is individually reshaped and bidi-processed.

        Parameters
        ----------
        draw : ImageDraw.ImageDraw
            The draw context.
        text : str
            Raw Arabic text.
        font : ImageFont
            Font to render with.
        position : tuple[int, int]
            (x, y) starting position (text is centred horizontally from x).
        color : str
            Text colour.
        max_width : int
            Maximum pixel width before wrapping.

        Returns
        -------
        int
            The Y position after the last rendered line.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self._render_arabic_text_sync,
            draw,
            text,
            font,
            position,
            color,
            max_width,
        )

    @staticmethod
    def _render_arabic_text_sync(
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        position: tuple[int, int],
        color: str,
        max_width: int,
    ) -> int:
        """Synchronous implementation of Arabic text rendering.

        Uses Pillow's native raqm/harfbuzz layout engine for proper
        Arabic shaping, ligatures, and RTL direction — no manual
        reshaping needed.
        """
        center_x, y = position
        line_spacing = 16

        # Estimate characters per line from max_width
        test_bbox = draw.textbbox((0, 0), "م" * 10, font=font,
                                   direction="rtl", language="ar")
        char_width = (test_bbox[2] - test_bbox[0]) / 10
        chars_per_line = max(int(max_width / char_width), 10)

        # Wrap in logical order
        wrapped_lines = textwrap.wrap(text, width=chars_per_line)

        for line in wrapped_lines:
            # Use Pillow's raqm engine — handles shaping + bidi natively
            bbox = draw.textbbox((0, 0), line, font=font,
                                  direction="rtl", language="ar")
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]

            x = center_x - text_w // 2
            draw.text((x, y), line, fill=color, font=font,
                      direction="rtl", language="ar")
            y += text_h + line_spacing

        return y

    # ── sync rendering helpers ───────────────────────────────────────────

    def _render_quote_card_sync(
        self,
        arabic_text: str,
        english_text: str,
        source_ref: str,
        palette: dict[str, Any],
        size: tuple[int, int],
        output_path: str,
    ) -> None:
        """CPU-bound quote card rendering (runs in executor)."""
        width, height = size

        # 1. Create gradient background
        img = Image.new("RGB", size, color=palette["bg_start"])
        _draw_gradient(img, palette["bg_start"], palette["bg_end"])

        draw = ImageDraw.Draw(img)

        # 2. Islamic border pattern
        _draw_islamic_border(draw, size, palette["border_color"])

        # 3. Load fonts
        arabic_font_size = 48 if height <= 1080 else 56
        english_font_size = 28 if height <= 1080 else 32
        source_font_size = 22 if height <= 1080 else 26

        arabic_font = _load_font(_ARABIC_FONT_BOLD, arabic_font_size)
        english_font = _load_font(_ENGLISH_FONT, english_font_size)
        source_font = _load_font(_ENGLISH_FONT, source_font_size)

        # 4. Layout constants
        content_margin = 80
        max_text_width = width - content_margin * 2

        # Decorative bismillah ornament at top
        ornament = "\uFDFD"
        ornament_font = _load_font(_ARABIC_FONT, 36)
        ornament_bbox = draw.textbbox((0, 0), ornament, font=ornament_font,
                                       direction="rtl", language="ar")
        ornament_w = ornament_bbox[2] - ornament_bbox[0]
        draw.text(
            ((width - ornament_w) // 2, 70),
            ornament,
            fill=palette["accent"],
            font=ornament_font,
            direction="rtl",
            language="ar",
        )

        # 5. Render Arabic text
        arabic_start_y = height // 4 if height <= 1080 else height // 5
        y_after_arabic = self._render_arabic_text_sync(
            draw,
            arabic_text,
            arabic_font,
            (width // 2, arabic_start_y),
            palette["text_arabic"],
            max_text_width,
        )

        # 6. Divider line
        divider_y = y_after_arabic + 25
        div_margin = width // 4
        accent_rgb = palette["accent"]
        draw.line(
            [(div_margin, divider_y), (width - div_margin, divider_y)],
            fill=accent_rgb,
            width=2,
        )

        # 7. Render English text
        english_y = divider_y + 30
        english_lines = textwrap.wrap(english_text, width=55)
        for line in english_lines:
            bbox = draw.textbbox((0, 0), line, font=english_font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.text(
                ((width - tw) // 2, english_y),
                line,
                fill=palette["text_english"],
                font=english_font,
            )
            english_y += th + 10

        # 8. Source citation at bottom
        if source_ref:
            source_display = f"— {source_ref}"
            source_bbox = draw.textbbox((0, 0), source_display, font=source_font)
            source_w = source_bbox[2] - source_bbox[0]
            draw.text(
                ((width - source_w) // 2, height - 90),
                source_display,
                fill=palette["text_source"],
                font=source_font,
            )

        # 9. Save
        img.save(output_path, "PNG", quality=95, optimize=True)

    def _compose_video_sync(
        self,
        bg_path: str,
        audio_path: str,
        output_path: str,
    ) -> None:
        """Compose a video from a static image and audio track using MoviePy 2.x."""
        import imageio_ffmpeg
        os.environ["FFMPEG_BINARY"] = imageio_ffmpeg.get_ffmpeg_exe()
        from moviepy import AudioFileClip, ImageClip

        audio = AudioFileClip(audio_path)
        duration = audio.duration

        # Create image clip with same duration as the audio
        image_clip = ImageClip(bg_path).with_duration(duration)

        # Combine image and audio
        video = image_clip.with_audio(audio)

        video.write_videofile(
            output_path,
            fps=24,
            codec="libx264",
            audio_codec="aac",
            logger=None,  # suppress MoviePy's verbose logging
        )

        # Cleanup MoviePy resources
        audio.close()
        video.close()

    def _generate_srt(
        self,
        text: str,
        audio_path: str,
        srt_path: str,
    ) -> None:
        """Generate a basic SRT subtitle file from narration text.

        Splits the text into chunks and distributes them evenly across
        the audio duration. For production use, consider using Whisper
        for word-level timestamps.
        """
        from moviepy import AudioFileClip

        audio = AudioFileClip(audio_path)
        total_duration = audio.duration
        audio.close()

        # Split into manageable chunks (roughly by sentence / clause)
        import re

        sentences = re.split(r"[.،؛:!؟\n]+", text)
        sentences = [s.strip() for s in sentences if s.strip()]

        if not sentences:
            sentences = textwrap.wrap(text, width=40)

        if not sentences:
            sentences = [text]

        chunk_duration = total_duration / len(sentences)

        with open(srt_path, "w", encoding="utf-8") as f:
            for idx, sentence in enumerate(sentences):
                start = idx * chunk_duration
                end = min((idx + 1) * chunk_duration, total_duration)

                start_ts = self._seconds_to_srt_time(start)
                end_ts = self._seconds_to_srt_time(end)

                f.write(f"{idx + 1}\n")
                f.write(f"{start_ts} --> {end_ts}\n")
                f.write(f"{sentence}\n\n")

    @staticmethod
    def _seconds_to_srt_time(seconds: float) -> str:
        """Convert seconds to SRT timestamp format ``HH:MM:SS,mmm``."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    @staticmethod
    async def _burn_subtitles(
        input_video: str,
        srt_path: str,
        output_path: str,
    ) -> None:
        """Burn subtitles into a video using FFmpeg.

        Uses sync subprocess.run (Windows SelectorEventLoop doesn't
        support async subprocesses). Runs in executor to avoid blocking.
        """
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

        # Escape paths for FFmpeg filter (Windows backslash issues)
        srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:")

        cmd = [
            ffmpeg_exe,
            "-y",
            "-i", input_video,
            "-vf", f"subtitles='{srt_escaped}':force_style='FontSize=22,PrimaryColour=&HFFFFFF,Alignment=2,MarginV=60'",
            "-c:a", "copy",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            output_path,
        ]

        logger.debug("ffmpeg_burning_subtitles", cmd=" ".join(cmd))

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(cmd, capture_output=True, timeout=120),
        )

        if result.returncode != 0:
            error_msg = result.stderr.decode("utf-8", errors="replace")
            logger.warning(
                "ffmpeg_subtitle_burn_failed_using_raw",
                returncode=result.returncode,
                stderr=error_msg[:300],
            )
            # Fallback: re-encode without subtitles
            cmd_fallback = [
                ffmpeg_exe,
                "-y",
                "-i", input_video,
                "-c:a", "copy",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                output_path,
            ]
            result2 = await loop.run_in_executor(
                None,
                lambda: subprocess.run(cmd_fallback, capture_output=True, timeout=120),
            )
            if result2.returncode != 0:
                import shutil
                shutil.copy2(input_video, output_path)
                logger.warning("ffmpeg_fallback_copy", output=output_path)
                return

        logger.info("subtitles_burned", output=output_path)
