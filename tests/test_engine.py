import pytest

from app.engine import DuplicateEventConflict, PositionEngine
from app.models import TradeEvent


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


def position_map(engine: PositionEngine) -> dict[str, dict[str, int]]:
    return {
        item.instrument: item.model_dump(by_alias=True, exclude={"instrument"})
        for item in engine.positions()
    }


def test_new_events_create_expected_positions() -> None:
    engine = PositionEngine()

    engine.apply_event(event("e1", "T1", "NEW", "AAPL", 100, 1), watermark=1)
    engine.apply_event(event("e2", "T2", "NEW", "AAPL", -30, 1), watermark=2)
    engine.apply_event(event("e3", "T3", "NEW", "MSFT", 50, 1), watermark=3)

    assert position_map(engine) == {
        "AAPL": {"netPosition": 70, "totalBuys": 100, "totalSells": 30},
        "MSFT": {"netPosition": 50, "totalBuys": 50, "totalSells": 0},
    }
    assert engine.watermark == 3


def test_amend_replaces_quantity_not_delta() -> None:
    engine = PositionEngine()

    engine.apply_event(event("e1", "T1", "NEW", "AAPL", 100, 1), watermark=1)
    engine.apply_event(event("e2", "T1", "AMEND", "AAPL", 75, 2), watermark=2)

    assert engine.position_for("AAPL").model_dump(by_alias=True) == {
        "instrument": "AAPL",
        "netPosition": 75,
        "totalBuys": 75,
        "totalSells": 0,
    }


def test_amend_can_change_buy_to_sell_bucket() -> None:
    engine = PositionEngine()

    engine.apply_event(event("e1", "T1", "NEW", "AAPL", 100, 1), watermark=1)
    engine.apply_event(event("e2", "T1", "AMEND", "AAPL", -25, 2), watermark=2)

    assert engine.position_for("AAPL").model_dump(by_alias=True) == {
        "instrument": "AAPL",
        "netPosition": -25,
        "totalBuys": 0,
        "totalSells": 25,
    }


def test_cancel_zeroes_trade_quantity() -> None:
    engine = PositionEngine()

    engine.apply_event(event("e1", "T1", "NEW", "AAPL", 100, 1), watermark=1)
    engine.apply_event(event("e2", "T1", "CANCEL", "AAPL", 100, 2), watermark=2)

    assert engine.position_for("AAPL").model_dump(by_alias=True) == {
        "instrument": "AAPL",
        "netPosition": 0,
        "totalBuys": 0,
        "totalSells": 0,
    }


def test_out_of_order_amend_before_new_keeps_latest_sequence_state() -> None:
    engine = PositionEngine()

    first = engine.apply_event(event("e2", "T1", "AMEND", "AAPL", 120, 2), watermark=1)
    late = engine.apply_event(event("e1", "T1", "NEW", "AAPL", 100, 1), watermark=2)

    assert first.applied is True
    assert late.stale is True
    assert engine.position_for("AAPL").net_position == 120
    assert engine.watermark == 2


def test_late_lower_sequence_cancel_does_not_corrupt_state() -> None:
    engine = PositionEngine()

    engine.apply_event(event("e1", "T1", "NEW", "AAPL", 100, 1), watermark=1)
    engine.apply_event(event("e3", "T1", "AMEND", "AAPL", 140, 3), watermark=2)
    late = engine.apply_event(event("e2", "T1", "CANCEL", "AAPL", 100, 2), watermark=3)

    assert late.stale is True
    assert engine.position_for("AAPL").net_position == 140


def test_duplicate_event_id_is_ignored() -> None:
    engine = PositionEngine()
    trade_event = event("e1", "T1", "NEW", "AAPL", 100, 1)

    applied = engine.apply_event(trade_event, watermark=1)
    duplicate = engine.apply_event(trade_event, watermark=2)

    assert applied.applied is True
    assert duplicate.duplicate is True
    assert duplicate.applied is False
    assert engine.position_for("AAPL").net_position == 100


def test_duplicate_event_id_with_different_payload_is_rejected() -> None:
    engine = PositionEngine()

    engine.apply_event(event("e1", "T1", "NEW", "AAPL", 100, 1), watermark=1)

    with pytest.raises(DuplicateEventConflict):
        engine.apply_event(event("e1", "T1", "NEW", "AAPL", 200, 1), watermark=2)
