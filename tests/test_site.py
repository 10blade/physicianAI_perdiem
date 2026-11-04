"""The shareable web page."""

from pathlib import Path

from perdiem_finder import config, site

ROOT = Path(__file__).resolve().parents[1]


def job(**kw):
    base = {"status": "qualified", "company": "Turing", "title": "Cardiologist", "url": "https://example.com/c",
            "pay": "$200–$300/hr", "pay_min_hourly": 200, "pay_max_hourly": 300, "employment_type": "CONTRACTOR",
            "location": "Remote", "listings": 1, "posted": "2026-11-04", "first_seen": "2026-11-04", "new": True}
    base.update(kw)
    return base


PAYLOAD = {
    "updated": "2026-11-04T13:10:00Z",
    "min_hourly_usd": 100,
    "jobs": [
        job(),
        job(company="Evil Co", title='Physician <script>alert("x")</script>', url="javascript:alert(1)",
            pay="$150/hr", pay_min_hourly=150, pay_max_hourly=150, new=False),
        job(company="Prolific", title="Doctors - AI Training", pay="$130+/hr", pay_min_hourly=130,
            pay_max_hourly=None, listings=45, posted="2025-10-08"),
        job(status="pay_unlisted", company="Corti", title="Clinical Advisor, Psychiatry", pay="",
            pay_min_hourly=None, pay_max_hourly=None),
    ],
}


def test_page_renders_and_escapes_posting_text():
    cfg = config.load(ROOT / "config.yaml")
    page = site.render(PAYLOAD, cfg)
    assert page.startswith("<!doctype html>") and "<title>Physician AI Per Diem</title>" in page
    assert "Showing 3 of 3 roles" in page and "Pay not posted" in page
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in page
    assert '<script>alert("x")' not in page and "javascript:alert" not in page
    assert 'data-spec="cardiology"' in page and "45 listings" in page
    assert "Posted Oct 8, 2025" in page  # older year shown in full
    assert 'property="og:title"' in page and "Content-Security-Policy" in page


def test_fragment_has_no_document_wrapper():
    cfg = config.load(ROOT / "config.yaml")
    fragment = site.render(PAYLOAD, cfg, standalone=False)
    assert fragment.startswith("<title>")
    assert "<html" not in fragment and "<body" not in fragment and "<!doctype" not in fragment.lower()


def test_specialties_and_links():
    assert site.specialties("Hospitalist / Internist - Medical AI Evaluation") == ["Hospital & Internal Medicine"]
    assert site.specialties("Physicians - AI Research Participants") == [site.GENERAL]
    assert site.safe_url("https://a.example/x") == "https://a.example/x"
    assert site.safe_url("javascript:alert(1)") == "" and site.safe_url(None) == ""
    assert site.kind_label("CONTRACTOR, PART TIME") == "Contract · Part-time"
    assert site.kind_label("OTHER") == ""


def test_write_creates_pages_files(tmp_path):
    cfg = config.load(ROOT / "config.yaml")
    page = site.write(PAYLOAD, cfg, tmp_path / "docs")
    assert page.read_text().startswith("<!doctype html>")
    assert (tmp_path / "docs" / ".nojekyll").exists()
