# NursePlus — Documentation Quality & Placement Risk Radar

Built for the BorderPlus Product Analyst Intern JD ("Do" module: care-note
documentation feedback against medical/compliance guidelines).

## The actual problem this solves

Foreign nurses placed in Germany rarely fail clinically — they fail at
*Pflegedokumentation* (care documentation), because it's a compliance
artifact: it determines a patient's reimbursable care level (Pflegegrad)
and is what gets reviewed in MDK/MDS audits. BorderPlus's real exposure
isn't "a nurse writes bad notes" — it's that they have **no signal on
placement risk until a hospital escalates a complaint**, by which point
they've already absorbed the cost of a bad placement and strained a
repeat B2B account.

This tool reframes "Do" from a single-nurse feedback feature into the
**sensor layer for a placement risk radar**: every care note a nurse
submits gets scored, logged, and aggregated — so degrading documentation
quality shows up as a leading indicator before a hospital ever picks up
the phone.

## What's actually built

- **Rubric-based scoring engine** (`scorer.py`) — deterministic, fully
  explainable, no external API. Checks documentation against 8
  AEDL-aligned categories (the real structuring framework used in German
  Pflegedokumentation), flags vague boilerplate phrasing common in actual
  audit findings (e.g. "wie gewohnt"), rewards measurable detail
  (numbers, units, timestamps), and penalizes missing high-stakes
  categories (vitals, medication timing, wound care) extra, because those
  are disproportionately linked to reimbursement disputes.
- **Relational schema + real SQL analytics** (`db.py`) — facilities,
  nurses, submissions. Every dashboard number is a SQL query, not a
  pandas afterthought: cohort error frequency, per-nurse weekly trend vs.
  cohort baseline at the same tenure week, a risk-tiering query that
  compares recent vs. prior performance, facility-level rollups, and
  origin-country/German-level correlation.
- **Two-audience UI** (`app.py`) — nurse-facing submission + trend view,
  and a BorderPlus-internal Risk Radar that's the actual point of the
  product: a sortable, filterable list of nurses by risk tier, with a
  transparent (not black-box) scoring rule explained inline.
- **Rubric Reference page** — the scoring logic is inspectable in the UI
  itself. A nurse or facility partner can see exactly what's being
  checked and why, and exactly what the tool does *not* claim (it doesn't
  verify medical correctness; it isn't a substitute for real MDK audit
  criteria).
- **Synthetic seed data** (`seed.py`) — 30 nurses across 5 facilities, 10
  weeks of generated submissions with engineered trajectories (improving,
  declining, stable-strong, stable-weak) and one structurally weaker
  facility, so the analytics queries have real signal to find on first
  run. Clearly demo data, not a claim of real placements.

## Why no LLM API

Scoring is intentionally rule-based, not LLM-based. This makes the tool
free to run, fully auditable (every score traces to an explicit rule),
and — more importantly for the interview — it's the more defensible
artifact: anyone can wrap a documentation-checker in a prompt; building
the actual rubric logic and the SQL analytics on top of it is the part
that demonstrates judgment.

## Running locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

First run auto-creates `borderplus.db` (SQLite) and seeds it. Delete the
`.db` file to reset.

## File structure

```
app.py        Streamlit UI, all 5 views
scorer.py     Rubric engine (pure functions, no I/O)
db.py         SQLite schema + all SQL analytics queries
seed.py       Synthetic data generator (idempotent)
requirements.txt
```
