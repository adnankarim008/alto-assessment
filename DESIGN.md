# Design Note

## Assignment Requirement Mapping

This submission covers the required deliverables as follows.

Runnable application:

- Implemented as a Python/FastAPI service in `app/main.py`.
- Core position logic is implemented in `app/engine.py`.
- Events are persisted through SQLite in `app/store.py`.

Local setup guide:

- Covered in `README.md` with install, test, and run commands.

Automated tests:

- Covered in `tests/test_engine.py` and `tests/test_recovery_and_api.py`.
- Tests validate duplicates, conflicting duplicates, amendments, cancellations, out-of-order events, stale lower-sequence events, API responses, and recovery replay.

API or simple UI:

- `GET /` provides a simple dashboard.
- `GET /positions` exposes current position by instrument.
- `GET /positions/{instrument}` exposes one instrument.
- `GET /stats` exposes buy/sell histogram data.
- `GET /watermark` exposes the latest processing watermark.
- `GET /events/stream` streams dashboard updates with Server-Sent Events.

Design note requirements:

- Idempotency and deduplication: covered in `Idempotency And Deduplication`.
- Out-of-order event handling: covered in `Out-Of-Order Handling`.
- Fault recovery and checkpointing strategy: covered in `Fault Recovery`.
- Read/write path separation: covered in `Read/Write Isolation`.
- Hotspot mitigation and scalability architecture: covered in `Hotspot Mitigation And Scalability`.

Code quality and documentation:

- Simple cohesive structure: core engine, persistence, service orchestration, API, dashboard, and models are separated by responsibility.
- Industry-standard naming: Python modules/classes/functions use clear snake_case/PascalCase conventions and domain terms from the prompt.
- Easy local setup: `README.md` includes environment setup, dependency install, test command, run command, example ingestion, and API endpoints.
- Professional documentation: `README.md` covers operation, while this `DESIGN.md` maps the implementation to the assessment requirements and production strategy.

## Summary

This implementation is a local, runnable version of a production-style position engine.

The runnable app uses:

- FastAPI for API/dashboard access
- SQLite as a durable event/idempotency log
- An in-process `PositionEngine` for deterministic state transitions
- An in-memory read model for low-latency position reads

The production design would evolve the same core logic into GKE sharded workers, a durable database-backed event log, Memorystore for hot reads, and a stateless Cloud Run read API.

## Implementation Notes

This repository intentionally separates the system into small modules:

- `app/models.py`: validates external event/response schemas.
- `app/store.py`: owns durable event persistence and eventId uniqueness.
- `app/engine.py`: owns deterministic position-state transitions.
- `app/service.py`: coordinates the durable-before-memory processing boundary.
- `app/api.py`: exposes JSON ingestion/read endpoints.
- `app/dashboard.py`: exposes the simple HTML dashboard.
- `app/main.py`: wires the application together and runs startup recovery.

The local app should be run with one Uvicorn worker because its in-memory read model is process-local. This is acceptable for the runnable case study because it keeps the implementation simple and makes correctness easy to verify.

Production should not scale this exact process-local memory model by simply adding random web workers. Production should separate the write workers, shared durable state, shared hot cache, and stateless read API.

Local versus production responsibilities:

| Concern | Local implementation | Production direction |
| --- | --- | --- |
| Durable event log | SQLite | Aurora/Postgres, AlloyDB, Spanner, DynamoDB, or equivalent |
| Hot read cache | In-process `PositionEngine` maps | Redis/Memorystore/ElastiCache |
| Write scaling | Single process | Sharded workers by `hash(tradeId)` |
| Read scaling | Same FastAPI app | Stateless read API plus shared cache/read projection |
| Recovery | Replay SQLite event log | Load durable checkpoint, replay newer durable events |

## Logging And Observability

The runnable app uses Python standard-library logging, configured by `LOG_LEVEL`.

Logged events include:

- startup recovery start/completion
- recovered event count and watermark
- duplicate event ids ignored
- duplicate event ids with conflicting payloads rejected
- stale lower/equal sequence events ignored for live state
- batch ingestion summaries
- graceful shutdown

Successful per-event application is logged at `DEBUG` rather than `INFO` because high-throughput systems should not emit one info log for every normal event. Production observability would add metrics for event rate, duplicate rate, stale-event rate, processing lag, cache hit rate, recovery time, and watermark age.

## Advanced Core Requirements

### 1. Exact-Once State Semantics

How the runnable app handles it:

