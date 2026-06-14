"""Tests for configuration loading and validation."""

import os
import tempfile
from datetime import date
from pathlib import Path

import pytest

from src.config import (
    AppConfig,
    BahnCard,
    FilterConfig,
    PassengerConfig,
    RouteConfig,
    SearchWindow,
    TicketClass,
    TripType,
    load_config,
)


class TestSearchWindow:
    """Date resolution logic."""

    def test_today_resolves_to_ref_date(self):
        sw = SearchWindow(start_date="today", end_date="today+7")
        ref = date(2026, 6, 14)
        assert sw.resolved_start(ref) == date(2026, 6, 14)
        assert sw.resolved_end(ref) == date(2026, 6, 21)

    def test_today_plus_n(self):
        sw = SearchWindow(start_date="today+3", end_date="today+10")
        ref = date(2026, 1, 1)
        assert sw.resolved_start(ref) == date(2026, 1, 4)
        assert sw.resolved_end(ref) == date(2026, 1, 11)

    def test_today_minus_n(self):
        sw = SearchWindow(start_date="today-5", end_date="today-1")
        ref = date(2026, 6, 10)
        assert sw.resolved_start(ref) == date(2026, 6, 5)
        assert sw.resolved_end(ref) == date(2026, 6, 9)

    def test_iso_format(self):
        sw = SearchWindow(start_date="2026-03-01", end_date="2026-03-15")
        assert sw.resolved_start() == date(2026, 3, 1)
        assert sw.resolved_end() == date(2026, 3, 15)

    def test_invalid_offset_raises(self):
        sw = SearchWindow(start_date="today+abc", end_date="today+7")
        with pytest.raises(ValueError):
            sw.resolved_start()

    def test_invalid_date_raises(self):
        sw = SearchWindow(start_date="not-a-date", end_date="today+7")
        with pytest.raises(ValueError):
            sw.resolved_start()


class TestPassengerConfig:
    """Passenger validation."""

    def test_valid_age(self):
        p = PassengerConfig(age=28, bahncard=BahnCard.NONE)
        assert p.age == 28

    def test_negative_age_rejected(self):
        with pytest.raises(Exception):
            PassengerConfig(age=-1)

    def test_bahncard_values(self):
        for bc in BahnCard:
            p = PassengerConfig(age=30, bahncard=bc)
            assert p.bahncard == bc


class TestFilterConfig:
    """Filter validation and clamping."""

    def test_direct_only_clamps_max_transfers(self):
        cfg = FilterConfig(
            target_price=50.0,
            direct_only=True,
            max_transfers=3,
        )
        assert cfg.max_transfers == 0

    def test_non_direct_keeps_max_transfers(self):
        cfg = FilterConfig(
            target_price=50.0,
            direct_only=False,
            max_transfers=2,
        )
        assert cfg.max_transfers == 2

    def test_target_price_must_be_positive(self):
        with pytest.raises(Exception):
            FilterConfig(target_price=0)


class TestLoadConfig:
    """Config file loading."""

    def test_loads_valid_config(self):
        # Use the project's config.toml
        cfg = load_config(
            Path(__file__).parent.parent / "config.toml"
        )
        assert isinstance(cfg, AppConfig)
        assert len(cfg.routes) >= 1
        assert cfg.routes[0].from_station == "Berlin Hbf"
        assert cfg.filters.target_price > 0
        assert cfg.passenger.age == 28

    def test_trip_type_enum(self):
        cfg = load_config(
            Path(__file__).parent.parent / "config.toml"
        )
        assert len(cfg.routes) >= 1
        assert cfg.routes[0].trip_type == TripType.SINGLE
