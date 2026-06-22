from __future__ import annotations

"""FastAPI application factory.

This module is intentionally thin: it wires together the durable store, service,
API router, dashboard router, and startup recovery. Route handlers live in
`app.api` and `app.dashboard` so the app remains easy to navigate.
"""

import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI

from app.api import create_api_router
from app.dashboard import create_dashboard_router
from app.logging_config import configure_logging
from app.service import PositionService
from app.store import EventStore


logger = logging.getLogger(__name__)


def create_app(db_path: str | Path | None = None) -> FastAPI:
    """Create a configured FastAPI app.

    `db_path` is injectable so tests can run against isolated temporary
    databases. In normal local runs, `POSITION_DB_PATH` or `position_engine.db`
    is used.
    """

    configure_logging()
    database_path = db_path or os.getenv("POSITION_DB_PATH", "position_engine.db")
    service = PositionService(EventStore(database_path))
    recovered_events = service.recover()
    logger.info(
        "position engine startup complete db_path=%s recovered_events=%s watermark=%s",
        database_path,
        recovered_events,
        service.engine.watermark,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Close SQLite resources when FastAPI shuts down."""

        try:
            yield
        finally:
            logger.info("position engine shutdown starting")
            service.close()
            logger.info("position engine shutdown complete")

    app = FastAPI(
        title="High-Throughput Position Engine",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.service = service
    app.state.recovered_events = recovered_events

    app.include_router(create_dashboard_router(service))
    app.include_router(create_api_router(service, recovered_events))

    return app


app = create_app()
