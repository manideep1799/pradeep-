"""Stage 4 — Enrich.

- Own-practice detection: surname in clinic name, OR is_founder from extraction,
  OR the doctor is the only one listed at their clinic.
- Hospital-only doctors: attempt a targeted OSM search for a private clinic
  and link it if found. This is what turns unreachable names into leads.
- Social links: already captured verbatim during extraction (Stage 2a). We
  never visit Instagram or fetch profile data — only store the handle/URL.
"""

import json
import re

import config
import db
from sources import osm


def _is_founder_from_sources(conn, doctor_id: int) -> bool:
    rows = conn.execute(
        "SELECT raw_data FROM sources WHERE doctor_id = ?", (doctor_id,)
    ).fetchall()
    for row in rows:
        if not row["raw_data"]:
            continue
        try:
            data = json.loads(row["raw_data"])
        except json.JSONDecodeError:
            continue
        if data.get("is_founder") is True:
            return True
    return False


def _name_tokens(doctor: dict) -> list[str]:
    """Every meaningful token of the normalized name — not just the last one.
    Clinics here get named after a founder's first name just as often as
    their surname (e.g. "Tanvir Hospital" for Dr. Tanvir Singh). A lot of
    doctors are also listed as "Name Initial" (e.g. "Santhi A"), a common
    South Indian convention — a single-letter token is rejected, since it
    would substring-match almost any clinic name that happens to contain
    that letter."""
    tokens = (doctor.get("name_normalized") or "").split()
    return [t for t in tokens if len(t) >= 3]


def _has_ownership_title(doctor: dict) -> bool:
    designation = (doctor.get("designation") or "").lower()
    return any(kw in designation for kw in config.OWNERSHIP_TITLE_KEYWORDS)


def compute_role_category(doctor: dict) -> str:
    """Founder (has_own_practice) > Senior Decision-Maker (a Director/
    Chairman/CEO/HOD-style title that didn't clear the founder bar, e.g.
    blocked by chain size) > Staff (everyone else — still shown, still
    scored, just the default bucket). Call with has_own_practice already
    resolved for this run, not a possibly-stale value from before enrich."""
    if doctor.get("has_own_practice"):
        return "Founder"
    designation = (doctor.get("designation") or "").lower()
    if any(kw in designation for kw in config.SENIOR_DESIGNATION_KEYWORDS):
        return "Senior Decision-Maker"
    return "Staff"


def detect_own_practice(conn, doctor: dict) -> bool:
    if _is_founder_from_sources(conn, doctor["id"]):
        return True

    clinic_name = (doctor.get("clinic_name") or "").lower()
    # Whole-word match, not substring — otherwise a short name like "ram"
    # would false-match inside an unrelated word like "pharma".
    for token in _name_tokens(doctor):
        if re.search(rf"\b{re.escape(token)}\b", clinic_name):
            return True

    if doctor.get("clinic_place_id"):
        clinic_doctor_count = db.doctors_at_clinic_count(conn, doctor["clinic_place_id"])
        if clinic_doctor_count == 1:
            return True
        # A CEO/Owner/Proprietor/Managing Director title is a real ownership
        # signal at a small practice. At a large multi-doctor chain (e.g. a
        # CEO of a pan-India hospital chain), that title almost certainly
        # means a hired executive, not the personal commercial autonomy over
        # marketing spend that's actually the PRD's target ICP — so the
        # title only counts below a small-clinic size threshold.
        if clinic_doctor_count <= config.SMALL_CLINIC_MAX_DOCTORS and _has_ownership_title(doctor):
            return True

    return False


def _link_private_clinic(conn, doctor: dict) -> bool:
    """Best-effort: search OSM for a clinic matching the doctor's name.
    Returns True if a clinic was found and linked."""
    found = osm.find_single_clinic(doctor["name"])
    if not found or not found.get("place_id"):
        return False

    found["locality"] = found.get("locality") or doctor.get("locality")
    db.upsert_clinic(conn, found)

    updates = {"clinic_place_id": found["place_id"], "clinic_name": found["name"]}
    if not doctor.get("phone") and found.get("phone"):
        updates["phone"] = found["phone"]
    if not doctor.get("website") and found.get("website"):
        updates["website"] = found["website"]
    db.update_doctor(conn, doctor["id"], updates)
    return True


def run_enrichment(conn, dry_run: bool = False) -> dict:
    doctors = [dict(d) for d in db.all_doctors(conn)]

    own_practice_set = 0
    clinics_linked = 0
    role_categories_set = 0

    # Link hospital-only doctors to a private clinic FIRST. Own-practice
    # detection below depends on clinic_name (surname match), so it must run
    # after linking or a newly-linked doctor never gets flagged.
    hospital_only = [
        d for d in doctors
        if not d.get("clinic_place_id") and d.get("hospital_affiliation")
    ]

    for doctor in hospital_only:
        if dry_run:
            print(f"[dry-run] would search a private clinic for {doctor['name']!r}")
            continue
        if _link_private_clinic(conn, doctor):
            clinics_linked += 1

    doctors = [dict(d) for d in db.all_doctors(conn)] if not dry_run else doctors

    for doctor in doctors:
        if dry_run:
            continue
        own_practice = detect_own_practice(conn, doctor)
        role_category = compute_role_category({**doctor, "has_own_practice": own_practice})

        updates = {}
        if own_practice != bool(doctor.get("has_own_practice")):
            updates["has_own_practice"] = int(own_practice)
            if own_practice:
                own_practice_set += 1
        if role_category != doctor.get("role_category"):
            updates["role_category"] = role_category
            role_categories_set += 1
        if updates:
            db.update_doctor(conn, doctor["id"], updates)

    return {
        "doctors_evaluated": len(doctors),
        "own_practice_flags_set": own_practice_set,
        "role_categories_set": role_categories_set,
        "hospital_only_doctors": len(hospital_only),
        "private_clinics_linked": clinics_linked,
    }
