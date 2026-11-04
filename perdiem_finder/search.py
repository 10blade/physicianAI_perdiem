"""Run one full search: every company's job board, watched pages, and Google Jobs."""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional

from . import detect, matching
from .config import Company, Config
from .http import Http, HttpError
from .models import Job
from .sources import google_jobs, pages
from .sources.ats import BOARDS, Board
from .util import norm, redact

DETECT_HIT_DAYS = 30  # re-check an auto-detected board after this many days
DETECT_MISS_DAYS = 14  # look again for a company with no board after this many days


@dataclass
class SourceStatus:
    name: str
    where: str  # "greenhouse:xai", a page URL, or "google_jobs"
    state: str  # ok | error | not_found | skipped
    postings: int = 0
    matches: int = 0
    note: str = ""


@dataclass
class SearchResult:
    jobs: list[Job]  # every physician posting found (any status except "irrelevant")
    statuses: list[SourceStatus]
    scanned: int
    boards_cache: dict
    partial: bool = False


def _classify_all(jobs: list[Job], settings: matching.Settings, board: Optional[Board] = None,
                  slug: str = "", http: Optional[Http] = None) -> None:
    for job in jobs:
        matching.classify(job, settings)
    if board is None or http is None:
        return
    budget = settings.max_enrich_per_board
    for job in jobs:
        if budget <= 0:
            break
        if not board.needs_enrich(job):
            continue
        budget -= 1
        try:
            changed = board.enrich(slug, http, job)
        except (HttpError, ValueError, TypeError, AttributeError):
            changed = False
        if changed:
            matching.classify(job, settings)


def _count_matches(jobs: list[Job], settings: matching.Settings) -> int:
    reported = matching.reported_statuses(settings)
    return sum(1 for j in jobs if j.status in reported)


def _resolve(company: Company, http: Http, cached: Optional[dict], today: date):
    """(board spec or None, new cache entry or None, note)."""
    if company.board:
        return company.board, None, ""
    if cached:
        checked = str(cached.get("checked") or "1970-01-01")
        try:
            age = (today - date.fromisoformat(checked)).days
        except ValueError:
            age = 10**6
        if cached.get("board") and age < DETECT_HIT_DAYS:
            return cached["board"], None, "auto-detected"
        if not cached.get("board") and age < DETECT_MISS_DAYS:
            return None, None, f"no public job board found (checked {checked})"
    found = detect.detect(company.name, http, company.aliases)
    entry = {"board": found, "checked": today.isoformat()}
    return found, entry, "auto-detected" if found else "no public job board found"


def _run_company(company: Company, http: Http, settings: matching.Settings, cached: Optional[dict], today: date):
    spec, entry, note = _resolve(company, http, cached, today)
    if not spec:
        return [], SourceStatus(company.name, "—", "not_found", note=note), entry, 0
    ats, slug = spec.split(":", 1)
    board = BOARDS[ats]
    try:
        jobs = board.fetch(slug, http, company.name)
    except HttpError as exc:
        if exc.status == 404 and not company.board:
            entry = {"board": None, "checked": "1970-01-01"}  # board is gone: look again next run
        return [], SourceStatus(company.name, spec, "error", note=redact(str(exc))), entry, 0
    except Exception as exc:  # an unexpected response shape should not stop the whole run
        return [], SourceStatus(company.name, spec, "error", note=redact(f"{type(exc).__name__}: {exc}")), entry, 0
    for job in jobs:
        job.category = company.category
    _classify_all(jobs, settings, board, slug, http)
    kept = [j for j in jobs if j.status != "irrelevant"]
    status = SourceStatus(company.name, spec, "ok", len(jobs), _count_matches(kept, settings), note)
    return kept, status, entry, len(jobs)


def _run_page(page: dict, http: Http, settings: matching.Settings):
    name = str(page.get("name") or page["url"])
    try:
        jobs, note = pages.fetch(page, http)
    except HttpError as exc:
        return [], SourceStatus(name, page["url"], "error", note=redact(str(exc))), 0
    except Exception as exc:
        return [], SourceStatus(name, page["url"], "error", note=redact(f"{type(exc).__name__}: {exc}")), 0
    for job in jobs:
        job.category = str(page.get("category") or "")
    _classify_all(jobs, settings)
    kept = [j for j in jobs if j.status != "irrelevant"]
    state = "skipped" if note.startswith("skipped") else "ok"
    return kept, SourceStatus(name, page["url"], state, len(jobs), _count_matches(kept, settings), note), len(jobs)


