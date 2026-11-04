"""Each job-board adapter, fed a synthetic response in that API's documented shape."""

from datetime import date

from perdiem_finder.matching import Settings, classify
from perdiem_finder.sources import google_jobs, pages
from perdiem_finder.sources.ats import BOARDS

GH = "https://boards-api.greenhouse.io/v1/boards/acme"


def test_greenhouse_list_and_pay_transparency(fake_http):
    http = fake_http(
        {
            f"{GH}/jobs": {
                "jobs": [
                    {
                        "id": 123,
                        "title": "Physician Reviewer (Contract)",
                        "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/123",
                        "location": {"name": "Remote - US"},
                        "updated_at": "2026-10-30T12:00:00-04:00",
                        "content": "&lt;p&gt;Board-certified physicians wanted. Independent contractor.&lt;/p&gt;",
                        "metadata": [{"name": "Employment Type", "value": "Contract"}],
                    }
                ]
            },
            f"{GH}/jobs/123": {
                "pay_input_ranges": [
                    {"min_cents": 15000, "max_cents": 25000, "currency_type": "USD", "title": "Hourly", "blurb": ""}
                ]
            },
        }
    )
    board = BOARDS["greenhouse"]
    [job] = board.fetch("acme", http, "Acme")
    assert job.title == "Physician Reviewer (Contract)"
    assert job.description.startswith("Board-certified physicians wanted")
    assert job.employment_type == "Contract" and job.posted == "2026-10-30"
    classify(job, Settings())
    assert job.status == "pay_unlisted" and board.needs_enrich(job)
    assert board.enrich("acme", http, job)
    classify(job, Settings())
    assert job.status == "qualified" and job.pay.label() == "$150–$250/hr"


def test_ashby_compensation(fake_http):
    url = "https://api.ashbyhq.com/posting-api/job-board/acme"
    http = fake_http(
        {
            url: {
                "jobs": [
                    {
                        "id": "abc",
                        "title": "Medical Expert",
                        "location": "Remote",
                        "isRemote": True,
                        "isListed": True,
                        "employmentType": "Contract",
                        "publishedAt": "2026-11-01T09:00:00Z",
                        "jobUrl": "https://jobs.ashbyhq.com/acme/abc",
                        "descriptionPlain": "MD or DO required.",
                        "compensation": {
                            "compensationTierSummary": "$120 – $180 per hour",
                            "summaryComponents": [
                                {"compensationType": "Salary", "interval": "1 HOUR", "currencyCode": "USD",
                                 "minValue": 120, "maxValue": 180, "summary": "$120 – $180 per hour"},
                                {"compensationType": "EquityPercentage", "interval": "NONE", "minValue": 0.1, "maxValue": 0.2},
                            ],
                        },
                    },
                    {"id": "hidden", "title": "Physician", "isListed": False},
                ]
            }
        }
    )
    [job] = BOARDS["ashby"].fetch("acme", http, "Acme")
    assert job.structured_pay.label() == "$120–$180/hr"
    classify(job, Settings())
    assert job.status == "qualified" and job.remote is True


def test_lever_salary_range(fake_http):
    http = fake_http(
        {
            "https://api.lever.co/v0/postings/acme": [
                {
                    "id": "p1",
                    "text": "AI Clinical Fellow",
                    "categories": {"commitment": "Contractor", "location": "Remote"},
                    "descriptionPlain": "For attending physicians.",
                    "lists": [{"text": "Requirements", "content": "<li>MD or DO</li>"}],
                    "hostedUrl": "https://jobs.lever.co/acme/p1",
                    "createdAt": 1793836800000,
                    "workplaceType": "remote",
                    "salaryRange": {"min": 100, "max": 120, "currency": "USD", "interval": "per-hour-wage"},
                }
            ]
        }
    )
    [job] = BOARDS["lever"].fetch("acme", http, "Acme")
    assert job.structured_pay.label() == "$100–$120/hr"
    assert "MD or DO" in job.description and job.remote is True and job.posted
    classify(job, Settings())
    assert job.status == "qualified"


def test_workable_fetches_detail_for_promising_titles(fake_http):
    http = fake_http(
        {
            "https://apply.workable.com/api/v1/widget/accounts/acme": {
                "name": "Acme",
                "jobs": [
                    {"title": "Physician (MD/DO) - Medical AI Evaluation", "shortcode": "AB12",
                     "employment_type": "Contract", "url": "https://apply.workable.com/j/AB12", "country": "United States"}
                ],
            },
            "https://apply.workable.com/api/v2/accounts/acme/jobs/AB12": {
                "description": "<p>Evaluate AI answers. $160 per hour.</p>",
                "requirements": "<p>Board certified.</p>",
            },
        }
    )
    board = BOARDS["workable"]
    [job] = board.fetch("acme", http, "Acme")
    classify(job, Settings())
    assert job.status == "pay_unlisted" and board.needs_enrich(job)
    assert board.enrich("acme", http, job)
    classify(job, Settings())
    assert job.status == "qualified" and job.pay.label() == "$160/hr"


