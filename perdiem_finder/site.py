"""The shareable web page: docs/index.html, rebuilt from results/latest.json after every run.

Everything is rendered on the server side, so the page reads fine without
JavaScript; a small script adds search, specialty and pay filters, sorting and
a copy-link button. All text from job postings is HTML-escaped, and only
http(s) links are kept.
"""

from __future__ import annotations

import html
import json
import math
import re
from datetime import date, datetime
from pathlib import Path

from .config import Config

SPECIALTIES = [
    ("Anesthesiology", r"an(?:a)?esth"),
    ("Cardiology", r"cardi"),
    ("Dermatology", r"dermat"),
    ("Emergency Medicine", r"emergency"),
    ("Endocrinology", r"endocrin"),
    ("Family & Primary Care", r"family|primary care|general practi"),
    ("Gastroenterology", r"gastro|hepatolog"),
    ("Hematology & Oncology", r"h(?:a)?emat|oncolog"),
    ("Hospital & Internal Medicine", r"hospitalist|hospital medicine|internal medicine|internist|inpatient"),
    ("Infectious Disease", r"infectious"),
    ("Nephrology", r"nephrol"),
    ("Neurology", r"neurolog"),
    ("OB/GYN", r"obstet|gyn"),
    ("Ophthalmology", r"ophthalm"),
    ("Pathology", r"patholog"),
    ("Pediatrics", r"p(?:a)?ediatr|neonat"),
    ("Psychiatry", r"psychiatr"),
    ("Pulmonary & Critical Care", r"pulmon|critical care|intensiv"),
    ("Radiology", r"radiolog"),
    ("Rheumatology", r"rheumat"),
    ("Surgery", r"surg"),
    ("Urology", r"urolog"),
]
GENERAL = "Any specialty"
_SPEC_RE = [(label, re.compile(rx, re.I)) for label, rx in SPECIALTIES]
_KIND = {
    "contractor": "Contract", "contract": "Contract", "part time": "Part-time", "parttime": "Part-time",
    "part-time": "Part-time", "full time": "Full-time", "fulltime": "Full-time", "full-time": "Full-time",
    "temporary": "Temporary", "freelance": "Freelance", "per diem": "Per diem", "flexible": "Flexible hours",
    "intern": "", "other": "",
}


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def safe_url(url) -> str:
    url = str(url or "").strip()
    return url if re.match(r"^https?://", url, re.I) else ""


def slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")


def specialties(title: str) -> list[str]:
    found = [label for label, rx in _SPEC_RE if rx.search(title or "")]
    return found or [GENERAL]


def kind_label(raw: str) -> str:
    out = []
    for part in re.split(r"[,/]", str(raw or "").replace("_", " ")):
        key = part.strip().lower()
        if not key:
            continue
        label = _KIND.get(key, part.strip().capitalize())
        if label and label not in out:
            out.append(label)
    return " · ".join(out)


def _day(value: str, ref: date, verb: str) -> str:
    try:
        d = date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return ""
    text = f"{d:%b} {d.day}" + (f", {d.year}" if d.year != ref.year else "")
    return f"{verb} {text}"


def _money(value: float) -> str:
    return f"${value:,.0f}"


def _top(job: dict) -> float:
    values = [v for v in (job.get("pay_min_hourly"), job.get("pay_max_hourly")) if v is not None]
    return max(values) if values else 0.0


def _bar(job: dict, scale: float, threshold: float) -> str:
    lo, hi = job.get("pay_min_hourly"), job.get("pay_max_hourly")
    if lo is None and hi is None:
        return ""
    pct = lambda v: max(0.0, min(100.0, v / scale * 100))  # noqa: E731
    if lo is not None and hi is not None:
        left, width, cls = pct(lo), max(pct(hi) - pct(lo), 1.6), ""
    elif lo is not None:  # "$130+/hr"
        left, width, cls = pct(lo), min(14.0, 100 - pct(lo)), " open-hi"
    else:  # "up to $200/hr"
        left, width, cls = 0.0, pct(hi), " open-lo"
    width = min(width, 100 - left)
    return (
        f'<div class="bar" aria-hidden="true"><i class="fill{cls}" style="left:{left:.1f}%;width:{width:.1f}%"></i>'
        f'<b class="mark" style="left:{pct(threshold):.1f}%"></b></div>'
    )


