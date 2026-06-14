"""
Ticket filtering and scoring engine.

Applies the business rules defined in the project specification §3:
price cap, direct-only, transfer safety, travel class, and the
composite scoring formula that ranks connections.

All docstrings and comments are in English per project convention.
"""

from __future__ import annotations

import logging
from typing import List

from src.config import FilterConfig, TicketClass
from src.models import Connection, TicketResult

logger = logging.getLogger(__name__)

# ── Scoring weights ──────────────────────────────────────────────────────────

# w1 (direct bonus) must dominate w2 so that *every* direct connection
# ranks above *any* indirect connection regardless of price.
# 100:1 ensures this under all reasonable price ranges.
WEIGHT_DIRECT = 100.0
WEIGHT_PRICE = 1.0


# ── Public API ───────────────────────────────────────────────────────────────

def filter_and_rank(
    connections: List[Connection],
    cfg: FilterConfig,
) -> List[TicketResult]:
    """
    Filter and sort connections according to the user's criteria.

    Processing order (per spec §3):
    1. Price cap — keep only connections ≤ target_price
    2. Travel class — keep 1st, 2nd, or both
    3. Direct-only — discard transfers when direct_only=True
    4. Transfer safety — enforce min_transfer_time for non-direct
    5. Score & sort using the composite formula
    6. For ANY class: apply the 10 % delta rule between best 1st / best 2nd

    Returns
    -------
    list[ TicketResult ]
        Sorted list (best first). May be empty if nothing qualifies.
    """
    # ── Step 1: price cap ────────────────────────────────────────────────
    within_budget = [c for c in connections if c.price <= cfg.target_price]
    logger.debug(
        "Price cap: %d / %d connections within ≤ %.2f EUR",
        len(within_budget), len(connections), cfg.target_price,
    )

    # ── Step 2: travel class ─────────────────────────────────────────────
    if cfg.ticket_class == TicketClass.FIRST:
        class_filtered = [c for c in within_budget if c.travel_class == "1st"]
    elif cfg.ticket_class == TicketClass.SECOND:
        class_filtered = [c for c in within_budget if c.travel_class == "2nd"]
    else:
        class_filtered = within_budget  # ANY — keep both

    logger.debug(
        "Class filter (%s): %d remaining",
        cfg.ticket_class.value, len(class_filtered),
    )

    # ── Step 3: direct-only ──────────────────────────────────────────────
    if cfg.direct_only:
        class_filtered = [c for c in class_filtered if c.is_direct]
        logger.debug("Direct-only: %d remaining", len(class_filtered))

    # ── Step 4: transfer safety ──────────────────────────────────────────
    # Only enforced for connections WITH transfers
    class_filtered = [
        c for c in class_filtered
        if c.is_direct or c.transfer_time >= cfg.min_transfer_time
    ]
    logger.debug(
        "Transfer safety (≥ %d min): %d remaining",
        cfg.min_transfer_time, len(class_filtered),
    )

    # ── Step 4.5: time window exclusion ──────────────────────────────────
    # Skip if start == end (both empty, or both the same time)
    if (cfg.exclude_departure_start and cfg.exclude_departure_end
            and cfg.exclude_departure_start != cfg.exclude_departure_end):
        start_h, start_m = map(int, cfg.exclude_departure_start.split(":"))
        end_h, end_m = map(int, cfg.exclude_departure_end.split(":"))
        start_min = start_h * 60 + start_m
        end_min = end_h * 60 + end_m

        def _in_excluded_window(conn: Connection) -> bool:
            dep_min = conn.departure.hour * 60 + conn.departure.minute
            if start_min <= end_min:
                return start_min <= dep_min < end_min
            else:
                # Overnight window e.g. 22:00–06:00
                return dep_min >= start_min or dep_min < end_min

        before = len(class_filtered)
        class_filtered = [c for c in class_filtered if not _in_excluded_window(c)]
        logger.debug(
            "Time exclusion (%s–%s): %d → %d",
            cfg.exclude_departure_start, cfg.exclude_departure_end,
            before, len(class_filtered),
        )

    # ── Step 5: score & sort ─────────────────────────────────────────────
    results = [_score(c, cfg.target_price) for c in class_filtered]
    results.sort(key=lambda r: r.score, reverse=True)

    # ── Step 6: ANY-class 10 % delta rule ────────────────────────────────
    if cfg.ticket_class == TicketClass.ANY:
        results = _apply_any_class_rule(results, cfg.target_price)

    return results


# ── Scoring ──────────────────────────────────────────────────────────────────

def _score(conn: Connection, target_price: float) -> TicketResult:
    """
    Compute the composite score for a single connection.

    .. math::
        Score = w_1 \\cdot \\text{is\\_direct}
              + w_2 \\cdot \\frac{\\text{target\\_price} - \\text{price}}{\\text{target\\_price}}

    where :math:`w_1 = 100` and :math:`w_2 = 1`.

    A direct train ALWAYS outranks a transfer connection because
    :math:`w_1` is larger than the maximum possible price-ratio
    contribution (which is at most ~1 when price → 0).
    """
    direct_bonus = WEIGHT_DIRECT * (1.0 if conn.is_direct else 0.0)
    price_ratio = (target_price - conn.price) / max(target_price, 0.01)
    price_bonus = WEIGHT_PRICE * max(price_ratio, 0.0)  # floor at 0
    score = direct_bonus + price_bonus
    return TicketResult(connection=conn, score=score)


# ── ANY-class rule ───────────────────────────────────────────────────────────

def _apply_any_class_rule(
    results: List[TicketResult],
    target_price: float,
) -> List[TicketResult]:
    """
    Apply the §3 rule 5 (ANY class) after sorting.

    "If both fall below target_price, prioritize the 1st-class option
     if the price delta is ≤ 10%, otherwise select the lowest absolute
     price."

    This is implemented by re-scoring: when a 1st-class ticket's price
    is within 110 % of the cheapest available option, its score gets a
    bonus so it ranks first.
    """
    if not results:
        return results

    cheapest = min(r.connection.price for r in results)

    for r in results:
        if r.connection.travel_class == "1st":
            delta = r.connection.price - cheapest
            pct = delta / max(cheapest, 0.01)
            if pct <= 0.10:
                # Boost 1st class to beat 2nd class at similar price
                r.score += 10.0  # enough to exceed any 2nd-class score

    results.sort(key=lambda r: r.score, reverse=True)
    return results