- Deduplication uses `eventId` as the idempotency key.
- SQLite enforces `event_id` uniqueness in the durable event log.
- A SHA-256 payload hash detects the dangerous case where the same `eventId` is reused with different content.
- `AMEND` replaces the current quantity for a trade.
- `CANCEL` sets the current quantity for a trade to zero.
- `sequenceNumber` is tracked per `tradeId`; only a higher sequence number can update live state.

Why this satisfies the requirement:

- Duplicate delivery does not double-count positions.
- Out-of-order older events are preserved in the audit log but do not corrupt current positions.
- The final state for a trade reflects the highest sequence number seen.

### 2. Skewed Scalability And Hotspots

Requirement target:

- Baseline throughput: 1,000 events/second.
- Peak burst: 10,000 events/second.
- Extreme instrument skew must not cause head-of-line blocking for unrelated instruments.

How the local app demonstrates the idea:

- The engine state transition is deterministic and isolated by `tradeId`.
- The SQLite event table includes a `shard_id` derived from `tradeId`, showing how production workers would claim work by shard.
- The runnable app intentionally uses SQLite for simple local setup and correctness/recovery demonstration; it is not presented as the final production throughput store.

Production strategy:

- Shard by `tradeId`, not `instrument`.
- Use many virtual shards, such as 128 or 256, mapped onto fewer worker pods.
- Scale GKE workers based on shard backlog and processing lag.
- Rebalance virtual shards across workers when one worker is overloaded.
- Avoid one global write lock; each shard should have one active writer while different shards process concurrently.
- Use a production durable append/state store such as Aurora/Postgres, AlloyDB, Spanner, or DynamoDB instead of SQLite.
- Keep reads on the in-memory/shared hot read model so dashboard traffic does not compete with ingestion writes.

Why this matters:

- A hot instrument like `AAPL` should not force all `AAPL` trades through one processing lane.
- Different trades for the same instrument can process in parallel while preserving ordering for each individual `tradeId`.
- Horizontal shard scaling and bounded dashboard snapshots are the intended path for reaching the 1,000/sec baseline and absorbing 10,000/sec bursts in production.

### 3. Fault Tolerance And Zero-Data-Loss Recovery

How the runnable app handles it:

- An event is written to SQLite before it is applied to the in-memory engine.
- If the app crashes after the SQLite write but before the memory update, startup replay applies the event.
- The in-memory read model is treated as rebuildable cache, not the source of truth.

Production strategy:

- Store events durably in Postgres/AlloyDB/Spanner before acknowledgement.
- Store periodic durable checkpoints per shard.
- On restart, load the latest checkpoint and replay durable events after that checkpoint.
- Rebuild/warm the hot cache before marking the service healthy.

Why this satisfies the requirement:

- Accepted events are not lost after a crash.
- Checkpoints bound replay time so recovery can target the 30-second requirement.

### 4. Bounded Dual-Read Path

How the runnable app handles it:

- Writes go through `POST /events` and `POST /events/batch`.
- Reads go through `GET /positions`, `GET /stats`, `GET /watermark`, and the dashboard.
- Reads are served from the in-memory read model instead of scanning the event log.
- Responses include a watermark so dashboard freshness is visible.
- The dashboard uses Server-Sent Events to receive pushed snapshots when the watermark changes.
- The dashboard stream is bounded to top-N data so a large number of instruments does not force the browser to render every row on every update.

Production strategy:

- Write path: ingestion service plus sharded workers.
- Hot read path: Memorystore/Redis for sub-5ms reads.
- Durable read fallback: Firestore/Postgres read projection.
- Read API: stateless Cloud Run service that scales independently from workers.
- Large books should use paginated/filtered position queries and delta updates rather than streaming the full book on every event.

Why this matters:

- Dashboard traffic does not lock ingestion.
- Ingestion spikes do not directly take down the read API.
- Risk managers can see if the dashboard is fresh or lagging through the watermark.

### 5. Required Outputs

The runnable API exposes:

- Current position by instrument: `GET /positions` and `GET /positions/{instrument}`.
- Total buy quantity by instrument: `totalBuys` in position responses.
- Total sell quantity by instrument: `totalSells` in position responses.
- Buy/sell histograms: `GET /stats`.
- Live processing watermark: `GET /watermark` and included in `/positions`.
- Simple real-time dashboard: `GET /` backed by `GET /events/stream`.

## Idempotency And Deduplication

`eventId` is the primary idempotency key.

Every accepted event is stored in SQLite with a unique constraint on `event_id` and a SHA-256 payload hash.

If the same `eventId` arrives again:

- Same payload hash: treated as a duplicate and ignored.
- Different payload hash: rejected as a data-integrity conflict.

Hashing is used for integrity checking, not as a replacement for the raw idempotency key.

