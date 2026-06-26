"""
db.py

SQLite persistence layer + the analytical SQL that powers the risk radar.

Everything that can reasonably be expressed as SQL is expressed as SQL --
this is deliberate. The point of routing documentation scores into a
relational schema instead of just storing them in memory/session state is
that the value of this tool is the aggregate analytics, not the single-note
feedback, and aggregate analytics over time-series + categorical data is
exactly what SQL is for.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

DB_PATH = Path(__file__).parent / "borderplus.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS facilities (
    facility_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    city          TEXT NOT NULL,
    country       TEXT NOT NULL DEFAULT 'Germany'
);

CREATE TABLE IF NOT EXISTS nurses (
    nurse_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    facility_id   INTEGER NOT NULL REFERENCES facilities(facility_id),
    origin_country TEXT NOT NULL,
    placement_date TEXT NOT NULL,    -- ISO date
    german_level  TEXT NOT NULL      -- B1, B2, C1
);

CREATE TABLE IF NOT EXISTS submissions (
    submission_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    nurse_id             INTEGER NOT NULL REFERENCES nurses(nurse_id),
    submitted_at         TEXT NOT NULL,   -- ISO datetime
    tenure_week          INTEGER NOT NULL, -- weeks since placement_date at submission time
    raw_text             TEXT NOT NULL,
    completeness_score   REAL NOT NULL,
    specificity_score    REAL NOT NULL,
    overall_score        REAL NOT NULL,
    word_count           INTEGER NOT NULL,
    measurable_anchors   INTEGER NOT NULL,
    categories_present   TEXT NOT NULL,   -- JSON list
    categories_missing   TEXT NOT NULL,   -- JSON list
    high_stakes_missing  TEXT NOT NULL,   -- JSON list
    vague_phrases_found  TEXT NOT NULL,   -- JSON list
    primary_gap          TEXT
);

CREATE INDEX IF NOT EXISTS idx_submissions_nurse ON submissions(nurse_id);
CREATE INDEX IF NOT EXISTS idx_submissions_time ON submissions(submitted_at);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def is_empty() -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM nurses").fetchone()
        return row["c"] == 0


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def insert_facility(name: str, city: str, country: str = "Germany") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO facilities (name, city, country) VALUES (?, ?, ?)",
            (name, city, country),
        )
        return cur.lastrowid


def insert_nurse(name: str, facility_id: int, origin_country: str,
                  placement_date: str, german_level: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO nurses (name, facility_id, origin_country,
                                    placement_date, german_level)
               VALUES (?, ?, ?, ?, ?)""",
            (name, facility_id, origin_country, placement_date, german_level),
        )
        return cur.lastrowid


def insert_submission(nurse_id: int, submitted_at: str, tenure_week: int,
                       raw_text: str, score_dict: dict) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO submissions (
                   nurse_id, submitted_at, tenure_week, raw_text,
                   completeness_score, specificity_score, overall_score,
                   word_count, measurable_anchors, categories_present,
                   categories_missing, high_stakes_missing,
                   vague_phrases_found, primary_gap
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                nurse_id, submitted_at, tenure_week, raw_text,
                score_dict["completeness_score"], score_dict["specificity_score"],
                score_dict["overall_score"], score_dict["word_count"],
                score_dict["measurable_anchors"],
                json.dumps(score_dict["categories_present"]),
                json.dumps(score_dict["categories_missing"]),
                json.dumps(score_dict["high_stakes_missing"]),
                json.dumps(score_dict["vague_phrases_found"]),
                score_dict["primary_gap"],
            ),
        )
        return cur.lastrowid