def test_recruitee_and_smartrecruiters(fake_http):
    http = fake_http(
        {
            "https://acme.recruitee.com/api/offers/": {
                "offers": [
                    {"id": 7, "title": "Radiologist - AI Annotation", "careers_url": "https://acme.recruitee.com/o/r",
                     "employment_type_code": "freelance", "description": "<p>Read scans.</p>",
                     "salary": {"min": "110", "max": "140", "period": "hour", "currency": "USD"}}
                ]
            },
            "https://api.smartrecruiters.com/v1/companies/acme/postings": {
                "totalFound": 1,
                "content": [{"id": "999", "name": "Physician Consultant - AI", "typeOfEmployment": {"label": "Part-time"},
                             "location": {"city": "Boston", "country": "us", "remote": True}}],
            },
            "https://api.smartrecruiters.com/v1/companies/acme/postings/999": {
                "postingUrl": "https://jobs.smartrecruiters.com/acme/999-physician",
                "jobAd": {"sections": {"jobDescription": {"text": "<p>Up to $200/hr.</p>"}}},
            },
        }
    )
    [r] = BOARDS["recruitee"].fetch("acme", http, "Acme")
    classify(r, Settings())
    assert r.status == "qualified" and r.pay.label() == "$110–$140/hr"

    board = BOARDS["smartrecruiters"]
    [s] = board.fetch("acme", http, "Acme")
    classify(s, Settings())
    assert board.needs_enrich(s) and board.enrich("acme", http, s)
    classify(s, Settings())
    assert s.status == "qualified" and s.url.endswith("999-physician")


def test_google_jobs(fake_http):
    http = fake_http(
        {
            google_jobs.ENDPOINT: {
                "jobs_results": [
                    {
                        "title": "Physician - AI Trainer",
                        "company_name": "Mercor",
                        "location": "Anywhere",
                        "via": "via LinkedIn",
                        "description": "Evaluate AI model outputs as a physician.",
                        "detected_extensions": {"salary": "130–170 an hour", "schedule_type": "Contractor",
                                                "posted_at": "3 days ago", "work_from_home": True},
                        "apply_options": [{"title": "LinkedIn", "link": "https://example.com/apply"}],
                    }
                ]
            }
        }
    )
    jobs, errors = google_jobs.fetch({"queries": ["physician AI"]}, http, "secret", date(2026, 11, 4))
    assert not errors
    [job] = jobs
    assert job.posted == "2026-11-01" and job.via == "LinkedIn"
    classify(job, Settings())
    assert job.status == "qualified" and job.pay.label() == "$130–$170/hr"
    assert http.calls[0][1]["engine"] == "google_jobs"


INDEX = """
<html><body>
  <ul>
    <li><a href="/physician-emergency-medicine">Physician - Emergency Medicine</a> Remote · Contract · $200 - $300 hourly</li>
    <li><a href="/backend-engineer">Backend Engineer</a> $150k/yr</li>
    <li><a href="/cardiologist">Cardiologist</a></li>
  </ul>
</body></html>
"""
DETAIL = """
<html><head><script type="application/ld+json">
{"@context": "https://schema.org", "@type": "JobPosting", "title": "Cardiologist",
 "datePosted": "2026-10-20", "employmentType": "CONTRACTOR", "jobLocationType": "TELECOMMUTE",
 "hiringOrganization": {"@type": "Organization", "name": "Turing"},
 "baseSalary": {"@type": "MonetaryAmount", "currency": "USD",
   "value": {"@type": "QuantitativeValue", "minValue": 150, "maxValue": 220, "unitText": "HOUR"}}}
</script></head><body><h1>Cardiologist</h1></body></html>
"""


def test_careers_page_watcher(fake_http):
    base = "https://careers.example.com"
    http = fake_http(
        text_routes={
            f"{base}/robots.txt": "User-agent: *\nDisallow: /private\n",
            f"{base}/": INDEX,
            f"{base}/physician-emergency-medicine": "<html><body><h1>Physician - Emergency Medicine</h1><p>Contract role.</p></body></html>",
            f"{base}/cardiologist": DETAIL,
        }
    )
    jobs, note = pages.fetch({"name": "Example", "url": f"{base}/"}, http)
    by_title = {j.title: j for j in jobs}
    assert set(by_title) == {"Physician - Emergency Medicine", "Cardiologist"}  # the engineer link is not followed
    for j in jobs:
        classify(j, Settings())
    em = by_title["Physician - Emergency Medicine"]
    assert em.status == "qualified" and em.pay.label() == "$200–$300/hr"
    cardio = by_title["Cardiologist"]
    assert cardio.company == "Turing" and cardio.posted == "2026-10-20" and cardio.remote is True
    assert cardio.status == "qualified" and cardio.pay.label() == "$150–$220/hr"
    assert "2 physician-looking links" in note


def test_careers_page_respects_robots(fake_http):
    base = "https://blocked.example.com"
    http = fake_http(text_routes={f"{base}/robots.txt": "User-agent: *\nDisallow: /\n", f"{base}/jobs": INDEX})
    jobs, note = pages.fetch({"url": f"{base}/jobs"}, http)
    assert jobs == [] and note.startswith("skipped")
    assert all(not url.endswith("/jobs") for url, _ in http.calls)

    forbidden = fake_http(text_routes={f"{base}/robots.txt": 403, f"{base}/jobs": INDEX})
    jobs, note = pages.fetch({"url": f"{base}/jobs"}, forbidden)
    assert jobs == [] and note.startswith("skipped")


def test_careers_page_lists_links_beyond_the_limit_from_their_card(fake_http):
    base = "https://careers.example.com"
    http = fake_http(text_routes={f"{base}/": INDEX, f"{base}/physician-emergency-medicine": "<h1>EM</h1>"})
    jobs, note = pages.fetch({"url": f"{base}/", "max_links": 1}, http)
    assert {j.url for j in jobs} == {f"{base}/physician-emergency-medicine", f"{base}/cardiologist"}
    assert not any(url.endswith("/cardiologist") for url, _ in http.calls)  # listed, not opened
    assert "1 opened" in note
