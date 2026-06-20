import asyncio
import sys
from app.config import Settings
from app.services.background_manager import BackgroundVideoManager, NATURE_QUERIES, ISLAMIC_QUERIES

async def main():
    # 1. Load Settings
    settings = Settings()
    if not getattr(settings, "pixabay_api_key", None):
        print("[ERROR] Pixabay API Key is missing in your .env file.")
        sys.exit(1)

    print("=" * 60)
    print("  Pixabay Background Video Tester")
    print("=" * 60)

    # 2. Select Theme
    theme = None
    if len(sys.argv) > 1:
        theme = sys.argv[1]
        print(f"Using theme from arguments: '{theme}'")
    else:
        print("\nPre-approved Nature Queries:")
        for idx, q in enumerate(NATURE_QUERIES, 1):
            print(f"  {idx:2d}. {q}")
        
        print("\nPre-approved Islamic Queries:")
        for idx, q in enumerate(ISLAMIC_QUERIES, len(NATURE_QUERIES) + 1):
            print(f"  {idx:2d}. {q}")
        
        print(f"  {len(NATURE_QUERIES) + len(ISLAMIC_QUERIES) + 1:2d}. Enter a custom theme")

        try:
            choice = input(f"\nSelect a number (1-{len(NATURE_QUERIES) + len(ISLAMIC_QUERIES) + 1}): ").strip()
            if not choice:
                print("No selection. Exiting.")
                return

            choice_idx = int(choice)
            all_queries = NATURE_QUERIES + ISLAMIC_QUERIES
            
            if 1 <= choice_idx <= len(all_queries):
                theme = all_queries[choice_idx - 1]
            elif choice_idx == len(all_queries) + 1:
                theme = input("Enter custom theme: ").strip()
            else:
                print("Invalid choice. Exiting.")
                return
        except ValueError:
            print("Invalid input. Exiting.")
            return

    if not theme:
        print("No theme selected. Exiting.")
        return

    # 3. Initialize Background Manager
    bg_manager = BackgroundVideoManager(settings)

    print("\n" + "-" * 60)
    print(f"Fetching video for theme: '{theme}'")
    print("Excluding: humans, moon, domestic animals, and white/bright backgrounds...")
    print("Enforcing: Category=nature (for nature themes) and Resolution=HD/4K...")
    print("-" * 60)

    try:
        # Force a new download to verify API results
        path = await bg_manager.get_background_video(force_new=True, theme=theme)
        
        print("\n" + "=" * 60)
        print("  SUCCESS!")
        print("=" * 60)
        print(f"Downloaded File Path: {path}")
        print(f"Query Used: {bg_manager._build_query(theme)}")
        print("Open the video file to verify it looks correct (dark, HD/4K, no animals, no moon, no humans).")
        print("=" * 60)

    except Exception as e:
        print("\n" + "=" * 60)
        print("  FAILED!")
        print("=" * 60)
        print(f"Error: {e}")
        print("=" * 60)

if __name__ == "__main__":
    import structlog
    # Disable verbose logs for cleaner output, or keep them if needed
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="YYYY-MM-DD HH:mm:ss"),
            structlog.dev.ConsoleRenderer(colors=True),
        ]
    )
    asyncio.run(main())
