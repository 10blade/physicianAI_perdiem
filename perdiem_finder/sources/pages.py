"""Best-effort watcher for careers pages that have no public job API.

Platforms such as Turing, micro1 or OpenTrain publish listings only as web
pages. For each configured page this module:

1. checks robots.txt and skips the page if crawling is disallowed;
2. reads schema.org ``JobPosting`` data embedded in the page, if any;
3. follows up to ``max_links`` links whose text looks like a physician role,
   and reads each linked page's ``JobPosting`` data (title, pay, type, date),
   falling back to the page text.

Pages that render their listings with JavaScript yield nothing; the run's
source table then shows "0 postings" so you know to check that page by hand.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from .. import matching
from .. import pay as pay_mod
from ..http import USER_AGENT, Http, HttpError
from ..models import Job, Pay
from ..util import html_to_text, iso_date

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
_SKIP_TEXT = {"script", "style", "noscript", "template", "svg"}
_BLOCK = {"p", "div", "li", "ul", "ol", "tr", "td", "th", "section", "article", "header", "footer", "h1", "h2",
          "h3", "h4", "h5", "h6", "br", "table", "dd", "dt"}
_AUTO_CLOSE = {"p", "li", "dt", "dd", "tr", "td", "th", "option"}
_URL_HINT = re.compile(
    r"physician|doctor|\bmd\b|-md-|-md$|m-d|medical|medicine|clinic|clinician|radiolog|patholog|cardiolog|dermatolog"
    r"|oncolog|psychiatr|neurolog|pediatric|surgeon|hospitalist|internal-medicine|family-medicine|emergency-medicine",
    re.I,
)
_CONTEXT_LIMIT = 600


class _Node:
    __slots__ = ("tag", "attrs", "children", "parent", "_text")

    def __init__(self, tag: str, attrs: dict, parent: Optional["_Node"]):
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children: list = []
        self._text: Optional[str] = None

    def text(self) -> str:
        """Visible text of this element (computed bottom-up without recursion, then cached)."""
        stack: list[tuple[_Node, bool]] = [(self, False)]
        while stack:
            node, children_done = stack.pop()
            if node._text is not None:
                continue
            if not children_done:
                stack.append((node, True))
                for child in node.children:
                    if isinstance(child, _Node) and child.tag not in _SKIP_TEXT and child._text is None:
                        stack.append((child, False))
                continue
            parts: list[str] = []
            for child in node.children:
                if isinstance(child, str):
                    parts.append(child)
                elif child.tag not in _SKIP_TEXT:
                    sep = "\n" if child.tag in _BLOCK else " "
                    parts.append(sep + (child._text or "") + sep)
            text = re.sub(r"[ \t\r\f\v ]+", " ", "".join(parts))
            node._text = re.sub(r"\s*\n\s*", "\n", text).strip()
        return self._text or ""


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {}, None)
        self.stack = [self.root]
        self.links: list[_Node] = []
        self.ld_json: list[str] = []
        self.h1: list[_Node] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in _AUTO_CLOSE and self.stack[-1].tag == tag:
            self.stack.pop()  # an unclosed <li> or <p> ends where the next one starts
        node = _Node(tag, {k: (v or "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag == "a" and node.attrs.get("href"):
            self.links.append(node)
        elif tag == "h1":
            self.h1.append(node)
        elif tag == "title":
            self._in_title = True
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(_Node(tag, {k: (v or "") for k, v in attrs}, self.stack[-1]))

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        node = self.stack[-1]
        if node.tag == "script" and "ld+json" in node.attrs.get("type", "").lower():
            self.ld_json.append(data)
        if self._in_title:
            self.title += data
        node.children.append(data)


def _parse(html: str) -> _Tree:
    tree = _Tree()
    try:
        tree.feed(html)
        tree.close()
    except Exception:  # malformed markup: keep whatever was parsed
        pass
    return tree


def _job_postings(tree: _Tree) -> list[dict]:
    found = []
    for raw in tree.ld_json:
        try:
            data = json.loads(raw.strip())
        except ValueError:
            continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                kind = item.get("@type")
                if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                    found.append(item)
                for key in ("@graph", "itemListElement", "item", "mainEntity"):
                    if key in item:
                        stack.append(item[key])
    return found


def _ld_pay(posting: dict) -> Optional[Pay]:
    salary = posting.get("baseSalary") or posting.get("estimatedSalary")
    if isinstance(salary, list):
        salary = salary[0] if salary else None
    if not isinstance(salary, dict):
        return None
    currency = str(salary.get("currency") or "USD")
    value = salary.get("value")
    if isinstance(value, dict):
        lo = value.get("minValue", value.get("value"))
        hi = value.get("maxValue", value.get("value"))
        unit = str(value.get("unitText") or salary.get("unitText") or "")
    else:
        lo = hi = value
        unit = str(salary.get("unitText") or "")
    return pay_mod.from_range(lo, hi, unit, currency, raw=f"{lo}–{hi} {currency} per {unit.lower() or '?'}")


def _ld_location(posting: dict) -> tuple[str, Optional[bool]]:
    remote = True if str(posting.get("jobLocationType") or "").upper() == "TELECOMMUTE" else None
    places = posting.get("jobLocation") or []
    if isinstance(places, dict):
        places = [places]
    names = []
    for place in places:
        address = (place or {}).get("address") or {}
        if isinstance(address, dict):
            country = address.get("addressCountry")
            if isinstance(country, dict):
                country = country.get("name")
            parts = [address.get("addressLocality"), address.get("addressRegion"), country]
            names.append(", ".join(str(p) for p in parts if p))
        elif address:
            names.append(str(address))
    if not names:
        requirements = posting.get("applicantLocationRequirements") or []
        if isinstance(requirements, dict):
            requirements = [requirements]
        names = [str(r.get("name")) for r in requirements if isinstance(r, dict) and r.get("name")]
    location = " / ".join(dict.fromkeys(n for n in names if n))
    if remote and "remote" not in location.lower():
        location = f"Remote{' (' + location + ')' if location else ''}"
    return location, remote


def _from_posting(posting: dict, url: str, page_name: str) -> Job:
    org = posting.get("hiringOrganization") or {}
    company = org.get("name") if isinstance(org, dict) else str(org or "")
    kind = posting.get("employmentType") or ""
    if isinstance(kind, list):
        kind = ", ".join(str(k) for k in kind)
    location, remote = _ld_location(posting)
    job_url = str(posting.get("url") or url)
    return Job(
        source=f"page:{urlparse(url).netloc}",
        company=str(company or page_name),
        title=html_to_text(posting.get("title") or ""),
        url=job_url,
        job_id=job_url,
        location=location,
        remote=remote,
        employment_type=str(kind),
        posted=iso_date(posting.get("datePosted")),
        description=html_to_text(posting.get("description") or ""),
        structured_pay=_ld_pay(posting),
    )


def _context(node: _Node) -> str:
    """Text of the largest block around a link that is still small enough to be one listing card."""
    best = node.text()
    current = node.parent
    while current is not None and current.tag != "root":
        text = current.text()
        if len(text) > _CONTEXT_LIMIT:
            break
        best = text
        current = current.parent
    return best


class _Robots:
    """robots.txt rules, following Python's urllib.robotparser conventions."""

    def __init__(self, http: Http):
        self.http = http
        self.cache: dict[str, object] = {}

    def allowed(self, url: str) -> bool:
        parts = urlparse(url)
        root = f"{parts.scheme}://{parts.netloc}"
        if root not in self.cache:
            try:
                text = self.http.get_text(f"{root}/robots.txt")
            except HttpError as exc:
                # 401/403 means "keep out"; a missing robots.txt means crawling is allowed.
                self.cache[root] = "deny" if exc.status in (401, 403) else None
            else:
                parser = RobotFileParser()
                parser.parse(text.splitlines())
                self.cache[root] = parser
        rules = self.cache[root]
        if rules == "deny":
            return False
        return rules is None or rules.can_fetch(USER_AGENT, url)