def _row(job: dict, ref: date, scale: float, threshold: float, with_pay: bool) -> str:
    title, url = job.get("title") or "(untitled)", safe_url(job.get("url"))
    link = (
        f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(title)}</a>' if url else esc(title)
    )
    location = str(job.get("location") or ("Remote" if job.get("remote") else ""))
    short_location = location if len(location) <= 60 else location[:58].rstrip(" /,") + "…"
    listings = int(job.get("listings") or 1)
    meta = [f'<span class="co">{esc(job.get("company"))}</span>']
    kind = kind_label(job.get("employment_type") or "")
    if kind:
        meta.append(f"<span>{esc(kind)}</span>")
    if short_location:
        meta.append(f'<span title="{esc(location)}">{esc(short_location)}</span>')
    if listings > 1:
        meta.append(f"<span>{listings} listings</span>")
    when = _day(job.get("posted") or "", ref, "Posted") or _day(job.get("first_seen") or "", ref, "Found")
    if when:
        meta.append(f"<span>{esc(when)}</span>")
    new = bool(job.get("new"))
    specs = specialties(title)
    search = " ".join(str(job.get(k) or "") for k in ("title", "company", "location", "employment_type"))
    lo = job.get("pay_min_hourly")
    attrs = (
        f'data-top="{_top(job):.0f}" data-lo="{(lo or 0):.0f}" data-new="{int(new)}" '
        f'data-date="{esc((job.get("posted") or job.get("first_seen") or "")[:10])}" '
        f'data-spec="{esc(" ".join(slug(s) for s in specs))}" data-search="{esc(search.lower())}"'
    )
    badge = '<span class="new">New</span>' if new else ""
    pay = ""
    if with_pay:
        pay = f'<div class="pay"><span class="rate">{esc(job.get("pay"))}</span>{_bar(job, scale, threshold)}</div>'
    return (
        f'<li class="row{"" if with_pay else " nopay"}" {attrs}>'
        f'<div class="what"><h3>{badge}{link}</h3><p class="meta">{"".join(meta)}</p></div>{pay}</li>'
    )


