"""Write the results files: latest.md / .json / .csv, sources.md and new.md."""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from pathlib import Path

from .config import Config
from .models import Job
from .search import SearchResult
from .util import redact

STATE_LABEL = {"ok": "✅ ok", "error": "❌ error", "not_found": "➖ no public board", "skipped": "⏭️ skipped"}


def _cell(value, limit: int | None = None) -> str:
    text = " ".join(str(value if value is not None else "").split()).replace("|", "\\|")
    if limit and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _link(title: str, url: str) -> str:
    title = _cell(title, 90).replace("[", "(").replace("]", ")") or "(untitled)"
    return f"[{title}]({url.replace(' ', '%20').replace(')', '%29')})" if url else title


def _kind(job: Job) -> str:
    kind = job.employment_type.replace("_", " ").strip()
    if kind:
        return {"fulltime": "Full-time", "parttime": "Part-time"}.get(kind.lower().replace(" ", ""), kind)
    return "flexible" if job.flexible else ""


def _location(job: Job) -> str:
    return job.location or ("Remote" if job.remote else "")


def _table(jobs: list[Job], with_pay: bool) -> list[str]:
    header = ["", "Company", "Role", "Pay", "Type", "Location", "Posted"]
    if not with_pay:
        header.remove("Pay")
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for job in jobs:
        cells = ["🆕" if job.is_new else "", _cell(job.company, 40), _link(job.title, job.url)]
        if with_pay:
            cells.append(_cell(job.pay.label() if job.pay else "", 45))
        cells += [_cell(_kind(job), 24), _cell(_location(job), 45), _cell(job.posted or "—", 12)]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _by_pay(jobs: list[Job]) -> list[Job]:
    return sorted(jobs, key=lambda j: (-(j.pay.top or 0) if j.pay else 0, j.company.lower(), j.title.lower()))


def _by_name(jobs: list[Job]) -> list[Job]:
    return sorted(jobs, key=lambda j: (not j.is_new, j.company.lower(), j.title.lower()))


def _repo_link(path: str) -> str:
    server, repo = os.environ.get("GITHUB_SERVER_URL"), os.environ.get("GITHUB_REPOSITORY")
    return f"{server}/{repo}/blob/main/{path}" if server and repo else path


def render_latest(result: SearchResult, cfg: Config, now: datetime) -> str:
    s = cfg.settings
    bar = f"${s.min_hourly:,.0f}"
    qualified = _by_pay([j for j in result.jobs if j.status == "qualified"])
    unlisted = _by_name([j for j in result.jobs if j.status == "pay_unlisted"])
    salaried = _by_pay([j for j in result.jobs if j.status == "salaried"])
    searched = sum(1 for st in result.statuses if st.state == "ok")
    failed = [st for st in result.statuses if st.state == "error"]
    rule = "the top of the posted range" if s.pay_rule == "top" else "the bottom of the posted range"

    lines = [f"# Physician AI tasks paying {bar}+/hr", ""]
    if result.partial:
        lines += ["> ⚠️ Partial run (only some sources were searched).", ""]
    lines += [
        f"Updated **{now:%Y-%m-%d %H:%M} UTC** · {searched} of {len(result.statuses)} sources searched · "
        f"{result.scanned:,} postings scanned",
        "",
        f"## ✅ Pays {bar}/hr or more — {len(qualified)} role{'s' if len(qualified) != 1 else ''}",
        "",
    ]
    if qualified:
        lines += _table(qualified, with_pay=True)
        lines += [
            "",
            f"🆕 = new since the last run. Pay is copied from the posting; a role counts when {rule} reaches "
            f"{bar}/hr. ≈ marks an hourly estimate converted from a daily (÷8) or annual (÷2,080) figure.",
        ]
    else:
        lines.append("_None found in this run._")

    if s.include_pay_unlisted:
        lines += ["", f"## 🟡 Physician roles with flexible hours, pay not posted — {len(unlisted)}", ""]
        if unlisted:
            lines += ["These match on role and schedule but the posting gives no rate. Many pay well; check before applying.", ""]
            lines += _table(unlisted, with_pay=False)
        else:
            lines.append("_None found in this run._")

    if s.include_salaried and salaried:
        lines += ["", f"## Salaried physician roles at AI companies — {len(salaried)}", ""]
        lines += _table(salaried, with_pay=True)

    if cfg.check_directly:
        lines += [
            "",
            "## 🔎 Check these platforms directly",
            "",
            "They have no public job feed this tool can read, and they post much of the best-paid physician AI work:",
            "",
        ]
        for link in cfg.check_directly:
            lines.append(f"- [{_cell(link.name)}]({link.url})" + (f" — {_cell(link.note)}" if link.note else ""))

    lines += ["", "## Sources", ""]
    if failed:
        names = ", ".join(_cell(st.name) for st in failed)
        lines.append(f"{len(failed)} source(s) failed this run: {names}.")
    else:
        lines.append("Every reachable source answered.")
    lines.append(f"Details for all {len(result.statuses)} sources: [sources.md](sources.md).")
    return "\n".join(lines) + "\n"