def bulk_insert_submissions(rows: Iterable[tuple]) -> None:
    with get_conn() as conn:
        conn.executemany(
            """INSERT INTO submissions (
                   nurse_id, submitted_at, tenure_week, raw_text,
                   completeness_score, specificity_score, overall_score,
                   word_count, measurable_anchors, categories_present,
                   categories_missing, high_stakes_missing,
                   vague_phrases_found, primary_gap
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )


# ---------------------------------------------------------------------------
# Reads / dataframes (returned as list[dict] so app.py can hand straight to
# pandas without this module depending on pandas itself)
# ---------------------------------------------------------------------------

def _rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


def list_facilities() -> list[dict]:
    with get_conn() as conn:
        return _rows_to_dicts(conn.execute(
            "SELECT * FROM facilities ORDER BY name"
        ).fetchall())


def list_nurses() -> list[dict]:
    with get_conn() as conn:
        return _rows_to_dicts(conn.execute(
            """SELECT n.*, f.name AS facility_name, f.city AS facility_city
               FROM nurses n JOIN facilities f ON n.facility_id = f.facility_id
               ORDER BY n.name"""
        ).fetchall())


def get_nurse(nurse_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT n.*, f.name AS facility_name, f.city AS facility_city
               FROM nurses n JOIN facilities f ON n.facility_id = f.facility_id
               WHERE n.nurse_id = ?""",
            (nurse_id,),
        ).fetchone()
        return dict(row) if row else None


def nurse_submissions(nurse_id: int) -> list[dict]:
    with get_conn() as conn:
        return _rows_to_dicts(conn.execute(
            """SELECT * FROM submissions WHERE nurse_id = ?
               ORDER BY submitted_at""",
            (nurse_id,),
        ).fetchall())


# --- Analytics queries -----------------------------------------------------

def q_cohort_error_frequency() -> list[dict]:
    """How often each AEDL category is missing, across all submissions.
    This is the single most actionable cohort-level number: it tells
    BorderPlus which category to prioritize in the shared 'Learn' content,
    independent of any one nurse."""
    with get_conn() as conn:
        rows = conn.execute("SELECT categories_missing FROM submissions").fetchall()
    counts: dict[str, int] = {}
    total = len(rows)
    for r in rows:
        for cat in json.loads(r["categories_missing"]):
            counts[cat] = counts.get(cat, 0) + 1
    return sorted(
        [{"category": k, "missing_count": v,
          "missing_rate_pct": round(100 * v / total, 1) if total else 0}
         for k, v in counts.items()],
        key=lambda x: -x["missing_count"],
    )


def q_nurse_trend(nurse_id: int) -> list[dict]:
    """Weekly average overall_score for one nurse, in tenure-week order --
    this is what a slope/decline detector runs on."""
    with get_conn() as conn:
        return _rows_to_dicts(conn.execute(
            """SELECT tenure_week,
                      ROUND(AVG(overall_score), 1) AS avg_score,
                      COUNT(*) AS n_notes
               FROM submissions
               WHERE nurse_id = ?
               GROUP BY tenure_week
               ORDER BY tenure_week""",
            (nurse_id,),
        ).fetchall())


def q_cohort_baseline_by_week() -> list[dict]:
    """Cohort-wide average score per tenure week, across ALL nurses. Used
    as the comparison baseline: a nurse below this line for her own tenure
    week is underperforming relative to peers at the same stage, not just
    relative to her own past -- which is the comparison that actually
    matters for a risk flag (a nurse can be 'improving' and still be behind
    where she should be)."""
    with get_conn() as conn:
        return _rows_to_dicts(conn.execute(
            """SELECT tenure_week,
                      ROUND(AVG(overall_score), 1) AS cohort_avg_score,
                      COUNT(DISTINCT nurse_id) AS n_nurses
               FROM submissions
               GROUP BY tenure_week
               ORDER BY tenure_week"""
        ).fetchall())