CSS = r"""
/* Layout: one reading column. Header, then a filter bar, then listing rows; each row's pay range
   sits on one shared $/hr scale with the pay bar marked, the way a lab value sits against its reference range. */
:root {
  --paper: #f3f6f4;      /* page ground, hospital linen */
  --sheet: #ffffff;      /* controls and raised surfaces */
  --ink: #12201c;        /* primary text */
  --ink-2: #51625b;      /* secondary text */
  --rule: #d8e1dd;       /* hairlines and bar track */
  --scrub: #0a6a4d;      /* accent: links, active chips, pay bars */
  --scrub-wash: #e2f0ea; /* accent background */
  --fresh: #a8401a;      /* "new" marker */
  --fresh-wash: #fbe8df;
  --display: "Source Serif 4", Georgia, "Times New Roman", serif;
  --body: "Public Sans", system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --step--1: 0.875rem; --step-0: 1rem; --step-1: 1.25rem; --step-2: 1.75rem; --step-3: clamp(2rem, 5vw, 3rem);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --paper: #0c1311; --sheet: #131c19; --ink: #e3ebe7; --ink-2: #9cb0a8; --rule: #263430;
    --scrub: #5ccaa0; --scrub-wash: #15302a; --fresh: #f29467; --fresh-wash: #3a2017; color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --paper: #0c1311; --sheet: #131c19; --ink: #e3ebe7; --ink-2: #9cb0a8; --rule: #263430;
  --scrub: #5ccaa0; --scrub-wash: #15302a; --fresh: #f29467; --fresh-wash: #3a2017; color-scheme: dark;
}
*, *::before, *::after { box-sizing: border-box; }
:root { padding-top: env(safe-area-inset-top, 0px); padding-bottom: env(safe-area-inset-bottom, 0px); }
[hidden] { display: none !important; }
body { margin: 0; background: var(--paper); color: var(--ink); font: 400 var(--step-0)/1.55 var(--body); }
body::before { content: ""; position: fixed; inset: 0 0 auto; height: env(safe-area-inset-top, 0px); z-index: 10; background: var(--paper); }
a { color: var(--scrub); text-underline-offset: 0.18em; }
a:hover { text-decoration-thickness: 2px; }
:focus-visible { outline: 3px solid var(--scrub); outline-offset: 2px; border-radius: 4px; }
.wrap { max-width: 64rem; margin: 0 auto; padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
header.top { padding-block: 2.5rem 1.5rem; display: grid; gap: 0.9rem; }
.eyebrow { margin: 0; font-size: var(--step--1); letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink-2); font-weight: 600; }
h1 { margin: 0; font: 700 var(--step-3)/1.08 var(--display); letter-spacing: -0.01em; text-wrap: balance; }
.lede { margin: 0; max-width: 62ch; font-size: var(--step-1); line-height: 1.45; color: var(--ink); }
.facts { display: flex; flex-wrap: wrap; gap: 0.5rem 1.75rem; margin: 0.25rem 0 0; padding: 0; list-style: none; color: var(--ink-2); }
.facts strong { color: var(--ink); font-size: var(--step-1); font-variant-numeric: tabular-nums; margin-right: 0.3rem; }
.share { display: flex; flex-wrap: wrap; align-items: center; gap: 0.6rem; }
button, select, input { font: inherit; color: inherit; }
.btn { border: 1px solid var(--scrub); background: var(--scrub); color: var(--sheet); border-radius: 8px; padding: 0.45rem 0.95rem; font-weight: 600; cursor: pointer; }
.btn:hover { filter: brightness(1.08); }
.btn.quiet { background: transparent; color: var(--scrub); }
#share-status { color: var(--ink-2); font-size: var(--step--1); }
#share-url { width: min(100%, 26rem); padding: 0.4rem 0.6rem; border: 1px solid var(--rule); border-radius: 6px; background: var(--sheet); }
.bar-wrap { border-block: 1px solid var(--rule); background: var(--paper); }
@media (min-width: 46rem) { .bar-wrap { position: sticky; top: env(safe-area-inset-top, 0px); z-index: 5; } }
.controls { padding-block: 0.85rem; display: grid; gap: 0.7rem; }
.line { display: flex; flex-wrap: wrap; gap: 0.6rem 1rem; align-items: center; }
.search { flex: 1 1 16rem; min-width: 0; }
.search input { width: 100%; padding: 0.55rem 0.8rem; border: 1px solid var(--rule); border-radius: 8px; background: var(--sheet); }
.seg { display: inline-flex; border: 1px solid var(--rule); border-radius: 8px; overflow: hidden; background: var(--sheet); }
.seg input { position: absolute; opacity: 0; pointer-events: none; }
.seg label { padding: 0.45rem 0.75rem; cursor: pointer; font-variant-numeric: tabular-nums; border-left: 1px solid var(--rule); }
.seg label:first-of-type { border-left: 0; }
.seg input:checked + label { background: var(--scrub); color: var(--sheet); font-weight: 600; }
.seg input:focus-visible + label { outline: 3px solid var(--scrub); outline-offset: -3px; }
.toggle { display: inline-flex; gap: 0.45rem; align-items: center; cursor: pointer; }
.toggle input { width: 1.1rem; height: 1.1rem; accent-color: var(--scrub); }
select { padding: 0.45rem 0.6rem; border: 1px solid var(--rule); border-radius: 8px; background: var(--sheet); }
.chips { display: flex; flex-wrap: wrap; gap: 0.4rem; }
@media (max-width: 46rem) { .chips { flex-wrap: nowrap; overflow-x: auto; padding-bottom: 0.25rem; scrollbar-width: thin; } }
.chip { flex: 0 0 auto; border: 1px solid var(--rule); background: var(--sheet); border-radius: 999px; padding: 0.3rem 0.8rem; font-size: var(--step--1); cursor: pointer; white-space: nowrap; }
.chip span { color: var(--ink-2); margin-left: 0.3rem; font-variant-numeric: tabular-nums; }
.chip[aria-pressed="true"] { background: var(--scrub); border-color: var(--scrub); color: var(--sheet); font-weight: 600; }
.chip[aria-pressed="true"] span { color: inherit; opacity: 0.85; }
main { padding-block: 1.25rem 2rem; display: grid; gap: 2.5rem; }
section h2 { margin: 0 0 0.75rem; font: 700 var(--step-2)/1.2 var(--display); text-wrap: balance; }
.status { margin: 0 0 0.5rem; color: var(--ink-2); font-size: var(--step--1); }
ul.rows { list-style: none; margin: 0; padding: 0; }
.row, .head { display: grid; grid-template-columns: minmax(0, 1fr) 15rem; gap: 0.35rem 2rem; align-items: center; }
.row { padding-block: 0.95rem; border-top: 1px solid var(--rule); }
.row.nopay { grid-template-columns: minmax(0, 1fr); }
.head { padding-block: 0.3rem; color: var(--ink-2); font-size: 0.75rem; letter-spacing: 0.06em; text-transform: uppercase; font-weight: 600; }
.what { min-width: 0; }
.what h3 { margin: 0; font-size: 1.06rem; line-height: 1.35; font-weight: 600; overflow-wrap: anywhere; }
.what h3 a { color: var(--ink); text-decoration-color: var(--rule); }
.what h3 a:hover { color: var(--scrub); text-decoration-color: currentColor; }
.meta { margin: 0.2rem 0 0; color: var(--ink-2); font-size: var(--step--1); display: flex; flex-wrap: wrap; gap: 0.15rem 0; }
.meta span + span::before { content: "·"; margin: 0 0.45rem; color: var(--rule); }
.meta .co { color: var(--ink); font-weight: 600; }
.new { display: inline-block; vertical-align: 0.12em; margin-right: 0.5rem; padding: 0.05rem 0.45rem; border-radius: 4px; background: var(--fresh-wash); color: var(--fresh); font-size: 0.7rem; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; }
.pay { display: grid; gap: 0.35rem; }
.rate { font-weight: 700; font-variant-numeric: tabular-nums; font-size: 1.02rem; }
.bar { position: relative; height: 0.4rem; border-radius: 999px; background: var(--rule); }
.bar .fill { position: absolute; top: 0; bottom: 0; border-radius: 999px; background: var(--scrub); }
.bar .fill.open-hi { background: linear-gradient(to right, var(--scrub), transparent); }
.bar .fill.open-lo { background: linear-gradient(to left, var(--scrub), transparent); }
.bar .mark { position: absolute; top: -0.25rem; bottom: -0.25rem; width: 2px; margin-left: -1px; background: var(--ink); opacity: 0.6; }
.axis { position: relative; height: 1rem; font-variant-numeric: tabular-nums; text-transform: none; letter-spacing: 0; font-weight: 500; }
.axis span { position: absolute; transform: translateX(-50%); white-space: nowrap; }
.axis span:first-child { transform: none; }
.axis span:last-child { transform: translateX(-100%); }
.axis span.bar-label { color: var(--ink); font-weight: 700; }
.empty { padding: 1.25rem 0; border-top: 1px solid var(--rule); color: var(--ink-2); }
details { border-top: 1px solid var(--rule); }
details > summary { cursor: pointer; padding-block: 0.9rem; font-weight: 600; }
.links { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 19rem), 1fr)); gap: 0.25rem 2rem; }
.links li { padding-block: 0.65rem; border-top: 1px solid var(--rule); min-width: 0; }
.links a { font-weight: 600; }
.links p { margin: 0.15rem 0 0; color: var(--ink-2); font-size: var(--step--1); }
.about { display: grid; gap: 0.5rem 3rem; grid-template-columns: repeat(auto-fit, minmax(min(100%, 22rem), 1fr)); }
.about > div { min-width: 0; }
.about h3 { margin: 0 0 0.35rem; font: 700 var(--step-1)/1.3 var(--display); }
.about p, .about li { max-width: 62ch; }
.about ul { padding-left: 1.1rem; margin: 0; display: grid; gap: 0.35rem; }
.fine { color: var(--ink-2); font-size: var(--step--1); }
footer { padding-block: 1.5rem 2.5rem; border-top: 1px solid var(--rule); color: var(--ink-2); font-size: var(--step--1); display: flex; flex-wrap: wrap; gap: 0.5rem 1.5rem; }
@media (max-width: 40rem) {
  .row, .head { grid-template-columns: minmax(0, 1fr); }
  .head .label { display: none; }
  .pay { grid-template-columns: auto minmax(0, 1fr); align-items: center; gap: 0.9rem; }
}
@media (prefers-reduced-motion: no-preference) { .row { transition: background-color 0.15s; } .row:hover { background: color-mix(in srgb, var(--scrub-wash) 55%, transparent); } }
"""

