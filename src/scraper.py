"""
DB ticket scraper — URL-based search, no DOM form-filling.

Flow:
  1. Navigate directly to the search results URL (GET-based)
  2. Wait for connection headings to appear
  3. Parse the results

All selectors verified against live bahn.de 2026-06-14.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import urllib.parse
from datetime import date, datetime, timedelta
from typing import List

from src.browser import BrowserFactory
from src.config import BahnCard, PassengerConfig, RouteConfig, TicketClass
from src.models import Connection

logger = logging.getLogger(__name__)

RESULTS_URL = "https://www.bahn.de/buchung/fahrplan/suche"

# ── Station IBNR database ───────────────────────────────────────────────────

KNOWN: dict[str, tuple[str, int, int]] = {
    "berlin hbf":             ("8011160", 13369549, 52525589),
    "berlin hauptbahnhof":    ("8011160", 13369549, 52525589),
    "münchen hbf":            ("8000261", 11558339, 48140229),
    "münchen hauptbahnhof":   ("8000261", 11558339, 48140229),
    "hamburg hbf":            ("8002549", 10006909, 53552706),
    "hamburg hauptbahnhof":   ("8002549", 10006909, 53552706),
    "frankfurt(main)hbf":     ("8000105",  8662500, 50106893),
    "frankfurt(m)hbf":        ("8000105",  8662500, 50106893),
    "frankfurt hbf":          ("8000105",  8662500, 50106893),
    "frankfurt flughafen":    ("8070003",  8570828, 50051828),
    "köln hbf":               ("8000207",  6958730, 50943029),
    "koln hbf":               ("8000207",  6958730, 50943029),
    "stuttgart hbf":          ("8000096",  9181534, 48784009),
    "düsseldorf hbf":         ("8000085",  6788976, 51217689),
    "dusseldorf hbf":         ("8000085",  6788976, 51217689),
    "leipzig hbf":            ("8010205", 12381094, 51345764),
    "dresden hbf":            ("8010085", 13731605, 51040600),
    "hannover hbf":           ("8000152",  9741024, 52376747),
    "nürnberg hbf":           ("8000284", 11082629, 49446208),
    "nurnberg hbf":           ("8000284", 11082629, 49446208),
    "bremen hbf":             ("8000050",  8813360, 53083422),
    "erfurt hbf":             ("8010001", 11037423, 50972627),
    "mannheim hbf":           ("8000244",  8471231, 49479336),
    "karlsruhe hbf":          ("8000191",  8402027, 48993818),
    "freiburg hbf":           ("8000107",  7840986, 47997793),
    "ulm hbf":                ("8000170",  9983340, 48398280),
    "augsburg hbf":           ("8000013", 10886834, 48365380),
    "würzburg hbf":           ("8000260",  9935475, 49801818),
    "wurzburg hbf":           ("8000260",  9935475, 49801818),
    "dortmund hbf":           ("8000080",  7501988, 51517552),
    "essen hbf":              ("8000098",  7014316, 51450847),
}

BAHNCARD_URL: dict[BahnCard, str] = {
    BahnCard.NONE:              "KLASSENLOS",
    BahnCard.BAHNCARD_25_2ND:   "BC25-2",
    BahnCard.BAHNCARD_50_2ND:   "BC50-2",
    BahnCard.BAHNCARD_25_1ST:   "BC25-1",
    BahnCard.BAHNCARD_50_1ST:   "BC50-1",
}


def _lookup(name: str) -> tuple[str, int, int] | None:
    key = name.lower().strip()
    if key in KNOWN:
        return KNOWN[key]
    for k, v in KNOWN.items():
        if key in k or k in key:
            return v
    return None


def _station_id(name: str, ibnr: str, x: int, y: int) -> str:
    """Build A=1@O=...@L=...@ station ID with timestamp."""
    ts = int(time.time())
    return f"A=1@O={urllib.parse.quote(name)}@X={x}@Y={y}@U=80@L={ibnr}@p={ts}@"


# ── Scraper ──────────────────────────────────────────────────────────────────

class DBScraper:

    def __init__(self) -> None:
        self._factory = BrowserFactory()

    async def search(
        self,
        route: RouteConfig,
        search_date: date,
        passenger: PassengerConfig,
        ticket_class: TicketClass,
    ) -> List[Connection]:
        async with self._factory as (browser, context):
            page = await context.new_page()
            page.set_default_timeout(90_000)
            try:
                self._current_date = search_date  # for _parse_one to use
                url = self._build_url(route, search_date, passenger, ticket_class)
                logger.info("→ %s", url[:100])

                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

                # Wait for connections to appear (bp=true loads all on one page)
                for i in range(25):
                    await asyncio.sleep(2)

                    for btn_text in ["Schließen", "Allow all cookies", "Alle akzeptieren"]:
                        try:
                            btn = page.locator(f'button:has-text("{btn_text}")').first
                            if await btn.is_visible(timeout=500):
                                await btn.click()
                                logger.info("Dismissed: %s", btn_text)
                                await asyncio.sleep(1)
                        except Exception:
                            pass

                    # Wait for Bestpreise slot buttons then try each
                    slots = page.locator('button:has-text("Von"):has-text("Uhr"):has-text("ab")')
                    n = await slots.count()
                    if n > 0:
                        logger.info("Bestpreise slots loaded: %d after %ds", n, (i + 1) * 2)
                        all_conns = await self._try_all_slots(page, route, slots)
                        return all_conns
                else:
                    logger.warning("Bestpreise slots not found — trying parse without click")
                    conns = await self._parse(page, route)
                    if conns:
                        return conns
                return []

            except Exception:
                logger.exception("Search failed")
                raise
            finally:
                await page.close()

    # ── Cookie ────────────────────────────────────────────────────────────

    # ── URL builder ───────────────────────────────────────────────────────

    def _build_url(
        self,
        route: RouteConfig,
        search_date: date,
        passenger: PassengerConfig,
        ticket_class: TicketClass,
    ) -> str:
        o = _lookup(route.from_station)
        d = _lookup(route.to_station)
        if not o or not d:
            raise ValueError(f"Unknown station: {route.from_station} / {route.to_station}")

        o_ibnr, o_x, o_y = o
        d_ibnr, d_x, d_y = d

        # Age group — DB uses internal codes, NOT literal age ranges!
        # Verified from form-generated URL: adult "27-64" → code "13:16"
        age = passenger.age
        if 6 <= age <= 14:
            age_range = "9:16"      # Kind
        elif 15 <= age <= 26:
            age_range = "9:16"      # Jugendlicher (same code)
        elif age >= 65:
            age_range = "13:16"     # Senior (same code as adult)
        else:
            age_range = "13:16"     # Erwachsener (verified against live site)

        bc = BAHNCARD_URL.get(passenger.bahncard, "KLASSENLOS")

        params = {
            "sts": "true",
            "so": route.from_station,
            "zo": route.to_station,
            "kl": "1" if ticket_class == TicketClass.FIRST else "2",
            "r": f"{age_range}:{bc}:1",
            "soid": _station_id(route.from_station, o_ibnr, o_x, o_y),
            "zoid": _station_id(route.to_station, d_ibnr, d_x, d_y),
            "sot": "ST",
            "zot": "ST",
            "soei": o_ibnr,
            "zoei": d_ibnr,
            "hd": f"{search_date.isoformat()}T00:00:00",
            "hza": "D",
            "hz": "[]",
            "ar": "false",
            "s": "true",
            "d": "false",
            "vm": "00,01,02,03,04,05,06,07,08,09",
            "fm": "false",
            "bp": "true",
            "dlt": "false",
            "dltv": "false",
        }

        # URL-encode: the hash fragment needs proper encoding for @ = etc.
        qs = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        return f"{RESULTS_URL}#{qs}"

    # ── Slot selection ────────────────────────────────────────────────────

    async def _try_all_slots(self, page: Page, route, slots) -> list[Connection]:
        """Click each Bestpreise slot (cheapest first), collect ALL connections."""

        from src.config import load_config
        try:
            cfg = load_config("config.toml")
            exc_s = cfg.filters.exclude_departure_start
            exc_e = cfg.filters.exclude_departure_end
        except Exception:
            exc_s = exc_e = ""

        def _inside(slot_text: str) -> bool:
            if not exc_s or not exc_e or exc_s == exc_e:
                return False
            st = re.search(r'(\d{1,2}:\d{2})\s*Uhr', slot_text)
            et = re.search(r'bis\s+(\d{1,2}:\d{2})', slot_text)
            if not st or not et:
                return False
            ss = int(st.group(1)[:2]) * 60 + int(st.group(1)[3:])
            se = int(et.group(1)[:2]) * 60 + int(et.group(1)[3:])
            es = int(exc_s[:2]) * 60 + int(exc_s[3:])
            ee = int(exc_e[:2]) * 60 + int(exc_e[3:])
            if se == 0: se = 1440
            if ee == 0: ee = 1440
            if es <= ee:
                return ss >= es and se <= ee
            return ss >= es or se <= ee

        # Build sorted (index, price) list
        slot_info = []
        for i in range(await slots.count()):
            text = await slots.nth(i).inner_text()
            if _inside(text):
                continue
            pm = re.search(r'ab\s+(\d+[.,]\d{2})', text)
            if pm:
                p = float(pm.group(1).replace(',', '.'))
                slot_info.append((i, p))

        slot_info.sort(key=lambda x: x[1])
        logger.info("Slots by price: %s", [f"#{i}({p}€)" for i, p in slot_info])

        all_conns = []
        seen = set()

        for idx, price in slot_info:
            logger.info("Slot #%d (%.2f€) ...", idx, price)
            await slots.nth(idx).click()
            await asyncio.sleep(3)
            for _ in range(10):
                conns = await self._parse(page, route)
                if conns:
                    break
                await asyncio.sleep(1)
            new = [c for c in conns if c.uid not in seen]
            for c in new:
                seen.add(c.uid)
            all_conns.extend(new)
            logger.info("  → %d new (%d total)", len(new), len(all_conns))

        return all_conns

    async def _parse(self, page: Page, route: RouteConfig) -> List[Connection]:
        results: List[Connection] = []
        seen: set[str] = set()

        # Each connection's text from its own reiseloesung__item container
        rows = await page.evaluate("""
            () => {
                const items = document.querySelectorAll('[class*=\"reiseloesung__item\"]');
                return Array.from(items).map(el => el.innerText.replace(/\\n/g, ' '));
            }
        """)

        logger.info("Found %d connection rows", len(rows))

        for i, row_text in enumerate(rows):
            try:
                conn = self._parse_one(row_text, route)
                if conn and conn.uid not in seen:
                    seen.add(conn.uid)
                    results.append(conn)
                    logger.debug("  %.2f€ %s %s", conn.price,
                                 "direct" if conn.is_direct else f"{conn.transfers}x",
                                 conn.train_types)
            except Exception as exc:
                logger.debug("Skip row %d: %s", i, exc)

        logger.info("Parsed %d connections", len(results))
        return results

    def _parse_one(self, text: str, route: RouteConfig) -> Connection | None:
        # h2 text: "HH:MM\n–\nHH:MM" or "HH:MM – HH:MM"
        # Parent text contains: Dauer, ab X,XX €, Verkehrsmittel, Von:, Nach:, ICE...
        times_m = re.search(r"(\d{1,2}:\d{2})\s*[–\-]\s*(\d{1,2}:\d{2})", text)
        if not times_m:
            return None
        dep, arr = self._times(times_m.group(1), times_m.group(2))

        # Price: "ab X,XX €" — find cheapest price in text
        prices = re.findall(r"ab\s+(\d+[.,]\d{2})", text)
        price = min(float(p.replace(",", ".")) for p in prices) if prices else None

        # Duration: "Dauer: X Stunden Y Minuten" or "Dauer: X h Y min"
        dur_min = self._duration(text)

        # Transfers: "X Umstieg" or "X Umstiege"
        tr_m = re.search(r"(\d+)\s*Umstieg", text)
        transfers = int(tr_m.group(1)) if tr_m else 0

        # Train types
        trains = re.findall(r"\b(ICE|IC|EC|RE|RB|S\d*|IRE|TGV|RJ|NJ|FLX)\b", text)
        trains = list(dict.fromkeys(trains))

        # Class
        tclass = "1st" if "1. Klasse" in text else "2nd"

        return Connection(
            from_station=route.from_station,
            to_station=route.to_station,
            departure=dep,
            arrival=arr,
            price=price or 999.0,
            travel_class=tclass,
            transfers=transfers,
            transfer_time=15 if transfers > 0 else 0,
            is_direct=transfers == 0,
            train_types=trains,
            link="",
        )

    # ── Parsers ───────────────────────────────────────────────────────────

    def _times(self, dep: str, arr: str) -> tuple[datetime, datetime]:
        base = self._current_date or datetime.now()
        if isinstance(base, date) and not isinstance(base, datetime):
            base = datetime(base.year, base.month, base.day)
        dh, dm = map(int, dep.split(":"))
        ah, am = map(int, arr.split(":"))
        departure = base.replace(hour=dh, minute=dm, second=0, microsecond=0)
        arrival = base.replace(hour=ah, minute=am, second=0, microsecond=0)
        if arrival <= departure:
            arrival += timedelta(days=1)
        return departure, arrival

    @staticmethod
    def _duration(text: str) -> int:
        h = re.search(r"(\d+)\s*Stunden?", text)
        m = re.search(r"(\d+)\s*Minuten?", text)
        hours = int(h.group(1)) if h else 0
        mins = int(m.group(1)) if m else 0
        if hours == 0 and mins == 0:
            h2 = re.search(r"(\d+)\s*h", text)
            m2 = re.search(r"(\d+)\s*min", text)
            hours = int(h2.group(1)) if h2 else 0
            mins = int(m2.group(1)) if m2 else 0
        return hours * 60 + mins
