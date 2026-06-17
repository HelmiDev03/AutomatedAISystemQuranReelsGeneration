"""
Standalone Dua Generator

Generates a Dua quote card end-to-end and saves the output metadata 
to `latest_dua_generation.json` to be picked up by the publisher script.
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
from app.services.rag_engine import RAGEngine
from doua_generator.generate_post import generate as generate_dua_image

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
    print_ok("All AI services initialized")

    # Load recent topics
    recent_topics_file = "latest_dua_topics.json"
    recent_topics = []
    if os.path.exists(recent_topics_file):
        try:
            with open(recent_topics_file, "r", encoding="utf-8") as f:
                recent_topics = json.load(f)
        except Exception:
            recent_topics = []

    # 2. Topic Selection (LLM chooses the topic)
    print_header("Selecting Dynamic Topic via LLM")
    try:
        recent_topics_str = ", ".join(f"'{t}'" for t in recent_topics) if recent_topics else "None"
        prompt = (
            f"Choose a unique, beautiful, and inspirational Islamic topic suitable for a Dua (supplication) post. "
            f"It MUST NOT be any of these recently used topics: [{recent_topics_str}]. "
            "Respond with only the topic name (2-5 words), without any quotes, numbering, or introductory text."
            "the dua must be in only arabic text. Do not answer in any other language."
            "it must not be a quran verse or hadith"
        )
        
        response = await generator._client.chat.completions.create(
            model=generator._model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=50
        )
        topic_name = response.choices[0].message.content.strip().strip('"').strip("'").strip(".")
        if not topic_name:
            topic_name = "Supplication and Remembrance"
    except Exception as e:
        print_fail(f"LLM Topic Selection Failed: {e}")
        topic_name = "Supplication and Remembrance"
        
    print_ok(f"LLM Selected topic: {topic_name}")

    # 3. Generating Content
    print_header("Generating Content")
    start = time.time()
    
    try:
        generated = await generator.generate(
            content_type="dua",
            topic_name=topic_name,
            media_format="quote_card",
        )
    except Exception as e:
        print_fail(f"LLM Generation Failed: {e}")
        sys.exit(1)
        
    elapsed = time.time() - start
    print_ok(f"Content generated in {elapsed:.1f}s!")

    arabic = generated.get("arabic_text", "")
    caption = generated.get("caption", "")

    # 4. Image Rendering
    print_header("Rendering Dua Image")
    try:          
        image_path = generate_dua_image(arabic)
        print_ok(f"Dua image card saved to: {image_path}")
    except Exception as e:
        print_fail(f"Dua image rendering failed: {e}")
        sys.exit(1)
        
    # 5. Save Metadata for Publisher
    print_header("Finalizing")
    output_data = {
        "status": "SUCCESS",
        "image_path": image_path,
        "caption": caption,
        "timestamp": time.time()
    }
    
    with open("latest_dua_generation.json", "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
        
    print_ok("Saved latest_dua_generation.json")

    # Update recent topics
    recent_topics.append(topic_name)
    if len(recent_topics) > 5:
        recent_topics.pop(0)
    with open(recent_topics_file, "w", encoding="utf-8") as f:
        json.dump(recent_topics, f, ensure_ascii=False, indent=2)
    print_ok(f"Updated recent topics list in {recent_topics_file}")
    print_ok("Generation Pipeline Complete! Run publish_dua.py next.")

if __name__ == "__main__":
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
