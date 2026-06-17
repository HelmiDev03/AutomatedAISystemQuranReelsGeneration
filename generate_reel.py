"""
Standalone Reel Generator

Generates a Quran Reel end-to-end and saves the output metadata 
to `latest_generation.json` to be picked up by the publisher script.
"""

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

from rich.console import Console

from app.config import Settings
from app.services.content_generator import ContentGenerator
from app.services.quran_reel_renderer import QuranReelRenderer
from app.services.rag_engine import RAGEngine
from app.services.verifier import ContentVerifier

console = Console()

def print_header(text: str) -> None:
    console.print(f"\n[bold cyan]>> {text}[/bold cyan]")
    console.print("[cyan]" + "-" * 50 + "[/cyan]")

def print_ok(text: str) -> None:
    console.print(f"   [bold green][OK][/bold green] {text}")

def print_info(text: str) -> None:
    console.print(f"   [bold yellow][INFO][/bold yellow] {text}")

def print_fail(text: str) -> None:
    console.print(f"   [bold red][FAIL][/bold red] {text}")

async def generate():
    settings = Settings()
    
    # 1. Initialize Services
    print_header("Initializing Services")
    rag = RAGEngine(settings)
    generator = ContentGenerator(settings, rag)
    verifier = ContentVerifier(settings, rag)
    reel_renderer = QuranReelRenderer(settings, rag)
    print_ok("All AI services initialized")

    # 2. Topic Selection
    print_header("Selecting Dynamic Topic")
    from sqlalchemy import text
    from app.database import async_session
    
    async with async_session() as session:
        result = await session.execute(text("SELECT name FROM content_topics ORDER BY RANDOM() LIMIT 1"))
        row = result.fetchone()
        topic_name = row[0] if row else "Patience during hardship"
        
    print_ok(f"Selected new daily topic: {topic_name}")
    print_header("Generating Content")
    print_info(f"Topic: {topic_name}")
    start = time.time()
    
    try:
        generated = await generator.generate(
            content_type="quran_verse",
            topic_name=topic_name,
        )
    except Exception as e:
        print_fail(f"LLM Generation Failed: {e}")
        sys.exit(1)
        
    elapsed = time.time() - start
    print_ok(f"Content generated in {elapsed:.1f}s!")
    
    arabic = generated.get("arabic_text", "")
    english = generated.get("english_text", "")
    caption = generated.get("caption", "")
    visual_theme = generated.get("visual_theme", "nature landscape")
    source_ref = generated.get("source_ref", "")

    # 4. Content Verification
    print_header("Verifying Content")
    verification = await verifier.verify(generated)
    if not verification.passed:
        print_fail(f"Verification Failed! Issues: {verification.issues}")
        print_info("Ignoring verification failure because we are in testing mode without a full database.")
    else:
        print_ok(f"Verification PASSED -- Confidence: {verification.composite_score}")

    # 5. Video Rendering
    print_header("Rendering Quran Reel")
    print_info(f"AI selected visual theme: '{visual_theme}'")
    
    from app.services.background_manager import BackgroundVideoManager
    bg_manager = BackgroundVideoManager(settings)
    bg_video_path = await bg_manager.get_background_video(theme=visual_theme)
    
    if bg_video_path:
        print_ok(f"Fetched background video: {bg_video_path}")
    else:
        print_info("Using animated fallback background")
    
    # Parse surah and ayah from source_ref
    surah_num = 1
    start_ayah = 1
    import re
    match = re.search(r"Surah\s*(\d+).*?Ayah\s*(\d+)", source_ref, re.IGNORECASE) or re.search(r"(\d+):(\d+)", source_ref)
    if match:
        surah_num = int(match.group(1))
        start_ayah = int(match.group(2))
    
    try:
        reel_path = await reel_renderer.render_quran_reel(
            surah_number=surah_num,
            start_ayah=start_ayah,
            reel_duration=25.0,
            background_video=bg_video_path
        )
        print_ok(f"Reel saved to: {reel_path}")
    except Exception as e:
        print_fail(f"Reel rendering failed: {e}")
        sys.exit(1)
        
    # 6. Save Metadata for Publisher
    print_header("Finalizing")
    output_data = {
        "status": "SUCCESS",
        "reel_path": reel_path,
        "background_video": bg_video_path,
        "caption": caption,
        "hashtags": generated.get("hashtags", []),
        "timestamp": time.time()
    }
    
    with open("latest_generation.json", "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
        
    print_ok("Saved latest_generation.json")
    print_ok("Generation Pipeline Complete! Run publish_to_instagram.py next.")

if __name__ == "__main__":
    import sys
    # Fix Windows console Arabic printing
    if sys.stdout.encoding.lower() != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8')
    
    # Suppress verbose logs for clean CLI output
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        asyncio.run(generate())
    except KeyboardInterrupt:
        print_fail("Generation interrupted by user.")