def q_risk_radar() -> list[dict]:
    """Core internal-facing query: one row per nurse with her latest score,
    her trend direction (recent 2 weeks vs prior 2 weeks), and how she
    compares to the cohort baseline for her current tenure week. This is
    the table that turns 'reactive complaint-handling' into 'proactive
    intervention list.'"""
    with get_conn() as conn:
        nurses = _rows_to_dicts(conn.execute(
            """SELECT n.nurse_id, n.name, f.name AS facility_name,
                      n.origin_country, n.placement_date
               FROM nurses n JOIN facilities f ON n.facility_id = f.facility_id"""
        ).fetchall())

        result = []
        for nurse in nurses:
            subs = _rows_to_dicts(conn.execute(
                """SELECT tenure_week, overall_score, submitted_at
                   FROM submissions WHERE nurse_id = ? ORDER BY tenure_week""",
                (nurse["nurse_id"],),
            ).fetchall())
            if not subs:
                continue

            weeks = sorted(set(s["tenure_week"] for s in subs))
            latest_week = weeks[-1]
            latest_scores = [s["overall_score"] for s in subs if s["tenure_week"] == latest_week]
            latest_avg = sum(latest_scores) / len(latest_scores)

            recent_weeks = [w for w in weeks if w > latest_week - 2]
            prior_weeks = [w for w in weeks if w <= latest_week - 2]
            recent_scores = [s["overall_score"] for s in subs if s["tenure_week"] in recent_weeks]
            prior_scores = [s["overall_score"] for s in subs if s["tenure_week"] in prior_weeks]
            recent_avg = sum(recent_scores) / len(recent_scores) if recent_scores else latest_avg
            prior_avg = sum(prior_scores) / len(prior_scores) if prior_scores else recent_avg
            trend_delta = round(recent_avg - prior_avg, 1)

            cohort_row = conn.execute(
                """SELECT AVG(overall_score) AS avg_score FROM submissions
                   WHERE tenure_week = ?""",
                (latest_week,),
            ).fetchone()
            cohort_avg = cohort_row["avg_score"] if cohort_row and cohort_row["avg_score"] is not None else latest_avg
            vs_cohort = round(latest_avg - cohort_avg, 1)

            if latest_avg < 50 or (trend_delta < -8 and latest_avg < 70):
                risk_tier = "High"
            elif latest_avg < 65 or trend_delta < -3 or vs_cohort < -8:
                risk_tier = "Medium"
            else:
                risk_tier = "Low"

            result.append({
                "nurse_id": nurse["nurse_id"],
                "name": nurse["name"],
                "facility": nurse["facility_name"],
                "origin_country": nurse["origin_country"],
                "tenure_week": latest_week,
                "latest_score": round(latest_avg, 1),
                "trend_delta": trend_delta,
                "vs_cohort_baseline": vs_cohort,
                "risk_tier": risk_tier,
                "n_notes": len(subs),
            })

        result.sort(key=lambda r: ({"High": 0, "Medium": 1, "Low": 2}[r["risk_tier"]], r["latest_score"]))
        return result


def q_facility_summary() -> list[dict]:
    """Facility-level rollup -- distinguishes 'this nurse is struggling'
    from 'this facility produces struggling nurses,' which is a different
    intervention (facility onboarding process vs. individual coaching)."""
    with get_conn() as conn:
        return _rows_to_dicts(conn.execute(
            """SELECT f.name AS facility_name, f.city,
                      COUNT(DISTINCT n.nurse_id) AS n_nurses,
                      ROUND(AVG(s.overall_score), 1) AS avg_score,
                      ROUND(MIN(s.overall_score), 1) AS min_score
               FROM facilities f
               JOIN nurses n ON n.facility_id = f.facility_id
               JOIN submissions s ON s.nurse_id = n.nurse_id
               GROUP BY f.facility_id
               ORDER BY avg_score ASC"""
        ).fetchall())


def q_origin_country_summary() -> list[dict]:
    """Does documentation quality correlate with origin country / German
    level at intake? If yes, that's an argument for pre-placement language
    investment, not post-placement remediation -- a genuinely different
    business decision than what any single nurse's dashboard would surface."""
    with get_conn() as conn:
        return _rows_to_dicts(conn.execute(
            """SELECT n.origin_country, n.german_level,
                      COUNT(DISTINCT n.nurse_id) AS n_nurses,
                      ROUND(AVG(s.overall_score), 1) AS avg_score
               FROM nurses n JOIN submissions s ON s.nurse_id = n.nurse_id
               GROUP BY n.origin_country, n.german_level
               ORDER BY avg_score ASC"""
        ).fetchall())
