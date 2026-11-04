"""Data types shared by every part of the search."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .util import norm

BASIS_ADJECTIVE = {"day": "daily", "week": "weekly", "month": "monthly", "year": "annual"}


def _money(value: float) -> str:
    if abs(value - round(value)) < 0.005:
        return f"${round(value):,}"
    return f"${value:,.2f}"


@dataclass
class Pay:
    """Pay normalised to US dollars per hour.

    ``lo`` and ``hi`` are hourly figures. Either may be None when the posting
    gives only one bound: "up to $200/hr" has no floor and "$120+/hr" has no
    ceiling. ``basis`` is the unit the employer used; daily, weekly, monthly
    and annual figures are converted to an hourly estimate (8 h/day, 40 h/week,
    2,080 h/year).
    """

    lo: Optional[float] = None
    hi: Optional[float] = None
    basis: str = "hour"
    raw: str = ""

    @property
    def top(self) -> Optional[float]:
        """Highest hourly figure the posting mentions."""
        return self.hi if self.hi is not None else self.lo

    def label(self) -> str:
        if self.lo is not None and self.hi is not None:
            core = _money(self.lo) if abs(self.lo - self.hi) < 0.005 else f"{_money(self.lo)}–{_money(self.hi)}"
        elif self.lo is not None:
            core = f"{_money(self.lo)}+"
        elif self.hi is not None:
            core = f"up to {_money(self.hi)}"
        else:
            return "not listed"
        if self.basis == "hour":
            return f"{core}/hr"
        return f"≈{core}/hr ({BASIS_ADJECTIVE.get(self.basis, self.basis)} rate)"


@dataclass
class Job:
    """One posting, normalised from whichever job board it came from."""

    source: str  # e.g. "greenhouse:xai" or "google_jobs"
    company: str
    title: str
    url: str
    job_id: str
    location: str = ""
    remote: Optional[bool] = None
    employment_type: str = ""  # the board's own label: Contract, PartTime, ...
    posted: str = ""  # YYYY-MM-DD when the board gives a date
    description: str = ""  # plain text
    structured_pay: Optional[Pay] = None  # pay the board returned as data
    pay_text: str = ""  # a short salary string the board returned as text
    category: str = ""
    requires_ai_context: bool = False  # broad searches must mention AI work
    via: str = ""
    listings: int = 1  # postings merged into this row (one role posted per city)

    # Filled in by matching.classify()
    pay: Optional[Pay] = None
    title_score: int = 0
    physician_score: int = 0
    matched_terms: list[str] = field(default_factory=list)
    flexible: bool = False
    status: str = ""

    # Filled in by state.update_seen()
    first_seen: str = ""
    is_new: bool = False

    @property
    def key(self) -> str:
        """Identity used to spot new postings between runs."""
        if self.source.startswith("page:"):
            # Careers pages often repeat one role under several URLs (".../cardiologist-29",
            # ".../cardiologist-30"), so the company and title identify it, not the URL.
            return f"{self.source}:{norm(self.company)}:{norm(self.title)}"
        return f"{self.source}:{self.job_id}"

    def to_dict(self) -> dict:
        pay = self.pay
        return {
            "company": self.company,
            "title": self.title,
            "url": self.url,
            "status": self.status,
            "pay": pay.label() if pay else "",
            "pay_min_hourly": pay.lo if pay else None,
            "pay_max_hourly": pay.hi if pay else None,
            "pay_basis": pay.basis if pay else "",
            "pay_quote": pay.raw if pay else "",
            "employment_type": self.employment_type,
            "location": self.location,
            "listings": self.listings,
            "remote": self.remote,
            "posted": self.posted,
            "first_seen": self.first_seen,
            "new": self.is_new,
            "category": self.category,
            "source": self.source,
            "via": self.via,
            "physician_terms": self.matched_terms,
        }
