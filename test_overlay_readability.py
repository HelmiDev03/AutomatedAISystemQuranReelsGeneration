import asyncio
import sys
import os
from app.config import Settings
from app.services.rag_engine import RAGEngine
from app.services.quran_reel_renderer import QuranReelRenderer

# =============================================================================
# READABILITY TEST CONFIGURATION
# 
# EDIT THIS PATH: Put the path to the bright/white background video you want to test.
# It can be an absolute path or a relative path in your project folder.
# =============================================================================
TEST_BACKGROUND_VIDEO = r"C:\Users\helmi\OneDrive\Desktop\automate\backgrounds\17723-284467863.mp4"
# =============================================================================

async def main():
    settings = Settings()
    
    print("=" * 60)
    print("  Quran Reel Overlay Readability Tester")
    print("=" * 60)

    # Validate background file path
    if not TEST_BACKGROUND_VIDEO:
        print("[ERROR] Please edit the 'TEST_BACKGROUND_VIDEO' path in this file first!")
        sys.exit(1)
        
    if not os.path.exists(TEST_BACKGROUND_VIDEO):
        print(f"[ERROR] The background video file does not exist at:\n  {TEST_BACKGROUND_VIDEO}")
        print("Please place a video file there or edit the variable at the top of this script.")
        sys.exit(1)

    print(f"\n[1] Using background video: {TEST_BACKGROUND_VIDEO}")
    print("[2] Initializing services (RAGEngine and QuranReelRenderer)...")
    
    rag = RAGEngine(settings)
    renderer = QuranReelRenderer(settings, rag)

    print("[3] Rendering test reel with Surah Al-Fatiha (Verses 1-7)...")
    print("    This will download the real recitation audio and render the video")
    print("    applying the 40% opacity dark overlay behind the text.")
    print("-" * 60)

    try:
        # We start at Surah 1 (Al-Fatiha), Ayah 1, and set duration to 22.0 seconds
        output_path = await renderer.render_quran_reel(
            surah_number=1,
            start_ayah=1,
            reel_duration=22.0,
            background_video=TEST_BACKGROUND_VIDEO
        )
        
        print("-" * 60)
        print("\n" + "=" * 60)
        print("  TEST REEL RENDERED SUCCESSFULLY!")
        print("=" * 60)
        print(f"Output File: {output_path}")
        print("Open this file in your video player to check if the white text")
        print("is clear and legible against the bright parts of the video.")
        print("=" * 60)

    except Exception as e:
        print("-" * 60)
        print("\n" + "=" * 60)
        print("  RENDER FAILED!")
        print("=" * 60)
        print(f"Error: {e}")
        print("=" * 60)

if __name__ == "__main__":
    import structlog
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="YYYY-MM-DD HH:mm:ss"),
            structlog.dev.ConsoleRenderer(colors=True),
        ]
    )
    asyncio.run(main())
