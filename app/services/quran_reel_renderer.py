"""Quran Reel Renderer — creates Instagram reels with real recitation.

Pipeline:
    1. Download surah recitation from mp3quran.net (Muhammad Al-Luhaidan)
    2. Extract the verse range audio segment
    3. Use nature background video (or Ken Burns on image)
    4. Overlay Arabic verse text synced to recitation
    5. Export as 1080x1920 MP4 reel
"""

from __future__ import annotations

import asyncio
import math
import os
import random
import subprocess
import textwrap
import uuid
from pathlib import Path
from typing import Any

import httpx
import structlog
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from app.config import Settings

logger = structlog.get_logger(__name__)

# ── paths ────────────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_OUTPUT_DIR = _PROJECT_ROOT / "media_output"
_BACKGROUNDS_DIR = _PROJECT_ROOT / "backgrounds"
_AUDIO_CACHE_DIR = _PROJECT_ROOT / "audio_cache"
_FONTS_DIR = _PROJECT_ROOT / "fonts"

_ARABIC_FONT = _FONTS_DIR / "Amiri-Regular.ttf"
_ARABIC_FONT_BOLD = _FONTS_DIR / "Amiri-Bold.ttf"

# ── reciter config ───────────────────────────────────────────────────────────
# Muhammad Al-Luhaidan — beautiful recitation
_RECITER_BASE_URL = "https://server8.mp3quran.net/lhdan"

# ── reel dimensions ─────────────────────────────────────────────────────────
_REEL_SIZE = (1080, 1920)


def _ensure_dirs() -> None:
    """Create required directories."""
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _BACKGROUNDS_DIR.mkdir(parents=True, exist_ok=True)
    _AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _load_font(font_path: Path, size: int) -> ImageFont.FreeTypeFont:
    """Load a font with fallback to Windows system Arabic fonts."""
    if font_path.is_file():
        return ImageFont.truetype(str(font_path), size)

    import platform
    if platform.system() == "Windows":
        win_fonts = Path("C:/Windows/Fonts")
        for fb in ["tahoma.ttf", "tahomabd.ttf", "arial.ttf", "segoeui.ttf"]:
            fb_path = win_fonts / fb
            if fb_path.is_file():
                return ImageFont.truetype(str(fb_path), size)

    return ImageFont.load_default()


def _get_ffmpeg() -> str:
    """Get path to FFmpeg binary (bundled with imageio_ffmpeg)."""
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


