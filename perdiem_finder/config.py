"""Load and check config.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from .matching import Settings
from .sources.ats import BOARDS


class ConfigError(ValueError):
    pass


@dataclass
class Company:
    name: str
    board: Optional[str] = None  # "ats:slug"; None means detect automatically
    category: str = ""
    aliases: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class Link:
    name: str
    url: str
    note: str = ""


@dataclass
class Config:
    settings: Settings
    companies: list[Company]
    pages: list[dict]
    google_jobs: dict
    check_directly: list[Link]


def _settings(raw: dict) -> Settings:
    s = Settings()
    if "min_hourly_usd" in raw:
        s.min_hourly = float(raw["min_hourly_usd"])
    if "pay_rule" in raw:
        rule = str(raw["pay_rule"]).strip().lower()
        if rule not in ("top", "floor"):
            raise ConfigError("settings.pay_rule must be 'top' or 'floor'")
        s.pay_rule = rule
    for key in ("include_pay_unlisted", "include_salaried"):
        if key in raw:
            setattr(s, key, bool(raw[key]))
    if "workers" in raw:
        s.workers = max(1, int(raw["workers"]))
    return s


def load(path: str | Path) -> Config:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from None

    companies = []
    seen = set()
    for i, item in enumerate(raw.get("companies") or []):
        if not isinstance(item, dict) or not item.get("name"):
            raise ConfigError(f"companies[{i}] needs a name")
        name = str(item["name"]).strip()
        if name.lower() in seen:
            raise ConfigError(f"company listed twice: {name}")
        seen.add(name.lower())
        board = item.get("board")
        if board:
            board = str(board).strip()
            ats, _, slug = board.partition(":")
            if ats not in BOARDS or not slug:
                raise ConfigError(
                    f"{name}: board must look like '<type>:<slug>' with type one of {', '.join(BOARDS)} (got {board!r})"
                )
        companies.append(
            Company(
                name=name,
                board=board or None,
                category=str(item.get("category") or ""),
                aliases=[str(a) for a in item.get("aliases") or []],
                note=str(item.get("note") or ""),
            )
        )

    pages = []
    for i, item in enumerate(raw.get("pages") or []):
        if not isinstance(item, dict) or not str(item.get("url", "")).startswith("http"):
            raise ConfigError(f"pages[{i}] needs a url starting with http")
        pages.append(dict(item))

    links = []
    for i, item in enumerate(raw.get("check_directly") or []):
        if not isinstance(item, dict) or not item.get("name") or not item.get("url"):
            raise ConfigError(f"check_directly[{i}] needs a name and a url")
        links.append(Link(name=str(item["name"]), url=str(item["url"]), note=str(item.get("note") or "")))

    return Config(
        settings=_settings(raw.get("settings") or {}),
        companies=companies,
        pages=pages,
        google_jobs=dict(raw.get("google_jobs") or {}),
        check_directly=links,
    )
