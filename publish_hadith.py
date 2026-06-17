"""
Standalone Hadith Instagram Publisher

Reads `latest_hadith_generation.json`, validates the output, uploads to Cloudinary, 
publishes the image to Instagram, and performs local and Cloudinary cleanup.
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
    if not os.path.exists("latest_hadith_generation.json"):
        print_fail("latest_hadith_generation.json not found! Run generate_hadith.py first.")
        sys.exit(1)
        
    try:
        with open("latest_hadith_generation.json", "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print_fail(f"Could not read generation data: {e}")
        sys.exit(1)
        
    if data.get("status") != "SUCCESS":
        print_fail(f"Generation did not complete successfully. Status: {data.get('status')}")
        sys.exit(1)
        
    image_path = data.get("image_path")
    if not image_path or not os.path.exists(image_path):
        print_fail(f"Hadith image file missing: {image_path}")
        sys.exit(1)
        
    if os.path.getsize(image_path) == 0:
        print_fail(f"Hadith image file is empty (0 bytes): {image_path}")
        sys.exit(1)
        
    caption = data.get("caption", "")
        
    print_ok("Validation Passed! Ready to publish.")
    print_info(f"File: {image_path}")
    
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
        upload_result = await storage.upload_image(image_path)
        cdn_url = upload_result["url"]
        print_ok(f"Uploaded securely to Cloudinary: {cdn_url}")
    except Exception as e:
        print_fail(f"Cloudinary upload failed: {e}")
        sys.exit(1)
        
    # 3. Publish to Instagram
    print_header("Publishing to Instagram")
    try:
        publisher = InstagramPublisher(settings)
        ig_media_id = await publisher.publish_image(
            image_url=cdn_url,
            caption=caption
        )
        print_ok(f"Successfully published Hadith to Instagram! Media ID: {ig_media_id}")
    except Exception as e:
        print_fail(f"Instagram publishing failed: {e}")
        sys.exit(1)
    finally:
        await publisher.close()
        
    print_header("Cleaning Up")
    try:
        # Delete local image
        os.remove(image_path)
        print_ok(f"Deleted local generated image to save space: {os.path.basename(image_path)}")
        
        # Delete from Cloudinary to free up space
        if "upload_result" in locals() and "storage" in locals():
            cloudinary_public_id = upload_result.get("public_id")
            if cloudinary_public_id:
                await storage.delete_asset(public_id=cloudinary_public_id, resource_type="image")
                print_ok("Deleted image from Cloudinary to save space.")
        
        # Clean up the generation JSON to prevent accidental double-posting
        if os.path.exists("latest_hadith_generation.json"):
            os.remove("latest_hadith_generation.json")
            print_ok("Deleted latest_hadith_generation.json")
    except Exception as e:
        print_fail(f"Could not perform cleanup: {e}")
        
    print_header("DONE")
    print_ok("The Hadith post is now live on your Instagram!")

if __name__ == "__main__":
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        asyncio.run(publish())
    except KeyboardInterrupt:
        print_fail("Publishing interrupted by user.")
