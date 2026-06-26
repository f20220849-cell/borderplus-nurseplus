"""
app.py

NursePlus - Documentation Quality & Placement Risk Radar

Two audiences, one underlying dataset:
  1. Nurse-facing: submit a care note, get rubric-based feedback, get
     routed to the right "Learn" module for her actual gap.
  2. BorderPlus-facing: a risk radar across the whole nurse cohort, so
     placement risk is visible before a facility escalates a complaint.

No external LLM API is used. Scoring is a deterministic rubric engine
(scorer.py) -- this is intentional: it's auditable, free to run, and the
thing actually being demonstrated (SQL + rubric design + analytics) does
not require an LLM in the loop.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pandas as pd
import streamlit as st

import db
from scorer import (CATEGORIES, CATEGORY_LABELS, CUE_TRANSLATIONS,
                     LEARN_MODULE_MAP, VAGUE_PHRASE_TRANSLATIONS, score_note)
from seed import run_seed

st.set_page_config(
    page_title="NursePlus | Documentation Risk Radar",
    page_icon=None,
    layout="wide",
)

# ---------------------------------------------------------------------------
# Minimal, restrained styling -- no gradients, no emoji avalanche, system
# fonts. The goal is "internal ops tool a hospital network would actually
# run," not "AI demo."
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    /* Every rule below sets both background AND text color explicitly --
       never just one -- so this can't go low-contrast regardless of the
       visitor's OS/browser dark-mode setting. */
    .stApp, .stApp p, .stApp span, .stApp label, .stApp div { color: #1a1a1a; }
    .stApp { background-color: #ffffff; }
    h1, h2, h3 { font-weight: 600; letter-spacing: -0.01em; color: #1a1a1a; }
    div[data-testid="stMetricValue"] { font-size: 1.6rem; color: #1a1a1a; }
    div[data-testid="stMetricLabel"] { color: #444444; }
    .risk-high { color: #b3261e; font-weight: 600; }
    .risk-medium { color: #97650f; font-weight: 600; }
    .risk-low { color: #2e7d32; font-weight: 600; }
    .small-note { color: #444444; font-size: 0.85rem; }
    section[data-testid="stSidebar"] { background-color: #f1f3f4; }
    section[data-testid="stSidebar"] * { color: #1a1a1a !important; }
    .block-container { padding-top: 1.5rem; max-width: 1150px; }
    div[data-testid="stExpander"] { background-color: #ffffff; border: 1px solid #ddd; }
    div[data-testid="stExpander"] * { color: #1a1a1a; }
    code, .stCode, pre { color: #1a1a1a !important; background-color: #f1f3f4 !important; }
    [data-testid="stCodeBlock"] * { color: #1a1a1a !important; }
    /* Tabs: inactive tab labels default to a low-contrast theme color --
       force both states explicitly. */
    button[data-baseweb="tab"] { color: #1a1a1a !important; }
    button[data-baseweb="tab"] p { color: #1a1a1a !important; }
    div[data-baseweb="tab-highlight"] { background-color: #b3261e !important; }
    /* Charts (st.bar_chart / st.line_chart use vega-lite under the hood,
       which inherits a dark template if the browser is in dark mode --
       force a white chart background + dark axis text regardless. */
    div[data-testid="stVegaLiteChart"] { background-color: #ffffff !important; }
    div[data-testid="stVegaLiteChart"] canvas { background-color: #ffffff !important; }
</style>
""", unsafe_allow_html=True)

db.init_db()
run_seed()


def risk_css_class(tier: str) -> str:
    return {"High": "risk-high", "Medium": "risk-medium", "Low": "risk-low"}.get(tier, "")


# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------
st.sidebar.title("NursePlus")
st.sidebar.caption("Documentation Quality & Placement Risk")

page = st.sidebar.radio(
    "View",
    [
        "Submit a Care Note",
        "My Documentation Trend",
        "Risk Radar (BorderPlus Internal)",
        "Cohort Analytics",
        "Rubric Reference",
    ],
    label_visibility="collapsed",
)