def _run_google(cfg: dict, http: Http, settings: matching.Settings, today: date):
    name = "Google Jobs (via SerpApi)"
    if cfg.get("enabled") is False or not cfg.get("queries"):
        return [], None, 0
    key = os.environ.get("SERPAPI_KEY", "").strip()
    if not key:
        return [], SourceStatus(name, "google_jobs", "skipped", note="add a SERPAPI_KEY secret to turn this on"), 0
    jobs, errors = google_jobs.fetch(cfg, http, key, today)
    for job in jobs:
        job.category = "Found via Google Jobs"
    _classify_all(jobs, settings)
    kept = [j for j in jobs if j.status != "irrelevant"]
    state = "error" if errors and not jobs else "ok"
    note = redact("; ".join(errors))[:300]
    return kept, SourceStatus(name, "google_jobs", state, len(jobs), _count_matches(kept, settings), note), len(jobs)


def _is_secondary(job: Job) -> bool:
    return job.source == "google_jobs" or job.source.startswith("page:")


# The last " - segment" or "(segment)" of a title.
_SUFFIX_RE = re.compile(r"^(?P<base>.+?)\s*(?:\s[-–—|:]\s*(?P<dash>[^-–—|:()]+)|\((?P<paren>[^()]+)\))\s*$")
_PLACE_WORDS = {
    "remote", "us", "usa", "uk", "united", "states", "kingdom", "canada", "worldwide", "global", "anywhere",
    "est", "pst", "cst", "mst", "et", "pt", "ct", "mt", "time", "timezone", "zone", "hybrid", "onsite",
}
_MAX_LOCATION = 300


def _strip_place(title: str, location: str) -> str:
    """ "Doctors - AI Training - Houston" -> "Doctors - AI Training" when the job is in Houston."""
    m = _SUFFIX_RE.match(title or "")
    if not m:
        return title
    words = re.findall(r"[a-z]+", (m.group("dash") or m.group("paren") or "").lower())
    place = (location or "").lower()
    if words and all(len(w) <= 2 or w in _PLACE_WORDS or w in place for w in words):
        return m.group("base").strip()
    return title


def dedupe(jobs: list[Job]) -> list[Job]:
    """Drop repeats.

    A Google Jobs or careers-page result is dropped when a job-board API returned
    the same company and title. A board that lists one role in several
    locations ("Doctors - AI Training - Houston", "... - Miami") is shown once,
    with the locations combined.
    """
    primary = {(norm(j.company), norm(j.title)) for j in jobs if not _is_secondary(j)}
    out: list[Job] = []
    secondary_seen: set = set()
    merged: dict = {}
    for job in jobs:
        key = (norm(job.company), norm(job.title))
        if _is_secondary(job):
            if key in primary or key in secondary_seen:
                continue
            secondary_seen.add(key)
            out.append(job)
            continue
        base_title = _strip_place(job.title, job.location)
        merge_key = (job.source, norm(job.company), norm(base_title), job.pay.label() if job.pay else "", job.status)
        kept = merged.get(merge_key)
        if kept is not None:
            kept.title = _strip_place(kept.title, kept.location)
            if job.location and job.location not in kept.location and len(kept.location) < _MAX_LOCATION:
                kept.location = f"{kept.location} / {job.location}" if kept.location else job.location
            continue
        merged[merge_key] = job
        out.append(job)
    return out


def search(
    cfg: Config,
    http: Http,
    boards_cache: dict,
    today: date,
    only: Optional[set[str]] = None,
    use_google: bool = True,
    log: Callable[[str], None] = lambda message: None,
) -> SearchResult:
    settings = cfg.settings
    cache = dict(boards_cache)
    companies = [c for c in cfg.companies if not only or norm(c.name) in only]
    page_list = [p for p in cfg.pages if not only or norm(p.get("name", "")) in only]
    jobs: list[Job] = []
    statuses: list[SourceStatus] = []
    scanned = 0

    def record(found, status, count):
        nonlocal scanned
        jobs.extend(found)
        statuses.append(status)
        scanned += count
        extra = f" ({status.note})" if status.note else ""
        log(f"{status.state:>9}  {status.name}: {status.postings} postings, {status.matches} matches{extra}")

    with ThreadPoolExecutor(max_workers=settings.workers) as pool:
        futures = {
            pool.submit(_run_company, c, http, settings, cache.get(c.name), today): c for c in companies
        }
        page_futures = {pool.submit(_run_page, p, http, settings): p for p in page_list}
        for future in as_completed(list(futures) + list(page_futures)):
            if future in futures:
                found, status, entry, count = future.result()
                if entry is not None:
                    cache[futures[future].name] = entry
            else:
                found, status, count = future.result()
            record(found, status, count)

    if use_google and not only:
        found, status, count = _run_google(cfg.google_jobs, http, settings, today)
        if status is not None:
            record(found, status, count)

    statuses.sort(key=lambda s: s.name.lower())
    return SearchResult(dedupe(jobs), statuses, scanned, cache, partial=bool(only))
