"""FastAPI application factory and lifespan management."""

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import init_db

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup and shutdown lifecycle."""
    settings = get_settings()
    logger.info(
        "Starting Islamic Content Automation",
        review_mode=settings.review_mode,
        posting_interval=settings.posting_interval_minutes,
    )

    # Initialize database tables (dev mode)
    await init_db()
    logger.info("Database initialized")

    yield

    logger.info("Shutting down")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="Islamic Content Automation",
        description=(
            "Automated Islamic content generation, verification, "
            "and publishing pipeline for Instagram."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routers
    from app.routers import analytics, content, health, publish

    app.include_router(health.router, tags=["Health"])
    app.include_router(content.router, prefix="/api/v1/content", tags=["Content"])
    app.include_router(publish.router, prefix="/api/v1/publish", tags=["Publishing"])
    app.include_router(analytics.router, prefix="/api/v1/analytics", tags=["Analytics"])

    return app


app = create_app()
