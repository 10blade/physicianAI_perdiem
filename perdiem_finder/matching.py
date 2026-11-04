"""Decide whether a posting is a flexible physician task, and whether it pays enough."""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import pay as pay_mod
from .models import Job

_SPECIALIST = (
    r"(?:radiolog|patholog|cardiolog|dermatolog|oncolog|neurolog|anesthesiolog|anaesthesiolog|ophthalmolog"
    r"|gastroenterolog|endocrinolog|rheumatolog|pulmonolog|nephrolog|urolog|hematolog|haematolog"
    r"|otolaryngolog|neonatolog)ists?"
    r"|psychiatrists?|p(?:a)?ediatricians?|internists?|hospitalists?|intensivists?|obstetricians?"
    r"|gyn(?:a)?ecologists?|geriatricians?|allergists?|surgeons?|physiatrists?"
)
_SPECIALTY = (
    r"radiology|pathology|cardiology|dermatology|oncology|neurology|an(?:a)?esthesiology|ophthalmology"
    r"|gastroenterology|endocrinology|rheumatology|pulmonology|nephrology|urology|h(?:a)?ematology"
    r"|otolaryngology|neonatology|psychiatry|p(?:a)?ediatrics|obstetrics|gyn(?:a)?ecology|ob\s*/?\s*gyn"
    r"|neurosurgery|orthop(?:a)?edics?|general\s+surgery|emergency\s+medicine|internal\s+medicine"
    r"|family\s+medicine|critical\s+care|primary\s+care|infectious\s+diseases?|sports\s+medicine"
    r"|palliative\s+care|geriatrics|nuclear\s+medicine|plastic\s+surgery|physical\s+medicine"
    r"|preventive\s+medicine|occupational\s+medicine|hospital\s+medicine"
)
# "MD" as a degree, not the Maryland abbreviation ("Bethesda, MD 20814") or "MD Anderson".
_MD = r"(?<!, )(?<!,)(?<![A-Za-z])M\.?D\.?s?(?![A-Za-z])(?!\s*\d{5})(?!\s+Anderson)"
_DO = r"(?<![A-Za-z])D\.O\.(?![A-Za-z])|\bMD\s*/\s*DO\b|\bDO\s*/\s*MD\b|\bMD\s+or\s+DO\b"
_PHYSICIAN = r"\bphysicians?\b(?!\s+(?:assistants?|associates?|extenders?)\b)"
_DOCTOR = (
    r"\bdoctors?\b(?!\s+of\s+(?:pharmacy|philosophy|nursing|physical|dental|psychology|education"
    r"|business|chiropractic|audiology|optometry|occupational))"
)

# (weight, pattern, case_sensitive). A title scores its single strongest term.
_TITLE_TERMS = [
    (5, _PHYSICIAN, False),
    (5, _DOCTOR, False),
    (5, _MD, True),
    (5, _DO, True),
    (5, r"\bMBBS\b|\bMBChB\b", True),
    (5, rf"\b(?:{_SPECIALIST})\b", False),
    (4, rf"\b(?:{_SPECIALTY})\b", False),
    (4, r"\bmedical\s+director\b", False),
    (
        3,
        r"\b(?:medical|clinical|healthcare|health)\s+(?:experts?|specialists?|reviewers?|advisors?|advisers?"
        r"|consultants?|annotators?|evaluators?|raters?|trainers?|tutors?|smes?|fellows?"
        r"|subject[-\s]matter\s+experts?)\b",
        False,
    ),
    (3, r"\bclinicians?\b|\bmedicine\b", False),
    (2, r"\bmedical\b|\bclinical\b|\bhealthcare\b|\bbiomedical\b", False),
    (1, r"\bhealth\b", False),
]
# Words that describe the kind of task an AI company pays experts for.
_TASK_WORDS = re.compile(
    r"\b(?:tutor|trainer|expert|reviewer|evaluator|annotator|rater|specialist|consultant|advisor|adviser"
    r"|fellow|labeler|grader|red[-\s]?team\w*|researcher)s?\b",
    re.I,
)
# (weight, pattern, case_sensitive). The description adds up distinct terms, capped.
_DESC_TERMS = [
    (2, _PHYSICIAN, False),
    (2, r"\bmedical\s+doctors?\b", False),
    (2, r"\bboard[-\s](?:certified|eligible)\b", False),
    (2, r"\bmedical\s+(?:degree|licen[cs]e|licensure|school)\b", False),
    (2, r"\bresidency\b|\battending\b|\bfellowship[-\s]trained\b", False),
    (2, r"\bUSMLE\b|\bMBBS\b|\bECFMG\b|\bCOMLEX\b", True),
    (2, _MD, True),
    (2, _DO, True),
    (1, rf"\b(?:{_SPECIALIST})\b|\b(?:{_SPECIALTY})\b", False),
    (1, r"\bclinicians?\b|\bpatient\s+care\b|\bclinical\s+(?:practice|experience)\b", False),
]
_DESC_CAP = 6

