"""Public job-board APIs of applicant tracking systems (Greenhouse, Ashby, Lever, ...).

Each board type turns its API's postings into ``Job`` objects. All of these
endpoints are public, documented, and need no API key.
"""

from __future__ import annotations

import re
from typing import Optional

from .. import pay as pay_mod
from ..http import Http, HttpError
from ..models import Job
from ..util import html_to_text, iso_date


class Board:
    ats = ""

    def careers_url(self, slug: str) -> str:
        return ""

    def fetch(self, slug: str, http: Http, company: str) -> list[Job]:
        raise NotImplementedError

    def count(self, slug: str, http: Http) -> int:
        """Number of open postings; raises HttpError when the board does not exist."""
        return len(self.fetch(slug, http, ""))

    def board_name(self, slug: str, http: Http) -> Optional[str]:
        """The employer name the board reports, when the API exposes one."""
        return None

    def needs_enrich(self, job: Job) -> bool:
        return False

    def enrich(self, slug: str, http: Http, job: Job) -> bool:
        """Fetch more detail for one promising posting. Returns True if anything changed."""
        return False


def _source(board: Board, slug: str) -> str:
    return f"{board.ats}:{slug}"


class Greenhouse(Board):
    """https://developers.greenhouse.io/job-board.html"""

    ats = "greenhouse"
    api_root = "https://boards-api.greenhouse.io/v1/boards"
    site_root = "https://job-boards.greenhouse.io"

    def _api(self, slug: str) -> str:
        return f"{self.api_root}/{slug}"

    def careers_url(self, slug: str) -> str:
        return f"{self.site_root}/{slug}"

    def count(self, slug: str, http: Http) -> int:
        return len(http.get_json(f"{self._api(slug)}/jobs").get("jobs") or [])

    def board_name(self, slug: str, http: Http) -> Optional[str]:
        try:
            return (http.get_json(self._api(slug)) or {}).get("name")
        except HttpError:
            return None

    def fetch(self, slug: str, http: Http, company: str) -> list[Job]:
        data = http.get_json(f"{self._api(slug)}/jobs", params={"content": "true"})
        jobs = []
        for j in data.get("jobs") or []:
            kind = ""
            for meta in j.get("metadata") or []:
                if isinstance(meta, dict) and re.search(
                    r"employment|job\s*type|commitment|time\s*type|worker\s*type", str(meta.get("name") or ""), re.I
                ):
                    value = meta.get("value")
                    kind = ", ".join(map(str, value)) if isinstance(value, list) else str(value or "")
                    break
            jobs.append(
                Job(
                    source=_source(self, slug),
                    company=company or j.get("company_name") or slug,
                    title=(j.get("title") or "").strip(),
                    url=j.get("absolute_url") or self.careers_url(slug),
                    job_id=str(j.get("id")),
                    location=(j.get("location") or {}).get("name") or "",
                    employment_type=kind,
                    posted=iso_date(j.get("first_published") or j.get("updated_at")),
                    description=html_to_text(j.get("content")),
                )
            )
        return jobs

    def needs_enrich(self, job: Job) -> bool:
        # The list endpoint has no pay; pay-transparency ranges live on each job.
        return job.status in ("pay_unlisted", "full_time_unlisted") and job.structured_pay is None

    def enrich(self, slug: str, http: Http, job: Job) -> bool:
        data = http.get_json(f"{self._api(slug)}/jobs/{job.job_id}", params={"pay_transparency": "true"})
        pays = []
        for r in data.get("pay_input_ranges") or []:
            hint = f"{r.get('title') or ''} {r.get('blurb') or ''}".strip()
            if re.search(r"hour", hint, re.I):
                interval = "hour"
            elif re.search(r"annual|year|salary", hint, re.I):
                interval = "year"
            else:
                interval = ""
            lo, hi = (r.get("min_cents") or 0) / 100, (r.get("max_cents") or 0) / 100
            currency = r.get("currency_type") or "USD"
            raw = f"{hint + ': ' if hint else ''}{lo:,.0f}–{hi:,.0f} {currency}"
            pay = pay_mod.from_range(lo, hi, interval, currency, raw=raw)
            if pay:
                pays.append(pay)
        job.structured_pay = pay_mod.combine(pays)
        return job.structured_pay is not None


