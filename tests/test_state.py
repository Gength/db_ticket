"""Tests for state management — price history recording and lookup."""

import json
from datetime import datetime, timedelta, timezone

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


class TestLastPrice:
    """Price history lookup."""

    def test_no_history_returns_none(self, tmp_path):
        db = tmp_path / "test_history.json"
        state = StateManager(db)
        assert state.last_price("uid-1") is None

    def test_returns_most_recent_price(self, tmp_path):
        db = tmp_path / "test_history.json"
        state = StateManager(db)

        r1 = NotificationRecord(
            connection_uid="uid-1",
            price=50.0,
            travel_class="2nd",
            notified_at="2026-06-13T08:00:00+00:00",
            notification_type="match",
            from_station="A",
            to_station="B",
            departure=NOW.isoformat(),
        )
        r2 = NotificationRecord(
            connection_uid="uid-1",
            price=40.0,
            travel_class="2nd",
            notified_at="2026-06-14T08:00:00+00:00",
            notification_type="match",
            from_station="A",
            to_station="B",
            departure=NOW.isoformat(),
        )
        state.save(r1)
        state.save(r2)

        assert state.last_price("uid-1") == 40.0

    def test_different_uid_returns_none(self, tmp_path):
        db = tmp_path / "test_history.json"
        state = StateManager(db)

        r = NotificationRecord(
            connection_uid="uid-1",
            price=50.0,
            travel_class="2nd",
            notified_at="2026-06-13T08:00:00+00:00",
            notification_type="match",
            from_station="A",
            to_station="B",
            departure=NOW.isoformat(),
        )
        state.save(r)

        assert state.last_price("uid-2") is None

    def test_type_filter_respected(self, tmp_path):
        """match and fallback lookups don't cross-contaminate."""
        db = tmp_path / "test_history.json"
        state = StateManager(db)

        r = NotificationRecord(
            connection_uid="uid-1",
            price=50.0,
            travel_class="2nd",
            notified_at="2026-06-13T08:00:00+00:00",
            notification_type="match",
            from_station="A",
            to_station="B",
            departure=NOW.isoformat(),
        )
        state.save(r)

        assert state.last_price("uid-1", "match") == 50.0
        assert state.last_price("uid-1", "fallback") is None


class TestSaveAndPrune:
    """Record persistence and cleanup."""

    def test_save_persists_record(self, tmp_path):
        db = tmp_path / "test_history.json"
        state = StateManager(db)

        ticket = _ticket("uid-1", 50.0)
        rec = NotificationRecord.from_ticket(ticket)
        state.save(rec)

        assert state.last_price("uid-1") == 50.0

    def test_prune_removes_old_records(self, tmp_path):
        db = tmp_path / "test_history.json"
        state = StateManager(db)

        old = NotificationRecord(
            connection_uid="old",
            price=10.0,
            travel_class="2nd",
            notified_at=(datetime.now(timezone.utc) - timedelta(days=31)).isoformat(),
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

        db.parent.mkdir(parents=True, exist_ok=True)
        with open(db, "w", encoding="utf-8") as f:
            json.dump([old.to_dict(), new.to_dict()], f)

        removed = state.prune()
        assert removed == 1

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
