import asyncio
import logging
import sys
import chromadb

from app.corpus.quran_loader import QuranLoader

async def seed_quran():
    # Setup logging to see progress
    logging.basicConfig(level=logging.INFO)
    
    print("Initializing ChromaDB Client...")
    client = chromadb.PersistentClient(path='./chroma_data')
    
    print("Starting full Quran download and embedding... (this will take several minutes)")
    loader = QuranLoader()
    
    # Download and embed all 6236 verses
    await loader.load_full_quran(chroma_client=client)
    
    print("Finished seeding the database!")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(seed_quran())
