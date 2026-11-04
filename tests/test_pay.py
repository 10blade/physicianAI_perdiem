import pytest

from perdiem_finder import pay
from perdiem_finder.models import Pay


def read(text, require_currency=True):
    combined = pay.combine(pay.parse_text(text, require_currency=require_currency))
    return (combined.lo, combined.hi, combined.basis) if combined else None


@pytest.mark.parametrize(
    "text, expected",
    [
        ("$100/hr", (100, 100, "hour")),
        ("$100 - $150 per hour", (100, 150, "hour")),
        ("Pay: $120–$180/hour", (120, 180, "hour")),
        ("USD 150/hr", (150, 150, "hour")),
        ("150 USD per hour", (150, 150, "hour")),
        ("Compensation: $175 - $200 / Hourly", (175, 200, "hour")),
        ("$150 / hr Hourly contract Remote", (150, 150, "hour")),
        ("130-150 USD/hr, depending on experience", (130, 150, "hour")),
        ("Salary: 80.00 - 120.00 USD / Hour", (80, 120, "hour")),
        ("between $100 and $175 an hour", (100, 175, "hour")),
        ("Hourly rate: $130 - $160", (130, 160, "hour")),
        ("$40 - $120 Hourly", (40, 120, "hour")),
        ("Earn up to $200/hr", (None, 200, "hour")),
        ("Upto $130/hr", (None, 130, "hour")),
        ("$120+/hr", (120, None, "hour")),
        ("starting at $90 per hour", (90, None, "hour")),
        ("$1,200 per day", (150, 150, "day")),
        ("$200k/yr", (96.15, 96.15, "year")),
        ("$100-150k a year", (48.08, 72.12, "year")),
        ("US based candidates: $45/hour - $100/hour depending on factors", (45, 100, "hour")),
        ("$50/hr for nurses, $150/hr for physicians", (50, 150, "hour")),
        ("$150/hr (about $300,000 a year)", (150, 150, "hour")),
    ],
)
def test_reads_pay(text, expected):
    assert read(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "10-20 hours per week",
        "Commit 15 hrs/week",
        "€100/hr",
        "CA$100/hr",
        "CAD 100 per hour",
        "$5 per task",
        "$500 referral bonus",
        "401(k) and $2,000 annual bonus",
        "100-150 an hour",  # no currency marker in free text
    ],
)
def test_ignores_non_pay(text):
    assert read(text) is None


def test_salary_fields_may_omit_currency():
    assert read("100–150 an hour", require_currency=False) == (100, 150, "hour")


def test_structured_ranges():
    assert pay.from_range(120, 180, "1 HOUR") == Pay(120, 180, "hour", "")
    assert pay.from_range(100, 160, "per-hour-wage").basis == "hour"
    assert pay.from_range(1200, 1600, "per-day-wage") == Pay(150, 200, "day", "")
    assert pay.from_range(150000, 200000, "1 YEAR").basis == "year"
    # Greenhouse ranges have no interval: size decides.
    assert pay.from_range(150, 250, "").basis == "hour"
    assert pay.from_range(180000, 220000, "").basis == "year"
    assert pay.from_range(5000, 8000, "") is None
    assert pay.from_range(100, 150, "1 HOUR", currency="EUR") is None
    assert pay.from_range(None, None, "1 HOUR") is None


def test_combine_prefers_hourly_and_keeps_open_ends():
    hourly = Pay(130, 160, "hour", "a")
    yearly = Pay(90, 90, "year", "b")
    assert pay.combine([yearly, hourly]).basis == "hour"
    assert pay.combine([Pay(120, None, "hour")]).label() == "$120+/hr"
    assert pay.combine([Pay(None, 200, "hour")]).label() == "up to $200/hr"
    assert pay.combine([Pay(100, 150, "hour"), Pay(None, 200, "hour")]).label() == "$100–$200/hr"
    assert pay.combine([]) is None


def test_labels():
    assert Pay(150, 150, "hour").label() == "$150/hr"
    assert Pay(62.5, 62.5, "hour").label() == "$62.50/hr"
    assert Pay(150, 187.5, "day").label() == "≈$150–$187.50/hr (daily rate)"