class GreenhouseEU(Greenhouse):
    """Greenhouse boards hosted in the EU (job-boards.eu.greenhouse.io)."""

    ats = "greenhouse_eu"
    api_root = "https://boards-api.eu.greenhouse.io/v1/boards"
    site_root = "https://job-boards.eu.greenhouse.io"


class Ashby(Board):
    """https://developers.ashbyhq.com/docs/public-job-posting-api"""

    ats = "ashby"

    def _api(self, slug: str) -> str:
        return f"https://api.ashbyhq.com/posting-api/job-board/{slug}"

    def careers_url(self, slug: str) -> str:
        return f"https://jobs.ashbyhq.com/{slug}"

    def count(self, slug: str, http: Http) -> int:
        return len(http.get_json(self._api(slug)).get("jobs") or [])

    def fetch(self, slug: str, http: Http, company: str) -> list[Job]:
        data = http.get_json(self._api(slug), params={"includeCompensation": "true"})
        jobs = []
        for j in data.get("jobs") or []:
            if j.get("isListed") is False:
                continue
            comp = j.get("compensation") or {}
            components = list(comp.get("summaryComponents") or [])
            for tier in comp.get("compensationTiers") or []:
                components += tier.get("components") or []
            pays = []
            for c in components:
                kind = str(c.get("compensationType") or "").lower()
                if kind.startswith("equity") or kind in ("commission", "bonus"):
                    continue
                pay = pay_mod.from_range(
                    c.get("minValue"),
                    c.get("maxValue"),
                    str(c.get("interval") or ""),
                    str(c.get("currencyCode") or "USD"),
                    raw=str(c.get("summary") or ""),
                )
                if pay:
                    pays.append(pay)
            summaries = [comp.get("compensationTierSummary"), comp.get("scrapeableCompensationSalarySummary")]
            url = j.get("jobUrl") or j.get("applyUrl") or self.careers_url(slug)
            locations = [j.get("location")] + [
                s.get("location") for s in j.get("secondaryLocations") or [] if isinstance(s, dict)
            ]
            jobs.append(
                Job(
                    source=_source(self, slug),
                    company=company or slug,
                    title=(j.get("title") or "").strip(),
                    url=url,
                    job_id=str(j.get("id") or url.rstrip("/").rsplit("/", 1)[-1]),
                    location=" / ".join(dict.fromkeys(str(x) for x in locations if x)),
                    remote=j.get("isRemote"),
                    employment_type=str(j.get("employmentType") or ""),
                    posted=iso_date(j.get("publishedAt")),
                    description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
                    structured_pay=pay_mod.combine(pays),
                    pay_text=" · ".join(dict.fromkeys(s for s in summaries if s)),
                )
            )
        return jobs


