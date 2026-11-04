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
    "europe", "emea", "latam", "apac", "brazil", "mexico", "spain", "portugal", "france", "germany", "italy",
    "ireland", "netherlands", "belgium", "switzerland", "austria", "poland", "sweden", "norway", "denmark",
    "finland", "india", "japan", "korea", "singapore", "philippines", "australia", "zealand", "argentina",
    "chile", "colombia", "israel", "egypt", "nigeria", "kenya", "africa", "south", "new",
}
_MAX_LOCATION = 300
_ROLE_SPLIT_RE = re.compile(r"\s[-–—|]\s")

# "Albuquerque, US", "Belfast, UK", "Brasília, Brazil", "Colorado Springs, United States"
_PLACE_PART = r"[^\W\d_](?:[^\W\d_]|[.'’ -])*"
_CITY_REGION_RE = re.compile(rf"^{_PLACE_PART},\s*{_PLACE_PART}$")


def _strip_place(title: str, location: str) -> str:
    """Drop a trailing place from a title.

    "Doctors - AI Training - Houston" -> "Doctors - AI Training" when the job is
    in Houston, and "Cardiologists (Freelance) - Austin, US" -> "Cardiologists
    (Freelance)". Specialties ("Physician - Emergency Medicine") are kept.
    """
    m = _SUFFIX_RE.match(title or "")
    if not m:
        return title
    segment = (m.group("dash") or m.group("paren") or "").strip()
    words = re.findall(r"[a-z]+", segment.lower())
    place = (location or "").lower()
    if words and all(len(w) <= 2 or w in _PLACE_WORDS or w in place for w in words):
        return m.group("base").strip()
    if (
        _CITY_REGION_RE.match(segment)
        and all(part.strip()[:1].isupper() for part in segment.split(","))
        and matching.title_score(segment)[0] == 0
    ):
        return m.group("base").strip()
    return title


def _program(title: str) -> tuple[str, str] | None:
    """("Cardiologist", "AI Research Participants - United States (Remote)") from
    "Cardiologist - AI Research Participants - United States (Remote)"."""
    parts = _ROLE_SPLIT_RE.split(title or "", maxsplit=1)
    if len(parts) == 2 and len(parts[1].split()) >= 2:
        return parts[0].strip(), parts[1].strip()
    return None


def _merge_programs(jobs: list[Job]) -> list[Job]:
    """Collapse one program posted under many role names into one row.

    Boards such as Prolific's post "Cardiologist - AI Research Participants",
    "Dermatologist - AI Research Participants", ... (dozens of titles, same
    pay). Three or more postings from one board with the same program name,
    pay and status become a single row that names a few of the roles.
    """
    groups: dict[tuple, list[Job]] = {}
    for job in jobs:
        program = None if _is_secondary(job) else _program(job.title)
        if program:
            key = (job.source, norm(job.company), norm(program[1]), job.pay.label() if job.pay else "", job.status)
            groups.setdefault(key, []).append(job)
    dropped: set[int] = set()
    for members in groups.values():
        if len(members) < 3:
            continue
        keep, rest = members[0], members[1:]
        heads = [_program(m.title)[0] for m in members]
        program = _program(keep.title)[1]
        keep.title = f"{program} — {len(members)} roles incl. {', '.join(heads[:3])}"
        for other in rest:
            keep.listings += other.listings
            if other.location and other.location not in keep.location and len(keep.location) < _MAX_LOCATION:
                keep.location = f"{keep.location} / {other.location}" if keep.location else other.location
            dropped.add(id(other))
    return [j for j in jobs if id(j) not in dropped]


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
    # Keep the oldest posting of a merged group, so its "new" marker stays stable between runs.
    jobs = sorted(jobs, key=lambda j: (j.source, len(j.job_id), j.job_id))
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
            kept.listings += 1
            if job.location and job.location not in kept.location and len(kept.location) < _MAX_LOCATION:
                kept.location = f"{kept.location} / {job.location}" if kept.location else job.location
            continue
        merged[merge_key] = job
        out.append(job)
    return _merge_programs(out)


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
