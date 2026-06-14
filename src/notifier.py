"""
Email notification via QQ Mail SMTP (SSL, port 465).

Composes two notification payload variants per the project
specification §4.1:

* **Standard Match** — Markdown table of qualified tickets.
* **Fallback Notification** — Recommendations A & B with a
  prominent notice that no tickets were found under the
  target price.

All docstrings and comments are in English.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional

from src.config import SMTPConfig
from src.models import TicketResult

logger = logging.getLogger(__name__)


def send_match_notification(
    tickets: List[TicketResult],
    smtp_cfg: SMTPConfig,
    target_price: float,
    dry_run: bool = False,
) -> bool:
    """
    Send a "Match Found!" email with a Markdown table of qualified tickets.

    Parameters
    ----------
    tickets:
        Non-empty list of matched tickets (all ``is_fallback=False``).
    smtp_cfg:
        SMTP connection parameters (credentials from env vars).
    target_price:
        The price threshold from the user's config (for the subject line).
    dry_run:
        When True, log the email body instead of sending. Useful for testing.

    Returns
    -------
    bool
        True if the email was sent (or logged in dry-run mode) successfully.
    """
    subject = f"[DB Alert] Match Found! — {len(tickets)} ticket(s) ≤ {target_price:.2f} EUR"

    lines = [
        f"## 🎫 DB Ticket Alert — {len(tickets)} Match(es) Found",
        "",
        f"Target price: **≤ {target_price:.2f} EUR**",
        "",
        "| # | Date | From | To | Departure | Arrival | Duration | Price | Class | Transfers | Train |",
        "|---|------|------|----|-----------|---------|----------|-------|-------|-----------|-------|",
    ]

    for i, t in enumerate(tickets, start=1):
        conn = t.connection
        dep = conn.departure.strftime("%H:%M")
        arr = conn.arrival.strftime("%H:%M")
        date_str = conn.departure.strftime("%d.%m.%Y")
        dur = f"{conn.duration_minutes} min"
        transfers = "Direct" if conn.is_direct else str(conn.transfers)
        trains = ", ".join(conn.train_types) if conn.train_types else "—"
        link_cell = f"[Book]({conn.link})" if conn.link else "—"

        lines.append(
            f"| {i} | {date_str} | {conn.from_station} | {conn.to_station} "
            f"| {dep} | {arr} | {dur} "
            f"| {conn.price:.2f} € | {conn.travel_class} "
            f"| {transfers} | {trains} |"
        )

    body = "\n".join(lines)
    return _send_email(smtp_cfg, subject, body, dry_run)


def send_fallback_notification(
    rec_a: Optional[TicketResult],
    rec_b: Optional[TicketResult],
    smtp_cfg: SMTPConfig,
    target_price: float,
    dry_run: bool = False,
) -> bool:
    """
    Send a "No Matches — Alternative Recommendations" email.

    Parameters
    ----------
    rec_a:
        Recommendation A (cheapest over-budget), or None.
    rec_b:
        Recommendation B (relaxed direct-only), or None.
    smtp_cfg:
        SMTP connection parameters.
    target_price:
        The price threshold (for the notice text).
    dry_run:
        When True, log the email body instead of sending.

    Returns
    -------
    bool
        True if the email was sent / logged successfully.
    """
    subject = "[DB Info] No Matches — Alternative Recommendations"

    lines = [
        "## ℹ️ DB Ticket Scanner — No Matches Found",
        "",
        f"⚠️ **No tickets found under your target price of {target_price:.2f} EUR.**",
        "",
        "Here are the closest alternatives:",
        "",
    ]

    if rec_a is not None:
        conn = rec_a.connection
        dep = conn.departure.strftime("%H:%M")
        arr = conn.arrival.strftime("%H:%M")
        transfers = "Direct" if conn.is_direct else f"{conn.transfers} changes"
        lines.extend([
            "### Recommendation A — Cheapest Available",
            "",
            f"- **{conn.from_station} → {conn.to_station}**",
            f"- Date: {conn.departure.strftime('%d.%m.%Y')}",
            f"- Departure: {dep} · Arrival: {arr} · Duration: {conn.duration_minutes} min",
            f"- Price: **{conn.price:.2f} EUR** ({conn.travel_class})",
            f"- Transfers: {transfers}",
            f"- Train(s): {', '.join(conn.train_types) if conn.train_types else '—'}",
            "",
        ])

    if rec_b is not None:
        conn = rec_b.connection
        dep = conn.departure.strftime("%H:%M")
        arr = conn.arrival.strftime("%H:%M")
        date_str = conn.departure.strftime("%d.%m.%Y")
        lines.extend([
            "### Recommendation B — Best Transfer Connection (Direct-Only Relaxed)",
            "",
            f"- **{conn.from_station} → {conn.to_station}**",
            f"- Date: {date_str}",
            f"- Departure: {dep} · Arrival: {arr} · Duration: {conn.duration_minutes} min",
            f"- Price: **{conn.price:.2f} EUR** ({conn.travel_class})",
            f"- Transfers: {conn.transfers} (min transfer time: {conn.transfer_time} min)",
            f"- Train(s): {', '.join(conn.train_types) if conn.train_types else '—'}",
            "",
        ])

    if rec_a is None and rec_b is None:
        lines.append("No alternative connections were found in the search window.")

    body = "\n".join(lines)
    return _send_email(smtp_cfg, subject, body, dry_run)


# ── Low-level SMTP sender ────────────────────────────────────────────────────

def _send_email(
    smtp_cfg: SMTPConfig,
    subject: str,
    body: str,
    dry_run: bool = False,
) -> bool:
    """
    Send an email via SMTP_SSL (QQ Mail, port 465).

    Credentials are read from the environment variables specified
    in the SMTP config (``user_env`` / ``pass_env``).
    """
    user = smtp_cfg.user()
    password = smtp_cfg.password()

    if not user or not password:
        logger.error(
            "SMTP credentials missing. Set %s and %s environment variables.",
            smtp_cfg.user_env, smtp_cfg.pass_env,
        )
        return False

    to_addr = smtp_cfg.to_email
    if not to_addr:
        logger.error("No recipient email configured (smtp.to_email).")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr

    # Attach both plain-text and HTML versions
    plain = body  # Markdown is readable as plain text
    html = _markdown_to_html(body)
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    if dry_run:
        logger.info("DRY RUN — would send email:")
        logger.info("  Subject: %s", subject)
        logger.info("  To: %s", to_addr)
        logger.info("  Body:\n%s", body)
        return True

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_cfg.host, smtp_cfg.port, context=context) as server:
            server.login(user, password)
            server.sendmail(user, to_addr, msg.as_string())
        logger.info("Email sent successfully to %s", to_addr)
        return True
    except smtplib.SMTPException as exc:
        logger.error("SMTP error sending email: %s", exc)
        return False
    except Exception as exc:
        logger.error("Unexpected error sending email: %s", exc)
        return False


def _markdown_to_html(md: str) -> str:
    """
    Minimal Markdown → HTML converter for the table + headings.

    Avoids adding an external dependency (like ``markdown``) for
    the sake of a single use-case. Can be replaced with a proper
    renderer if richer formatting is needed.
    """
    lines = md.split("\n")
    html_lines: List[str] = []
    in_table = False
    in_thead = False

    for line in lines:
        stripped = line.strip()

        # Headings
        if stripped.startswith("## "):
            html_lines.append(f"<h2>{stripped[3:]}</h2>")
            continue
        if stripped.startswith("### "):
            html_lines.append(f"<h3>{stripped[4:]}</h3>")
            continue

        # Table rows
        if stripped.startswith("|") and "---" not in stripped:
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if not in_table:
                html_lines.append('<table border="1" cellpadding="4" cellspacing="0">')
                in_table = True
                in_thead = True
                tag = "th"
            else:
                tag = "td"
            if in_thead:
                html_lines.append("<thead><tr>")
                html_lines.extend(
                    f"<{tag}>{cell}</{tag}>" for cell in cells
                )
                html_lines.append("</tr></thead><tbody>")
                in_thead = False
            else:
                html_lines.append("<tr>")
                html_lines.extend(
                    f"<{tag}>{cell}</{tag}>" for cell in cells
                )
                html_lines.append("</tr>")
            continue

        # Table separator row
        if stripped.startswith("|---"):
            continue  # skip

        # Close table on non-table line
        if in_table:
            html_lines.append("</tbody></table>")
            in_table = False

        # Bold
        line_html = _inline_formatting(stripped) if stripped else "<br>"
        html_lines.append(f"<p>{line_html}</p>")

    if in_table:
        html_lines.append("</tbody></table>")

    return "\n".join(html_lines)


def _inline_formatting(text: str) -> str:
    """Convert bold (**text**) and links."""
    import re
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Inline links [text](url)
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', text)
    return text
