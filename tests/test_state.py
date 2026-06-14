"""Tests for state management and 48h dedup logic."""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.models import Connection, NotificationRecord, TicketResult
from src.state import StateManager


NOW = datetime(2026, 6, 14, 8, 0)


def _ticket(
    uid: str = "abc123",
    price: float = 50.0,
    is_fallback: bool = False,
) -> TicketResult:
    """Create a minimal test ticket with a predictable UID."""
    conn = Connection(
        from_station="A",
        to_station="B",
        departure=NOW,
        arrival=NOW + timedelta(hours=4),
        price=price,
        travel_class="2nd",
        transfers=0,
        transfer_time=0,
        is_direct=True,
        train_types=["ICE"],
        _uid_override=uid,
    )
    return TicketResult(connection=conn, score=90.0, is_fallback=is_fallback)


class TestStateManager:
    """Basic read/write and dedup behavior."""

    def test_empty_history_allows_notification(self, tmp_path):
        db = tmp_path / "test_history.json"
        state = StateManager(db)
        ticket = _ticket("uid-1", 50.0)
        assert state.should_notify(ticket) is True

    def test_same_uid_higher_price_suppressed(self, tmp_path):
        db = tmp_path / "test_history.json"
        state = StateManager(db)

        # First notification at 40 EUR
        rec = NotificationRecord(
            connection_uid="uid-1",
            price=40.0,
            travel_class="2nd",
            notified_at=datetime.now(timezone.utc).isoformat(),
            notification_type="match",
            from_station="A",
            to_station="B",
            departure=NOW.isoformat(),
        )
        state.save(rec)

        # Same connection at 50 EUR (higher price) → suppress
        ticket = _ticket("uid-1", 50.0)
        assert state.should_notify(ticket) is False

    def test_same_uid_lower_price_allowed(self, tmp_path):
        db = tmp_path / "test_history.json"
        state = StateManager(db)

        # First notification at 50 EUR
        rec = NotificationRecord(
            connection_uid="uid-1",
            price=50.0,
            travel_class="2nd",
            notified_at=datetime.now(timezone.utc).isoformat(),
            notification_type="match",
            from_station="A",
            to_station="B",
            departure=NOW.isoformat(),
        )
        state.save(rec)

        # Same connection at 30 EUR (lower price = better deal) → allow
        ticket = _ticket("uid-1", 30.0)
        assert state.should_notify(ticket) is True

    def test_different_uid_allowed(self, tmp_path):
        db = tmp_path / "test_history.json"
        state = StateManager(db)

        rec = NotificationRecord(
            connection_uid="uid-1",
            price=50.0,
            travel_class="2nd",
            notified_at=datetime.now(timezone.utc).isoformat(),
            notification_type="match",
            from_station="A",
            to_station="B",
            departure=NOW.isoformat(),
        )
        state.save(rec)

        # Different connection → allow
        ticket = _ticket("uid-2", 60.0)
        assert state.should_notify(ticket) is True

    def test_different_notification_type_not_suppressed(self, tmp_path):
        """A match notification shouldn't suppress a fallback for the same UID."""
        db = tmp_path / "test_history.json"
        state = StateManager(db)

        rec = NotificationRecord(
            connection_uid="uid-1",
            price=40.0,
            travel_class="2nd",
            notified_at=datetime.now(timezone.utc).isoformat(),
            notification_type="match",
            from_station="A",
            to_station="B",
            departure=NOW.isoformat(),
        )
        state.save(rec)

        # Same UID but fallback type → allow
        ticket = _ticket("uid-1", 50.0, is_fallback=True)
        assert state.should_notify(ticket) is True

    def test_expired_record_allows_notification(self, tmp_path):
        db = tmp_path / "test_history.json"
        state = StateManager(db)

        # Record older than 48h
        old_time = datetime.now(timezone.utc) - timedelta(hours=50)
        rec = NotificationRecord(
            connection_uid="uid-1",
            price=40.0,
            travel_class="2nd",
            notified_at=old_time.isoformat(),
            notification_type="match",
            from_station="A",
            to_station="B",
            departure=NOW.isoformat(),
        )
        state.save(rec)

        ticket = _ticket("uid-1", 50.0)
        assert state.should_notify(ticket) is True

    def test_prune_removes_old_records(self, tmp_path):
        db = tmp_path / "test_history.json"
        state = StateManager(db)

        old = NotificationRecord(
            connection_uid="old",
            price=10.0,
            travel_class="2nd",
            notified_at=(datetime.now(timezone.utc) - timedelta(hours=50)).isoformat(),
            notification_type="match",
            from_station="A",
            to_station="B",
            departure=NOW.isoformat(),
        )
        new = NotificationRecord(
            connection_uid="new",
            price=20.0,
            travel_class="2nd",
            notified_at=datetime.now(timezone.utc).isoformat(),
            notification_type="match",
            from_station="A",
            to_station="B",
            departure=NOW.isoformat(),
        )

        # Write both records directly to bypass save()'s auto-prune
        db.parent.mkdir(parents=True, exist_ok=True)
        with open(db, "w", encoding="utf-8") as f:
            json.dump([old.to_dict(), new.to_dict()], f)

        removed = state.prune()
        assert removed == 1

        # The old record should be gone
        with open(db) as f:
            data = json.load(f)
        uids = [r["connection_uid"] for r in data]
        assert "old" not in uids
        assert "new" in uids


class TestNotificationRecord:
    """Serialization round-trip."""

    def test_to_dict_and_back(self):
        rec = NotificationRecord(
            connection_uid="test-uid",
            price=29.90,
            travel_class="2nd",
            notified_at="2026-06-14T08:00:00+00:00",
            notification_type="match",
            from_station="A",
            to_station="B",
            departure="2026-06-14T08:00:00",
        )
        d = rec.to_dict()
        rec2 = NotificationRecord.from_dict(d)
        assert rec2.connection_uid == "test-uid"
        assert rec2.price == 29.90
        assert rec2.notification_type == "match"

    def test_from_ticket(self):
        conn = Connection(
            from_station="Berlin Hbf",
            to_station="München Hbf",
            departure=NOW,
            arrival=NOW + timedelta(hours=4),
            price=39.90,
            travel_class="2nd",
            transfers=0,
            transfer_time=0,
            is_direct=True,
            train_types=["ICE"],
        )
        ticket = TicketResult(connection=conn, score=100.0, is_fallback=False)
        rec = NotificationRecord.from_ticket(ticket)
        assert rec.connection_uid == conn.uid
        assert rec.price == 39.90
        assert rec.notification_type == "match"
