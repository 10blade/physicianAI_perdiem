"""Find which public job board a company uses, from its name alone."""

from __future__ import annotations

import re
from typing import Iterable, Optional

from .http import Http, HttpError
from .sources.ats import BOARDS, DETECT_ORDER
from .util import norm

_STOP = {"inc", "llc", "ltd", "corp", "co", "the", "company"}
_SUFFIXES = {"ai", "health", "healthcare", "hq", "labs", "io", "technologies", "tech", "medical", "app", "inc"}
_GENERIC = _SUFFIXES | _STOP | {"and", "of"}


def slug_variants(name: str, aliases: Iterable[str] = ()) -> list[str]:
    """Likely board slugs: "Hippocratic AI" -> hippocraticai, hippocratic-ai, hippocratic."""
    out: list[str] = []

    def add(slug: str) -> None:
        slug = slug.strip("-")
        if slug and slug not in out:
            out.append(slug)

    for alias in aliases:
        add(str(alias).strip().lower())
    words = re.findall(r"[a-z0-9]+", name.lower().replace("&", " and "))
    core = [w for w in words if w not in _STOP] or words
    add("".join(core))
    add("-".join(core))
    if len(core) > 1 and core[-1] in _SUFFIXES:
        add("".join(core[:-1]))
        add("-".join(core[:-1]))
    return out[:6]


def name_matches(board_name: Optional[str], company: str) -> bool:
    """Loose check that a board belongs to the company (when the board reports a name)."""
    if not board_name:
        return True
    board = norm(board_name)
    tokens = [t for t in re.findall(r"[a-z0-9]+", company.lower()) if t not in _GENERIC and len(t) > 2]
    if not tokens:
        return True
    return any(t in board for t in tokens) or board in norm(company)


def probe(name: str, http: Http, aliases: Iterable[str] = (), stop_at_first: bool = False) -> list[dict]:
    """Every board (type:slug) that answers for this company and has open postings."""
    hits = []
    for slug in slug_variants(name, aliases):
        for ats in DETECT_ORDER:
            board = BOARDS[ats]
            try:
                count = board.count(slug, http)
            except (HttpError, ValueError, AttributeError, TypeError):
                continue
            if count <= 0:
                continue
            label = board.board_name(slug, http)
            if not name_matches(label, name):
                continue
            hits.append({"board": f"{ats}:{slug}", "postings": count, "board_name": label or "", "url": board.careers_url(slug)})
            if stop_at_first:
                return hits
    return hits


def detect(name: str, http: Http, aliases: Iterable[str] = ()) -> Optional[str]:
    hits = probe(name, http, aliases, stop_at_first=True)
    return hits[0]["board"] if hits else None