JS = r"""
(function () {
  var list = document.getElementById("paid");
  if (list) {
    var rows = Array.prototype.slice.call(list.querySelectorAll("li.row"));
    var q = document.getElementById("q"), sort = document.getElementById("sort"), fresh = document.getElementById("fresh");
    var count = document.getElementById("count"), empty = document.getElementById("empty");
    var chips = Array.prototype.slice.call(document.querySelectorAll(".chip"));
    var mins = Array.prototype.slice.call(document.querySelectorAll('input[name="min"]'));
    var spec = "all";
    var apply = function () {
      var text = q.value.trim().toLowerCase(), min = 0, shown = 0;
      mins.forEach(function (m) { if (m.checked) { min = +m.value; } });
      rows.forEach(function (r) {
        var ok = (!text || r.getAttribute("data-search").indexOf(text) !== -1) &&
          (spec === "all" || (" " + r.getAttribute("data-spec") + " ").indexOf(" " + spec + " ") !== -1) &&
          (+r.getAttribute("data-top") >= min) &&
          (!fresh.checked || r.getAttribute("data-new") === "1");
        r.hidden = !ok;
        if (ok) { shown += 1; }
      });
      var byNew = sort.value === "new";
      rows.slice().sort(function (a, b) {
        var pay = (+b.getAttribute("data-top") - +a.getAttribute("data-top")) || (+b.getAttribute("data-lo") - +a.getAttribute("data-lo"));
        if (!byNew) { return pay; }
        var da = a.getAttribute("data-date"), db = b.getAttribute("data-date");
        return (db > da ? 1 : db < da ? -1 : 0) || pay;
      }).forEach(function (r) { list.appendChild(r); });
      count.textContent = "Showing " + shown + " of " + rows.length + " roles";
      empty.hidden = shown !== 0;
    };
    chips.forEach(function (chip) {
      chip.addEventListener("click", function () {
        spec = chip.getAttribute("data-spec");
        chips.forEach(function (c) { c.setAttribute("aria-pressed", c === chip ? "true" : "false"); });
        apply();
      });
    });
    [q, sort, fresh].concat(mins).forEach(function (el) {
      el.addEventListener("input", apply);
      el.addEventListener("change", apply);
    });
    document.getElementById("reset").addEventListener("click", function () {
      q.value = ""; fresh.checked = false; sort.value = "pay"; spec = "all";
      if (mins.length) { mins[0].checked = true; }
      chips.forEach(function (c) { c.setAttribute("aria-pressed", c.getAttribute("data-spec") === "all" ? "true" : "false"); });
      apply();
    });
    apply();
  }

  var share = document.getElementById("share");
  if (share) {
    var url = share.getAttribute("data-url"), status = document.getElementById("share-status"), field = document.getElementById("share-url");
    var showField = function () { field.hidden = false; field.focus(); field.select(); status.textContent = "Copy the link above."; };
    var copy = function () {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).then(function () { status.textContent = "Link copied."; }, showField);
      } else { showField(); }
    };
    share.addEventListener("click", function () {
      var framed = window.self !== window.top;
      if (!framed && navigator.share) {
        navigator.share({ title: document.title, url: url }).catch(function () {});
      } else { copy(); }
    });
  }
})();
"""


