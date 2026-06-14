# DB Weekend Sparpreis Ticket Scanner

Deutsche Bahn weekend promotional ticket scanner. Searches DB for
"Sparpreis" tickets matching price/class/transfer criteria and sends
email notifications via msmtp + bsd-mailx (any SMTP provider).

## Quick start

```bash
sudo apt install msmtp msmtp-mta bsd-mailx   # system MTA
vim ~/.msmtprc                               # SMTP credentials
vim config.toml                              # edit routes, price, passenger
./scan.sh                                    # headed dry-run scan
./test.sh                                    # run 51 pytest tests
```

## Requirements

- WSL2 (Ubuntu 22.04/24.04) with WSLg
- Python 3.12+, [uv](https://docs.astral.sh/uv/)
- Google Chrome (Playwright `channel="chrome"`)
- msmtp, msmtp-mta, bsd-mailx (`sudo apt install`)

## How it works

1. Reads `config.toml`
2. Launches Chromium (headed — Akamai blocks headless)
3. Constructs a `bp=true` search URL → navigates directly (no form-filling)
4. Bestpreise time-slot buttons load; clicks cheapest first, iterates all slots
5. Parses each connection: time, price, transfers, train type
6. Filters: price cap → class → direct-only → transfer safety → time exclusion
7. If matches found: consolidates all days, sends one email
8. If no matches: computes fallback recommendations A (cheapest) & B (relaxed direct)
9. Records notifications in `history.json` for price-change display in future emails

## SMTP configuration (.env)

All SMTP settings go into `.env` (not `config.toml`, to protect your email address):

```bash
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USER=your_email@qq.com
SMTP_PASS=your_app_password
SMTP_TO=you@example.com                          # comma-separated for multiple
# SMTP_CC=cc1@example.com,cc2@example.com        # optional, also supports multiple
```

Test with:

```bash
uv run python -m src.main test-email
```

## Configuration (`config.toml`)

```toml
[[routes.entries]]
from_station = "Berlin Hbf"
to_station = "München Hbf"
trip_type = "SINGLE"

[search_window]
start_date = "2026-07-13"
end_date = "2026-07-15"

[passenger]
age = 28
bahncard = "none"

[filters]
target_price = 30.0
ticket_class = "2nd"
direct_only = false
min_transfer_time = 15
exclude_departure_start = "00:00"
exclude_departure_end = "00:00"

# SMTP settings are in .env — see section above
```

## Project structure

```
src/
  main.py      CLI orchestration
  config.py    Pydantic models + TOML
  browser.py   Playwright factory
  scraper.py   Web extraction → Connection
  models.py    Connection, TicketResult, NotificationRecord
  filter.py    Filtering & scoring pipeline
  fallback.py  Recommendation A & B
  notifier.py  SMTP email composition
  state.py     history.json price history & recording
tests/         51 pytest tests
debug/         Debug shell scripts
```

## Cron

```
0 2 * * 6,0  cd /path/to/db_ticket && xvfb-run ./scan.sh
```
