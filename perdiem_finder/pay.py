"""Read pay out of job postings and normalise it to US dollars per hour."""

from __future__ import annotations

import re
from typing import Iterable, Optional

from .models import Pay

# Hours used to turn non-hourly pay into an hourly estimate.
HOURS_PER = {"hour": 1.0, "day": 8.0, "week": 40.0, "month": 2080.0 / 12, "year": 2080.0}
# When a posting quotes several units, the most granular one wins.
BASIS_ORDER = ("hour", "day", "week", "month", "year")
# Hourly figures outside this band are not believable rates.
HOURLY_BOUNDS = (10.0, 3000.0)

_TRANSLATE = {ord(c): "-" for c in "‐‑‒–—―−"}
_TRANSLATE.update({ord(c): " " for c in "    "})
_TRANSLATE[0x200B] = None

_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_CUR = r"US\s?\$|USD\s?|(?<![A-Za-z])\$"
_MONEY = (
    rf"(?P<c1>{_CUR})?\s*(?P<a>{_NUM})(?P<ak>[kK]\b)?"
    rf"(?:\s*(?:-|\bto\b|\band\b)\s*(?P<c2>{_CUR})?\s*(?P<b>{_NUM})(?P<bk>[kK]\b)?)?"
    rf"(?P<plus>\s*\+)?(?:\s*(?P<code>USD|dollars)\b)?"
)
# Free text is only read for hourly, daily and annual pay. Weekly or monthly
# figures in prose are usually totals, not rates.
_UNITS = {
    "hour": r"/\s*(?:hr|hrs|hour|hourly|h)\b|per\s+(?:hr|hour)\b|an?\s+hour\b|-?\s*hourly\b",
    "day": r"/\s*day\b|per\s+(?:day|diem)\b|an?\s+day\b|daily\b",
    "year": r"/\s*(?:yr|year|annum)\b|per\s+(?:yr|year|annum)\b|an?\s+year\b|annually\b|annual\b|yearly\b",
}
_UNIT_ALT = "|".join(f"(?P<u_{k}>{v})" for k, v in _UNITS.items())
_RATE_RE = re.compile(rf"{_MONEY}\s*(?:{_UNIT_ALT})", re.IGNORECASE)
_ANY_UNIT_RE = re.compile(rf"\s*(?:{_UNIT_ALT})", re.IGNORECASE)
# "Hourly rate: $120 - $160" (the unit comes before the number)
_LEAD_RE = re.compile(
    r"\b(?:hourly\s+(?:pay\s+|base\s+)?(?:rate|pay|wage|compensation|range)s?"
    r"|(?:pay\s+)?rate\s+per\s+hour|per[-\s]hour\s+rate)\b"
    rf"[^$\d\n]{{0,30}}?{_MONEY}",
    re.IGNORECASE,
)
_UPTO_RE = re.compile(r"\b(?:up\s*-?\s*to|as\s+much\s+as|max(?:imum)?(?:\s+of)?|top\s+rate\s+of)[\s:]*$", re.I)
_FROM_RE = re.compile(
    r"\b(?:from|starting\s+(?:at|from)|starts?\s+at|at\s+least|min(?:imum)?(?:\s+of)?"
    r"|over|above|more\s+than|exceeding|in\s+excess\s+of)[\s:]*$",
    re.I,
)
# Text just before an amount that marks it as another currency: "€100", "CAD 100", "CA$100".
_FOREIGN_RE = re.compile(
    r"(?:[£€₹¥₩]\s*|\b(?:CAD|AUD|GBP|EUR|INR|NZD|SGD|CHF)\s*|(?<![A-Za-z])(?:CA|AU|NZ|HK|C|A|S)\$\s*)$"
)

_INTERVALS = {
    "hour": ("1 hour", "hour", "hourly", "per-hour-wage", "per_hour", "per hour", "hr", "h"),
    "day": ("1 day", "day", "daily", "per-day-wage", "per_day", "per day"),
    "week": ("1 week", "week", "weekly", "per-week-salary", "per-week-wage", "per_week", "per week"),
    "month": ("1 month", "month", "monthly", "per-month-salary", "per-month-wage", "per_month", "per month"),
    "year": ("1 year", "year", "yearly", "annual", "annually", "per-year-salary", "per_year", "per year", "salary"),
}


def _amount(num: str, k: Optional[str]) -> float:
    value = float(num.replace(",", ""))
    return value * 1000 if k else value


def _rounded(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 2)


def _in_bounds(*values: Optional[float]) -> bool:
    lo_b, hi_b = HOURLY_BOUNDS
    return all(v is None or lo_b <= v <= hi_b for v in values)