# Titles that are never physician tasks, whatever else they mention.
_HARD_NEGATIVE = re.compile(
    r"\b(?:software|engineer(?:ing)?|developer|devops|data\s+(?:engineer|scientist|analyst)|account\s+executive"
    r"|account\s+manager|sales|marketing|recruit(?:er|ing)|talent|sourcer|product\s+manager|designer|attorney"
    r"|lawyer|counsel|paralegal|accountant|payroll|finance|investor)\b",
    re.I,
)
# Titles for other professions; skipped unless the title also names physicians.
_SOFT_NEGATIVE = re.compile(
    r"\b(?:nurse|nursing|nurse\s+practitioner|physician\s+assistant|physician\s+associate|pharmacist|pharmacy"
    r"|dentist|dental|veterinar\w*|therapist|psychologist|social\s+worker|dietitian|nutritionist|medical\s+assistant"
    r"|medical\s+coder|coding|scribe|phlebotom\w*|technician|technologist|intern(?:ship)?|students?|coordinator"
    r"|receptionist|billing|biller|administrative|analyst|program\s+manager|project\s+manager|operations"
    r"|customer\s+(?:success|support|service)|support\s+specialist)\b",
    re.I,
)
_SOFT_NEGATIVE_CS = re.compile(r"\b(?:RN|LPN|LVN|NP|CRNA|PA-C|PharmD|DDS|DMD|DPT)\b")

# Signs of contract, per diem or part-time work.
_FLEX_TITLE = re.compile(
    r"\b(?:contract(?:or)?|freelance\w*|part[-\s]?time|per[-\s]?diem|hourly|flexible|consultant|consulting"
    r"|advisor|adviser|fractional|prn|temporary|ai\s+(?:tutor|trainer))\b",
    re.I,
)
_FLEX_DESC = re.compile(
    r"\b(?:independent\s+contractor|contract\s+(?:role|position|basis|opportunity|engagement|work)"
    r"|contractor|freelance\w*|part[-\s]?time|per[-\s]?diem|paid\s+hourly|hourly\s+(?:rate|pay|contract)"
    r"|project[-\s]based|1099|as[-\s]needed|flexible\s+(?:hours|schedule|commitment|time)"
    r"|set\s+your\s+own\s+(?:hours|schedule)|work\s+on\s+your\s+own\s+schedule|asynchronous)\b",
    re.I,
)
# "5-10 hours per week" signals part-time work; "40 hours per week" does not.
_FEW_HOURS = re.compile(
    r"\b(?:[1-9]|[12]\d|3[0-5])(?:\s*(?:-|–|—|to)\s*(?:[1-9]|[12]\d|3[0-5]))?\+?\s*(?:hours?|hrs?)"
    r"\s*(?:per|/|a|each)\s*(?:week|wk)\b",
    re.I,
)
_FLEX_TYPE = re.compile(r"contract|part|temp|freelanc|per[\s_-]*diem|hourly|casual|seasonal|consult|prn", re.I)
_FULLTIME_TYPE = re.compile(r"full", re.I)

# For broad searches (Google Jobs), the posting must be about AI work.
_AI_UPPER = re.compile(r"(?<![A-Za-z])(?:AI|LLMs?|RLHF|GPT|NLP|ML)(?![A-Za-z])")
_AI_WORDS = re.compile(
    r"\b(?:artificial\s+intelligence|machine\s+learning|large\s+language\s+models?|generative|"
    r"model\s+(?:training|evaluation|outputs?|responses?)|training\s+data|data\s+(?:labell?ing|annotation)"
    r"|annotat\w+|chatbots?|foundation\s+models?|reinforcement\s+learning|prompt\s+writing|red[-\s]?teaming)\b",
    re.I,
)

