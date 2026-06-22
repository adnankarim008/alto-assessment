# High-Throughput Position Engine

Python/FastAPI implementation of a position engine that consumes trade lifecycle events and exposes current positions through an API and simple dashboard.

The engine handles:

- Duplicate `eventId`s
- Duplicate `eventId` with conflicting payload detection
- `NEW`, `AMEND`, and `CANCEL` actions
- Out-of-order sequence arrivals
- In-memory low-latency reads
- SQLite-backed durable event replay for recovery

## Local Setup

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Alternatively, install from `pyproject.toml` with development dependencies:

```bash
pip install -e '.[dev]'
```

Run tests:

```bash
python3 -m pytest
```

Run the API:

```bash
uvicorn app.main:app --reload
```

Open:

- Dashboard: `http://127.0.0.1:8000/`
- API docs: `http://127.0.0.1:8000/docs`
- Health: `http://127.0.0.1:8000/health`

By default, the app stores events in `position_engine.db`.

To use another SQLite file:

```bash
POSITION_DB_PATH=/tmp/positions.db uvicorn app.main:app --reload
```

Set log verbosity with `LOG_LEVEL`:

```bash
LOG_LEVEL=DEBUG uvicorn app.main:app --reload
```

Application logs include startup recovery, duplicate/conflicting event handling, stale event handling, batch ingestion summaries, and shutdown. Successful per-event application is logged at `DEBUG` to avoid noisy logs during high-throughput runs.

## Example Event

```bash
curl -X POST http://127.0.0.1:8000/events \
  -H 'Content-Type: application/json' \
  -d '{
    "eventId": "e1",
    "tradeId": "T1",
    "action": "NEW",
    "instrument": "AAPL",
    "quantity": 100,
    "sequenceNumber": 1
  }'
```

Batch example:

```bash
curl -X POST http://127.0.0.1:8000/events/batch \
  -H 'Content-Type: application/json' \
  -d '[
    {"eventId":"e1","tradeId":"T1","action":"NEW","instrument":"AAPL","quantity":100,"sequenceNumber":1},
    {"eventId":"e2","tradeId":"T2","action":"NEW","instrument":"AAPL","quantity":-30,"sequenceNumber":1},
    {"eventId":"e3","tradeId":"T3","action":"NEW","instrument":"MSFT","quantity":50,"sequenceNumber":1}
  ]'
```

Query positions:

```bash
curl http://127.0.0.1:8000/positions
```

Expected shape:

```json
{
  "watermark": 3,
  "positions": [
    {
      "instrument": "AAPL",
      "netPosition": 70,
      "totalBuys": 100,
      "totalSells": 30
    },
    {
      "instrument": "MSFT",
      "netPosition": 50,
      "totalBuys": 50,
      "totalSells": 0
    }
  ]
}
```

## API Endpoints

- `POST /events`
- `POST /events/batch`
- `GET /positions`
- `GET /positions/{instrument}`
- `GET /stats`
- `GET /watermark`
- `GET /health`
- `GET /events/stream`
- `GET /`

The dashboard uses Server-Sent Events through `GET /events/stream`, so the browser receives a new snapshot when the processing watermark changes instead of polling the API on a timer. The dashboard stream is intentionally bounded: it sends the top 50 instruments by absolute exposure and top 10 buy/sell histogram bars, plus the total instrument count and latest event age.

## Important Local Runtime Note

Run the local app with one Uvicorn worker:

```bash
uvicorn app.main:app --reload
```

or:

```bash
uvicorn app.main:app --workers 1
```

The local app keeps its read model in process memory. Running multiple Uvicorn workers would create multiple independent in-memory engines, which is not safe for write consistency in this demo architecture.

Production should scale with sharded workers, durable state, and a shared cache/read projection rather than random multi-process app memory.

## Project Structure

```text
app/
  api.py         # FastAPI JSON routes
  dashboard.py   # simple HTML dashboard route
  engine.py      # core position logic
  main.py        # FastAPI app factory and wiring
  models.py      # Pydantic event/response models
  service.py     # orchestration between store and engine
  store.py       # SQLite durable event log
tests/
  test_engine.py
  test_recovery_and_api.py
DESIGN.md
report.html
```

## Performance Report

`report.html` includes the local smoke-test results used to reason about throughput, read latency, and the difference between the in-memory engine path and the SQLite-backed durable path.
