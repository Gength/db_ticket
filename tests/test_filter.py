"""Tests for the filtering and scoring engine."""

from datetime import datetime, timedelta

import pytest

from src.config import FilterConfig, TicketClass
from src.filter import filter_and_rank
from src.models import Connection, TicketResult


# ── Helpers ──────────────────────────────────────────────────────────────────

NOW = datetime(2026, 6, 14, 8, 0)


def _conn(
    price: float,
    travel_class: str = "2nd",
    transfers: int = 0,
    transfer_time: int = 0,
    is_direct: bool = True,
    **kwargs,
) -> Connection:
    """Factory for test connections."""
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
        **kwargs,
    )


# ── Price Cap ────────────────────────────────────────────────────────────────

class TestPriceCap:
    def test_within_budget_kept(self):
        cfg = FilterConfig(target_price=50.0, ticket_class=TicketClass.ANY)
        conns = [_conn(30.0), _conn(49.99), _conn(50.01)]
        results = filter_and_rank(conns, cfg)
        assert len(results) == 2
        assert all(r.connection.price <= 50.0 for r in results)

    def test_all_over_budget_returns_empty(self):
        cfg = FilterConfig(target_price=10.0, ticket_class=TicketClass.ANY)
        conns = [_conn(20.0), _conn(30.0)]
        results = filter_and_rank(conns, cfg)
        assert len(results) == 0


# ── Travel Class ─────────────────────────────────────────────────────────────

class TestTravelClass:
    def test_first_class_only(self):
        cfg = FilterConfig(target_price=100.0, ticket_class=TicketClass.FIRST)
        conns = [_conn(50.0, "2nd"), _conn(60.0, "1st"), _conn(40.0, "2nd")]
        results = filter_and_rank(conns, cfg)
        assert len(results) == 1
        assert results[0].connection.travel_class == "1st"

    def test_second_class_only(self):
        cfg = FilterConfig(target_price=100.0, ticket_class=TicketClass.SECOND)
        conns = [_conn(50.0, "2nd"), _conn(60.0, "1st"), _conn(40.0, "2nd")]
        results = filter_and_rank(conns, cfg)
        assert len(results) == 2
        assert all(r.connection.travel_class == "2nd" for r in results)

    def test_any_class_keeps_both(self):
        cfg = FilterConfig(target_price=100.0, ticket_class=TicketClass.ANY)
        conns = [_conn(50.0, "2nd"), _conn(60.0, "1st")]
        results = filter_and_rank(conns, cfg)
        assert len(results) == 2


# ── Direct Only ──────────────────────────────────────────────────────────────

class TestDirectOnly:
    def test_direct_only_discards_transfers(self):
        cfg = FilterConfig(
            target_price=100.0,
            ticket_class=TicketClass.ANY,
            direct_only=True,
        )
        conns = [
            _conn(50.0, "2nd", transfers=0, is_direct=True),
            _conn(40.0, "2nd", transfers=1, transfer_time=20, is_direct=False),
        ]
        results = filter_and_rank(conns, cfg)
        assert len(results) == 1
        assert results[0].connection.is_direct

    def test_non_direct_keeps_transfers(self):
        cfg = FilterConfig(
            target_price=100.0,
            ticket_class=TicketClass.ANY,
            direct_only=False,
        )
        conns = [
            _conn(50.0, "2nd", transfers=0, is_direct=True),
            _conn(40.0, "2nd", transfers=1, transfer_time=20, is_direct=False),
        ]
        results = filter_and_rank(conns, cfg)
        assert len(results) == 2


# ── Transfer Safety ──────────────────────────────────────────────────────────

class TestTransferSafety:
    def test_transfer_time_below_min_excluded(self):
        cfg = FilterConfig(
            target_price=100.0,
            direct_only=False,
            min_transfer_time=15,
            ticket_class=TicketClass.ANY,
        )
        conns = [
            _conn(50.0, transfers=1, transfer_time=10, is_direct=False),
            _conn(60.0, transfers=1, transfer_time=20, is_direct=False),
        ]
        results = filter_and_rank(conns, cfg)
        assert len(results) == 1
        assert results[0].connection.transfer_time == 20

    def test_direct_trains_ignore_transfer_time(self):
        cfg = FilterConfig(
            target_price=100.0,
            direct_only=False,
            min_transfer_time=999,  # absurdly high
            ticket_class=TicketClass.ANY,
        )
        conns = [_conn(50.0, transfers=0, is_direct=True)]
        results = filter_and_rank(conns, cfg)
        assert len(results) == 1  # direct trains always pass


# ── Time window exclusion ────────────────────────────────────────────────────

