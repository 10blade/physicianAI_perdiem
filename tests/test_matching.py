import pytest

from perdiem_finder import matching
from perdiem_finder.matching import Settings
from perdiem_finder.models import Job, Pay


def job(title, description="", **kw):
    return Job(source="test:x", company="Acme AI", title=title, url="https://example.com/j", job_id=title, description=description, **kw)


@pytest.mark.parametrize(
    "title, description",
    [
        ("Physician - AI Trainer", ""),
        ("AI Tutor - Radiology", ""),
        ("Cardiology Expert", ""),
        ("Resident Medical Expert (MD/DO)", ""),
        ("General Clinician (MD/DO) - HLS", ""),
        ("Medical Expert", "Requirements: MD or DO, board-certified."),
        ("Medicine Tutor", "You hold a medical degree."),
        ("AI Clinical Fellow", "Open to attending physicians and fellows."),
    ],
)
def test_physician_roles_are_relevant(title, description):
    t, _ = matching.title_score(title)
    d, _ = matching.description_score(description)
    assert matching.is_relevant(title, t, d)


@pytest.mark.parametrize(
    "title, description",
    [
        ("Physician Assistant", ""),
        ("Registered Nurse - AI Trainer", "nurses"),
        ("Software Engineer, Clinical AI", "help physicians, board-certified advisors"),
        ("Oncology Account Executive", ""),
        ("Clinical Specialist - Rockville, MD", ""),
        ("Doctor of Pharmacy Reviewer", ""),
        ("Clinical Operations Manager", "work with physicians and clinicians"),
        ("Medical Expert", ""),  # needs some sign that physicians are wanted
        ("Expert Generalist", "physicians welcome"),
        ("Medical Student Ambassador", "MD"),
    ],
)
def test_other_roles_are_not(title, description):
    t, _ = matching.title_score(title)
    d, _ = matching.description_score(description)
    assert not matching.is_relevant(title, t, d)


def test_classify_statuses():
    s = Settings()
    j = job("Physician AI Trainer", "Contract. $150/hr.")
    matching.classify(j, s)
    assert j.status == "qualified" and j.pay.label() == "$150/hr"

    j = job("Physician AI Trainer", "Contract. $40 - $80 per hour.")
    matching.classify(j, s)
    assert j.status == "below_threshold"

    j = job("Physician AI Trainer", "Independent contractor, 5-10 hours per week.")
    matching.classify(j, s)
    assert j.status == "pay_unlisted" and j.flexible

    j = job("Staff Physician", "Full benefits.", employment_type="FullTime")
    matching.classify(j, s)
    assert j.status == "full_time_unlisted"

    j = job("Staff Physician", "", structured_pay=Pay(96.15, 120.19, "year"))
    matching.classify(j, s)
    assert j.status == "salaried"

    j = job("Senior Software Engineer", "$150/hr")
    matching.classify(j, s)
    assert j.status == "irrelevant" and j.pay is None


def test_pay_rule_floor():
    j = job("Physician Reviewer", "$80 - $150 per hour")
    matching.classify(j, Settings(pay_rule="top"))
    assert j.status == "qualified"
    matching.classify(j, Settings(pay_rule="floor"))
    assert j.status == "below_threshold"


def test_forty_hours_is_not_part_time():
    j = job("Staff Physician", "Schedule: 40 hours per week, Monday to Friday.")
    matching.classify(j, Settings())
    assert j.status == "full_time_unlisted"


def test_broad_results_must_mention_ai():
    j = job("Physician - Urgent Care", "Per diem shifts, $150/hr.", requires_ai_context=True)
    j.company = "Main Street Clinic"
    matching.classify(j, Settings())
    assert j.status == "irrelevant"
    j = job("Physician - AI Trainer", "Review LLM answers. $150/hr.", requires_ai_context=True)
    j.company = "Main Street Clinic"
    matching.classify(j, Settings())
    assert j.status == "qualified"
    j = job("Physician", "Per diem, $150/hr.", requires_ai_context=True)
    j.company = "Hippocratic AI"  # an AI company's own posting counts
    matching.classify(j, Settings())
    assert j.status == "qualified"


def test_json_ld_employment_types_count_as_flexible():
    for kind in ("CONTRACTOR", "PART_TIME", "PER_DIEM", "Contract", "Temporary"):
        assert matching.is_flexible(job("Physician", employment_type=kind))
    assert not matching.is_flexible(job("Physician", employment_type="FULL_TIME"))
