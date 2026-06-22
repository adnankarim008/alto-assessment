from __future__ import annotations

"""Pydantic request and response models used by the API and engine.

The external JSON schema uses camelCase field names from the case study, while
the Python code uses snake_case attributes. Pydantic aliases keep both sides
explicit and readable.
"""

import hashlib
import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TradeAction(str, Enum):
    """Supported trade lifecycle actions."""

    NEW = "NEW"
    AMEND = "AMEND"
    CANCEL = "CANCEL"


class TradeEvent(BaseModel):
    """Validated trade event matching the case-study schema."""

    model_config = ConfigDict(populate_by_name=True)

    event_id: str = Field(alias="eventId", min_length=1)
    trade_id: str = Field(alias="tradeId", min_length=1)
    action: TradeAction
    instrument: str = Field(min_length=1)
    quantity: int
    sequence_number: int = Field(alias="sequenceNumber", ge=0)

    @field_validator("instrument")
    @classmethod
    def normalize_instrument(cls, value: str) -> str:
        """Normalize instrument symbols so `aapl` and `AAPL` aggregate together."""

        return value.strip().upper()

    def canonical_payload(self) -> dict[str, Any]:
        """Return the normalized payload used for hashing and persistence."""

        return self.model_dump(by_alias=True)

    def payload_json(self) -> str:
        """Return deterministic JSON so the same event always hashes identically."""

        return json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))

    def payload_hash(self) -> str:
        """Hash the canonical payload for duplicate-conflict detection."""

        return hashlib.sha256(self.payload_json().encode("utf-8")).hexdigest()


class PositionView(BaseModel):
    """Read model returned for one instrument."""

    instrument: str
    net_position: int = Field(alias="netPosition")
    total_buys: int = Field(alias="totalBuys")
    total_sells: int = Field(alias="totalSells")

    model_config = ConfigDict(populate_by_name=True)


class ProcessingResult(BaseModel):
    """Outcome of attempting to process one event."""

    event_id: str = Field(alias="eventId")
    trade_id: str = Field(alias="tradeId")
    status: str
    applied: bool
    duplicate: bool = False
    stale: bool = False
    watermark: int
    message: str

    model_config = ConfigDict(populate_by_name=True)


class PositionsResponse(BaseModel):
    """Response containing all current instrument positions."""

    watermark: int
    positions: list[PositionView]


class BatchProcessingResponse(BaseModel):
    """Response for batch ingestion with per-event outcomes."""

    accepted: int
    rejected: int
    results: list[ProcessingResult]