def _build(m: re.Match, text: str, basis: str, require_currency: bool) -> Optional[Pay]:
    if require_currency and not (m.group("c1") or m.group("c2") or m.group("code")):
        return None
    start = m.start("c1") if m.group("c1") else m.start("a")
    before = text[max(0, start - 30) : start]
    if _FOREIGN_RE.search(before):
        return None
    a = _amount(m.group("a"), m.group("ak"))
    b = _amount(m.group("b"), m.group("bk")) if m.group("b") else None
    if b is not None and m.group("bk") and not m.group("ak") and a < 1000:
        a *= 1000  # "$100-150k"
    if b is None:
        if _UPTO_RE.search(before):
            lo, hi = None, a
        elif m.group("plus") or _FROM_RE.search(before):
            lo, hi = a, None
        else:
            lo, hi = a, a
    else:
        lo, hi = min(a, b), max(a, b)
    hours = HOURS_PER[basis]
    lo = lo / hours if lo is not None else None
    hi = hi / hours if hi is not None else None
    if not _in_bounds(lo, hi):
        return None
    return Pay(lo=_rounded(lo), hi=_rounded(hi), basis=basis, raw=m.group(0).strip())


def parse_text(text: Optional[str], require_currency: bool = True) -> list[Pay]:
    """Every pay rate mentioned in ``text``.

    Free text needs a currency marker ("$100/hr", "100 USD per hour") so that
    phrases like "10-20 hours per week" are not read as pay. Short salary
    fields such as Google Jobs' "100–150 an hour" can pass
    ``require_currency=False``.
    """
    if not text:
        return []
    text = text.translate(_TRANSLATE)
    found: list[Pay] = []
    spans: list[tuple[int, int]] = []
    for m in _RATE_RE.finditer(text):
        basis = next(k for k in _UNITS if m.group(f"u_{k}"))
        pay = _build(m, text, basis, require_currency)
        if pay:
            found.append(pay)
            spans.append(m.span())
    for m in _LEAD_RE.finditer(text):
        if any(s <= m.start("a") < e for s, e in spans):
            continue
        if _ANY_UNIT_RE.match(text, m.end()):
            continue  # a unit follows the number, so _RATE_RE already read it
        pay = _build(m, text, "hour", require_currency)
        if pay:
            found.append(pay)
    return found


def _basis_of(interval: str) -> Optional[str]:
    key = (interval or "").strip().lower()
    for basis, names in _INTERVALS.items():
        if key in names:
            return basis
    return None


def _number(value) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        number = float(str(value).replace(",", "").replace("$", ""))
    except ValueError:
        return None
    return number if number > 0 else None


def from_range(lo, hi, interval: str = "", currency: str = "USD", raw: str = "") -> Optional[Pay]:
    """Pay from a board's structured min/max fields.

    When the board gives no interval (Greenhouse pay ranges), the size of the
    numbers decides: up to $2,000 is hourly and $20,000 or more is annual.
    """
    if currency and currency.strip().upper() not in ("USD", "US$", "$"):
        return None
    lo, hi = _number(lo), _number(hi)
    if lo is None and hi is None:
        return None
    basis = _basis_of(interval)
    if basis is None:
        top = max(v for v in (lo, hi) if v is not None)
        if top <= 2000:
            basis = "hour"
        elif top >= 20000:
            basis = "year"
        else:
            return None
    hours = HOURS_PER[basis]
    lo_h = lo / hours if lo is not None else None
    hi_h = hi / hours if hi is not None else None
    if lo_h is not None and hi_h is not None and hi_h < lo_h:
        lo_h, hi_h = hi_h, lo_h
    if not _in_bounds(lo_h, hi_h):
        return None
    return Pay(lo=_rounded(lo_h), hi=_rounded(hi_h), basis=basis, raw=raw)


def combine(pays: Iterable[Optional[Pay]]) -> Optional[Pay]:
    """Merge every pay figure found for one posting into one range.

    Only figures in the most granular unit are kept, so "$150/hr (about
    $300K a year)" stays hourly.
    """
    pays = [p for p in pays if p is not None]
    if not pays:
        return None
    basis = min((p.basis for p in pays), key=BASIS_ORDER.index)
    group = [p for p in pays if p.basis == basis]
    floors = [p.lo for p in group if p.lo is not None]
    values = [v for p in group for v in (p.lo, p.hi) if v is not None]
    lo = min(floors) if floors else None
    top = max(values)
    open_ended = any(p.hi is None and p.lo == top for p in group)
    raws: list[str] = []
    for p in group:
        if p.raw and p.raw not in raws:
            raws.append(p.raw)
    return Pay(lo=lo, hi=None if open_ended else top, basis=basis, raw="; ".join(raws)[:160])