def _chips(jobs: list[dict]) -> str:
    counts: dict[str, int] = {}
    for job in jobs:
        for label in specialties(job.get("title") or ""):
            counts[label] = counts.get(label, 0) + 1
    ordered = sorted((label for label in counts if label != GENERAL), key=lambda s: (-counts[s], s))
    if GENERAL in counts:
        ordered.append(GENERAL)
    buttons = [f'<button type="button" class="chip" data-spec="all" aria-pressed="true">All<span>{len(jobs)}</span></button>']
    for label in ordered:
        buttons.append(
            f'<button type="button" class="chip" data-spec="{slug(label)}" aria-pressed="false">'
            f"{esc(label)}<span>{counts[label]}</span></button>"
        )
    return f'<div class="chips" role="group" aria-label="Specialty">{"".join(buttons)}</div>'


def _axis(scale: float, threshold: float) -> str:
    ticks = list(range(0, int(scale) + 1, 100))
    spans = []
    for i, t in enumerate(ticks):
        label = _money(t) + ("/hr" if i == len(ticks) - 1 else "")
        cls = ' class="bar-label"' if t == threshold else ""
        spans.append(f'<span{cls} style="left:{t / scale * 100:.1f}%">{label}</span>')
    return f'<div class="axis" aria-hidden="true">{"".join(spans)}</div>'


