"""Stage 5 — Score. Implements the weights from the PRD exactly.

review_count lives on `clinics`, not `doctors`, so it's joined in via
clinic_place_id. has_online_booking isn't a doctor column either — it comes
from the clinic-site extraction, stored in sources.raw_data.
"""

import json

import config
import db


def _clinic_review_count(conn, clinic_place_id: str | None) -> int | None:
    if not clinic_place_id:
        return None
    row = conn.execute(
        "SELECT review_count FROM clinics WHERE place_id = ?", (clinic_place_id,)
    ).fetchone()
    return row["review_count"] if row else None


def _has_online_booking(conn, doctor_id: int) -> bool:
    rows = conn.execute(
        "SELECT raw_data FROM sources WHERE doctor_id = ? AND source_type = 'clinic_site'",
        (doctor_id,),
    ).fetchall()
    for row in rows:
        if not row["raw_data"]:
            continue
        try:
            data = json.loads(row["raw_data"])
        except json.JSONDecodeError:
            continue
        if data.get("has_online_booking") is True:
            return True
    return False


def compute_score(conn, doctor: dict) -> tuple[int, list[str]]:
    w = config.SCORE_WEIGHTS
    score = 0
    reasons = []

    has_own_practice = bool(doctor.get("has_own_practice"))
    phone = doctor.get("phone")
    email = doctor.get("email")
    website = doctor.get("website")
    experience_years = doctor.get("experience_years")
    instagram_handle = doctor.get("instagram_handle")
    locality = doctor.get("locality")
    review_count = _clinic_review_count(conn, doctor.get("clinic_place_id"))
    has_online_booking = _has_online_booking(conn, doctor["id"])

    if has_own_practice:
        score += w["has_own_practice"]; reasons.append("owns practice")
    if phone or email:
        score += w["reachable"]; reasons.append("reachable")
    if website:
        score += w["has_website"]; reasons.append("has website")
    if experience_years is not None and config.EXPERIENCE_BAND_MIN <= experience_years <= config.EXPERIENCE_BAND_MAX:
        score += w["experience_band"]; reasons.append("experience band")
    if instagram_handle:
        score += w["has_instagram"]; reasons.append("has instagram")
    if has_online_booking:
        score += w["online_booking"]; reasons.append("online booking")
    if review_count and review_count > config.HIGH_REVIEW_COUNT_THRESHOLD:
        score += w["high_review_volume"]; reasons.append("high review volume")
    if locality in config.PRIME_LOCALITIES:
        score += w["prime_locality"]; reasons.append("prime locality")

    if not (phone or email):
        score += w["no_contact"]; reasons.append("no contact")
    if experience_years is not None and experience_years < config.JUNIOR_EXPERIENCE_THRESHOLD:
        score += w["junior"]; reasons.append("junior")

    return score, reasons


def run_scoring(conn, dry_run: bool = False) -> dict:
    doctors = [dict(d) for d in db.all_doctors(conn)]
    scored = 0
    score_ge_8 = 0

    for doctor in doctors:
        score, reasons = compute_score(conn, doctor)
        if score >= 8:
            score_ge_8 += 1
        if dry_run:
            continue
        db.update_doctor(conn, doctor["id"], {
            "score": score,
            "score_reasons": json.dumps(reasons),
        })
        scored += 1

    return {"doctors_scored": scored, "scoring_8_plus": score_ge_8}
