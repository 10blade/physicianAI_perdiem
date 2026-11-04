"""Files kept between runs: which postings were already seen, and detected job boards."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .models import Job


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def update_seen(jobs: list[Job], path: Path, today: date, keep_days: int = 120) -> None:
    """Set ``first_seen`` and ``is_new`` on each job and remember them.

    A posting counts as new the first time it appears, and again when it moves
    up to "qualified" (for example, when pay is added to a posting).
    """
    seen: dict = load_json(path, {})
    stamp = today.isoformat()
    for job in jobs:
        record = seen.get(job.key)
        if record is None:
            job.first_seen, job.is_new = stamp, True
            seen[job.key] = {"first_seen": stamp}
        else:
            job.first_seen = record.get("first_seen", stamp)
            job.is_new = job.status == "qualified" and record.get("status") != "qualified"
        seen[job.key].update(last_seen=stamp, status=job.status, company=job.company, title=job.title)
    cutoff = (today - timedelta(days=keep_days)).isoformat()
    seen = {k: v for k, v in seen.items() if v.get("last_seen", stamp) >= cutoff}
    save_json(path, seen)
