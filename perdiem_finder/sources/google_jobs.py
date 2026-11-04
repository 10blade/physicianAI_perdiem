"""Google Jobs results through SerpApi (optional; needs a SERPAPI_KEY).

Google Jobs collects postings from LinkedIn, Indeed, ZipRecruiter and company
sites, so it reaches platforms such as Mercor and micro1 that have no public
job API. https://serpapi.com/google-jobs-api
"""

from __future__ import annotations

import hashlib
from datetime import date

from ..http import Http, HttpError
from ..models import Job
from ..util import norm, relative_date

ENDPOINT = "https://serpapi.com/search.json"


def _to_job(result: dict, today: date) -> Job:
    ext = result.get("detected_extensions") or {}
    company = str(result.get("company_name") or "")
    title = str(result.get("title") or "")
    location = str(result.get("location") or "")
    link = next((o.get("link") for o in result.get("apply_options") or [] if o.get("link")), "")
    highlights = []
    for block in result.get("job_highlights") or []:
        items = block.get("items") or []
        highlights.append(f"{block.get('title') or ''}: " + " ".join(str(i) for i in items))
    digest = hashlib.sha1(f"{norm(company)}|{norm(title)}|{norm(location)}".encode()).hexdigest()[:16]
    work_from_home = ext.get("work_from_home")
    return Job(
        source="google_jobs",
        company=company,
        title=title,
        url=link or str(result.get("share_link") or ""),
        job_id=digest,
        location=location,
        remote=True if work_from_home else None,
        employment_type=str(ext.get("schedule_type") or ""),
        posted=relative_date(str(ext.get("posted_at") or ""), today),
        description="\n".join([str(result.get("description") or ""), *highlights]),
        pay_text=str(ext.get("salary") or ""),
        requires_ai_context=True,
        via=str(result.get("via") or "").removeprefix("via ").strip(),
    )


def fetch(cfg: dict, http: Http, api_key: str, today: date) -> tuple[list[Job], list[str]]:
    """Run every configured query. Returns (jobs, error messages)."""
    jobs: dict[str, Job] = {}
    errors: list[str] = []
    pages = max(1, int(cfg.get("pages_per_query") or 1))
    for query in cfg.get("queries") or []:
        params = {"engine": "google_jobs", "q": query, "hl": "en", "gl": "us", "api_key": api_key}
        if cfg.get("location"):
            params["location"] = cfg["location"]
        for _ in range(pages):
            try:
                data = http.get_json(ENDPOINT, params=params)
            except HttpError as exc:
                errors.append(f"{query!r}: {exc}")
                break
            if data.get("error"):
                if "hasn't returned any results" not in str(data["error"]):
                    errors.append(f"{query!r}: {data['error']}")
                break
            for result in data.get("jobs_results") or []:
                job = _to_job(result, today)
                jobs.setdefault(job.key, job)
            token = (data.get("serpapi_pagination") or {}).get("next_page_token")
            if not token:
                break
            params = {**params, "next_page_token": token}
    return list(jobs.values()), errors