def render_sources(result: SearchResult, now: datetime) -> str:
    lines = [
        "# Sources searched",
        "",
        f"Run of {now:%Y-%m-%d %H:%M} UTC. \"Matches\" counts physician postings that made the report "
        "(before repeats across sources and locations are merged).",
        "",
        "| Source | Board or page | Status | Postings | Matches | Note |",
        "|---|---|---|---|---|---|",
    ]
    for st in result.statuses:
        lines.append(
            f"| {_cell(st.name, 40)} | {_cell(st.where, 70)} | {STATE_LABEL.get(st.state, st.state)} | "
            f"{st.postings:,} | {st.matches} | {_cell(redact(st.note), 160)} |"
        )
    return "\n".join(lines) + "\n"


def render_new(result: SearchResult, cfg: Config) -> str:
    bar = f"${cfg.settings.min_hourly:,.0f}"
    new_q = _by_pay([j for j in result.jobs if j.status == "qualified" and j.is_new])
    new_u = _by_name([j for j in result.jobs if j.status == "pay_unlisted" and j.is_new])
    lines = [f"## {len(new_q)} new physician AI task(s) paying {bar}+/hr", ""]
    lines += _table(new_q, with_pay=True) if new_q else ["_None._"]
    if new_u and cfg.settings.include_pay_unlisted:
        lines += ["", f"### Also new: {len(new_u)} flexible physician role(s) with no posted pay", ""]
        lines += _table(new_u, with_pay=False)
    lines += ["", f"Full list: [results/latest.md]({_repo_link('results/latest.md')})"]
    return "\n".join(lines) + "\n"


def write_all(result: SearchResult, cfg: Config, out_dir: Path, now: datetime) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    reported = [j for j in result.jobs if j.status in ("qualified", "pay_unlisted", "salaried")]
    counts = {
        "qualified": sum(1 for j in result.jobs if j.status == "qualified"),
        "pay_unlisted": sum(1 for j in result.jobs if j.status == "pay_unlisted"),
        "new_qualified": sum(1 for j in result.jobs if j.status == "qualified" and j.is_new),
        "new_pay_unlisted": sum(1 for j in result.jobs if j.status == "pay_unlisted" and j.is_new),
        "sources_ok": sum(1 for st in result.statuses if st.state == "ok"),
        "sources_failed": sum(1 for st in result.statuses if st.state == "error"),
        "postings_scanned": result.scanned,
    }
    (out_dir / "latest.md").write_text(render_latest(result, cfg, now), encoding="utf-8")
    (out_dir / "sources.md").write_text(render_sources(result, now), encoding="utf-8")
    (out_dir / "new.md").write_text(render_new(result, cfg), encoding="utf-8")

    rows = [j.to_dict() for j in _by_pay([j for j in reported if j.status == "qualified"])]
    rows += [j.to_dict() for j in _by_name([j for j in reported if j.status != "qualified"])]
    payload = {
        "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "min_hourly_usd": cfg.settings.min_hourly,
        "pay_rule": cfg.settings.pay_rule,
        "partial_run": result.partial,
        "counts": counts,
        "jobs": rows,
    }
    (out_dir / "latest.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    fields = [
        "status", "new", "company", "title", "pay", "pay_min_hourly", "pay_max_hourly", "pay_basis",
        "employment_type", "location", "remote", "posted", "first_seen", "url", "category", "source", "pay_quote",
    ]
    with (out_dir / "latest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return counts
