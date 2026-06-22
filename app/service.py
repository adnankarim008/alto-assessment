from __future__ import annotations

"""Application service that coordinates durable storage and in-memory state."""

import logging

from app.engine import DuplicateEventConflict, PositionEngine
from app.models import PositionsResponse, ProcessingResult, TradeEvent
from app.store import EventStore, StoredEventConflict


logger = logging.getLogger(__name__)


class PositionService:
    """Orchestrates the zero-data-loss boundary for the local app.

    The service writes each event to the durable store first, then applies it to
    the in-memory engine. If the process crashes after the durable write, startup
    recovery replays the event and restores the exact state.
    """

    def __init__(self, store: EventStore) -> None:
        self.store = store
        self.engine = PositionEngine()

    def recover(self) -> int:
        """Rebuild the in-memory engine from all durable events."""

        logger.info("recovery started")
        recovered = 0
        for event_id, event in self.store.replay_events():
            self.engine.apply_event(event, watermark=event_id)
            recovered += 1
        logger.info("recovery completed recovered_events=%s watermark=%s", recovered, self.engine.watermark)
        return recovered

    def close(self) -> None:
        """Close durable resources owned by the service."""

        self.store.close()

    def process_event(self, event: TradeEvent) -> ProcessingResult:
        """Persist and apply a trade event exactly once."""

        try:
            inserted, stored_id = self.store.append_event(event)
        except StoredEventConflict as exc:
            logger.warning(
                "event rejected duplicate_payload_conflict event_id=%s trade_id=%s sequence=%s",
                event.event_id,
                event.trade_id,
                event.sequence_number,
            )
            raise DuplicateEventConflict(str(exc)) from exc

        if not inserted:
            logger.info("duplicate event ignored event_id=%s trade_id=%s", event.event_id, event.trade_id)
            return ProcessingResult(
                eventId=event.event_id,
                tradeId=event.trade_id,
                status="duplicate",
                applied=False,
                duplicate=True,
                watermark=self.engine.watermark,
                message="Duplicate eventId ignored",
            )

        result = self.engine.apply_event(event, watermark=stored_id)
        if result.stale:
            logger.info(
                "stale event ignored event_id=%s trade_id=%s sequence=%s watermark=%s",
                event.event_id,
                event.trade_id,
                event.sequence_number,
                result.watermark,
            )
        else:
            logger.debug(
                "event applied event_id=%s trade_id=%s action=%s sequence=%s watermark=%s",
                event.event_id,
                event.trade_id,
                event.action.value,
                event.sequence_number,
                result.watermark,
            )
        return result

    def positions(self) -> PositionsResponse:
        """Return the current read model with its processing watermark."""

        return PositionsResponse(watermark=self.engine.watermark, positions=self.engine.positions())
