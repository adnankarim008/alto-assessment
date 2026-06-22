from __future__ import annotations

"""FastAPI JSON routes for ingestion, positions, stats, and health."""

import logging

from fastapi import APIRouter, HTTPException

from app.engine import DuplicateEventConflict
from app.models import BatchProcessingResponse, PositionsResponse, ProcessingResult, TradeEvent
from app.service import PositionService


logger = logging.getLogger(__name__)


def create_api_router(service: PositionService, recovered_events: int) -> APIRouter:
    """Create API routes bound to a `PositionService` instance."""

    router = APIRouter()

    @router.post("/events", response_model=ProcessingResult)
    def ingest_event(event: TradeEvent) -> ProcessingResult:
        """Ingest one trade event."""

        try:
            return service.process_event(event)
        except DuplicateEventConflict as exc:
            logger.warning("single event ingestion rejected event_id=%s trade_id=%s", event.event_id, event.trade_id)
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/events/batch", response_model=BatchProcessingResponse)
    def ingest_batch(events: list[TradeEvent]) -> BatchProcessingResponse:
        """Ingest a batch of events while reporting per-event outcomes."""

        results: list[ProcessingResult] = []
        accepted = 0
        rejected = 0
        for event in events:
            try:
                result = service.process_event(event)
                if result.status != "duplicate":
                    accepted += 1
                results.append(result)
            except DuplicateEventConflict as exc:
                rejected += 1
                results.append(
                    ProcessingResult(
                        eventId=event.event_id,
                        tradeId=event.trade_id,
                        status="rejected",
                        applied=False,
                        duplicate=False,
                        stale=False,
                        watermark=service.engine.watermark,
                        message=str(exc),
                    )
                )
        logger.info(
            "batch ingestion completed event_count=%s accepted=%s rejected=%s watermark=%s",
            len(events),
            accepted,
            rejected,
            service.engine.watermark,
        )
        return BatchProcessingResponse(accepted=accepted, rejected=rejected, results=results)

    @router.get("/positions", response_model=PositionsResponse)
    def get_positions() -> PositionsResponse:
        """Return all instrument positions and the current watermark."""

        return service.positions()

    @router.get("/positions/{instrument}")
    def get_position(instrument: str) -> dict[str, object]:
        """Return one instrument's current position."""

        return {
            "watermark": service.engine.watermark,
            "position": service.engine.position_for(instrument).model_dump(by_alias=True),
        }

    @router.get("/stats")
    def get_stats() -> dict[str, object]:
        """Return buy/sell histogram data for the dashboard."""

        return service.engine.stats()

    @router.get("/watermark")
    def get_watermark() -> dict[str, int]:
        """Return the latest durable event row applied to the engine."""

        return {"watermark": service.engine.watermark}

    @router.get("/health")
    def health() -> dict[str, object]:
        """Return basic service health and recovery information."""

        return {
            "status": "ok",
            "watermark": service.engine.watermark,
            "storedEvents": service.store.count(),
            "recoveredEvents": recovered_events,
        }

    return router
