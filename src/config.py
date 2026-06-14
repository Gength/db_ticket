"""
Configuration loader for DB Weekend Ticket Scanner.

Parses config.toml into typed Pydantic models. Supports relative-date
strings ("today", "today+N") in the search_window section.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from enum import Enum
from pathlib import Path
from typing import List, Optional

import toml
from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enums ────────────────────────────────────────────────────────────────────

class TripType(str, Enum):
    SINGLE = "SINGLE"
    ROUND_TRIP = "ROUND_TRIP"


class BahnCard(str, Enum):
    NONE = "none"
    BAHNCARD_25_2ND = "25_2nd"
    BAHNCARD_50_2ND = "50_2nd"
    BAHNCARD_25_1ST = "25_1st"
    BAHNCARD_50_1ST = "50_1st"


class TicketClass(str, Enum):
    FIRST = "1st"
    SECOND = "2nd"
    ANY = "ANY"


# ── Sub-models ───────────────────────────────────────────────────────────────

class RouteConfig(BaseModel):
    """A single origin–destination pair with trip type."""
    from_station: str
    to_station: str
    trip_type: TripType = TripType.SINGLE


class SearchWindow(BaseModel):
    """Date range for ticket searches."""
    start_date: str = "today"
    end_date: str = "today+30"

    @staticmethod
    def _resolve_date(raw: str, ref: date) -> date:
        """Convert a string date spec into a concrete date."""
        raw = raw.strip().lower()
        if raw == "today":
            return ref
        if raw.startswith("today+") or raw.startswith("today-"):
            sign = 1 if "+" in raw else -1
            offset_str = raw.split("+")[-1].split("-")[-1].strip()
            try:
                offset = int(offset_str)
            except ValueError:
                raise ValueError(
                    f"Invalid date offset '{raw}'. Expected 'today+N' or 'today-N'."
                )
            return ref + timedelta(days=sign * offset)
        # Try ISO format
        try:
            return date.fromisoformat(raw)
        except ValueError:
            raise ValueError(
                f"Unrecognised date string '{raw}'. "
                f"Use 'today', 'today+N', or 'YYYY-MM-DD'."
            )

    def resolved_start(self, ref: Optional[date] = None) -> date:
        ref = ref or date.today()
        return self._resolve_date(self.start_date, ref)

    def resolved_end(self, ref: Optional[date] = None) -> date:
        ref = ref or date.today()
        return self._resolve_date(self.end_date, ref)


class PassengerConfig(BaseModel):
    """Traveller details affecting DB ticket pricing."""
    age: int = Field(ge=0, le=150)
    bahncard: BahnCard = BahnCard.NONE


class FilterConfig(BaseModel):
    """Criteria for post-scrape filtering and scoring."""
    target_price: float = Field(gt=0)
    ticket_class: TicketClass = TicketClass.ANY
    direct_only: bool = False
    max_transfers: int = Field(default=2, ge=0)
    min_transfer_time: int = Field(default=15, ge=0)
    exclude_departure_start: str = ""  # "HH:MM" — exclude departures from this time
    exclude_departure_end: str = ""    # "HH:MM" — exclude departures until this time

    @model_validator(mode="after")
    def _clamp_transfers(self) -> "FilterConfig":
        if self.direct_only:
            self.max_transfers = 0
        return self


class SMTPConfig(BaseModel):
    """SMTP credentials read from environment (or .env)."""
    host: str = "smtp.qq.com"
    port: int = 465
    to_email: str = ""

    def user(self) -> Optional[str]:
        val = os.environ.get("SMTP_USER")
        if val:
            return val
        return os.environ.get("SMTP_USER_FALLBACK")

    def password(self) -> Optional[str]:
        return os.environ.get("SMTP_PASS")


# ── Top-level config ─────────────────────────────────────────────────────────

class AppConfig(BaseModel):
    routes: List[RouteConfig]
    search_window: SearchWindow = SearchWindow()
    passenger: PassengerConfig
    filters: FilterConfig
    smtp: SMTPConfig = SMTPConfig()


# ── Loader ───────────────────────────────────────────────────────────────────

def load_config(path: str | Path = "config.toml") -> AppConfig:
    """Parse and validate a config.toml file into an AppConfig instance."""
    # Load .env if present — sets SMTP_USER, SMTP_PASS etc.
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    raw = toml.load(path)

    # TOML sub-tables: [[routes.entries]] → raw["routes"]["entries"]
    raw.setdefault("routes", {})
    raw["routes"] = raw.get("routes", {}).get("entries", raw.get("routes", []))

    return AppConfig(**raw)