def render(payload: dict, cfg: Config, standalone: bool = True) -> str:
    site = cfg.site
    title = site.get("title") or "Physician AI Per Diem"
    page_url = safe_url(site.get("url"))
    repo_url = safe_url(site.get("repo"))
    threshold = float(payload.get("min_hourly_usd") or cfg.settings.min_hourly)
    bar = _money(threshold)
    try:
        updated = datetime.strptime(payload["updated"], "%Y-%m-%dT%H:%M:%SZ")
    except (KeyError, ValueError):
        updated = datetime.utcnow()
    ref = updated.date()
    jobs = payload.get("jobs") or []
    paid = sorted((j for j in jobs if j.get("status") == "qualified"), key=lambda j: (-_top(j), -(j.get("pay_min_hourly") or 0)))
    unlisted = sorted((j for j in jobs if j.get("status") == "pay_unlisted"), key=lambda j: (j.get("company") or "").lower())
    top_rate = max((_top(j) for j in paid), default=0)
    scale = max(300.0, math.ceil(top_rate / 100) * 100)
    companies = len({(j.get("company") or "").lower() for j in paid})
    new_count = sum(1 for j in paid if j.get("new"))
    description = (
        f"Contract and part-time AI work for physicians that pays {bar}/hr or more: "
        f"{len(paid)} roles from {companies} companies, updated {updated:%b} {updated.day}, {updated.year}."
    )

    mins = [threshold] + [v for v in (150.0, 200.0) if v > threshold]
    seg = "".join(
        f'<input type="radio" name="min" id="min-{int(v)}" value="{v:.0f}"{" checked" if i == 0 else ""}>'
        f'<label for="min-{int(v)}">{_money(v)}+</label>'
        for i, v in enumerate(mins)
    )

    parts = []
    parts.append('<div class="wrap"><header class="top">')
    parts.append(f'<p class="eyebrow">Updated {updated:%b} {updated.day}, {updated.year} · refreshed every morning</p>')
    parts.append(f"<h1>{esc(title)}</h1>")
    parts.append(
        '<p class="lede">Paid, flexible work for physicians at AI companies: evaluating medical AI, writing '
        f"clinical cases, reviewing model answers, and some AI-assisted clinical care. Every role below pays {bar} an hour or more.</p>"
    )
    parts.append(
        f'<ul class="facts"><li><strong>{len(paid)}</strong>roles at {bar}+/hr</li>'
        f"<li><strong>{companies}</strong>companies</li>"
        f"<li><strong>{_money(top_rate)}/hr</strong>top posted rate</li>"
        f"<li><strong>{new_count}</strong>new since the last update</li></ul>"
    )
    if page_url:
        parts.append(
            f'<div class="share"><button type="button" class="btn" id="share" data-url="{esc(page_url)}">Share this list</button>'
            f'<span id="share-status" role="status"></span>'
            f'<input id="share-url" type="text" readonly value="{esc(page_url)}" aria-label="Link to this list" hidden></div>'
        )
    parts.append("</header></div>")

    parts.append('<div class="bar-wrap"><div class="wrap controls">')
    parts.append(
        '<div class="line"><div class="search">'
        '<input id="q" type="search" placeholder="Search role, company or place" autocomplete="off" aria-label="Search role, company or place"></div>'
        f'<div class="seg" role="radiogroup" aria-label="Minimum rate">{seg}</div>'
        '<label class="toggle"><input type="checkbox" id="fresh">New only</label>'
        '<select id="sort" aria-label="Sort"><option value="pay">Highest pay first</option><option value="new">Newest first</option></select></div>'
    )
    parts.append(_chips(paid))
    parts.append("</div></div>")

    parts.append('<div class="wrap"><main>')
    parts.append(f'<section aria-labelledby="paid-h"><h2 id="paid-h">Roles paying {bar}/hr or more</h2>')
    parts.append(f'<p class="status" id="count" role="status">Showing {len(paid)} of {len(paid)} roles</p>')
    parts.append(f'<div class="head"><span class="label">Role</span>{_axis(scale, threshold)}</div>')
    parts.append('<ul class="rows" id="paid">')
    parts.extend(_row(j, ref, scale, threshold, True) for j in paid)
    parts.append("</ul>")
    parts.append(
        '<p class="empty" id="empty" hidden>No roles match these filters. '
        '<button type="button" class="btn quiet" id="reset">Clear filters</button></p>'
    )
    parts.append(
        f'<p class="fine">The line on each bar marks {bar}/hr. Ranges are as posted; a role counts when the top of '
        f"its range reaches {bar}/hr. ≈ means the rate was converted from a daily or annual figure.</p></section>"
    )

    if unlisted:
        parts.append('<section aria-labelledby="unlisted-h"><h2 id="unlisted-h">Pay not posted</h2>')
        parts.append(
            f"<details><summary>{len(unlisted)} more physician roles with flexible hours, no rate in the posting</summary>"
            '<ul class="rows">'
        )
        parts.extend(_row(j, ref, scale, threshold, False) for j in unlisted)
        parts.append("</ul></details></section>")

    if cfg.check_directly:
        parts.append('<section aria-labelledby="direct-h"><h2 id="direct-h">Platforms to check by hand</h2>')
        parts.append(
            '<p class="status">These platforms post a lot of well-paid physician work, but their listings sit behind '
            "a sign-up or a page this search can't read.</p><ul class=\"links\">"
        )
        for link in cfg.check_directly:
            url = safe_url(link.url)
            name = f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(link.name)}</a>' if url else esc(link.name)
            parts.append(f"<li>{name}<p>{esc(link.note)}</p></li>")
        parts.append("</ul></section>")

    parts.append('<section aria-labelledby="about-h"><h2 id="about-h">How this list is made</h2><div class="about"><div>')
    parts.append(
        "<p>Every morning an automated search reads the public job boards of about 50 AI labs, AI-training "
        "platforms and health-AI companies, plus a few careers pages. It keeps roles for physicians (MD/DO or a "
        f"named specialty) with contract, part-time, per diem or hourly terms, and lists those whose posted pay reaches {bar}/hr.</p>"
        "<p>Roles posted once per city, or under many specialty names, are merged into one row with a listing count.</p>"
    )
    parts.append('</div><div><h3>Before you apply</h3><ul>')
    parts.append(
        "<li>Most roles are independent-contractor (1099) work: no benefits, and you handle your own taxes.</li>"
        "<li>Confirm the hourly rate in writing before any unpaid onboarding. Per-task and \"up to\" rates depend on speed and tier.</li>"
        "<li>Check licensure. Many roles need an active US license; a few accept residents.</li>"
        "<li>Clinical roles that involve patient care usually need malpractice cover. Ask who provides it.</li>"
    )
    parts.append("</ul></div></div>")
    parts.append(
        '<p class="fine">Listings are copied from public job postings and may have closed since the last update. '
        "This page is not affiliated with any company listed and is not an endorsement.</p></section>"
    )
    parts.append("</main>")

    foot = [f"<span>Updated {updated:%Y-%m-%d %H:%M} UTC</span>"]
    if repo_url:
        foot.append(f'<a href="{esc(repo_url)}" target="_blank" rel="noopener noreferrer">Source code and daily data on GitHub</a>')
    parts.append(f'<footer>{"".join(foot)}</footer></div>')
    body = "".join(parts)

    fonts = (
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;600;700'
        '&family=Source+Serif+4:opsz,wght@8..60,700&display=swap">'
    )
    if not standalone:  # an Artifact: the host supplies the document skeleton
        return (
            f'<title>{esc(title)}</title><meta name="claude:viewport-fit-sides" content="cover">{fonts}'
            f"<style>{CSS}</style>{body}<script>{JS}</script>\n"
        )
    csp = (
        "default-src 'none'; style-src 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
        "script-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; form-action 'none'"
    )
    og_url = f'<meta property="og:url" content="{esc(page_url)}"><link rel="canonical" href="{esc(page_url)}">' if page_url else ""
    head = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
        f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
        f"<title>{esc(title)}</title>"
        f'<meta name="description" content="{esc(description)}">'
        f'<meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(description)}">'
        f'<meta property="og:type" content="website">{og_url}<meta name="twitter:card" content="summary">'
        f"{fonts}<style>{CSS}</style></head><body>"
    )
    return f"{head}{body}<script>{JS}</script></body></html>\n"


def write(payload: dict, cfg: Config, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    page = out_dir / "index.html"
    page.write_text(render(payload, cfg, standalone=True), encoding="utf-8")
    return page


def load_payload(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


__all__ = ["render", "write", "load_payload", "specialties", "safe_url"]
