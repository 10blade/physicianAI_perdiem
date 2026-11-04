"""Command line: ``python -m perdiem_finder search | probe | pay``."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import config, detect, matching, report, state
from . import pay as pay_mod
from .http import Http
from .search import search
from .util import norm


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _github_output(values: dict) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def cmd_search(args: argparse.Namespace) -> int:
    cfg = config.load(args.config)
    if args.min_hourly is not None:
        cfg.settings.min_hourly = args.min_hourly
    only = {norm(x) for x in args.only.split(",") if x.strip()} if args.only else None
    data_dir, out_dir = Path(args.data), Path(args.out)
    boards_path = data_dir / "boards.json"
    now = datetime.now(timezone.utc)

    result = search(
        cfg,
        Http(),
        state.load_json(boards_path, {}),
        now.date(),
        only=only,
        use_google=not args.no_google,
        log=_log,
    )
    if not result.statuses:
        _log("Nothing to search: check the names given to --only.")
        return 2
    if not any(st.state == "ok" for st in result.statuses):
        _log("Every source failed (is the network up?). Results were not written.")
        return 1

    state.save_json(boards_path, result.boards_cache)
    reported = [j for j in result.jobs if j.status in matching.reported_statuses(cfg.settings)]
    if not result.partial:
        state.update_seen(reported, data_dir / "seen.json", now.date())
    counts = report.write_all(result, cfg, out_dir, now)

    bar = f"${cfg.settings.min_hourly:,.0f}"
    _log(
        f"\n{counts['qualified']} role(s) pay {bar}+/hr ({counts['new_qualified']} new); "
        f"{counts['pay_unlisted']} more have no posted pay. "
        f"{counts['sources_ok']} sources ok, {counts['sources_failed']} failed. "
        f"Results: {out_dir / 'latest.md'}"
    )
    _github_output(
        {
            "qualified": counts["qualified"],
            "new_qualified": counts["new_qualified"],
            "issue_title": f"{counts['new_qualified']} new physician AI task(s) paying {bar}+/hr "
            f"({now:%Y-%m-%d})",
        }
    )
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    names = [n.strip() for arg in args.names for n in arg.split(",") if n.strip()]
    http = Http()
    lines = ["| Company | Board (paste into config.yaml) | Open postings | Board name | Careers page |", "|---|---|---|---|---|"]
    for name in names:
        hits = detect.probe(name, http)
        if not hits:
            print(f"{name}: no public Greenhouse, Ashby, Lever, Workable, SmartRecruiters or Recruitee board found")
            lines.append(f"| {name} | not found | | | |")
        for hit in hits:
            print(f"{name}: board: {hit['board']}  ({hit['postings']} postings, {hit['url']})")
            lines.append(f"| {name} | `{hit['board']}` | {hit['postings']} | {hit['board_name']} | {hit['url']} |")
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as handle:
            handle.write("## Job board lookup\n\n" + "\n".join(lines) + "\n")
    return 0


def cmd_pay(args: argparse.Namespace) -> int:
    pays = pay_mod.parse_text(args.text, require_currency=not args.loose)
    if not pays:
        print("No pay rate found.")
        return 1
    for p in pays:
        print(f"{p.label():35} from {p.raw!r}")
    print(f"Combined: {pay_mod.combine(pays).label()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m perdiem_finder",
        description="Find per diem / contract AI tasks for physicians that pay $100+/hr.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("search", help="search every source and write the results files")
    s.add_argument("--config", default="config.yaml", help="settings and sources (default: config.yaml)")
    s.add_argument("--out", default="results", help="folder for the results files (default: results)")
    s.add_argument("--data", default="data", help="folder for state kept between runs (default: data)")
    s.add_argument("--min-hourly", type=float, help="override settings.min_hourly_usd")
    s.add_argument("--only", help="comma-separated company or page names to search (for testing)")
    s.add_argument("--no-google", action="store_true", help="skip the Google Jobs search")
    s.set_defaults(func=cmd_search)

    p = sub.add_parser("probe", help="find which public job board a company uses")
    p.add_argument("names", nargs="+", help='company names, e.g. "Hippocratic AI" or "Abridge, Suki"')
    p.add_argument("--summary", help="also append a markdown table to this file")
    p.set_defaults(func=cmd_probe)

    t = sub.add_parser("pay", help="show how a piece of text is read as pay")
    t.add_argument("text")
    t.add_argument("--loose", action="store_true", help="accept amounts without a $ or USD marker")
    t.set_defaults(func=cmd_pay)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except config.ConfigError as exc:
        _log(f"Config problem: {exc}")
        return 2
