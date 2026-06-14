"""
Fallback recommendation engine.

When zero connections satisfy the user's price cap, this module
computes two "Additional Recommendations" (保底建议) per the
project specification §3 rule 6:

* **Recommendation A** — Cheapest Over-Budget:
  The absolute lowest-price connection across the entire search
  window, regardless of the target_price threshold.

* **Recommendation B** — Best Alternative Route:
  When ``direct_only`` was set, relax this restriction and find
  the cheapest transfer connection (≥ 1 transfer) that still
  respects the minimum transfer time.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from src.config import FilterConfig, TicketClass
from src.models import Connection, TicketResult

logger = logging.getLogger(__name__)


def compute_fallbacks(
    connections: List[Connection],
    cfg: FilterConfig,
) -> tuple[Optional[TicketResult], Optional[TicketResult]]:
    """
    Compute Recommendations A and B from the full connection pool.

    Parameters
    ----------
    connections:
        ALL scraped connections (before any filtering).
    cfg:
        The user's filter configuration.

    Returns
    -------
    (rec_a, rec_b):
        Two optional TicketResult objects. ``is_fallback`` is set to
        True on both, and ``fallback_type`` is "A" / "B" respectively.
        Either may be None if no suitable connection exists at all.
    """
    if not connections:
        logger.info("No connections at all — fallbacks impossible")
        return None, None

    # ── Recommendation A: cheapest over-budget ───────────────────────────
    rec_a = _cheapest_overall(connections, cfg)

    # ── Recommendation B: relax direct-only ──────────────────────────────
    rec_b = _relax_direct(connections, cfg)

    return rec_a, rec_b


# ── Recommendation A ─────────────────────────────────────────────────────────

def _cheapest_overall(
    connections: List[Connection],
    cfg: FilterConfig,
) -> Optional[TicketResult]:
    """
    Find the single cheapest connection, ignoring the target_price cap
    but still respecting class and transfer-safety constraints.
    """
    # Apply class filter
    pool = _apply_class_filter(connections, cfg.ticket_class)

    # Apply transfer safety (but NOT direct_only)
    pool = [
        c for c in pool
        if c.is_direct or c.transfer_time >= cfg.min_transfer_time
    ]

    if not pool:
        logger.info("Recommendation A: no connections after class/safety filter")
        return None

    cheapest = min(pool, key=lambda c: c.price)
    result = TicketResult(
        connection=cheapest,
        score=0.0,
        is_fallback=True,
        fallback_type="A",
    )
    logger.info(
        "Recommendation A: %.2f EUR (%s → %s, %s transfers)",
        cheapest.price, cheapest.from_station, cheapest.to_station,
        "direct" if cheapest.is_direct else f"{cheapest.transfers}",
    )
    return result


# ── Recommendation B ─────────────────────────────────────────────────────────

def _relax_direct(
    connections: List[Connection],
    cfg: FilterConfig,
) -> Optional[TicketResult]:
    """
    If direct_only was True, find the cheapest transfer connection
    (≥ 1 transfer) that satisfies min_transfer_time and class filter.

    If direct_only was False, there is no relaxation to apply —
    return None (Recommendation B is only relevant when direct_only
    restricted the results).
    """
    if not cfg.direct_only:
        return None  # No relaxation needed

    # Apply class filter
    pool = _apply_class_filter(connections, cfg.ticket_class)

    # Keep only connections WITH transfers AND sufficient transfer time
    pool = [
        c for c in pool
        if not c.is_direct and c.transfer_time >= cfg.min_transfer_time
    ]

    if not pool:
        logger.info("Recommendation B: no transfer connections available")
        return None

    cheapest = min(pool, key=lambda c: c.price)
    result = TicketResult(
        connection=cheapest,
        score=0.0,
        is_fallback=True,
        fallback_type="B",
    )
    logger.info(
        "Recommendation B: %.2f EUR (%s → %s, %d transfers, %d min transfer)",
        cheapest.price, cheapest.from_station, cheapest.to_station,
        cheapest.transfers, cheapest.transfer_time,
    )
    return result


# ── Helpers ──────────────────────────────────────────────────────────────────

def _apply_class_filter(
    connections: List[Connection],
    ticket_class: TicketClass,
) -> List[Connection]:
    """Keep only connections matching the requested travel class."""
    if ticket_class == TicketClass.FIRST:
        return [c for c in connections if c.travel_class == "1st"]
    elif ticket_class == TicketClass.SECOND:
        return [c for c in connections if c.travel_class == "2nd"]
    return connections  # ANY
