"""Small text and date helpers."""

from __future__ import annotations

import html
import os
import re
from datetime import date, datetime, timedelta, timezone

_SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)
_BLOCK_RE = re.compile(
    r"</?(?:p|div|br|li|ul|ol|h[1-6]|tr|table|section|article|header|footer|blockquote)\b[^>]*>", re.I
)
_TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(value) -> str:
    """Plain text from HTML, including HTML that was entity-escaped (Greenhouse)."""
    if not value:
        return ""
    text = html.unescape(str(value))
    text = _SCRIPT_RE.sub(" ", text)
    text = _BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v ]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()


def iso_date(value) -> str:
    """YYYY-MM-DD from an ISO timestamp, epoch milliseconds/seconds or a date string."""
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return ""
    m = re.match(r"\s*(\d{4}-\d{2}-\d{2})", str(value))
    return m.group(1) if m else ""


_AGO_RE = re.compile(r"(\d+)\+?\s*(minute|hour|day|week|month)s?\s+ago", re.I)


def relative_date(text: str, today: date) -> str:
    """Turn Google Jobs' "3 days ago" into a date."""
    if not text:
        return ""
    lowered = text.lower()
    if "just" in lowered or "today" in lowered:
        return today.isoformat()
    if "yesterday" in lowered:
        return (today - timedelta(days=1)).isoformat()
    m = _AGO_RE.search(text)
    if not m:
        return ""
    n, unit = int(m.group(1)), m.group(2).lower()
    days = {"minute": 0, "hour": 0, "day": n, "week": 7 * n, "month": 30 * n}[unit]
    return (today - timedelta(days=days)).isoformat()


def norm(text) -> str:
    """Lower-case letters and digits only, for comparing names."""
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


_SECRET_PARAM_RE = re.compile(r"(api_key|apikey|token|key)=[^&\s'\"]+", re.I)


def redact(message: str) -> str:
    """Remove API keys from error text before it is logged or committed."""
    message = _SECRET_PARAM_RE.sub(r"\1=***", str(message))
    secret = os.environ.get("SERPAPI_KEY")
    if secret:
        message = message.replace(secret, "***")
    return message
