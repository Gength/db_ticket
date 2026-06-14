"""
CLI entry point for DB Weekend Ticket Scanner.

Orchestrates the full pipeline:
  config → scrape → filter → fallback → dedup → notify

Supports two subcommands:
  * ``scan`` — run the full ticket search (default for cron).
  * ``test-email`` — send exactly one test email to verify SMTP config.

Environment variables:
  * ``DEBUG_VISUAL`` — set to "true" to launch a visible browser window.
  * ``SMTP_USER`` / ``SMTP_PASS`` — QQ Mail credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date, timedelta
from typing import List

from src.config import AppConfig, FilterConfig, RouteConfig, TicketClass, load_config
from src.filter import filter_and_rank
from src.fallback import compute_fallbacks
from src.models import Connection, NotificationRecord, TicketResult
from src.notifier import send_fallback_notification, send_match_notification
from src.scraper import DBScraper
from src.state import StateManager

logger = logging.getLogger("db_scanner")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    """Parse args and dispatch to the appropriate subcommand."""
    parser = argparse.ArgumentParser(
        prog="db-scanner",
        description="Deutsche Bahn Weekend Sparpreis Ticket Scanner",
    )
    sub = parser.add_subparsers(dest="command", help="Subcommand")

    # --- scan ---
    scan_parser = sub.add_parser("scan", help="Run the full ticket search pipeline")
    scan_parser.add_argument(
        "-c", "--config",
        default="config.toml",
        help="Path to config file (default: config.toml)",
    )
    scan_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log emails instead of sending them",
    )
    scan_parser.add_argument(
        "--limit-dates",
        type=int,
        default=0,
        help="Only search the first N dates (0 = all)",
    )
    scan_parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug-level logging",
    )
    scan_parser.add_argument(
        "--screenshots",
        action="store_true",
        help="Save per-step screenshots to /tmp/db_scanner_screenshots/",
    )
    scan_parser.add_argument(
        "--interactive",
        action="store_true",
        help="Pause at each step (press Enter to continue) — needs DEBUG_VISUAL=true",
    )

    # --- test-email ---
    test_parser = sub.add_parser("test-email", help="Send a single test email")
    test_parser.add_argument(
        "-c", "--config",
        default="config.toml",
        help="Path to config file (default: config.toml)",
    )

    args = parser.parse_args()

    # Set up logging
    level = logging.DEBUG if getattr(args, "verbose", False) else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.command == "test-email":
        asyncio.run(_cmd_test_email(args))
    elif args.command == "scan":
        if getattr(args, "screenshots", False):
            os.environ["DB_SCREENSHOTS"] = "1"
        if getattr(args, "interactive", False):
            os.environ["DB_INTERACTIVE"] = "1"
        asyncio.run(_cmd_scan(args))
    else:
        parser.print_help()


# ── scan subcommand ──────────────────────────────────────────────────────────

async def _cmd_scan(args) -> None:
    """Run the full scanning pipeline."""
    cfg = load_config(args.config)
    state = StateManager("history.json")
    scraper = DBScraper()

    logger.info("=" * 60)
    logger.info("DB Weekend Ticket Scanner — starting scan")
    logger.info("Routes: %d | Date window: %s → %s",
                len(cfg.routes),
                cfg.search_window.resolved_start(),
                cfg.search_window.resolved_end())
    logger.info("Filters: ≤ %.2f EUR | class=%s | direct_only=%s",
                cfg.filters.target_price,
                cfg.filters.ticket_class.value,
                cfg.filters.direct_only)
    logger.info("=" * 60)

    all_matches: list[TicketResult] = []
    all_fb_a: list[TicketResult] = []
    all_fb_b: list[TicketResult] = []

    for route in cfg.routes:
        logger.info("── Route: %s → %s (%s) ──",
                    route.from_station, route.to_station, route.trip_type.value)

        dates = _date_range(cfg, args.limit_dates)

        for day in dates:
            logger.info("Searching: %s", day.strftime("%Y-%m-%d"))

            try:
                connections = await scraper.search(
                    route, day, cfg.passenger, cfg.filters.ticket_class,
                )
            except Exception as exc:
                logger.error("Scrape failed for %s on %s: %s",
                             route.from_station, day, exc)
                continue

            if not connections:
                logger.info("No connections for %s on %s",
                            route.from_station, day)
                continue

            matches = filter_and_rank(connections, cfg.filters)

            if matches:
                to_notify = _dedup(state, matches, cfg.filters)
                all_matches.extend(to_notify)
                if to_notify:
                    logger.info("  %d match(es) on %s", len(to_notify), day)
                else:
                    logger.info("  %d match(es) but suppressed by dedup", len(matches))
            else:
                rec_a, rec_b = compute_fallbacks(connections, cfg.filters)
                if rec_a and state.should_notify(rec_a):
                    all_fb_a.append(rec_a)
                if rec_b and state.should_notify(rec_b):
                    all_fb_b.append(rec_b)
                logger.info("  No matches on %s — fallback: A=%s B=%s",
                            day,
                            f"{rec_a.connection.price}€" if rec_a else "—",
                            f"{rec_b.connection.price}€" if rec_b else "—")

    # ── Send one consolidated email ──────────────────────────────────────
    if all_matches:
        logger.info("Sending match notification for %d ticket(s)", len(all_matches))
        success = send_match_notification(
            all_matches, cfg.smtp, cfg.filters.target_price,
            dry_run=args.dry_run,
        )
        if success:
            _record_notifications(state, all_matches)
            logger.info("✓ Match email sent")
        else:
            logger.error("Failed to send match email")
    elif all_fb_a or all_fb_b:
        logger.info("Sending fallback notification")
        rec_a = all_fb_a[0] if all_fb_a else None
        rec_b = all_fb_b[0] if all_fb_b else None
        success = send_fallback_notification(
            rec_a, rec_b, cfg.smtp, cfg.filters.target_price,
            dry_run=args.dry_run,
        )
        if success:
            if rec_a:
                _record_notifications(state, [rec_a])
            if rec_b:
                _record_notifications(state, [rec_b])
            logger.info("✓ Fallback email sent")
        else:
            logger.error("Failed to send fallback email")
    else:
        logger.info("No matches and no fallback recommendations — no email sent")

    logger.info("=" * 60)
    logger.info("Scan complete — %d match(es), %d fallback(s)",
                len(all_matches), len(all_fb_a) + len(all_fb_b))
    logger.info("=" * 60)


# ── test-email subcommand ────────────────────────────────────────────────────

async def _cmd_test_email(args) -> None:
    """Send exactly one test email to verify SMTP configuration."""
    cfg = load_config(args.config)

    logger.info("Testing SMTP configuration…")
    logger.info("  Host: %s:%d", cfg.smtp.host, cfg.smtp.port)
    logger.info("  User env: %s", cfg.smtp.user_env)

    user = cfg.smtp.user()
    if not user:
        logger.error("SMTP_USER environment variable not set — aborting test")
        sys.exit(1)
    logger.info("  User: %s", user)

    to_email = cfg.smtp.to_email
    if not to_email:
        logger.error("smtp.to_email is empty in config — aborting test")
        sys.exit(1)
    logger.info("  To: %s", to_email)

    # Build a dummy TicketResult for the test email
    from src.models import Connection

    fake_conn = Connection(
        from_station="Test Station A",
        to_station="Test Station B",
        departure=date.today(),
        arrival=date.today(),
        price=29.90,
        travel_class="2nd",
        transfers=0,
        transfer_time=0,
        is_direct=True,
        train_types=["ICE"],
    )
    fake_ticket = TicketResult(connection=fake_conn, score=100.0)

    success = send_match_notification(
        [fake_ticket], cfg.smtp, 49.90, dry_run=False,
    )

    if success:
        logger.info("✓ Test email sent successfully! Check your inbox.")
    else:
        logger.error("✗ Test email failed. Check credentials and SMTP settings.")
        sys.exit(1)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _date_range(cfg: AppConfig, limit: int = 0) -> List[date]:
    """Generate the list of dates to search."""
    start = cfg.search_window.resolved_start()
    end = cfg.search_window.resolved_end()
    if end < start:
        logger.warning("End date before start date — swapping")
        start, end = end, start

    days: List[date] = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
        if limit > 0 and len(days) >= limit:
            break
    return days


def _dedup(
    state: StateManager,
    tickets: List[TicketResult],
    cfg: FilterConfig,
) -> List[TicketResult]:
    """Filter tickets through the 48h dedup rule."""
    return [t for t in tickets if state.should_notify(t)]


def _record_notifications(
    state: StateManager,
    tickets: List[TicketResult],
) -> None:
    """Persist notification records after successful send."""
    for t in tickets:
        rec = NotificationRecord.from_ticket(t)
        state.save(rec)


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    main()