class TestTimeExclusion:
    def test_excludes_departure_in_window(self):
        cfg = FilterConfig(
            target_price=100.0,
            ticket_class=TicketClass.ANY,
            exclude_departure_start="00:00",
            exclude_departure_end="06:00",
        )
        conns = [
            _conn(50.0, transfers=0, is_direct=True),  # 08:00 departure (default NOW)
        ]
        # Override departure time for testing
        conns[0].departure = conns[0].departure.replace(hour=4, minute=0)
        results = filter_and_rank(conns, cfg)
        assert len(results) == 0  # 04:00 excluded

    def test_keeps_departure_outside_window(self):
        cfg = FilterConfig(
            target_price=100.0,
            ticket_class=TicketClass.ANY,
            exclude_departure_start="00:00",
            exclude_departure_end="06:00",
        )
        conns = [
            _conn(50.0, transfers=0, is_direct=True),
            _conn(40.0, transfers=0, is_direct=True),
        ]
        conns[0].departure = conns[0].departure.replace(hour=4, minute=0)   # excluded
        conns[1].departure = conns[1].departure.replace(hour=8, minute=0)   # kept
        results = filter_and_rank(conns, cfg)
        assert len(results) == 1
        assert results[0].connection.departure.hour == 8

    def test_overnight_window(self):
        cfg = FilterConfig(
            target_price=100.0,
            ticket_class=TicketClass.ANY,
            exclude_departure_start="22:00",
            exclude_departure_end="06:00",
        )
        conns = [
            _conn(50.0, transfers=0, is_direct=True),
            _conn(40.0, transfers=0, is_direct=True),
            _conn(30.0, transfers=0, is_direct=True),
        ]
        conns[0].departure = conns[0].departure.replace(hour=23, minute=0)  # excluded
        conns[1].departure = conns[1].departure.replace(hour=3, minute=0)   # excluded
        conns[2].departure = conns[2].departure.replace(hour=12, minute=0)  # kept
        results = filter_and_rank(conns, cfg)
        assert len(results) == 1
        assert results[0].connection.departure.hour == 12

    def test_same_start_end_skips_exclusion(self):
        cfg = FilterConfig(
            target_price=100.0,
            ticket_class=TicketClass.ANY,
            exclude_departure_start="00:00",
            exclude_departure_end="00:00",
        )
        conns = [_conn(50.0, transfers=0, is_direct=True)]
        conns[0].departure = conns[0].departure.replace(hour=4, minute=0)
        results = filter_and_rank(conns, cfg)
        assert len(results) == 1  # nothing excluded

# ── Scoring ──────────────────────────────────────────────────────────────────

class TestScoring:
    def test_direct_beats_transfer_regardless_of_price(self):
        """Direct trains must rank above transfer even when more expensive."""
        cfg = FilterConfig(target_price=100.0, direct_only=False, ticket_class=TicketClass.ANY)
        conns = [
            _conn(90.0, transfers=0, is_direct=True),    # expensive direct
            _conn(1.0, transfers=1, transfer_time=20, is_direct=False),  # cheap transfer
        ]
        results = filter_and_rank(conns, cfg)
        assert results[0].connection.is_direct
        assert results[0].connection.price == 90.0

    def test_cheaper_same_type_ranks_higher(self):
        """Among same type (both direct or both transfer), cheaper wins."""
        cfg = FilterConfig(target_price=100.0, direct_only=False, ticket_class=TicketClass.ANY)
        conns = [
            _conn(50.0, transfers=0, is_direct=True),
            _conn(30.0, transfers=0, is_direct=True),
        ]
        results = filter_and_rank(conns, cfg)
        assert results[0].connection.price == 30.0
        assert results[1].connection.price == 50.0


# ── ANY Class 10% Rule ───────────────────────────────────────────────────────

class TestAnyClassRule:
    def test_first_class_boosted_when_within_10_percent(self):
        """1st class ticket within 10% of cheapest should rank first."""
        cfg = FilterConfig(target_price=100.0, ticket_class=TicketClass.ANY)
        conns = [
            _conn(50.0, "2nd", transfers=0, is_direct=True),
            _conn(54.0, "1st", transfers=0, is_direct=True),  # 8% more
        ]
        results = filter_and_rank(conns, cfg)
        assert results[0].connection.travel_class == "1st"

    def test_first_class_not_boosted_when_above_10_percent(self):
        """1st class ticket >10% above cheapest stays ranked normally."""
        cfg = FilterConfig(target_price=100.0, ticket_class=TicketClass.ANY)
        conns = [
            _conn(50.0, "2nd", transfers=0, is_direct=True),
            _conn(60.0, "1st", transfers=0, is_direct=True),  # 20% more
        ]
        results = filter_and_rank(conns, cfg)
        # 2nd class at 50 should still be first (cheaper, both direct)
        # Actually, 1st gets a 10pt boost but 2nd has price bonus.
        # 2nd: score = 100 + (100-50)/100 = 100.5
        # 1st: score = 100 + (100-60)/100 + 10 = 110.4
        # Hmm, the 10pt boost might override. Let me check...
        # Actually that's the spec: prioritize 1st if ≤10% delta.
        # 20% > 10%, so 1st should NOT be boosted.
        # But with our current scoring, 1st still gets 10pt... wait no.
        # The boost is only applied if delta ≤ 10%. So for 20% delta, no boost.
        # Let me verify: 2nd at 50.0 has price ratio = 0.5, score = 100 + 0.5 = 100.5
        # 1st at 60.0 has price ratio = 0.4, score = 100 + 0.4 = 100.4 (no boost)
        # So 2nd wins. Good.
        assert results[0].connection.travel_class == "2nd"


# ── Empty input ──────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_list(self):
        cfg = FilterConfig(target_price=50.0, ticket_class=TicketClass.ANY)
        results = filter_and_rank([], cfg)
        assert results == []

    def test_no_matches_after_filtering(self):
        cfg = FilterConfig(target_price=50.0, ticket_class=TicketClass.FIRST)
        conns = [_conn(30.0, "2nd"), _conn(40.0, "2nd")]
        results = filter_and_rank(conns, cfg)
        assert len(results) == 0