## Out-Of-Order Handling

Ordering is enforced per `tradeId` using `sequenceNumber`.

The engine tracks the latest state per trade:

- latest sequence number
- instrument
- current quantity
- status

If an event has a lower or equal sequence number than the latest known state, it is accepted into the durable event log but ignored for the live position view.

This handles cases such as:

```text
AMEND seq=2 arrives before NEW seq=1
```

The sequence 2 state becomes current. When sequence 1 later arrives, it is retained for audit/replay ordering but does not corrupt the current position.

## Position Calculation

`NEW` and `AMEND` set the absolute trade quantity. They are not treated as deltas.

`CANCEL` sets the trade quantity to zero.

For each latest trade update, the engine removes the old trade quantity from the instrument aggregate and adds the new quantity.

The read model exposes:

- `netPosition`: signed current exposure
- `totalBuys`: current positive open quantity
- `totalSells`: current absolute negative open quantity
- `watermark`: latest durable event row processed by the engine

## Fault Recovery

The local app writes accepted events to SQLite before applying them to memory.

On startup, it replays all durable events in insertion order and rebuilds:

- processed event ids
- latest trade state
- instrument positions
- buy/sell totals
- watermark

The in-memory cache is a speed layer only. It can be rebuilt from durable state after a crash.

Production recovery would add durable checkpoints to avoid replaying the full event log. Recovery would load the latest checkpoint and replay only events after that checkpoint, targeting exact recovery within 30 seconds.

## Read/Write Isolation

Writes go through the position engine and update authoritative state.

Reads are served from an in-memory read model:

- `GET /positions`
- `GET /positions/{instrument}`
- `GET /stats`
- dashboard page

This avoids scanning or locking the durable event log for every dashboard request.

Production would use Memorystore for hot reads and a durable read projection in Firestore/Postgres as fallback.

## Caching Strategy

The local implementation uses the `PositionEngine` in-memory maps as the hot read cache:

- `tradeId -> latest trade state`
- `instrument -> current net/buy/sell position`
- latest processing watermark

This gives fast reads without recalculating positions from the event log on every request.

Important rule:

```text
The cache is not the source of truth.
```

SQLite is the local durable source of truth. If the app restarts, the cache is rebuilt by replaying stored events.

In production on AWS, AWS does not automatically handle this application-level cache. The recommended AWS strategy would be:

```text
Write workers
  -> durable event/state store in Aurora Postgres or DynamoDB
  -> update hot cache in ElastiCache Redis/Valkey

Read API
  -> read from ElastiCache first
  -> fall back to durable read projection if cache is missing/stale
```

Recommended production cache pattern:

- Write-through for position updates: after durable state is committed, update Redis/Valkey.
- Cache-aside for reads: read API checks Redis/Valkey first, then falls back to the durable read projection.
- Rebuild on failure: if Redis/Valkey is flushed or unavailable, rebuild it from checkpoints plus event replay or from the durable read projection.
- Watermark every cache value: expose freshness so dashboards can detect stale reads.

AWS services that fit this:

- ElastiCache Redis/Valkey for the hot position cache.
- Aurora Postgres or DynamoDB for durable state/read projection.
- EKS/ECS for write workers.
- ECS/Fargate, Lambda, or API Gateway plus Lambda for stateless read APIs.

CloudFront or API Gateway caching is not the primary cache for this workload because position data is dynamic and correctness-sensitive. It may be useful for static dashboard assets, but not as the source for live positions.

## Hotspot Mitigation And Scalability

The production design shards workers by `tradeId`, not `instrument`.

Reason:

- Ordering is required per trade lifecycle.
- A hot instrument should not force all of its independent trades through one worker.
- Sharding by `tradeId` avoids head-of-line blocking under instrument skew.

Production worker assignment:

```text
shard_id = hash(tradeId) % shard_count
```

Each shard has one active writer at a time. Independent shards process concurrently.

The database can start with logical sharding via a `shard_id` column and indexes. Physical database sharding is only needed later if volume requires it.

## Production Architecture

```text
Venue gateway
  -> HTTP/gRPC ingestion service
  -> durable event log in Postgres/AlloyDB/Spanner
  -> GKE sharded workers keyed by hash(tradeId)
  -> durable checkpoints
  -> Memorystore hot read cache
  -> Firestore/Postgres read projection
  -> Cloud Run stateless read API
```

GKE is preferred for long-running sharded workers because it provides stronger control over worker lifecycle, shard ownership, and autoscaling.

Cloud Run is preferred for the read API because it is stateless, simple to operate, and scales automatically.
