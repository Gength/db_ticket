# REASONIX.md — DB Weekend Ticket Scanner

## Stack

- **Language:** Python 3.12+
- **Package manager:** uv (Astral)
- **Browser engine:** Playwright (Chromium via `channel="chrome"`)
- **Config:** Pydantic v2 + TOML (`config.toml`)
- **Test:** pytest + pytest-asyncio (asyncio_mode=auto)

## Layout

```
config.toml        # user-editable routes, filters, passenger, timeouts
scan.sh            # headed scan (dry-run by default)
test.sh            # pytest runner
src/
  main.py          # CLI: `scan` / `test-email` subcommands, daily orchestration
  config.py        # Pydantic models + config.toml loader + TimeoutsConfig defaults
  browser.py       # Playwright persistent-context factory (headed/headless)
  scraper.py       # Web extraction: constructs bp=true URL → Bestpreise slots →
                   #   click cheapest-first → parse Connection objects
  models.py        # Connection, TicketResult, NotificationRecord
  filter.py        # Pipeline: price cap → class → direct-only → transfer safety →
                   #   time exclusion → scoring
  fallback.py      # Rec A (cheapest over-budget) & Rec B (relaxed direct-only)
  notifier.py      # Mail SMTP_SSL, Markdown table + HTML body
  state.py         # history.json price history
tests/             # test_config, test_filter, test_fallback, test_state, test_notifier (50 tests)
debug/             # debug shell scripts
```

## Commands

```bash
./scan.sh                          # headed dry-run, all dates
./scan.sh --dry-run --limit-dates=3
./test.sh                          # pytest -v
uv run python -m src.main test-email
.env && ./scan.sh                      # produce, no dry-run (SMTP settings in .env)
```

## Conventions

- All files use `from __future__ import annotations`
- All docstrings and comments are in English
- Config is single source of truth — `load_config("config.toml")`
- `history.json` is auto-managed; do not edit manually

## Watch out for

- **Akamai WAF blocks headless Chromium.** Must run headed
  (`DEBUG_VISUAL=true`) or wrap with `xvfb-run` for cron.
- **Persistent browser data** at `~/.db_scanner_browser_data`. Delete to
  force fresh cookie consent.
- **`r=` parameter:** DB uses internal age codes, NOT literal ages.
  Adult (27-64) = `13:16`. Using `27:64` triggers a warning dialog.
- **Station IBNR** is hardcoded in `scraper.py`. Add unknown stations there.
- **Email is NOT dry-run by default** — pass `--dry-run` to test without sending.
- **All SMTP settings** (host, port, to, cc, user, pass) come from `.env` file
  or environment variables. Nothing SMTP-related is in config.toml.