class Lever(Board):
    """https://github.com/lever/postings-api"""

    ats = "lever"
    api_root = "https://api.lever.co/v0/postings"
    site_root = "https://jobs.lever.co"

    def careers_url(self, slug: str) -> str:
        return f"{self.site_root}/{slug}"

    def _get(self, slug: str, http: Http, **params) -> list:
        url = f"{self.api_root}/{slug}"
        data = http.get_json(url, params={"mode": "json", **params})
        if not isinstance(data, list):
            raise HttpError(200, url, "unexpected response")
        return data

    def count(self, slug: str, http: Http) -> int:
        return len(self._get(slug, http, limit=1))

    def fetch(self, slug: str, http: Http, company: str) -> list[Job]:
        jobs = []
        for p in self._get(slug, http):
            cats = p.get("categories") or {}
            salary = p.get("salaryRange") or {}
            structured = None
            if salary:
                structured = pay_mod.from_range(
                    salary.get("min"),
                    salary.get("max"),
                    str(salary.get("interval") or ""),
                    str(salary.get("currency") or "USD"),
                    raw=f"{salary.get('min')}–{salary.get('max')} {salary.get('currency') or ''} {salary.get('interval') or ''}".strip(),
                )
            sections = [p.get("descriptionPlain") or html_to_text(p.get("description"))]
            for item in p.get("lists") or []:
                sections.append(f"{item.get('text') or ''}\n{html_to_text(item.get('content'))}")
            sections.append(p.get("additionalPlain") or html_to_text(p.get("additional")))
            workplace = str(p.get("workplaceType") or "").lower()
            jobs.append(
                Job(
                    source=_source(self, slug),
                    company=company or slug,
                    title=(p.get("text") or "").strip(),
                    url=p.get("hostedUrl") or p.get("applyUrl") or self.careers_url(slug),
                    job_id=str(p.get("id") or ""),
                    location=cats.get("location") or ", ".join(cats.get("allLocations") or []),
                    remote=(workplace == "remote") if workplace not in ("", "unspecified") else None,
                    employment_type=str(cats.get("commitment") or ""),
                    posted=iso_date(p.get("createdAt")),
                    description="\n".join(s for s in sections if s and s.strip()),
                    structured_pay=structured,
                    pay_text=p.get("salaryDescriptionPlain") or html_to_text(p.get("salaryDescription")),
                )
            )
        return jobs


class LeverEU(Lever):
    ats = "lever_eu"
    api_root = "https://api.eu.lever.co/v0/postings"
    site_root = "https://jobs.eu.lever.co"


class Workable(Board):
    ats = "workable"

    def careers_url(self, slug: str) -> str:
        return f"https://apply.workable.com/{slug}/"

    def _list(self, slug: str, http: Http, details: bool = True) -> dict:
        params = {"details": "true"} if details else None
        return http.get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}", params=params)

    def count(self, slug: str, http: Http) -> int:
        return len(self._list(slug, http, details=False).get("jobs") or [])

    def board_name(self, slug: str, http: Http) -> Optional[str]:
        try:
            return self._list(slug, http, details=False).get("name")
        except HttpError:
            return None

    def fetch(self, slug: str, http: Http, company: str) -> list[Job]:
        data = self._list(slug, http)
        jobs = []
        for j in data.get("jobs") or []:
            code = str(j.get("shortcode") or j.get("code") or "")
            fallback = f"https://apply.workable.com/{slug}/j/{code}/" if code else self.careers_url(slug)
            jobs.append(
                Job(
                    source=_source(self, slug),
                    company=company or data.get("name") or slug,
                    title=(j.get("title") or "").strip(),
                    url=j.get("url") or j.get("shortlink") or fallback,
                    job_id=code or str(j.get("url") or j.get("title")),
                    location=", ".join(str(x) for x in (j.get("city"), j.get("state"), j.get("country")) if x),
                    remote=j.get("telecommuting"),
                    employment_type=str(j.get("employment_type") or ""),
                    posted=iso_date(j.get("published_on") or j.get("created_at")),
                    description=html_to_text(j.get("description")),
                )
            )
        return jobs

    def needs_enrich(self, job: Job) -> bool:
        return not job.description and job.title_score >= 2

    def enrich(self, slug: str, http: Http, job: Job) -> bool:
        data = http.get_json(f"https://apply.workable.com/api/v2/accounts/{slug}/jobs/{job.job_id}")
        parts = [html_to_text(data.get(k)) for k in ("description", "requirements", "benefits")]
        job.description = "\n".join(p for p in parts if p)
        return bool(job.description)