REPORTED = ("qualified", "pay_unlisted")


def _compile(terms):
    return [(w, re.compile(p) if cs else re.compile(p, re.I), p) for w, p, cs in terms]


_TITLE_RE = _compile(_TITLE_TERMS)
_DESC_RE = _compile(_DESC_TERMS)


@dataclass
class Settings:
    min_hourly: float = 100.0
    pay_rule: str = "top"  # "top": the top of the range must reach min_hourly; "floor": the bottom must
    include_pay_unlisted: bool = True
    include_salaried: bool = False
    workers: int = 8
    max_enrich_per_board: int = 40


def title_score(title: str) -> tuple[int, list[str]]:
    best, terms = 0, []
    for weight, rx, _ in _TITLE_RE:
        m = rx.search(title or "")
        if m:
            terms.append(m.group(0).strip())
            best = max(best, weight)
    if 3 <= best < 5 and _TASK_WORDS.search(title or ""):
        best += 1  # "AI Tutor - Radiology", "Medical Expert"
    return best, terms


def description_score(text: str) -> tuple[int, list[str]]:
    total, terms = 0, []
    for weight, rx, _ in _DESC_RE:
        m = rx.search(text or "")
        if m:
            terms.append(m.group(0).strip())
            total += weight
    return min(total, _DESC_CAP), terms


def is_relevant(title: str, t_score: int, d_score: int) -> bool:
    if _HARD_NEGATIVE.search(title or ""):
        return False
    if t_score < 5 and (_SOFT_NEGATIVE.search(title or "") or _SOFT_NEGATIVE_CS.search(title or "")):
        return False
    if t_score >= 5:
        return True
    if t_score >= 3:
        return t_score + d_score >= 5
    if t_score == 2:
        return d_score >= 4  # "Clinical ..." needs strong evidence that physicians are wanted
    return False


def has_ai_context(*texts: str) -> bool:
    blob = " ".join(t for t in texts if t)
    return bool(_AI_UPPER.search(blob) or _AI_WORDS.search(blob))


def is_flexible(job: Job) -> bool:
    kind = job.employment_type or ""
    if _FLEX_TYPE.search(kind):
        return True
    if job.pay and job.pay.basis in ("hour", "day"):
        return True
    if _FULLTIME_TYPE.search(kind):
        return False
    text = job.description or ""
    return bool(_FLEX_TITLE.search(job.title or "") or _FLEX_DESC.search(text) or _FEW_HOURS.search(text))


def classify(job: Job, settings: Settings) -> None:
    """Score a posting and set ``job.status`` to one of:

    qualified           physician task paying at least ``min_hourly`` per hour
    pay_unlisted        physician task with flexible hours but no pay in the posting
    below_threshold     physician task paying less than ``min_hourly``
    salaried            physician role paid as an annual salary
    full_time_unlisted  physician role, full-time, no pay listed
    irrelevant          not a physician task
    """
    t_score, t_terms = title_score(job.title)
    d_score, d_terms = description_score(job.description)
    job.title_score = t_score
    job.physician_score = t_score + d_score
    job.matched_terms = list(dict.fromkeys(t_terms + d_terms))[:8]
    relevant = is_relevant(job.title, t_score, d_score)
    if relevant and job.requires_ai_context:
        relevant = has_ai_context(job.title, job.description, job.company)
    if not relevant:
        job.status = "irrelevant"
        return

    job.pay = pay_mod.combine(
        [
            job.structured_pay,
            *pay_mod.parse_text(job.pay_text, require_currency=False),
            *pay_mod.parse_text(job.description),
        ]
    )
    job.flexible = is_flexible(job)
    pay = job.pay
    if pay and pay.basis in ("hour", "day"):
        if settings.pay_rule == "floor":
            ok = pay.lo is not None and pay.lo >= settings.min_hourly
        else:
            ok = pay.top is not None and pay.top >= settings.min_hourly
        job.status = "qualified" if ok else "below_threshold"
    elif pay:
        job.status = "salaried"
    else:
        job.status = "pay_unlisted" if job.flexible else "full_time_unlisted"


def reported_statuses(settings: Settings) -> tuple[str, ...]:
    statuses = ["qualified"]
    if settings.include_pay_unlisted:
        statuses.append("pay_unlisted")
    if settings.include_salaried:
        statuses.append("salaried")
    return tuple(statuses)