class QuranReelRenderer:
    """Renders Instagram reels with real Quran recitation over nature video."""

    def __init__(self, settings: Settings, rag_engine: Any = None) -> None:
        self._settings = settings
        self._rag_engine = rag_engine
        _ensure_dirs()

    # ── Audio: Ayah-by-Ayah Fetching & Concatenation ───────────────────────

    async def fetch_audio_segment(self, surah_number: int, start_ayah: int, min_duration: float = 25.0) -> tuple[str, list[dict[str, Any]], float]:
        import imageio_ffmpeg
        os.environ["FFMPEG_BINARY"] = imageio_ffmpeg.get_ffmpeg_exe()
        from moviepy import AudioFileClip

        reciter = self._settings.quran_reciter or "ar.alafasy"
        fetched_ayahs = []
        audio_clips = []
        total_duration = 0.0
        current_ayah = start_ayah
        
        segment_id = uuid.uuid4().hex[:8]
        output_path = str(_OUTPUT_DIR / f"segment_{segment_id}.mp3")

        logger.info("quran_reel.fetching_ayahs_offline", surah=surah_number, start_ayah=start_ayah)

        while total_duration < min_duration:
            # 1. Fetch Ayah Metadata from ChromaDB via rag_engine
            if not self._rag_engine:
                raise ValueError("Offline rendering requires rag_engine to be passed to QuranReelRenderer")
                
            # Query the exact verse
            result = await self._rag_engine._run_sync(
                self._rag_engine._quran_col.get,
                where={"$and": [{"surah_number": surah_number}, {"ayah_number": current_ayah}]},
                include=["metadatas"]
            )
            
            if not result["metadatas"]:
                if current_ayah > start_ayah:
                    break  # Reached end of surah
                raise ValueError(f"Verse {surah_number}:{current_ayah} not found in ChromaDB")
                
            meta = result["metadatas"][0]
            surah_name = meta.get("surah_name_english", f"Surah {surah_number}")
            surah_name_arabic = meta.get("surah_name_arabic", "")
            arabic_text = meta.get("arabic_text", "")
            
            # 2. Load Local MP3 File
            audio_path = _AUDIO_CACHE_DIR / f"{surah_number}_{current_ayah}_{reciter}.mp3"
            if not audio_path.exists():
                raise FileNotFoundError(f"Missing local audio file: {audio_path}. Please run download_all_audio.py")
                
            logger.info("quran_reel.loaded_local_ayah", surah=surah_number, ayah=current_ayah)
            
            # 3. Load AudioClip to get duration
            clip = AudioFileClip(str(audio_path))
            duration = clip.duration
            
            fetched_ayahs.append({
                "surah_name": surah_name,
                "surah_name_arabic": surah_name_arabic,
                "verse_number": current_ayah,
                "arabic_text": arabic_text,
                "duration": duration,
                "start_time": total_duration,
                "end_time": total_duration + duration
            })
            
            audio_clips.append(clip)
            total_duration += duration
            current_ayah += 1

        if not audio_clips:
            raise ValueError("No audio clips were successfully loaded.")

        logger.info("quran_reel.concatenating_audio", count=len(audio_clips), duration=total_duration)
        from moviepy import concatenate_audioclips
        final_audio = concatenate_audioclips(audio_clips)
        final_audio.write_audiofile(output_path, logger=None, fps=44100)
        
        # Cleanup clips from memory to release file handles
        for c in audio_clips:
            c.close()
        final_audio.close()

        logger.info("quran_reel.audio_ready", verses=len(fetched_ayahs), duration=total_duration)
        return output_path, fetched_ayahs, total_duration

    # ── Background: Nature video or Ken Burns ────────────────────────────

    def get_random_background(self) -> str | None:
        """Get a random background video from the backgrounds/ folder.

        Returns None if no videos are available.
        """
        video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
        videos = [
            f for f in _BACKGROUNDS_DIR.iterdir()
            if f.suffix.lower() in video_exts and f.name.startswith("pixabay_")
        ]
        if videos:
            chosen = random.choice(videos)
            logger.info("background.selected", path=str(chosen))
            return str(chosen)
        return None

    def create_nature_background_frames(
        self,
        duration: float,
        fps: int = 24,
    ) -> list[str]:
        """Create animated background frames with a nature-inspired design.

        Generates a dark gradient background with subtle star/particle
        animation and a slow zoom (Ken Burns) effect.

        Returns list of frame image paths.
        """
        width, height = _REEL_SIZE
        num_frames = int(duration * fps)
        frame_paths = []
        uid = uuid.uuid4().hex[:6]
        frames_dir = _OUTPUT_DIR / f"frames_{uid}"
        frames_dir.mkdir(exist_ok=True)

        # Generate star positions once
        stars = [(random.randint(0, width), random.randint(0, height),
                  random.randint(1, 3), random.random()) for _ in range(80)]

        for i in range(num_frames):
            progress = i / max(num_frames - 1, 1)

            # Gradient colors (dark teal to dark navy — nature night)
            r1 = int(5 + 10 * math.sin(progress * math.pi))
            g1 = int(15 + 20 * math.sin(progress * math.pi))
            b1 = int(30 + 15 * math.sin(progress * math.pi))

            r2 = int(10 + 8 * math.sin(progress * math.pi + 1))
            g2 = int(25 + 15 * math.sin(progress * math.pi + 1))
            b2 = int(45 + 20 * math.sin(progress * math.pi + 1))

            img = Image.new("RGB", (width, height))
            pixels = img.load()

            for y in range(height):
                ratio = y / height
                r = int(r1 + (r2 - r1) * ratio)
                g = int(g1 + (g2 - g1) * ratio)
                b = int(b1 + (b2 - b1) * ratio)
                for x in range(width):
                    pixels[x, y] = (r, g, b)

            # Draw twinkling stars
            draw = ImageDraw.Draw(img)
            for sx, sy, sr, phase in stars:
                brightness = int(180 + 75 * math.sin(progress * 6 + phase * 10))
                alpha = max(50, brightness)
                draw.ellipse(
                    [sx - sr, sy - sr, sx + sr, sy + sr],
                    fill=(alpha, alpha, alpha),
                )

            # Save frame
            frame_path = str(frames_dir / f"frame_{i:04d}.png")
            img.save(frame_path, "PNG")
            frame_paths.append(frame_path)

        logger.info("background_frames.created", count=num_frames)
        return frame_paths

    # ── Text overlay: Arabic verse rendering ─────────────────────────────

    def render_verse_frame(
        self,
        verse_text: str,
        verse_ref: str,
        width: int = 1080,
        height: int = 1920,
        opacity: float = 1.0,
    ) -> Image.Image:
        """Render a transparent overlay with Arabic verse text.

        Returns an RGBA image that can be composited onto the background.
        """
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Removed the semi-transparent dark box as requested
        box_margin = 60
        box_top = height // 2 - 200
        box_bottom = height // 2 + 200

        # Arabic text
        arabic_font = _load_font(_ARABIC_FONT_BOLD, 76)
        text_alpha = int(255 * opacity)
        text_color = (255, 255, 255, text_alpha)

        # Word wrap and render with raqm
        center_x = width // 2
        y = box_top + 40

        max_text_width = width - box_margin * 2 - 40

        # Wrap text precisely by pixel width
        words = verse_text.split()
        lines = []
        current_line = []
        for word in words:
            test_line = " ".join(current_line + [word])
            bbox = draw.textbbox((0, 0), test_line, font=arabic_font,
                                  direction="rtl", language="ar")
            line_width = bbox[2] - bbox[0]
            if line_width <= max_text_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(" ".join(current_line))
                    current_line = [word]
                else:
                    lines.append(word)
                    current_line = []
        if current_line:
            lines.append(" ".join(current_line))

        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=arabic_font,
                                  direction="rtl", language="ar")
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            x = center_x - tw // 2
            draw.text((x, y), line, fill=text_color, font=arabic_font,
                      direction="rtl", language="ar")
            y += th + 20

        # Source reference below in Arabic
        if verse_ref:
            # Use Arabic font for the reference (Surah name + Ayah)
            ref_font = _load_font(_ARABIC_FONT_BOLD, 44)
            ref_color = (200, 200, 200, text_alpha)
            ref_bbox = draw.textbbox((0, 0), verse_ref, font=ref_font,
                                      direction="rtl", language="ar")
            ref_w = ref_bbox[2] - ref_bbox[0]
            draw.text(
                (center_x - ref_w // 2, y + 40), # Place it relative to the last line of the verse
                verse_ref,
                fill=ref_color,
                font=ref_font,
                direction="rtl", 
                language="ar"
            )

        return overlay

    # ── Main render pipeline ─────────────────────────────────────────────

    async def render_quran_reel(
        self,
        surah_number: int,
        start_ayah: int,
        *,
        reel_duration: float = 25.0,
        background_video: str | None = None,
    ) -> str:
        """Render a Quran reel with real recitation and precise Ayah transcript.

        Parameters
        ----------
        surah_number : int
            Surah number (1-114).
        start_ayah : int
            The Ayah number to start the recitation from.
        reel_duration : float
            Target reel duration in seconds (default 25.0s). The bot will keep fetching verses until it hits this.
        background_video : str | None
            Path to a background video. If None, tries backgrounds/ folder
            or creates an animated background.

        Returns
        -------
        str
            Path to the final MP4 reel.
        """
        uid = uuid.uuid4().hex[:8]
        ffmpeg = _get_ffmpeg()
        os.environ["FFMPEG_BINARY"] = ffmpeg

        logger.info("quran_reel.start",
                     surah=surah_number, start_ayah=start_ayah,
                     duration=reel_duration)

        # Step 1: Fetch sequential Ayahs and concatenate
        segment_path, fetched_verses, actual_duration = await self.fetch_audio_segment(
            surah_number=surah_number, 
            start_ayah=start_ayah, 
            min_duration=reel_duration
        )

        logger.info("quran_reel.audio_ready", duration=actual_duration, verses=len(fetched_verses))

        # Step 4: Prepare background
        if not background_video:
            background_video = self.get_random_background()

        if not background_video:
            raise ValueError(
                "No Pixabay background video available. "
                "Animated backgrounds or non-Pixabay local fallback backgrounds are strictly disabled."
            )

        if not os.path.exists(background_video):
            raise FileNotFoundError(f"Background video file not found at: {background_video}")

        # Step 5: Create video with text overlays
        final_path = str(_OUTPUT_DIR / f"quran_reel_{uid}.mp4")

        # Use provided/found background video
        await self._compose_with_video_bg(
            background_video, segment_path, fetched_verses,
            actual_duration, final_path
        )

        # Clean up segment
        try:
            os.remove(segment_path)
        except OSError:
            pass

        size_kb = os.path.getsize(final_path) / 1024
        logger.info("quran_reel.done", path=final_path, size_kb=f"{size_kb:.0f}")
        return final_path

    async def _compose_with_video_bg(
        self,
        bg_video_path: str,
        audio_path: str,
        verses: list[dict],
        duration: float,
        output_path: str,
    ) -> None:
        """Compose reel using a real background video."""
        from moviepy import (
            VideoFileClip, AudioFileClip, ImageClip,
            CompositeVideoClip, ColorClip,
        )

        loop = asyncio.get_running_loop()

        def _compose() -> None:
            # Load background video
            bg = VideoFileClip(bg_video_path)

            # Loop or trim to match duration
            if bg.duration < duration:
                # Loop the video
                loops = math.ceil(duration / bg.duration)
                from moviepy import concatenate_videoclips
                bg = concatenate_videoclips([bg] * loops)

            bg = bg.subclipped(0, duration)

            # Resize to reel dimensions (crop to fit)
            bg = bg.resized(height=_REEL_SIZE[1])
            if bg.w < _REEL_SIZE[0]:
                bg = bg.resized(width=_REEL_SIZE[0])

            # Center crop
            if bg.size != list(_REEL_SIZE):
                bg = bg.cropped(
                    x_center=bg.w // 2, y_center=bg.h // 2,
                    width=_REEL_SIZE[0], height=_REEL_SIZE[1],
                )

            # Create a semi-transparent dark overlay (40% opacity black) to ensure white text
            # remains perfectly readable on bright/white background videos.
            overlay = ColorClip(
                size=_REEL_SIZE,
                color=(0, 0, 0),
                duration=duration
            ).with_opacity(0.4)

            # Create verse overlay clips
            verse_clips = self._create_verse_clips(verses, duration)

            # Load audio
            audio = AudioFileClip(audio_path)

            # Composite: background video -> dark overlay -> text overlays
            final = CompositeVideoClip([bg, overlay] + verse_clips, size=_REEL_SIZE)
            final = final.with_audio(audio).with_duration(duration)

            final.write_videofile(
                output_path, fps=24, codec="libx264",
                audio_codec="aac", logger=None,
                preset="fast",
            )

            audio.close()
            bg.close()
            final.close()

        await loop.run_in_executor(None, _compose)

    async def _compose_with_generated_bg(
        self,
        audio_path: str,
        verses: list[dict],
        duration: float,
        output_path: str,
    ) -> None:
        """Compose reel with a generated animated background."""
        from moviepy import (
            AudioFileClip, ImageClip,
            CompositeVideoClip,
        )

        loop = asyncio.get_running_loop()

        def _compose() -> None:
            width, height = _REEL_SIZE

            # Create a beautiful static background with gradient
            bg_img = self._create_dark_nature_bg(width, height)
            bg_path = str(_OUTPUT_DIR / f"bg_tmp_{uuid.uuid4().hex[:6]}.png")
            bg_img.save(bg_path, "PNG")

            # Use Ken Burns (slow zoom) effect
            bg_clip = ImageClip(bg_path).with_duration(duration)

            # Apply slow zoom via resize
            def zoom_effect(get_frame, t):
                """Slow zoom from 1.0x to 1.15x over duration."""
                import numpy as np
                frame = get_frame(t)
                zoom = 1.0 + 0.15 * (t / duration)
                h, w = frame.shape[:2]
                new_h, new_w = int(h * zoom), int(w * zoom)

                # Resize frame
                from PIL import Image
                img = Image.fromarray(frame)
                img = img.resize((new_w, new_h), Image.LANCZOS)

                # Center crop back to original size
                left = (new_w - w) // 2
                top = (new_h - h) // 2
                img = img.crop((left, top, left + w, top + h))

                return np.array(img)

            bg_clip = bg_clip.transform(zoom_effect)

            # Create verse overlay clips
            verse_clips = self._create_verse_clips(verses, duration)

            # Load audio
            audio = AudioFileClip(audio_path)

            # Composite
            final = CompositeVideoClip([bg_clip] + verse_clips, size=_REEL_SIZE)
            final = final.with_audio(audio).with_duration(duration)

            final.write_videofile(
                output_path, fps=24, codec="libx264",
                audio_codec="aac", logger=None,
                preset="fast",
            )

            audio.close()
            final.close()

            # Cleanup
            try:
                os.remove(bg_path)
            except OSError:
                pass

        await loop.run_in_executor(None, _compose)

    def _create_dark_nature_bg(self, width: int, height: int) -> Image.Image:
        """Create a dark, nature-inspired background image."""
        img = Image.new("RGB", (width, height))
        pixels = img.load()

        # Dark gradient: deep navy to dark teal
        for y in range(height):
            ratio = y / height
            r = int(8 + 12 * ratio)
            g = int(15 + 25 * ratio)
            b = int(35 + 20 * ratio)
            for x in range(width):
                pixels[x, y] = (r, g, b)

        # Add subtle star/light dots
        draw = ImageDraw.Draw(img)
        for _ in range(60):
            x = random.randint(0, width)
            y = random.randint(0, height // 2)  # Stars in upper half
            r = random.randint(1, 2)
            brightness = random.randint(100, 200)
            draw.ellipse([x-r, y-r, x+r, y+r],
                         fill=(brightness, brightness, brightness))

        # Soft blur for dreamy effect
        img = img.filter(ImageFilter.GaussianBlur(radius=1))

        return img

    @staticmethod
    def _normalize_surah_name_ar(name: str) -> str:
        """Replace single-letter Surah names with their full word spellings."""
        import unicodedata
        # NFC normalisation
        text = unicodedata.normalize("NFC", name.strip())
        # Remove Arabic diacritical marks (U+064B–U+065F, U+0670, U+0653)
        diacritics = set(range(0x064B, 0x0660)) | {0x0670, 0x0653, 0x0654, 0x0655}
        text = "".join(ch for ch in text if ord(ch) not in diacritics)
        
        words = text.split()
        for idx, w in enumerate(words):
            if w == "ص":
                words[idx] = "صاد"
            elif w == "ق":
                words[idx] = "قاف"
            elif w == "ن":
                words[idx] = "نون"
            elif w == "يس":
                words[idx] = "ياسين"
        return " ".join(words)

    def _create_verse_clips(
        self,
        verses: list[dict],
        total_duration: float,
    ) -> list:
        """Create MoviePy ImageClips for each verse with exact fade in/out timing.
        
        Uses the exact 'duration' and 'start_time' keys from the Ayah audio.
        """
        from moviepy import ImageClip
        from moviepy.video.fx import CrossFadeIn, CrossFadeOut
        import numpy as np

        if not verses:
            return []

        clips = []

        for v in verses:
            arabic = v.get("arabic_text", "")
            verse_num = v.get("verse_number", "")
            surah_name_ar = v.get("surah_name_arabic", "")
            if surah_name_ar:
                surah_name_ar = self._normalize_surah_name_ar(surah_name_ar)
            ref = f" {surah_name_ar} : {verse_num} " if surah_name_ar else ""
            
            # Exact timing from the fetched MP3s!
            verse_duration = v.get("duration", 3.0)
            start_time = v.get("start_time", 0.0)

            # Render the verse overlay image
            overlay = self.render_verse_frame(arabic, ref)
            overlay_array = np.array(overlay)

            # Create ImageClip with fade effects
            fade_dur = min(0.5, verse_duration / 4)
            clip = (
                ImageClip(overlay_array)
                .with_duration(verse_duration)
                .with_start(start_time)
                .with_position(("center", "center"))
                .with_effects([
                    CrossFadeIn(fade_dur),
                    CrossFadeOut(fade_dur),
                ])
            )

            clips.append(clip)

        return clips
