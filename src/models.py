"""
Domain models for DB Weekend Ticket Scanner.

Defines the core data structures used throughout the pipeline:
scraped connections, filtered ticket results, and notification records
for the 48-hour dedup cache.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# ── Connection (raw scraped data) ────────────────────────────────────────────

@dataclass
class Connection:
    """
    A single train connection as scraped from the DB booking site.

    All monetary values are in EUR.
    """

    from_station: str
    to_station: str
    departure: datetime
    arrival: datetime
    price: float                # in EUR
    travel_class: str           # "1st" or "2nd"
    transfers: int              # number of train changes
    transfer_time: int          # minimum transfer time in minutes (0 if direct)
    is_direct: bool             # True when transfers == 0
    train_types: list[str] = field(default_factory=list)  # e.g. ["ICE", "RE"]
    link: str = ""              # deep-link to the booking page
    _uid_override: Optional[str] = field(default=None, repr=False)

    @property
    def duration_minutes(self) -> int:
        """Total journey duration in minutes."""
        delta = self.arrival - self.departure
        return int(delta.total_seconds() // 60)

    @property
    def uid(self) -> str:
        """
        Stable identifier for deduplication.

        Two connections are considered the same when they share the
        origin, destination, departure time, and train types.

        A ``_uid_override`` can be set for deterministic testing.
        """
        if self._uid_override is not None:
            return self._uid_override
        key = (
            f"{self.from_station}|{self.to_station}|"
            f"{self.departure.isoformat()}|{'|'.join(self.train_types)}"
        )
        return hashlib.sha256(key.encode()).hexdigest()[:16]


# ── TicketResult (post-filter) ───────────────────────────────────────────────

@dataclass
class TicketResult:
    """
    A connection that passed all filter criteria, annotated with
    scoring metadata for sorting.
    """

    connection: Connection
    score: float = 0.0          # computed sorting score (higher = better)
    is_fallback: bool = False   # True when this is a fallback recommendation
    fallback_type: str = ""     # "A" or "B" when is_fallback is True

    def __post_init__(self) -> None:
        # Ensure naive datetimes are handled consistently
        pass


# ── NotificationRecord (history cache entry) ─────────────────────────────────

@dataclass
class NotificationRecord:
    """
    A record of a sent notification, persisted in history.json for
    the 48-hour anti-spam window.
    """

    connection_uid: str
    price: float
    travel_class: str
    notified_at: str            # ISO-8601 timestamp
    notification_type: str      # "match" or "fallback"
    from_station: str
    to_station: str
    departure: str              # ISO-8601

    @staticmethod
    def from_ticket(ticket: TicketResult) -> "NotificationRecord":
        """Create a record from a ticket result."""
        conn = ticket.connection
        ntype = "fallback" if ticket.is_fallback else "match"
        return NotificationRecord(
            connection_uid=conn.uid,
            price=conn.price,
            travel_class=conn.travel_class,
            notified_at=datetime.now(timezone.utc).isoformat(),
            notification_type=ntype,
            from_station=conn.from_station,
            to_station=conn.to_station,
            departure=conn.departure.isoformat(),
        )

    def to_dict(self) -> dict:
        return {
            "connection_uid": self.connection_uid,
            "price": self.price,
            "travel_class": self.travel_class,
            "notified_at": self.notified_at,
            "notification_type": self.notification_type,
            "from_station": self.from_station,
            "to_station": self.to_station,
            "departure": self.departure,
        }

    @staticmethod
    def from_dict(d: dict) -> "NotificationRecord":
        return NotificationRecord(**d)
