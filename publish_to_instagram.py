"""
Standalone Instagram Publisher

Reads `latest_generation.json`, validates the output, uploads to Cloudinary, 
and publishes the reel to Instagram.
"""

import asyncio
import json
import logging
import os
import sys

from rich.console import Console

from app.config import Settings
from app.services.cloudinary_storage import CloudinaryStorage
from app.services.publisher import InstagramPublisher

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

async def publish():
    print_header("Validating Generation Data")
    
    # 1. Strict Validation
    if not os.path.exists("latest_generation.json"):
        print_fail("latest_generation.json not found! Run generate_reel.py first.")
        sys.exit(1)
        
    try:
        with open("latest_generation.json", "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print_fail(f"Could not read generation data: {e}")
        sys.exit(1)
        
    if data.get("status") != "SUCCESS":
        print_fail(f"Generation did not complete successfully. Status: {data.get('status')}")
        sys.exit(1)
        
    reel_path = data.get("reel_path")
    if not reel_path or not os.path.exists(reel_path):
        print_fail(f"Reel video file missing: {reel_path}")
        sys.exit(1)
        
    if os.path.getsize(reel_path) == 0:
        print_fail(f"Reel video file is empty (0 bytes): {reel_path}")
        sys.exit(1)
        
    caption = data.get("caption", "")
        
    print_ok("Validation Passed! Ready to publish.")
    print_info(f"File: {reel_path}")
    
    settings = Settings()
    
    # Check if Instagram is configured
    if not settings.instagram_access_token or not settings.instagram_business_id:
        print_fail("Instagram credentials missing from .env!")
        print_info("Set INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_BUSINESS_ID.")
        sys.exit(1)
    
    # 2. Upload to Cloudinary
    print_header("Uploading to Cloudinary")
    try:
        storage = CloudinaryStorage(settings)
        upload_result = await storage.upload_video(reel_path)
        cdn_url = upload_result["url"]
        print_ok(f"Uploaded securely to Cloudinary: {cdn_url}")
    except Exception as e:
        print_fail(f"Cloudinary upload failed: {e}")
        sys.exit(1)
        
    # 3. Publish to Instagram
    print_header("Publishing to Instagram")
    
    max_retries = 10
    publisher = InstagramPublisher(settings)
    
    for attempt in range(1, max_retries + 1):
        try:
            ig_media_id = await publisher.publish_reel(
                video_url=cdn_url,
                caption=caption,
                share_to_feed=True
            )
            print_ok(f"Successfully published to Instagram! Media ID: {ig_media_id}")
            break
        except Exception as e:
            print_fail(f"Attempt {attempt}/{max_retries} failed: {e}")
            if attempt == max_retries:
                print_fail("All 10 attempts to publish to Instagram failed. Exiting.")
                await publisher.close()
                sys.exit(1)
            import random
            sleep_time = random.uniform(5, 10)
            print_info(f"Waiting {sleep_time:.1f} seconds before retrying...")
            await asyncio.sleep(sleep_time)
            
    await publisher.close()
        
    print_header("Cleaning Up")
    try:
        os.remove(reel_path)
        print_ok(f"Deleted generated reel to save space: {os.path.basename(reel_path)}")
        
        bg_video = data.get("background_video")
        if bg_video and os.path.exists(bg_video):
            os.remove(bg_video)
            print_ok(f"Deleted Pixabay background video to save space: {os.path.basename(bg_video)}")
        
        # Delete from Cloudinary to free up space
        if "upload_result" in locals() and "storage" in locals():
            cloudinary_public_id = upload_result.get("public_id")
            if cloudinary_public_id:
                await storage.delete_asset(public_id=cloudinary_public_id, resource_type="video")
                print_ok("Deleted reel video from Cloudinary to save space.")
        
        # Also clean up the generation JSON to prevent accidental double-posting
        if os.path.exists("latest_generation.json"):
            os.remove("latest_generation.json")
    except Exception as e:
        print_fail(f"Could not perform cleanup: {e}")
        
    print_header("DONE")
    print_ok("The Reel is now live on your Instagram!")

if __name__ == "__main__":
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        asyncio.run(publish())
    except KeyboardInterrupt:
        print_fail("Publishing interrupted by user.")
