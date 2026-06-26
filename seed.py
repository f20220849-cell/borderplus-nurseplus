"""
seed.py

Generates synthetic-but-structurally-realistic data so the dashboards have
something to show on first run. This is clearly labeled as demo data in the
UI -- the point is to demonstrate that the analytics queries work correctly
against a real distribution (improving nurses, declining nurses, a
structurally weak facility), not to claim these are real placements.
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta

import db
from scorer import score_note

random.seed(42)

FACILITIES = [
    ("St. Elisabeth Klinikum", "Muenchen"),
    ("Caritas Seniorenzentrum", "Koeln"),
    ("Vivantes Pflegeheim Nord", "Berlin"),
    ("Asklepios Klinik Barmbek", "Hamburg"),
    ("Augustinum Seniorenresidenz", "Stuttgart"),
]

ORIGINS = ["India", "Philippines", "Vietnam", "India", "Philippines"]
GERMAN_LEVELS = ["B1", "B2", "B1", "C1"]
FIRST_NAMES = [
    "Anjali", "Priya", "Maria Clara", "Thi", "Ravi", "Nguyen", "Divya",
    "Carmela", "Sunita", "Hoang", "Meera", "Josefa", "Kavya", "Linh",
    "Asha", "Rosario", "Pooja", "Trang", "Lakshmi", "Imelda",
]
LAST_NAMES = [
    "Kumar", "Santos", "Tran", "Reddy", "Cruz", "Pham", "Nair", "Dela Cruz",
    "Singh", "Le", "Menon", "Bautista", "Iyer", "Vu", "Gomez",
]

GOOD_FRAGMENTS = {
    "vitals": "Blutdruck 128/82, Puls 76/min, Temperatur 36.7C gemessen um 08:00 Uhr.",
    "mobility": "Patient mit Gehhilfe mobilisiert, 2x im Flur gelaufen, Transfer selbststaendig.",
    "hygiene": "Koerperpflege im Bad durchgefuehrt, Mundpflege nach dem Fruehstueck.",
    "nutrition": "Fruehstueck vollstaendig gegessen, 400ml Fluessigkeit getrunken.",
    "wound_care": "Wunde am linken Unterschenkel gereinigt, Verbandwechsel um 10:30 Uhr, Wundheilung fortschreitend.",
    "medication": "Schmerzmittel (Ibuprofen 400mg) um 09:00 Uhr verabreicht.",
    "pain": "Patient klagt ueber leichte Schmerzen, NRS 3/10, nach Medikation NRS 1/10.",
    "incidents": "Keine Sturzereignisse, Arzt ueber leichte Unruhe informiert.",
}

VAGUE_FRAGMENTS = {
    "vitals": "Vitalzeichen wie gewohnt.",
    "mobility": "Mobilisation wie gewohnt durchgefuehrt.",
    "hygiene": "Koerperpflege wie gewohnt.",
    "nutrition": "Hat gegessen, alles in Ordnung.",
    "wound_care": "Wunde versorgt, soweit gut.",
    "medication": "Medikamente gegeben.",
    "pain": "Patient ist zufrieden, keine Beschwerden.",
    "incidents": "Keine Besonderheiten.",
}

ALL_CATEGORIES = list(GOOD_FRAGMENTS.keys())


def build_note(quality: str) -> str:
    if quality == "strong":
        n_categories = random.randint(6, 8)
        vague_chance = 0.05
    elif quality == "developing":
        n_categories = random.randint(4, 6)
        vague_chance = 0.30
    else:
        n_categories = random.randint(2, 4)
        vague_chance = 0.65

    chosen = random.sample(ALL_CATEGORIES, n_categories)
    sentences = []
    for cat in chosen:
        if random.random() < vague_chance:
            sentences.append(VAGUE_FRAGMENTS[cat])
        else:
            sentences.append(GOOD_FRAGMENTS[cat])
    random.shuffle(sentences)
    return " ".join(sentences)


def nurse_trajectory(kind: str, n_weeks: int) -> list:
    if kind == "improving":
        cut1, cut2 = n_weeks // 3, 2 * n_weeks // 3
        return (["weak"] * cut1) + (["developing"] * (cut2 - cut1)) + (["strong"] * (n_weeks - cut2))
    if kind == "stable_strong":
        return ["strong" if random.random() > 0.15 else "developing" for _ in range(n_weeks)]
    if kind == "stable_weak":
        return ["developing" if random.random() > 0.5 else "weak" for _ in range(n_weeks)]
    if kind == "declining":
        cut = n_weeks // 2
        return (["strong"] * cut) + (["developing"] * (n_weeks - cut - 2)) + (["weak"] * 2)
    raise ValueError(kind)


def run_seed(n_weeks: int = 10, notes_per_week: int = 3) -> None:
    db.init_db()
    if not db.is_empty():
        return

    facility_ids = [db.insert_facility(name, city) for name, city in FACILITIES]
    weak_facility_id = facility_ids[1]

    nurses_per_facility = 6
    facility_assignments = []
    for fid in facility_ids:
        facility_assignments.extend([fid] * nurses_per_facility)
    random.shuffle(facility_assignments)

    used_names = set()
    today = date.today()
    nurse_id_to_kind = {}

    for facility_id in facility_assignments:
        while True:
            name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
            if name not in used_names:
                used_names.add(name)
                break

        origin = random.choice(ORIGINS)
        level = random.choice(GERMAN_LEVELS)
        placement_date = today - timedelta(weeks=n_weeks + random.randint(0, 2))

        if facility_id == weak_facility_id:
            kind = random.choices(
                ["declining", "stable_weak", "improving", "stable_strong"],
                weights=[35, 35, 20, 10],
            )[0]
        else:
            kind = random.choices(
                ["improving", "stable_strong", "stable_weak", "declining"],
                weights=[35, 35, 20, 10],
            )[0]

        nurse_id = db.insert_nurse(
            name=name, facility_id=facility_id, origin_country=origin,
            placement_date=placement_date.isoformat(), german_level=level,
        )
        nurse_id_to_kind[nurse_id] = kind

    rows_to_insert = []
    for nurse_id, kind in nurse_id_to_kind.items():
        weekly_quality = nurse_trajectory(kind, n_weeks)
        nurse = db.get_nurse(nurse_id)
        placement = date.fromisoformat(nurse["placement_date"])

        for week_idx, quality in enumerate(weekly_quality, start=1):
            week_start = placement + timedelta(weeks=week_idx - 1)
            for _ in range(notes_per_week):
                day_offset = random.randint(0, 6)
                submitted_at = week_start + timedelta(days=day_offset)
                note_text = build_note(quality)
                result = score_note(note_text).as_dict()
                rows_to_insert.append((
                    nurse_id,
                    submitted_at.isoformat() + "T08:00:00",
                    week_idx,
                    note_text,
                    result["completeness_score"],
                    result["specificity_score"],
                    result["overall_score"],
                    result["word_count"],
                    result["measurable_anchors"],
                    json.dumps(result["categories_present"]),
                    json.dumps(result["categories_missing"]),
                    json.dumps(result["high_stakes_missing"]),
                    json.dumps(result["vague_phrases_found"]),
                    result["primary_gap"],
                ))

    db.bulk_insert_submissions(rows_to_insert)


if __name__ == "__main__":
    run_seed()
    print("Seed complete.")
