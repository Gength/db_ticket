"""
State management for DB Weekend Ticket Scanner.

Maintains a ``history.json`` file tracking all sent notifications.
Implements the 48-hour anti-spam rule: a notification is suppressed
when the same connection was already notified at the same or lower
price within the last 48 hours.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

from src.models import NotificationRecord, TicketResult


class StateManager:
    """
    Thread-safe JSON-backed notification history.

    Rule (from spec §4.2):
        Do not send an alert if the exact same train connection at the
        same or **higher** price was successfully notified within the
        last 48 hours.

    Interpretation: if we already told the user about a ticket at price
    P, we should skip a ticket for the same connection at price ≥ P
    within 48 h. A *lower* price (better deal) is always worth notifying.
    """

    DEDUP_HOURS = 48

    def __init__(self, path: str | Path = "history.json") -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    # ── Read / write ─────────────────────────────────────────────────────

    def _read(self) -> List[NotificationRecord]:
        """Load all records from the JSON file."""
        if not self._path.exists():
            return []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
        return [NotificationRecord.from_dict(r) for r in raw]

    def save(self, record: NotificationRecord) -> None:
        """Append a single notification record to the history file."""
        with self._lock:
            records = self._read()
            records.append(record)
            # Prune records older than 48h to keep file small
            cutoff = datetime.now(timezone.utc) - timedelta(hours=self.DEDUP_HOURS)
            records = [
                r for r in records
                if datetime.fromisoformat(r.notified_at) > cutoff
            ]
            self._write(records)

    def _write(self, records: List[NotificationRecord]) -> None:
        """Overwrite the history file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(
                [r.to_dict() for r in records],
                f,
                ensure_ascii=False,
                indent=2,
            )

    # ── Dedup logic ──────────────────────────────────────────────────────

    def should_notify(self, ticket: TicketResult) -> bool:
        """
        Return True when the ticket is worth notifying under the
        48-hour rule.

        A notification is suppressed when the same connection UID was
        already notified within 48 h at a price ≤ the current ticket's
        price AND the notification type matches (match vs fallback).
        """
        conn = ticket.connection
        current_type = "fallback" if ticket.is_fallback else "match"
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.DEDUP_HOURS)

        with self._lock:
            records = self._read()

        for rec in records:
            if rec.connection_uid != conn.uid:
                continue
            if rec.notification_type != current_type:
                continue
            try:
                notified_at = datetime.fromisoformat(rec.notified_at)
            except ValueError:
                continue
            if notified_at < cutoff:
                continue
            # Already notified same connection at lower-or-equal price → skip
            if rec.price <= conn.price:
                return False

        return True

    # ── Housekeeping ─────────────────────────────────────────────────────

    def prune(self) -> int:
        """Remove records older than 48 h. Returns number removed."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.DEDUP_HOURS)
        with self._lock:
            before = self._read()
            after = [
                r for r in before
                if datetime.fromisoformat(r.notified_at) > cutoff
            ]
            if len(after) < len(before):
                self._write(after)
            return len(before) - len(after)
