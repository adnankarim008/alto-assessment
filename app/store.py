from __future__ import annotations

"""SQLite-backed durable event store for the runnable case-study app.

SQLite is used here because it keeps local setup simple while still proving the
important reliability boundary: an accepted event is written durably before it is
applied to in-memory state. Production would replace this with Aurora/Postgres,
AlloyDB, Spanner, DynamoDB, or another durable event/state store.
"""

import sqlite3
import time
from pathlib import Path
from threading import RLock

from app.models import TradeEvent


class StoredEventConflict(ValueError):
    """Raised when eventId uniqueness is violated with a different payload."""


class EventStore:
    """Append-only event log with idempotency metadata.

    The table stores the original event payload, a payload hash, and a logical
    shard id. The shard id is not required by the local single-process app, but it
    documents how production workers would claim work by tradeId shard.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._lock = RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def append_event(self, event: TradeEvent) -> tuple[bool, int]:
        """Persist an event before the engine mutates memory.

        Returns `(inserted, row_id)`. If `inserted` is false, the event was an
        exact duplicate and should not be applied again. If the same eventId is
        reused with a different payload, this raises `StoredEventConflict`.
        """

        payload_hash = event.payload_hash()
        payload_json = event.payload_json()
        shard_id = stable_shard_id(event.trade_id)

        with self._lock, self._conn:
            existing = self._conn.execute(
                "SELECT id, payload_hash FROM events WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()
            if existing is not None:
                if existing["payload_hash"] != payload_hash:
                    raise StoredEventConflict(
                        f"eventId {event.event_id} already exists with a different payload"
                    )
                return False, int(existing["id"])

            cursor = self._conn.execute(
                """
                INSERT INTO events (
                    event_id, trade_id, action, instrument, quantity, sequence_number,
                    payload_hash, payload_json, shard_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.trade_id,
                    event.action.value,
                    event.instrument,
                    event.quantity,
                    event.sequence_number,
                    payload_hash,
                    payload_json,
                    shard_id,
                    time.time(),
                ),
            )
            return True, int(cursor.lastrowid)

    def replay_events(self) -> list[tuple[int, TradeEvent]]:
        """Load durable events in insertion order for crash recovery."""

        with self._lock:
            rows = self._conn.execute(
                "SELECT id, payload_json FROM events ORDER BY id ASC"
            ).fetchall()
        return [(int(row["id"]), TradeEvent.model_validate_json(row["payload_json"])) for row in rows]

    def count(self) -> int:
        """Return the number of durable events stored locally."""

        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS count FROM events").fetchone()
            return int(row["count"])

    def latest_created_at(self) -> float | None:
        """Return the receive timestamp for the newest durable event."""

        with self._lock:
            row = self._conn.execute("SELECT MAX(created_at) AS latest FROM events").fetchone()
            return None if row["latest"] is None else float(row["latest"])

    def _init_schema(self) -> None:
        """Create the local event log schema and indexes if needed."""

        with self._lock, self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    trade_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    instrument TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    sequence_number INTEGER NOT NULL,
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    shard_id INTEGER NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_shard_id ON events (shard_id, id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_trade_sequence ON events (trade_id, sequence_number)"
            )


def stable_shard_id(trade_id: str, shard_count: int = 64) -> int:
    """Map a trade id to a stable logical shard.

    Python's built-in `hash()` is intentionally randomized between processes, so
    this simple deterministic hash keeps shard assignment stable across restarts.
    """

    value = 0
    for character in trade_id.encode("utf-8"):
        value = (value * 131 + character) % 2_147_483_647
    return value % shard_count
