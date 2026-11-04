"""End-to-end: config -> search -> report files -> seen-state, with synthetic boards."""

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from perdiem_finder import config, detect, report, state
from perdiem_finder.config import Company, Config, Link
from perdiem_finder.http import HttpError
from perdiem_finder.matching import Settings
from perdiem_finder.search import search
from perdiem_finder.util import redact

ROOT = Path(__file__).resolve().parents[1]
GH = "https://boards-api.greenhouse.io/v1/boards"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board"


def gh_job(job_id, title, content, kind="Contract"):
    return {"id": job_id, "title": title, "absolute_url": f"https://job-boards.greenhouse.io/acme/jobs/{job_id}",
            "location": {"name": "Remote"}, "updated_at": "2026-11-01T00:00:00Z", "content": content,
            "metadata": [{"name": "Employment Type", "value": kind}]}


ROUTES = {
    f"{GH}/acme/jobs": {
        "jobs": [
            gh_job(1, "Physician AI Trainer", "Board-certified physicians. $150 - $200 per hour."),
            gh_job(2, "Physician AI Trainer", "Board-certified physicians. $150 - $200 per hour."),  # same role, 2nd location
            gh_job(3, "Medical Expert (Contract)", "MD or DO. Independent contractor."),
            gh_job(4, "Physician Reviewer", "Physicians. $40-$60/hr."),
            gh_job(5, "Software Engineer", "We help physicians. $150/hr."),
        ]
    },
    f"{GH}/acme/jobs/3": {"pay_input_ranges": []},
    # "Hippocratic AI" has no pinned board: detection tries greenhouse, then ashby.
    f"{ASHBY}/hippocraticai": {
        "jobs": [{"id": "h1", "title": "Physician Researcher", "isListed": True, "employmentType": "Contract",
                  "jobUrl": "https://jobs.ashbyhq.com/hippocraticai/h1", "descriptionPlain": "Part-time, 5-10 hours per week."}]
    },
}


def make_config(**settings):
    return Config(
        settings=Settings(**settings),
        companies=[
            Company("Acme", board="greenhouse:acme", category="AI lab"),
            Company("Hippocratic AI", category="Health AI"),
            Company("Gone Inc", board="lever:gone"),
        ],
        pages=[],
        google_jobs={},
        check_directly=[Link("Mercor", "https://work.mercor.com/explore", "check by hand")],
    )


def test_full_run(fake_http, tmp_path):
    http = fake_http(ROUTES)
    cfg = make_config()
    result = search(cfg, http, {}, date(2026, 11, 4))

    by_name = {s.name: s for s in result.statuses}
    assert by_name["Acme"].state == "ok" and by_name["Acme"].postings == 5
    assert by_name["Hippocratic AI"].where == "ashby:hippocraticai"
    assert by_name["Gone Inc"].state == "error"
    assert result.boards_cache["Hippocratic AI"]["board"] == "ashby:hippocraticai"

    statuses = sorted((j.title, j.status) for j in result.jobs)
    assert statuses == [
        ("Medical Expert (Contract)", "pay_unlisted"),
        ("Physician AI Trainer", "qualified"),  # the duplicate location was merged
        ("Physician Researcher", "pay_unlisted"),
        ("Physician Reviewer", "below_threshold"),
    ]
    merged = next(j for j in result.jobs if j.title == "Physician AI Trainer")
    assert merged.location == "Remote"

    reported = [j for j in result.jobs if j.status in ("qualified", "pay_unlisted")]
    state.update_seen(reported, tmp_path / "seen.json", date(2026, 11, 4))
    assert all(j.is_new for j in reported)
    now = datetime(2026, 11, 4, 11, 30, tzinfo=timezone.utc)
    counts = report.write_all(result, cfg, tmp_path / "results", now)
    assert counts["qualified"] == 1 and counts["new_qualified"] == 1 and counts["pay_unlisted"] == 2

    latest = (tmp_path / "results" / "latest.md").read_text()
    assert "## ✅ Pays $100/hr or more — 1 role" in latest
    assert "[Physician AI Trainer](https://job-boards.greenhouse.io/acme/jobs/1)" in latest
    assert "$150–$200/hr" in latest and "Mercor" in latest and "Gone Inc" in latest
    data = json.loads((tmp_path / "results" / "latest.json").read_text())
    assert data["jobs"][0]["pay_min_hourly"] == 150
    assert (tmp_path / "results" / "latest.csv").read_text().startswith("status,new,company")
    assert "1 new physician AI task" in (tmp_path / "results" / "new.md").read_text()

    # Second run on the same postings: nothing is new any more.
    again = search(cfg, http, result.boards_cache, date(2026, 11, 5))
    reported = [j for j in again.jobs if j.status in ("qualified", "pay_unlisted")]
    state.update_seen(reported, tmp_path / "seen.json", date(2026, 11, 5))
    assert not any(j.is_new for j in reported)
    assert all(j.first_seen == "2026-11-04" for j in reported)


def test_detection_result_is_cached(fake_http):
    http = fake_http(ROUTES)
    cfg = make_config()
    cache = {"Hippocratic AI": {"board": None, "checked": "2026-11-01"}}
    result = search(cfg, http, cache, date(2026, 11, 4), only={"hippocraticai"})
    assert result.statuses[0].state == "not_found"
    assert not any("hippocraticai" in url for url, _ in http.calls)  # recent miss: not probed again
    assert result.partial


def test_slug_variants_and_name_check():
    assert detect.slug_variants("Hippocratic AI") == ["hippocraticai", "hippocratic-ai", "hippocratic"]
    assert detect.slug_variants("Viz.ai", ["vizai"])[0] == "vizai"
    assert detect.name_matches("Hippocratic AI", "Hippocratic AI")
    assert not detect.name_matches("Paige Lighting", "Glass Health")
    assert detect.name_matches(None, "Anything")


