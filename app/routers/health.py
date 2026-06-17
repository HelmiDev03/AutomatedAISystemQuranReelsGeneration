"""Health check endpoints."""

import structlog
from fastapi import APIRouter

logger = structlog.get_logger()

router = APIRouter()


@router.get("/health")
async def health_check():
    """Basic health check."""
    return {
        "status": "healthy",
        "service": "islamic-content-automation",
        "version": "0.1.0",
    }


@router.get("/health/detailed")
async def detailed_health():
    """Detailed health check — verifies database, Redis, and ChromaDB connectivity."""
    checks = {}

    # Check PostgreSQL
    try:
        from app.database import async_session
        from sqlalchemy import text

        async with async_session() as db:
            await db.execute(text("SELECT 1"))
        checks["database"] = {"status": "healthy"}
    except Exception as e:
        checks["database"] = {"status": "unhealthy", "error": str(e)}

    # Check Redis
    try:
        import redis.asyncio as aioredis
        from app.config import get_settings

        settings = get_settings()
        r = aioredis.from_url(settings.redis_url)
        await r.ping()
        await r.aclose()
        checks["redis"] = {"status": "healthy"}
    except Exception as e:
        checks["redis"] = {"status": "unhealthy", "error": str(e)}

    # Check ChromaDB
    try:
        import chromadb
        from app.config import get_settings

        settings = get_settings()
        client = chromadb.HttpClient(
            host=settings.chroma_host, port=settings.chroma_port
        )
        client.heartbeat()
        checks["chromadb"] = {"status": "healthy"}
    except Exception as e:
        checks["chromadb"] = {"status": "unhealthy", "error": str(e)}

    overall = "healthy" if all(
        c["status"] == "healthy" for c in checks.values()
    ) else "degraded"

    return {"status": overall, "checks": checks}
