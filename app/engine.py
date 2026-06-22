from __future__ import annotations

"""Deterministic in-memory position engine.

This module intentionally has no FastAPI or SQLite dependencies. Keeping the core
state machine isolated makes it easy to test the correctness rules that matter for
the case study: idempotency, per-trade ordering, amendments, cancellations, and
fast read-model access.
"""

from dataclasses import dataclass
from threading import RLock

from app.models import PositionView, ProcessingResult, TradeAction, TradeEvent


class DuplicateEventConflict(ValueError):
    """Raised when the same eventId arrives with different event contents."""


@dataclass
class TradeState:
    """Latest live state for a single trade lifecycle."""

    trade_id: str
    instrument: str
    quantity: int
    sequence_number: int
    status: str


@dataclass
class InstrumentPosition:
    """Read-optimized aggregate for one instrument."""

    instrument: str
    net_position: int = 0
    total_buys: int = 0
    total_sells: int = 0


class PositionEngine:
    """Applies trade events into current position state.

    The engine provides exactly-once state effects within one process by tracking
    processed event ids and payload hashes. In production, these same checks must
    also be backed by durable storage so they survive process crashes.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        # eventId -> payload hash. This lets us distinguish a harmless retry from
        # a dangerous reused eventId with different trade contents.
        self._processed_event_hashes: dict[str, str] = {}
        # tradeId -> latest state. Ordering is per trade, not global.
        self._trades: dict[str, TradeState] = {}
        # instrument -> aggregate view used by reads/dashboard.
        self._positions: dict[str, InstrumentPosition] = {}
        self._watermark = 0

    @property
    def watermark(self) -> int:
        with self._lock:
            return self._watermark

    def apply_event(self, event: TradeEvent, watermark: int | None = None) -> ProcessingResult:
        """Apply one event if it is new and newer than current trade state.

        AMEND is treated as an absolute quantity replacement, not a delta. CANCEL
        turns the trade quantity into zero. Lower/equal sequence numbers are kept
        out of the live view so late arrivals cannot roll state backwards.
        """

        payload_hash = event.payload_hash()

        with self._lock:
            next_watermark = self._watermark if watermark is None else max(self._watermark, watermark)

            existing_hash = self._processed_event_hashes.get(event.event_id)
            if existing_hash is not None:
                if existing_hash != payload_hash:
                    raise DuplicateEventConflict(
                        f"eventId {event.event_id} was reused with a different payload"
                    )
                self._watermark = next_watermark
                return ProcessingResult(
                    eventId=event.event_id,
                    tradeId=event.trade_id,
                    status="duplicate",
                    applied=False,
                    duplicate=True,
                    watermark=self._watermark,
                    message="Duplicate eventId ignored",
                )

            self._processed_event_hashes[event.event_id] = payload_hash
            self._watermark = next_watermark

            current = self._trades.get(event.trade_id)
            if current is not None and event.sequence_number <= current.sequence_number:
                return ProcessingResult(
                    eventId=event.event_id,
                    tradeId=event.trade_id,
                    status="stale",
                    applied=False,
                    stale=True,
                    watermark=self._watermark,
                    message="Lower or equal sequenceNumber retained for audit but ignored for live state",
                )

            old_instrument = current.instrument if current else event.instrument
            old_quantity = current.quantity if current else 0
            new_quantity = 0 if event.action == TradeAction.CANCEL else event.quantity
            new_status = "CANCELLED" if event.action == TradeAction.CANCEL else "OPEN"

            if current is not None:
                # Remove the previous latest state before adding the replacement.
                # This keeps AMEND/CANCEL semantics exact and prevents buys/sells
                # from being counted as historical volume.
                self._remove_quantity(old_instrument, old_quantity)

            self._add_quantity(event.instrument, new_quantity)
            self._trades[event.trade_id] = TradeState(
                trade_id=event.trade_id,
                instrument=event.instrument,
                quantity=new_quantity,
                sequence_number=event.sequence_number,
                status=new_status,
            )

            return ProcessingResult(
                eventId=event.event_id,
                tradeId=event.trade_id,
                status="applied",
                applied=True,
                watermark=self._watermark,
                message="Event applied to live position state",
            )

    def positions(self) -> list[PositionView]:
        """Return a stable snapshot of all instrument positions."""

        with self._lock:
            return [
                PositionView(
                    instrument=position.instrument,
                    netPosition=position.net_position,
                    totalBuys=position.total_buys,
                    totalSells=position.total_sells,
                )
                for position in sorted(self._positions.values(), key=lambda item: item.instrument)
            ]

    def position_for(self, instrument: str) -> PositionView:
        """Return one instrument's current position, defaulting to zero."""

        normalized = instrument.strip().upper()
        with self._lock:
            position = self._positions.get(normalized)
            if position is None:
                return PositionView(instrument=normalized, netPosition=0, totalBuys=0, totalSells=0)
            return PositionView(
                instrument=position.instrument,
                netPosition=position.net_position,
                totalBuys=position.total_buys,
                totalSells=position.total_sells,
            )

    def stats(self) -> dict[str, object]:
        """Return dashboard-friendly buy/sell histogram data."""

        with self._lock:
            positions = self.positions()
            return {
                "watermark": self._watermark,
                "buyHistogram": [
                    {"instrument": item.instrument, "quantity": item.total_buys} for item in positions
                ],
                "sellHistogram": [
                    {"instrument": item.instrument, "quantity": item.total_sells} for item in positions
                ],
            }

    def _add_quantity(self, instrument: str, quantity: int) -> None:
        """Add a trade's current quantity into the instrument aggregate."""

        position = self._positions.setdefault(instrument, InstrumentPosition(instrument=instrument))
        position.net_position += quantity
        if quantity > 0:
            position.total_buys += quantity
        elif quantity < 0:
            position.total_sells += abs(quantity)

    def _remove_quantity(self, instrument: str, quantity: int) -> None:
        """Remove a trade's previous quantity from the instrument aggregate."""

        position = self._positions.setdefault(instrument, InstrumentPosition(instrument=instrument))
        position.net_position -= quantity
        if quantity > 0:
            position.total_buys -= quantity
        elif quantity < 0:
            position.total_sells -= abs(quantity)