class Recruitee(Board):
    ats = "recruitee"

    def _api(self, slug: str) -> str:
        return f"https://{slug}.recruitee.com/api/offers/"

    def careers_url(self, slug: str) -> str:
        return f"https://{slug}.recruitee.com/"

    def count(self, slug: str, http: Http) -> int:
        return len(http.get_json(self._api(slug)).get("offers") or [])

    def fetch(self, slug: str, http: Http, company: str) -> list[Job]:
        data = http.get_json(self._api(slug))
        jobs = []
        for o in data.get("offers") or []:
            salary = o.get("salary") if isinstance(o.get("salary"), dict) else {}
            structured = None
            if salary:
                structured = pay_mod.from_range(
                    salary.get("min"),
                    salary.get("max"),
                    str(salary.get("period") or ""),
                    str(salary.get("currency") or "USD"),
                    raw=f"{salary.get('min')}–{salary.get('max')} {salary.get('currency') or ''} per {salary.get('period') or '?'}",
                )
            jobs.append(
                Job(
                    source=_source(self, slug),
                    company=company or o.get("company_name") or slug,
                    title=(o.get("title") or "").strip(),
                    url=o.get("careers_url") or o.get("careers_apply_url") or self.careers_url(slug),
                    job_id=str(o.get("id") or o.get("slug") or ""),
                    location=o.get("location") or ", ".join(str(x) for x in (o.get("city"), o.get("country")) if x),
                    remote=o.get("remote"),
                    employment_type=str(o.get("employment_type_code") or ""),
                    posted=iso_date(o.get("published_at") or o.get("created_at")),
                    description=html_to_text(f"{o.get('description') or ''}\n{o.get('requirements') or ''}"),
                    structured_pay=structured,
                )
            )
        return jobs


class SmartRecruiters(Board):
    """https://developers.smartrecruiters.com/docs/posting-api"""

    ats = "smartrecruiters"

    def _api(self, slug: str) -> str:
        return f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"

    def careers_url(self, slug: str) -> str:
        return f"https://jobs.smartrecruiters.com/{slug}"

    def count(self, slug: str, http: Http) -> int:
        return int(http.get_json(self._api(slug), params={"limit": 1}).get("totalFound") or 0)

    def board_name(self, slug: str, http: Http) -> Optional[str]:
        try:
            content = http.get_json(self._api(slug), params={"limit": 1}).get("content") or []
        except HttpError:
            return None
        return ((content[0] or {}).get("company") or {}).get("name") if content else None

    def fetch(self, slug: str, http: Http, company: str) -> list[Job]:
        jobs: list[Job] = []
        offset = 0
        while offset < 1000:
            data = http.get_json(self._api(slug), params={"limit": 100, "offset": offset})
            content = data.get("content") or []
            for p in content:
                loc = p.get("location") or {}
                pid = str(p.get("id") or "")
                jobs.append(
                    Job(
                        source=_source(self, slug),
                        company=company or (p.get("company") or {}).get("name") or slug,
                        title=(p.get("name") or "").strip(),
                        url=f"https://jobs.smartrecruiters.com/{slug}/{pid}",
                        job_id=pid,
                        location=loc.get("fullLocation")
                        or ", ".join(str(x) for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x),
                        remote=loc.get("remote"),
                        employment_type=str((p.get("typeOfEmployment") or {}).get("label") or ""),
                        posted=iso_date(p.get("releasedDate")),
                    )
                )
            offset += len(content)
            if not content or offset >= int(data.get("totalFound") or 0):
                break
        return jobs

    def needs_enrich(self, job: Job) -> bool:
        return not job.description and job.title_score >= 2

    def enrich(self, slug: str, http: Http, job: Job) -> bool:
        data = http.get_json(f"{self._api(slug)}/{job.job_id}")
        sections = (data.get("jobAd") or {}).get("sections") or {}
        parts = [
            html_to_text((sections.get(k) or {}).get("text"))
            for k in ("jobDescription", "qualifications", "additionalInformation")
        ]
        job.description = "\n".join(p for p in parts if p)
        if data.get("postingUrl"):
            job.url = data["postingUrl"]
        return bool(job.description)


BOARDS: dict[str, Board] = {
    b.ats: b
    for b in (Greenhouse(), GreenhouseEU(), Ashby(), Lever(), LeverEU(), Workable(), Recruitee(), SmartRecruiters())
}
# Order tried when a company's board is detected automatically.
DETECT_ORDER = ("greenhouse", "ashby", "lever", "workable", "smartrecruiters", "recruitee", "greenhouse_eu", "lever_eu")
