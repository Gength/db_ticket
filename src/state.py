"""
State management for DB Weekend Ticket Scanner.

Maintains a ``history.json`` file recording all sent notifications.
Used to display price changes (↑ / ↓) in emails — no dedup logic.
Scan frequency is controlled by cron.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from src.models import NotificationRecord


class StateManager:
    """
    Thread-safe JSON-backed notification history.

    Records every sent notification so the next scan can compare
    prices and show the change (cheaper / more expensive / same).
    Old records (>30 days) are pruned on save to keep the file small.
    """

    PRUNE_DAYS = 30

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
        """Append a single notification record and prune old entries."""
        with self._lock:
            records = self._read()
            records.append(record)
            cutoff = datetime.now(timezone.utc) - timedelta(days=self.PRUNE_DAYS)
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

    # ── Price history lookup ─────────────────────────────────────────────

    def last_price(self, uid: str, notification_type: str = "match") -> Optional[float]:
        """
        Return the most recent notified price for a connection UID,
        or None if never notified before.
        """
        best: Optional[float] = None
        best_time: Optional[datetime] = None

        with self._lock:
            records = self._read()

        for rec in records:
            if rec.connection_uid != uid:
                continue
            if rec.notification_type != notification_type:
                continue
            try:
                notified_at = datetime.fromisoformat(rec.notified_at)
            except ValueError:
                continue
            if best_time is None or notified_at > best_time:
                best = rec.price
                best_time = notified_at

        return best

    # ── Housekeeping ─────────────────────────────────────────────────────

    def prune(self) -> int:
        """Remove records older than 30 days. Returns number removed."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.PRUNE_DAYS)
        with self._lock:
            before = self._read()
            after = [
                r for r in before
                if datetime.fromisoformat(r.notified_at) > cutoff
            ]
            if len(after) < len(before):
                self._write(after)
            return len(before) - len(after)
