# physicianAI_perdiem

This repository searches for per diem, contract and part-time work that physicians can do for AI companies, paying **$100/hr or more**. The work can be clinical or research: training and evaluating medical AI, reviewing AI answers for clinical accuracy, or clinical care on AI-first services.

- **📋 Today's list: [results/latest.md](results/latest.md).** A GitHub Action refreshes it every morning.
- **📌 Researched snapshot: [OPPORTUNITIES.md](OPPORTUNITIES.md)**, gathered on 4 Nov 2026. It has 36 physician listings whose advertised pay reaches $100–$300/hr, from Turing, Mercor, micro1, Handshake AI, OpenTrain, AfterQuery, Glass Health, Counsel Health and others.

## What it searches

1. **Company job boards.** The public job-board APIs (Greenhouse, Ashby, Lever, Workable, SmartRecruiters, Recruitee) of about 50 AI labs, AI-training platforms and health-AI companies. The full list is in [`config.yaml`](config.yaml).
2. **Careers pages without an API**: Turing, OpenTrain AI and 24-MAG. These are read on a best-effort basis, and robots.txt is respected.
   - Mercor, micro1 and Handshake AI can't be read automatically, because their pages need JavaScript or ask robots to stay out. Every report links to them so you can check by hand.
3. **Google Jobs (optional).** This reaches listings on LinkedIn, Indeed and ZipRecruiter, where many Mercor and micro1 roles appear. It needs a free [SerpApi](https://serpapi.com) key.

## How a posting makes the list

- **It is a physician role.** The title names physicians, doctors, MD/DO or a specialty. A medical or clinical "expert", "reviewer" or "tutor" role also counts when its description asks for an MD/DO, board certification, residency or similar. Roles for nurses, PAs, pharmacists, engineers and sales staff are left out.
- **The hours are flexible.** The role is contract, part-time, per diem, freelance or hourly. Full-time salaried jobs are left out.
- **The pay reaches the bar.** The posted hourly rate reaches $100/hr. Daily rates are converted at 8 hours a day.

Physician roles with flexible hours but **no posted pay** go into a second list, because many of them pay well.

New postings are marked 🆕. Any morning that brings a new match also opens a **GitHub issue**, so you get an email. To receive those emails, use *Watch → Custom → Issues* at the top of this page.

## One-time setup

1. Open the **Actions** tab and enable workflows if GitHub asks.
2. Optional: create a free SerpApi key. Add it under **Settings → Secrets and variables → Actions → New repository secret**, named `SERPAPI_KEY`. The three daily queries in `config.yaml` stay within a small monthly plan.
3. Go to **Actions → Daily search → Run workflow** to get the first list now instead of waiting for the next morning.

## Change what it looks for

Edit [`config.yaml`](config.yaml) on GitHub with the pencil icon:

- `min_hourly_usd` sets the pay bar (default 100).
- `pay_rule` controls how ranges count:
  - `top` (default) counts "$80–$150/hr", because the range reaches $100.
  - `floor` only counts ranges whose bottom is at least $100.
- To **add a company**, add its name under `companies:`. The search finds its job board by itself.
  - To check a name first, go to **Actions → Daily search → Run workflow** and type the names into *probe*.
- To **add a careers page**, add it under `pages:`.

## Run it on your own computer

```bash
pip install -r requirements.txt
```

```bash
python -m perdiem_finder search
```

```bash
python -m perdiem_finder probe "Hippocratic AI, Abridge"
```

```bash
python -m perdiem_finder pay "Compensation: \$175 - \$200 / Hourly"
```

Results go to `results/` and include `latest.md`, `latest.csv` (opens in Excel), `latest.json` and `sources.md`. To run the tests:

```bash
pip install -r requirements-dev.txt && python -m pytest
```

## Good to know

- **Pay comes from the posting.** Confirm the rate in writing before you put time into unpaid onboarding. Per-task pay ("up to $X/hr equivalent") depends on how fast you work.
- **The best-paid work often needs a profile.** Mercor, micro1, Handshake AI and DataAnnotation show more work after you sign up. Every report ends with links to check them by hand.
- **Most of these are independent-contractor (1099) roles.** There are no benefits and you handle your own taxes. Clinical roles usually need malpractice cover, so ask who provides it.
- **The schedule can pause.** GitHub may pause scheduled workflows in public repositories after 60 days without activity. If the list stops updating, open **Actions → Daily search** and click *Enable workflow*.
