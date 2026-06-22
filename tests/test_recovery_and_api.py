from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.engine import DuplicateEventConflict
from app.main import create_app
from app.models import TradeEvent
from app.service import PositionService
from app.store import EventStore


def event(
    event_id: str,
    trade_id: str,
    action: str,
    instrument: str,
    quantity: int,
    sequence_number: int,
) -> TradeEvent:
    return TradeEvent(
        eventId=event_id,
        tradeId=trade_id,
        action=action,
        instrument=instrument,
        quantity=quantity,
        sequenceNumber=sequence_number,
    )


def test_replay_rebuilds_exact_state_after_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "positions.db"
    service = PositionService(EventStore(db_path))

    service.process_event(event("e1", "T1", "NEW", "AAPL", 100, 1))
    service.process_event(event("e2", "T1", "AMEND", "AAPL", 80, 2))
    service.process_event(event("e3", "T2", "NEW", "MSFT", -40, 1))

    restarted = PositionService(EventStore(db_path))
    recovered = restarted.recover()

    assert recovered == 3
    assert restarted.engine.position_for("AAPL").net_position == 80
    assert restarted.engine.position_for("MSFT").net_position == -40
    assert restarted.engine.watermark == 3


def test_store_ignores_duplicate_event_id_with_same_payload(tmp_path: Path) -> None:
    service = PositionService(EventStore(tmp_path / "positions.db"))

    service.process_event(event("e1", "T1", "NEW", "AAPL", 100, 1))
    response = service.process_event(event("e1", "T1", "NEW", "AAPL", 100, 1))

    assert response.duplicate is True
    assert service.engine.position_for("AAPL").net_position == 100


def test_store_rejects_duplicate_event_id_with_different_payload(tmp_path: Path) -> None:
    service = PositionService(EventStore(tmp_path / "positions.db"))

    service.process_event(event("e1", "T1", "NEW", "AAPL", 100, 1))

    with pytest.raises(DuplicateEventConflict):
        service.process_event(event("e1", "T1", "NEW", "AAPL", 200, 1))


def test_api_exposes_positions_stats_and_watermark(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "api.db"))

    response = client.post(
        "/events/batch",
        json=[
            {
                "eventId": "e1",
                "tradeId": "T1",
                "action": "NEW",
                "instrument": "AAPL",
                "quantity": 100,
                "sequenceNumber": 1,
            },
            {
                "eventId": "e2",
                "tradeId": "T2",
                "action": "NEW",
                "instrument": "AAPL",
                "quantity": -30,
                "sequenceNumber": 1,
            },
        ],
    )
    assert response.status_code == 200

    positions = client.get("/positions").json()
    assert positions == {
        "watermark": 2,
        "positions": [
            {
                "instrument": "AAPL",
                "netPosition": 70,
                "totalBuys": 100,
                "totalSells": 30,
            }
        ],
    }

    stats = client.get("/stats").json()
    assert stats["watermark"] == 2
    assert stats["buyHistogram"] == [{"instrument": "AAPL", "quantity": 100}]
    assert stats["sellHistogram"] == [{"instrument": "AAPL", "quantity": 30}]


def test_dashboard_uses_server_sent_events(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "dashboard.db"))

    response = client.get("/")

    assert response.status_code == 200
    assert "EventSource('/events/stream')" in response.text
    assert "Processing watermark" in response.text