st.sidebar.divider()
st.sidebar.caption(
    "Demo dataset: 20 synthetic nurse profiles across 5 facilities, "
    "10 weeks of generated submissions. Seeded once, stored in "
    "borderplus.db (SQLite)."
)

nurses = db.list_nurses()
nurse_options = {f"{n['name']} - {n['facility_name']}": n["nurse_id"] for n in nurses}


# ---------------------------------------------------------------------------
# Page: Submit a Care Note
# ---------------------------------------------------------------------------
if page == "Submit a Care Note":
    st.title("Submit a Care Note")
    st.markdown(
        "Paste a shift report (Pflegebericht) below. The tool checks "
        "**structure and specificity** against the AEDL documentation "
        "categories -- it does not evaluate medical correctness or replace "
        "clinical judgment."
    )

    col_left, col_right = st.columns([3, 2])

    with col_left:
        selected_label = st.selectbox("Nurse", list(nurse_options.keys()))
        nurse_id = nurse_options[selected_label]

        sample = (
            "Patient wirkt heute muede. Blutdruck wurde nicht gemessen. "
            "Koerperpflege wie gewohnt durchgefuehrt. Mittagessen teilweise "
            "gegessen. Keine Besonderheiten."
        )
        sample_en = (
            "(English: \"Patient seems tired today. Blood pressure was not "
            "measured. Personal care done as usual. Lunch partly eaten. "
            "Nothing notable.\")"
        )
        text = st.text_area(
            "Care note text (German -- this is the language real shift "
            "reports are written in)",
            value="",
            height=220,
            placeholder=sample,
        )
        use_sample = st.checkbox("Use example note instead")
        if use_sample:
            text = sample
            st.caption(sample_en)

        submit = st.button("Score this note", type="primary")

    if submit and text.strip():
        result = score_note(text)
        rd = result.as_dict()

        nurse = db.get_nurse(nurse_id)
        placement = date.fromisoformat(nurse["placement_date"])
        tenure_week = max(1, ((date.today() - placement).days // 7) + 1)
        db.insert_submission(
            nurse_id=nurse_id,
            submitted_at=datetime.now().isoformat(),
            tenure_week=tenure_week,
            raw_text=text,
            score_dict=rd,
        )

        with col_right:
            st.subheader("Result")
            c1, c2, c3 = st.columns(3)
            c1.metric("Overall", f"{rd['overall_score']:.0f}/100")
            c2.metric("Completeness", f"{rd['completeness_score']:.0f}/100")
            c3.metric("Specificity", f"{rd['specificity_score']:.0f}/100")

            if rd["high_stakes_missing"]:
                labels = ", ".join(CATEGORY_LABELS[c] for c in rd["high_stakes_missing"])
                st.error(f"High-stakes category missing entirely: {labels}")

            if rd["categories_missing"]:
                labels = ", ".join(CATEGORY_LABELS.get(c, c) for c in rd["categories_missing"])
                st.warning(f"Not addressed in this note: {labels}")
            else:
                st.success("All AEDL categories addressed.")

            if rd["vague_phrases_found"]:
                st.markdown("**Vague phrasing detected:**")
                for p in rd["vague_phrases_found"]:
                    gloss = VAGUE_PHRASE_TRANSLATIONS.get(p, "")
                    st.markdown(
                        f"- \u201c{p}\u201d (\u201c{gloss}\u201d) \u2014 "
                        f"asserts a state with no measurable detail behind it"
                    )

            if rd["measurable_anchors"]:
                st.markdown(
                    f"**Measurable anchors found:** {rd['measurable_anchors']} "
                    f"(numbers, units, timestamps)"
                )

            if rd["primary_gap"]:
                st.info(f"Suggested next step: {LEARN_MODULE_MAP[rd['primary_gap']]}")
            else:
                st.info("No specific gap detected -- this note clears the rubric.")

        st.divider()
        with st.expander("Category coverage detail"):
            for cat in CATEGORIES:
                present = cat in rd["categories_present"]
                st.markdown(
                    f"{'\u2713' if present else '\u2717'} {CATEGORY_LABELS[cat]}"
                )


# ---------------------------------------------------------------------------
# Page: My Documentation Trend (nurse-facing)
# ---------------------------------------------------------------------------
elif page == "My Documentation Trend":
    st.title("My Documentation Trend")
    selected_label = st.selectbox("Nurse", list(nurse_options.keys()))
    nurse_id = nurse_options[selected_label]

    trend = db.q_nurse_trend(nurse_id)
    subs = db.nurse_submissions(nurse_id)

    if not trend:
        st.info("No submissions yet for this nurse.")
    else:
        df_trend = pd.DataFrame(trend)
        baseline = pd.DataFrame(db.q_cohort_baseline_by_week())

        merged = df_trend.merge(baseline, on="tenure_week", how="left")
        merged = merged.rename(columns={
            "avg_score": "Your average score",
            "cohort_avg_score": "Cohort baseline",
        })

        st.line_chart(
            merged.set_index("tenure_week")[["Your average score", "Cohort baseline"]]
        )
        st.caption(
            "Weekly average score vs. cohort baseline for the same tenure "
            "week -- comparing against peers at the same stage, not just "
            "your own history."
        )

        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Recurring gaps")
            gap_counts: dict[str, int] = {}
            for s in subs:
                missing = json.loads(s["categories_missing"])
                for cat in missing:
                    gap_counts[cat] = gap_counts.get(cat, 0) + 1
            if gap_counts:
                gap_df = pd.DataFrame(
                    sorted(gap_counts.items(), key=lambda x: -x[1]),
                    columns=["category", "times missing"],
                )
                gap_df["category"] = gap_df["category"].map(lambda c: CATEGORY_LABELS.get(c, c))
                st.dataframe(gap_df, hide_index=True, use_container_width=True)

        with col2:
            st.subheader("Recommended next module")
            if gap_counts:
                top_gap = max(gap_counts, key=gap_counts.get)
                st.success(LEARN_MODULE_MAP[top_gap])
                st.caption(
                    f"Based on {gap_counts[top_gap]} notes missing this "
                    f"category out of {len(subs)} submitted."
                )

        with st.expander("Raw submission history"):
            hist_df = pd.DataFrame(subs)[
                ["submitted_at", "tenure_week", "overall_score",
                 "completeness_score", "specificity_score", "primary_gap"]
            ]
            st.dataframe(hist_df, hide_index=True, use_container_width=True)


# ---------------------------------------------------------------------------
# Page: Risk Radar (internal / BorderPlus-facing)
# ---------------------------------------------------------------------------
elif page == "Risk Radar (BorderPlus Internal)":
    st.title("Placement Risk Radar")
    st.markdown(
        "Each nurse's most recent score, short-term trend, and position "
        "relative to the cohort baseline at her tenure stage. Intended use: "
        "intervene on **High** and **Medium** rows before a facility "
        "escalates, not after."
    )

    radar = db.q_risk_radar()
    df = pd.DataFrame(radar)

    tier_counts = df["risk_tier"].value_counts().to_dict()
    c1, c2, c3 = st.columns(3)
    c1.metric("High risk", tier_counts.get("High", 0))
    c2.metric("Medium risk", tier_counts.get("Medium", 0))
    c3.metric("Low risk", tier_counts.get("Low", 0))

    tier_filter = st.multiselect(
        "Filter by risk tier", ["High", "Medium", "Low"],
        default=["High", "Medium"],
    )
    view = df[df["risk_tier"].isin(tier_filter)] if tier_filter else df

    display_df = view.rename(columns={
        "name": "Nurse", "facility": "Facility", "origin_country": "Origin",
        "tenure_week": "Tenure (wk)", "latest_score": "Latest score",
        "trend_delta": "Trend (recent vs prior)",
        "vs_cohort_baseline": "vs. cohort baseline", "risk_tier": "Risk",
        "n_notes": "Notes submitted",
    })
    st.dataframe(display_df, hide_index=True, use_container_width=True)

    st.caption(
        "Risk tier logic: High = latest score below 50, or a drop of more "
        "than 8 points combined with a score below 70. Medium = latest "
        "score below 65, a negative trend, or more than 8 points below the "
        "cohort baseline for the same tenure week. This is a transparent "
        "heuristic, intentionally simple enough to explain to a facility "
        "partner, not a black-box model."
    )


# ---------------------------------------------------------------------------
# Page: Cohort Analytics
# ---------------------------------------------------------------------------
elif page == "Cohort Analytics":
    st.title("Cohort Analytics")

    tab1, tab2, tab3 = st.tabs(
        ["Error frequency", "Facility comparison", "Origin / German level"]
    )

    with tab1:
        st.subheader("Which AEDL category gets skipped most often?")
        st.caption(
            "This is the number that should drive shared 'Learn' content "
            "priority -- independent of any one nurse."
        )
        freq = pd.DataFrame(db.q_cohort_error_frequency())
        freq["category"] = freq["category"].map(lambda c: CATEGORY_LABELS.get(c, c))
        st.bar_chart(freq.set_index("category")["missing_rate_pct"])
        st.dataframe(freq, hide_index=True, use_container_width=True)

    with tab2:
        st.subheader("Average documentation score by facility")
        st.caption(
            "Distinguishes 'this nurse is struggling' from 'this facility "
            "produces struggling nurses' -- the latter points at facility "
            "onboarding, not individual coaching."
        )
        fac = pd.DataFrame(db.q_facility_summary())
        st.bar_chart(fac.set_index("facility_name")["avg_score"])
        st.dataframe(fac, hide_index=True, use_container_width=True)

    with tab3:
        st.subheader("Score by origin country and German level at intake")
        st.caption(
            "If quality correlates with intake German level rather than "
            "facility or tenure, that argues for pre-placement language "
            "investment rather than post-placement remediation."
        )
        orig = pd.DataFrame(db.q_origin_country_summary())
        st.dataframe(orig, hide_index=True, use_container_width=True)


# ---------------------------------------------------------------------------
# Page: Rubric Reference
# ---------------------------------------------------------------------------
elif page == "Rubric Reference":
    st.title("Scoring Rubric Reference")
    st.markdown(
        "This page exists so the rubric is **inspectable**, not a black "
        "box -- a nurse or a facility partner can see exactly what is "
        "being checked and why."
    )

    st.subheader("1. Completeness (60% of overall score)")
    st.write(
        "Fraction of the 8 AEDL-aligned categories addressed anywhere in "
        "the note: vitals, mobility, hygiene, nutrition, wound care, "
        "medication, pain, incidents."
    )
    for cat, cues in CATEGORIES.items():
        with st.expander(CATEGORY_LABELS[cat]):
            cue_df = pd.DataFrame({
                "German term in note": [c.strip() for c in cues],
                "What it means": [CUE_TRANSLATIONS.get(c, "-") for c in cues],
            })
            st.dataframe(cue_df, hide_index=True, use_container_width=True)

    st.subheader("2. Specificity (40% of overall score)")
    st.write(
        "Density of measurable anchors (numbers + units: blood pressure "
        "readings, temperatures, timestamps, dosages, NRS pain scores) "
        "relative to note length, minus a penalty for known vague "
        "boilerplate phrases such as \u201cwie gewohnt\u201d (as usual) or "
        "\u201calles in Ordnung\u201d (all fine) that assert a state "
        "without evidence."
    )

    st.subheader("3. High-stakes penalty")
    st.write(
        "Vitals, medication timing, and wound care are weighted extra "
        "because they are disproportionately linked to reimbursement "
        "disputes and audit findings in German long-term care documentation. "
        "Missing one of these categories entirely costs 8 points off the "
        "overall score, on top of the completeness penalty."
    )

    st.subheader("What this rubric does NOT claim to do")
    st.markdown(
        "- It does not verify medical correctness of the care described.\n"
        "- It is not a substitute for actual MDK/MDS audit criteria, which "
        "are more detailed and facility-specific.\n"
        "- It is a deterministic keyword/pattern engine, not a trained "
        "model -- every score is fully explainable from the rules above."
    )
