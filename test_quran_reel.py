"""Test the Quran reel renderer with Al-Fatiha."""
import asyncio
import sys
import os

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


async def test_quran_reel():
    from app.config import get_settings
    from app.services.quran_reel_renderer import QuranReelRenderer

    settings = get_settings()
    renderer = QuranReelRenderer(settings)

    # Al-Fatiha verses (first 4 for a short reel)
    verses = [
        {
            "arabic_text": "بِسْمِ ٱللَّهِ ٱلرَّحْمَٰنِ ٱلرَّحِيمِ",
            "verse_number": 1,
            "surah_name": "Al-Fatiha",
        },
        {
            "arabic_text": "ٱلْحَمْدُ لِلَّهِ رَبِّ ٱلْعَالَمِينَ",
            "verse_number": 2,
            "surah_name": "Al-Fatiha",
        },
        {
            "arabic_text": "ٱلرَّحْمَٰنِ ٱلرَّحِيمِ",
            "verse_number": 3,
            "surah_name": "Al-Fatiha",
        },
        {
            "arabic_text": "مَٰلِكِ يَوْمِ ٱلدِّينِ",
            "verse_number": 4,
            "surah_name": "Al-Fatiha",
        },
    ]

    print("=" * 50)
    print("  Quran Reel Test - Al-Fatiha")
    print("=" * 50)

    from app.services.background_manager import BackgroundVideoManager

    print("\n[2] Fetching nature background video...")
    bg_manager = BackgroundVideoManager(settings)
    
    # Simulate LLM choosing a visual theme based on the verses
    visual_theme = "desert" 
    print(f"    -> AI selected theme: '{visual_theme}'")
    
    bg_video_path = await bg_manager.get_background_video(theme=visual_theme)
    print(f"    -> Using background: {bg_video_path}")

    print("\n[3] Rendering Reel...")

    try:
        path = await renderer.render_quran_reel(
            surah_number=1,
            verses=verses,
            reel_duration=25.0,  # 25 second reel
            start_time=10.0,     # Skip first 10 seconds (skips the Bismillah audio)
            background_video=bg_video_path,
        )

        size_kb = os.path.getsize(path) / 1024
        print(f"\n[OK] Reel generated!")
        print(f"     Path: {path}")
        print(f"     Size: {size_kb:.1f} KB")
        print(f"\n     Open this file to watch your Quran reel!")

    except Exception as e:
        print(f"\n[FAIL] {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_quran_reel())
