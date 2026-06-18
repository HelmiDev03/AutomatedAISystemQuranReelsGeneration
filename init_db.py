import asyncio
import logging
from app.database import init_db, async_session
from app.corpus.seed_topics import seed_topics

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("db_init")

async def setup():
    logger.info("Initializing SQLite database tables...")
    await init_db()
    logger.info("Database tables created successfully!")
    
    logger.info("Seeding content topics...")
    async with async_session() as session:
        count = await seed_topics(session)
        await session.commit()
    logger.info(f"Successfully seeded {count} topics into the database!")

if __name__ == "__main__":
    asyncio.run(setup())