def test_repository_config_loads():
    cfg = config.load(ROOT / "config.yaml")
    assert cfg.settings.min_hourly == 100 and cfg.settings.pay_rule == "top"
    assert len(cfg.companies) >= 20 and cfg.pages and cfg.check_directly
    assert len(cfg.google_jobs["queries"]) <= 3  # stays inside SerpApi's free plan when run daily


def test_bad_config_is_explained(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("companies:\n  - name: Acme\n    board: monster:acme\n")
    with pytest.raises(config.ConfigError, match="board must look like"):
        config.load(path)


def test_api_keys_never_leak():
    message = str(HttpError(401, "https://serpapi.com/search.json?q=x&api_key=SECRET123"))
    assert "SECRET123" not in message
    assert "SECRET123" not in redact("failed: https://serpapi.com/search.json?api_key=SECRET123&q=1")


def test_city_variants_of_one_role_are_merged():
    from perdiem_finder.models import Job, Pay
    from perdiem_finder.search import dedupe

    def posting(job_id, title, location, low=80):
        job = Job(source="greenhouse:p", company="Prolific", title=title, url=f"https://x/{job_id}",
                  job_id=job_id, location=location)
        job.pay, job.status = Pay(low, 150, "hour"), "qualified"
        return job

    out = dedupe([
        posting("1", "Doctors - AI Training - Houston", "Houston, Texas, United States"),
        posting("2", "Doctors - AI Training - Miami", "Miami, Florida, United States"),
        posting("3", "Doctors - AI Training (Remote, US)", "United States"),
        posting("4", "Doctors - AI Training - Boston", "Boston", low=60),  # different pay: kept apart
        posting("5", "Physician - Emergency Medicine", "Remote"),  # a specialty, not a place
        posting("6", "Physician - Emergency Medicine", "Remote"),
    ])
    titles = sorted(j.title for j in out)
    assert titles == ["Doctors - AI Training", "Doctors - AI Training - Boston", "Physician - Emergency Medicine"]
    training = next(j for j in out if j.title == "Doctors - AI Training")
    assert training.listings == 3 and training.job_id == "1"
    # "United States" is already part of the combined location, so it is not repeated.
    assert training.location == "Houston, Texas, United States / Miami, Florida, United States"


def test_one_program_posted_under_many_role_names_is_one_row():
    from perdiem_finder.models import Job, Pay
    from perdiem_finder.search import dedupe

    def posting(job_id, title, low=130):
        job = Job(source="greenhouse:p", company="Prolific", title=title, url=f"https://x/{job_id}",
                  job_id=job_id, location="United States")
        job.pay, job.status = Pay(low, None, "hour"), "qualified"
        return job

    program = "AI Research Participants - United States (Remote)"
    out = dedupe([
        posting("11", f"Cardiologist - {program}"),
        posting("12", f"Dermatologist - {program}"),
        posting("13", f"Hospitalist - {program}"),
        posting("14", f"Radiologist - {program}", low=90),  # different pay: its own row
        posting("15", "Ophthalmologists (Freelance - Remote) - Brasília, Brazil"),
        posting("16", "Ophthalmologists (Freelance - Remote) - Brazil"),
    ])
    titles = sorted(j.title for j in out)
    assert titles == [
        f"{program} — 3 roles incl. Cardiologist, Dermatologist, Hospitalist",
        "Ophthalmologists (Freelance - Remote)",
        f"Radiologist - {program}",
    ]
    assert next(j for j in out if j.title.startswith(program)).listings == 3


def test_one_role_posted_per_city_with_remote_location_is_merged():
    from perdiem_finder.models import Job, Pay
    from perdiem_finder.search import dedupe

    def posting(job_id, title):
        job = Job(source="greenhouse:p", company="Prolific", title=title, url=f"https://x/{job_id}",
                  job_id=job_id, location="Remote")
        job.pay, job.status = Pay(48, 200, "hour"), "qualified"
        return job

    out = dedupe([
        posting("103", "Cardiologists (Freelance - Remote) - Belfast, UK"),
        posting("101", "Cardiologists (Freelance - Remote) - Albuquerque, US"),
        posting("102", "Cardiologists (Freelance - Remote) - Colorado Springs, US"),
        posting("104", "Dermatologists (Freelance - Remote) - Austin, US"),
        posting("105", "Physician - Internal Medicine, Cardiology"),  # specialties, not a place
    ])
    by_title = {j.title: j for j in out}
    assert set(by_title) == {
        "Cardiologists (Freelance - Remote)",
        "Dermatologists (Freelance - Remote) - Austin, US",
        "Physician - Internal Medicine, Cardiology",
    }
    cardio = by_title["Cardiologists (Freelance - Remote)"]
    assert cardio.listings == 3 and cardio.job_id == "101"  # the oldest posting is kept


def test_careers_page_roles_keep_their_identity_across_urls(tmp_path):
    from perdiem_finder.models import Job

    def turing(url):
        job = Job(source="page:turing.betterteam.com", company="Turing", title="Cardiologist", url=url, job_id=url)
        job.status = "qualified"
        return job

    seen = tmp_path / "seen.json"
    first = turing("https://turing.betterteam.com/cardiologist-30")
    state.update_seen([first], seen, date(2026, 11, 4))
    second = turing("https://turing.betterteam.com/cardiologist-29")  # same role, another URL
    state.update_seen([second], seen, date(2026, 11, 5))
    assert first.is_new and not second.is_new and second.first_seen == "2026-11-04"