def _same_site(url: str, base: str) -> bool:
    host, base_host = urlparse(url).netloc.lower(), urlparse(base).netloc.lower()
    strip = lambda h: h[4:] if h.startswith("www.") else h  # noqa: E731
    host, base_host = strip(host), strip(base_host)
    return host == base_host or host.endswith("." + base_host) or base_host.endswith("." + host)


def fetch(page: dict, http: Http) -> tuple[list[Job], str]:
    """Jobs found on one configured page, plus a short note for the source table."""
    url = page["url"]
    name = page.get("name") or urlparse(url).netloc
    max_links = int(page.get("max_links") or 25)
    robots = _Robots(http)
    if not robots.allowed(url):
        return [], "skipped: robots.txt disallows this page"
    tree = _parse(http.get_text(url))

    jobs: dict[str, Job] = {}
    for posting in _job_postings(tree):
        job = _from_posting(posting, url, name)
        jobs.setdefault(job.url, job)

    candidates: list[tuple[str, str, str]] = []
    seen_urls = {url.rstrip("/")}
    for link in tree.links:
        href = urljoin(url, link.attrs["href"]).split("#", 1)[0]
        if not href.startswith("http") or href.rstrip("/") in seen_urls or not _same_site(href, url):
            continue
        text = link.text() or link.attrs.get("title", "") or link.attrs.get("aria-label", "")
        score, _ = matching.title_score(text)
        if score >= 3 or (score >= 2 and _URL_HINT.search(urlparse(href).path)):
            seen_urls.add(href.rstrip("/"))
            candidates.append((text.strip()[:200], href, _context(link)))

    followed = 0
    for index, (text, href, context) in enumerate(candidates):
        if href in jobs:
            continue
        detail = None
        # Open the first max_links listings; list the rest from the text around their link.
        if index < max_links and robots.allowed(href):
            try:
                detail = _parse(http.get_text(href))
            except HttpError:
                detail = None
            followed += 1
        postings = _job_postings(detail) if detail else []
        if postings:
            for posting in postings:
                job = _from_posting(posting, href, name)
                jobs.setdefault(job.url, job)
            continue
        title = text
        description = context
        if detail is not None:
            heading = detail.h1[0].text() if detail.h1 else ""
            title = heading or text
            description = f"{context}\n{detail.root.text()[:20000]}"
        jobs.setdefault(
            href,
            Job(
                source=f"page:{urlparse(url).netloc}",
                company=name,
                title=title.strip()[:200],
                url=href,
                job_id=href,
                description=description,
            ),
        )
    note = f"{len(candidates)} physician-looking links, {followed} opened"
    if not candidates and not jobs:
        note = "no job links found (the page may need JavaScript); check it by hand"
    return list(jobs.values()), note
