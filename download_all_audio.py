import asyncio
import logging
import os
import sys
from pathlib import Path

import httpx

from app.config import get_settings

# Same audio cache directory used by the reel renderer
AUDIO_CACHE_DIR = Path("audio_cache")
AUDIO_CACHE_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(message)s"
)
logger = logging.getLogger("audio_downloader")

async def download_audio():
    logger.info("Fetching full Quran metadata from Al-Quran Cloud...")
    
    # Use a real browser user-agent to avoid CDN blocks
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    # 1. Fetch metadata so we know the Surah:Ayah to Global Ayah mapping
    async with httpx.AsyncClient(timeout=60, headers=headers) as client:
        response = await client.get("https://api.alquran.cloud/v1/quran/quran-uthmani")
        response.raise_for_status()
        surahs = response.json()["data"]["surahs"]
        
        # Load settings and configure the reciter CDN
        settings = get_settings()
        reciter = settings.quran_reciter or "ar.alafasy"
        
        # Supported EveryAyah reciters (which use the 6-digit format: {surah:03d}{ayah:03d}.mp3)
        EVERYAYAH_MAPPING = {
            "ar.dawsari": "https://everyayah.com/data/Yasser_Ad-Dussary_128kbps",
            "ar.alafasy": "https://everyayah.com/data/Alafasy_128kbps"
        }
        
        use_everyayah = reciter in EVERYAYAH_MAPPING
        base_cdn_url = EVERYAYAH_MAPPING.get(reciter)
        
        logger.info(f"Active Quran reciter: {reciter} (Using EveryAyah CDN: {use_everyayah})")
        
        tasks = []
        
        # 2. Build the download queue
        for surah in surahs:
            surah_number = surah["number"]
            for ayah in surah["ayahs"]:
                ayah_number_in_surah = ayah["numberInSurah"]
                global_number = ayah["number"]
                
                audio_path = AUDIO_CACHE_DIR / f"{surah_number}_{ayah_number_in_surah}_{reciter}.mp3"
                if audio_path.exists():
                    continue
                    
                if use_everyayah:
                    # EveryAyah uses 6-digit formatting: {surah:03d}{ayah:03d}.mp3 (e.g., 001001.mp3)
                    download_url = f"{base_cdn_url}/{surah_number:03d}{ayah_number_in_surah:03d}.mp3"
                else:
                    # Fallback to Islamic Network CDN using global number
                    download_url = f"https://cdn.islamic.network/quran/audio/128/{reciter}/{global_number}.mp3"
                    
                tasks.append((download_url, audio_path, surah_number, ayah_number_in_surah, global_number))
                
        if not tasks:
            logger.info("All 6,236 audio files are already downloaded!")
            return
            
        logger.info(f"Need to download {len(tasks)} audio files. Starting concurrent download...")
        
        # Semaphore to limit concurrent downloads to avoid overwhelming the server
        semaphore = asyncio.Semaphore(15)
        
        async def fetch_and_save(url, path, surah, ayah, g_num):
            max_retries = 5
            for attempt in range(max_retries):
                async with semaphore:
                    try:
                        resp = await client.get(url, follow_redirects=True, timeout=30)
                        resp.raise_for_status()
                        with open(path, "wb") as f:
                            f.write(resp.content)
                        if g_num % 100 == 0:
                            logger.info(f"Downloaded Surah {surah} Ayah {ayah} (Global {g_num})...")
                        return
                    except Exception as e:
                        if attempt == max_retries - 1:
                            logger.error(f"Failed to download Surah {surah} Ayah {ayah}: {e}")
                        else:
                            await asyncio.sleep(2 ** attempt)

        # 3. Execute all downloads concurrently
        # Batch them to show progress
        batch_size = 500
        total = len(tasks)
        for i in range(0, total, batch_size):
            batch = tasks[i:i+batch_size]
            logger.info(f"Processing batch {i} to {i+len(batch)} of {total}...")
            await asyncio.gather(*(fetch_and_save(u, p, s, a, g) for u, p, s, a, g in batch))
            
    logger.info("Finished downloading all Quran audio!")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(download_audio())
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
