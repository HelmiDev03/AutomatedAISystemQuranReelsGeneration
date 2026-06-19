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

async def get_last_20_reel_verses(session) -> list[str]:
    from app.models.post import GeneratedPost, MediaFormat
    from sqlalchemy import select

    stmt = (
        select(GeneratedPost)
        .where(GeneratedPost.media_format == MediaFormat.REEL)
        .order_by(GeneratedPost.created_at.desc())
        .limit(20)
    )
    result = await session.execute(stmt)
    recent_reels = result.scalars().all()

    verses = []
    for reel in recent_reels:
        if reel.source_ref:
            verses.append(reel.source_ref.strip())
    return list(set(verses))

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
    MAX_ATTEMPTS = 3
    generated = None
    
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print_header(f"Generating Content (Attempt {attempt}/{MAX_ATTEMPTS})")
        print_info(f"Topic: {topic_name}")
        start = time.time()
        
        # Get recently used verses to exclude
        async with async_session() as session:
            exclude_list = await get_last_20_reel_verses(session)
        if exclude_list:
            print_info(f"Recently used verses (to exclude): {exclude_list}")

        try:
            generated = await generator.generate(
                content_type="quran_verse",
                topic_name=topic_name,
                exclude_verses=exclude_list,
            )
        except Exception as e:
            print_fail(f"LLM Generation Failed: {e}")
            if attempt < MAX_ATTEMPTS:
                print_info("Retrying with a new topic...")
                async with async_session() as session:
                    result = await session.execute(text("SELECT name FROM content_topics ORDER BY RANDOM() LIMIT 1"))
                    row = result.fetchone()
                    topic_name = row[0] if row else "Patience during hardship"
                print_ok(f"New topic: {topic_name}")
                continue
            sys.exit(1)
            
        elapsed = time.time() - start
        print_ok(f"Content generated in {elapsed:.1f}s!")
        
        # 4. Content Verification
        print_header("Verifying Content")
        verification = await verifier.verify(generated)
        if verification.passed:
            print_ok(f"Verification PASSED -- Confidence: {verification.composite_score}")
            
            # Check duplicate verse among last 20 reels
            source_ref = generated.get("source_ref", "").strip()
            is_dup = False
            
            if source_ref:
                # Compare against exclude_list
                import re
                match = re.search(r"Surah\s*(\d+).*?Ayah\s*(\d+)", source_ref, re.IGNORECASE) or re.search(r"(\d+):(\d+)", source_ref)
                if match:
                    s_num = int(match.group(1))
                    a_num = int(match.group(2))
                    for ref in exclude_list:
                        m = re.search(r"Surah\s*(\d+).*?Ayah\s*(\d+)", ref, re.IGNORECASE) or re.search(r"(\d+):(\d+)", ref)
                        if m:
                            if int(m.group(1)) == s_num and int(m.group(2)) == a_num:
                                is_dup = True
                                break
                                
            if is_dup:
                print_fail(f"Verse {source_ref} matches a verse used in the last 20 reels!")
                if attempt < MAX_ATTEMPTS:
                    print_info("Retrying with a new topic...")
                    async with async_session() as session:
                        result = await session.execute(text("SELECT name FROM content_topics ORDER BY RANDOM() LIMIT 1"))
                        row = result.fetchone()
                        topic_name = row[0] if row else "Patience during hardship"
                    print_ok(f"New topic: {topic_name}")
                    generated = None
                    continue
                else:
                    print_fail("CRITICAL: All attempts selected duplicate verses. Aborting pipeline.")
                    sys.exit(1)
            
            break
        else:
            print_fail(f"Verification Failed! Issues: {verification.issues}")
            if attempt < MAX_ATTEMPTS:
                print_info("LLM hallucinated. Retrying with a new topic...")
                async with async_session() as session:
                    result = await session.execute(text("SELECT name FROM content_topics ORDER BY RANDOM() LIMIT 1"))
                    row = result.fetchone()
                    topic_name = row[0] if row else "Patience during hardship"
                print_ok(f"New topic: {topic_name}")
                generated = None
                continue
            else:
                print_fail("CRITICAL: All attempts failed verification. Aborting pipeline.")
                sys.exit(1)
    
    if generated is None:
        print_fail("CRITICAL: No content was generated successfully.")
        sys.exit(1)

    arabic = generated.get("arabic_text", "")
    english = generated.get("english_text", "")
    visual_theme = generated.get("visual_theme", "nature landscape")
    source_ref = generated.get("source_ref", "")

    # Build the final Instagram caption in the mandatory format:
    # 1. Arabic reflection
    # 2. Arabic hashtags
    # 3. English reflection  
    # 4. English hashtags
    caption_arabic = generated.get("caption_arabic", "")
    caption_english = generated.get("caption_english", "")
    hashtags_ar = generated.get("hashtags_arabic", [])
    hashtags_en = generated.get("hashtags_english", [])
    
    ar_tags = " ".join(f"#{h.lstrip('#')}" for h in hashtags_ar)
    en_tags = " ".join(f"#{h.lstrip('#')}" for h in hashtags_en)
    
    caption = f"{caption_arabic}\n\n{ar_tags}\n\n{caption_english}\n\n{en_tags}"

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
