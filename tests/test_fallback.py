"""Tests for the fallback recommendation engine."""

from datetime import datetime, timedelta

import pytest

from src.config import FilterConfig, TicketClass
from src.fallback import compute_fallbacks
from src.models import Connection


NOW = datetime(2026, 6, 14, 8, 0)


def _conn(
    price: float,
    travel_class: str = "2nd",
    transfers: int = 0,
    transfer_time: int = 0,
    is_direct: bool = True,
) -> Connection:
    return Connection(
        from_station="Berlin Hbf",
        to_station="München Hbf",
        departure=NOW,
        arrival=NOW + timedelta(hours=4),
        price=price,
        travel_class=travel_class,
        transfers=transfers,
        transfer_time=transfer_time,
        is_direct=is_direct,
        train_types=["ICE"],
    )


class TestRecommendationA:
    """Cheapest over-budget (Recommendation A)."""

    def test_finds_cheapest_overall(self):
        cfg = FilterConfig(target_price=50.0, ticket_class=TicketClass.ANY)
        conns = [
            _conn(100.0),  # over budget
            _conn(80.0),   # over budget but cheaper
            _conn(120.0),
        ]
        rec_a, rec_b = compute_fallbacks(conns, cfg)
        assert rec_a is not None
        assert rec_a.connection.price == 80.0
        assert rec_a.is_fallback
        assert rec_a.fallback_type == "A"

    def test_respects_class_filter(self):
        cfg = FilterConfig(target_price=50.0, ticket_class=TicketClass.FIRST)
        conns = [
            _conn(40.0, "2nd"),  # wrong class
            _conn(90.0, "1st"),  # right class, over budget
        ]
        rec_a, _ = compute_fallbacks(conns, cfg)
        assert rec_a is not None
        assert rec_a.connection.travel_class == "1st"
        assert rec_a.connection.price == 90.0

    def test_respects_transfer_safety(self):
        cfg = FilterConfig(
            target_price=50.0,
            ticket_class=TicketClass.ANY,
            min_transfer_time=15,
        )
        conns = [
            _conn(80.0, transfers=1, transfer_time=5, is_direct=False),   # unsafe
            _conn(100.0, transfers=1, transfer_time=20, is_direct=False),  # safe
        ]
        rec_a, _ = compute_fallbacks(conns, cfg)
        assert rec_a is not None
        assert rec_a.connection.price == 100.0

    def test_empty_input_returns_none(self):
        cfg = FilterConfig(target_price=50.0, ticket_class=TicketClass.ANY)
        rec_a, rec_b = compute_fallbacks([], cfg)
        assert rec_a is None
        assert rec_b is None


class TestRecommendationB:
    """Relaxed direct-only (Recommendation B)."""

    def test_returns_none_when_direct_only_is_false(self):
        cfg = FilterConfig(
            target_price=50.0,
            ticket_class=TicketClass.ANY,
            direct_only=False,
        )
        conns = [_conn(80.0, transfers=1, transfer_time=20, is_direct=False)]
        _, rec_b = compute_fallbacks(conns, cfg)
        assert rec_b is None  # No relaxation needed

    def test_finds_cheapest_transfer_when_direct_only_was_true(self):
        cfg = FilterConfig(
            target_price=50.0,
            ticket_class=TicketClass.ANY,
            direct_only=True,
            min_transfer_time=10,
        )
        conns = [
            _conn(100.0, transfers=1, transfer_time=20, is_direct=False),
            _conn(90.0, transfers=2, transfer_time=15, is_direct=False),
        ]
        _, rec_b = compute_fallbacks(conns, cfg)
        assert rec_b is not None
        assert rec_b.connection.price == 90.0
        assert rec_b.is_fallback
        assert rec_b.fallback_type == "B"

    def test_excludes_direct_trains_from_recommendation_b(self):
        """B only returns transfer connections."""
        cfg = FilterConfig(
            target_price=50.0,
            ticket_class=TicketClass.ANY,
            direct_only=True,
        )
        conns = [
            _conn(60.0, transfers=0, is_direct=True),    # direct, excluded
            _conn(90.0, transfers=1, transfer_time=20, is_direct=False),
        ]
        _, rec_b = compute_fallbacks(conns, cfg)
        assert rec_b is not None
        assert not rec_b.connection.is_direct
        assert rec_b.connection.price == 90.0

    def test_respects_min_transfer_time(self):
        cfg = FilterConfig(
            target_price=50.0,
            ticket_class=TicketClass.ANY,
            direct_only=True,
            min_transfer_time=15,
        )
        conns = [
            _conn(80.0, transfers=1, transfer_time=10, is_direct=False),  # too tight
            _conn(100.0, transfers=1, transfer_time=20, is_direct=False),  # ok
        ]
        _, rec_b = compute_fallbacks(conns, cfg)
        assert rec_b is not None
        assert rec_b.connection.price == 100.0
        assert rec_b.connection.transfer_time >= 15

    def test_no_transfer_connections_returns_none(self):
        cfg = FilterConfig(
            target_price=50.0,
            ticket_class=TicketClass.ANY,
            direct_only=True,
        )
        conns = [_conn(80.0, transfers=0, is_direct=True)]
        _, rec_b = compute_fallbacks(conns, cfg)
        assert rec_b is None
