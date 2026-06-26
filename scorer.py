"""
scorer.py

Deterministic rubric engine for scoring Pflegedokumentation (nurse care notes).

Design intent
-------------
This is NOT a medical-correctness checker and does not claim to replicate
MDK/MDS audit logic. It checks the things that are objectively checkable
from text: which AEDL-aligned categories are present, whether the entry
contains measurable/specific detail vs. vague boilerplate, and whether
fields that are routinely flagged in audits (timed medication, wound
documentation, incident notes) are missing entirely.

The categories are based on the AEDL (Aktivitaeten und existenzielle
Erfahrungen des Lebens) framework, which is the standard structuring model
used in German Pflegedokumentation. Real audit rubrics are more detailed
and facility-specific; this is a defensible, explainable approximation
suitable for a coaching/triage tool, not a compliance sign-off tool.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List


# ---------------------------------------------------------------------------
# Category definitions
# ---------------------------------------------------------------------------
# Each category maps to a set of German lexical cues that indicate the note
# is addressing that AEDL domain at all. This is intentionally a recall-
# oriented keyword match (not an NER model) -- false positives are cheaper
# than false negatives for a coaching tool: we'd rather flag something as
# "addressed" too generously than tell a nurse she's missing something she
# actually wrote, using a slightly different word.

CATEGORIES: Dict[str, List[str]] = {
    "vitals": [
        "blutdruck", "rr ", "puls", "temperatur", "fieber", "atmung",
        "sauerstoffsaettigung", "spo2", "herzfrequenz", "bz ", "blutzucker",
    ],
    "mobility": [
        "mobilisation", "mobilisiert", "gehhilfe", "rollstuhl", "gelaufen",
        "bettlaegerig", "transfer", "lagerung", "umgelagert", "sturz",
    ],
    "hygiene": [
        "koerperpflege", "körperpflege", "gewaschen", "dusche", "geduscht",
        "intimpflege", "mundpflege", "rasiert", "angezogen",
    ],
    "nutrition": [
        "nahrung", "ernaehrung", "ernährung", "appetit", "getrunken",
        "trinkmenge", "fluessigkeit", "flüssigkeit", "sondenkost", "diaet",
        "diät", "kost",
    ],
    "wound_care": [
        "wunde", "verband", "wundversorgung", "dekubitus", "verbandwechsel",
        "wundheilung", "naht", "pflaster",
    ],
    "medication": [
        "medikament", "schmerzmittel", "tablette", "spritze", "insulin",
        "verabreicht", "gabe um", "uhr verabreicht", "tropfen",
    ],
    "pain": [
        "schmerz", "schmerzskala", "nrs", "klagt ueber", "klagt über",
        "schmerzfrei", "analgetikum",
    ],
    "incidents": [
        "sturz", "auffaelligkeit", "auffälligkeit", "besonderheit",
        "verweigert", "unruhe", "verwirrt", "notfall", "arzt informiert",
    ],
}

# Phrases that are extremely common filler in real Pflegeberichte and are
# specifically the thing auditors push back on: they assert a state without
# any measurable or observable detail behind them.
VAGUE_PHRASES = [
    "wie gewohnt",
    "alles in ordnung",
    "es geht ihm gut",
    "es geht ihr gut",
    "keine besonderheiten",
    "patient ist zufrieden",
    "soweit gut",
    "unauffaellig",
    "unauffällig",
    "normal verlaufen",
]

# A note that contains a number+unit pair is doing the thing vague notes
# don't: anchoring a claim to something measurable (130/85, 36.8, 14:00,
# 200ml, 5mg, NRS 3/10, etc).
MEASURABLE_PATTERN = re.compile(
    r"(\d{2,3}\s*/\s*\d{2,3})"          # blood pressure 130/85
    r"|(\d{1,3}[.,]\d\s*°?c\b)"         # temperature 36.8
    r"|(\d{1,2}:\d{2}\s*uhr)"           # timestamp 14:00 uhr
    r"|(\d+\s*(ml|mg|mmol|l|%|bpm))"    # dosage / volume / rate
    r"|(\bnrs\s*\d{1,2})",              # pain scale NRS 3
    re.IGNORECASE,
)

# Categories that, if missing AND the note is otherwise non-trivial in
# length, represent the highest-frequency real audit findings. These carry
# extra weight in the risk score because they're disproportionately linked
# to reimbursement disputes (vitals, medication timing, wound status).
HIGH_STAKES_CATEGORIES = {"vitals", "medication", "wound_care"}


@dataclass
class ScoreResult:
    categories_present: List[str] = field(default_factory=list)
    categories_missing: List[str] = field(default_factory=list)
    vague_phrases_found: List[str] = field(default_factory=list)
    measurable_anchors: int = 0
    word_count: int = 0
    completeness_score: float = 0.0   # 0-100, category coverage
    specificity_score: float = 0.0    # 0-100, measurable detail density
    overall_score: float = 0.0        # 0-100, weighted blend
    high_stakes_missing: List[str] = field(default_factory=list)
    primary_gap: str | None = None    # single category to route to "Learn"

    def as_dict(self) -> dict:
        return {
            "categories_present": self.categories_present,
            "categories_missing": self.categories_missing,
            "vague_phrases_found": self.vague_phrases_found,
            "measurable_anchors": self.measurable_anchors,
            "word_count": self.word_count,
            "completeness_score": round(self.completeness_score, 1),
            "specificity_score": round(self.specificity_score, 1),
            "overall_score": round(self.overall_score, 1),
            "high_stakes_missing": self.high_stakes_missing,
            "primary_gap": self.primary_gap,
        }


def score_note(text: str) -> ScoreResult:
    """Score a single care note against the rubric. Pure function, no I/O."""
    result = ScoreResult()
    if not text or not text.strip():
        result.categories_missing = list(CATEGORIES.keys())
        result.high_stakes_missing = list(HIGH_STAKES_CATEGORIES)
        result.primary_gap = "vitals"
        return result

    lowered = text.lower()
    result.word_count = len(text.split())

    for cat, cues in CATEGORIES.items():
        if any(cue in lowered for cue in cues):
            result.categories_present.append(cat)
        else:
            result.categories_missing.append(cat)

    result.vague_phrases_found = [p for p in VAGUE_PHRASES if p in lowered]
    result.measurable_anchors = len(MEASURABLE_PATTERN.findall(lowered))
    result.high_stakes_missing = [
        c for c in result.categories_missing if c in HIGH_STAKES_CATEGORIES
    ]

    # --- Completeness: fraction of the 8 AEDL-aligned categories touched ---
    result.completeness_score = (
        100.0 * len(result.categories_present) / len(CATEGORIES)
    )

    # --- Specificity: measurable anchors per 40 words, capped, minus vague
    #     phrase penalty. This rewards density of concrete detail rather
    #     than raw length (a long note can still be all filler). ---
    density = result.measurable_anchors / max(result.word_count, 1) * 40
    specificity = min(density * 35, 100.0)
    specificity -= len(result.vague_phrases_found) * 12
    result.specificity_score = max(0.0, min(100.0, specificity))

    # --- Overall: completeness weighted higher than specificity, with an
    #     explicit penalty if a high-stakes category is missing entirely,
    #     because that's the failure mode that actually gets escalated. ---
    base = 0.6 * result.completeness_score + 0.4 * result.specificity_score
    high_stakes_penalty = 8 * len(result.high_stakes_missing)
    result.overall_score = max(0.0, base - high_stakes_penalty)

    # --- Primary gap: what should be routed to "Learn" next. Prioritize
    #     high-stakes missing categories, then any missing category, then
    #     fall back to "specificity" itself if everything is present but
    #     vague. ---
    if result.high_stakes_missing:
        result.primary_gap = result.high_stakes_missing[0]
    elif result.categories_missing:
        result.primary_gap = result.categories_missing[0]
    elif result.vague_phrases_found:
        result.primary_gap = "specificity"
    else:
        result.primary_gap = None

    return result


# Human-readable labels + the "Learn" micro-module each gap routes to.
CATEGORY_LABELS = {
    "vitals": "Vitalzeichen (vital signs)",
    "mobility": "Mobilisation (mobility)",
    "hygiene": "Koerperpflege (hygiene)",
    "nutrition": "Ernaehrung (nutrition/fluids)",
    "wound_care": "Wundversorgung (wound care)",
    "medication": "Medikation (medication timing)",
    "pain": "Schmerzeinschaetzung (pain assessment)",
    "incidents": "Besonderheiten (incident reporting)",
    "specificity": "Konkrete Formulierung (specific, measurable language)",
}

LEARN_MODULE_MAP = {
    "vitals": "Module: Documenting vitals with correct units & thresholds",
    "mobility": "Module: Mobilisation terminology & transfer documentation",
    "hygiene": "Module: Koerperpflege documentation phrasing",
    "nutrition": "Module: Nutrition/fluid balance charting conventions",
    "wound_care": "Module: Wundversorgung documentation & dekubitus staging",
    "medication": "Module: Medication administration timing & Betaeubungsmittel rules",
    "pain": "Module: Standardized pain scales (NRS) in German charting",
    "incidents": "Module: Incident/Besonderheiten reporting & escalation language",
    "specificity": "Module: Replacing vague phrasing with measurable observations",
}
