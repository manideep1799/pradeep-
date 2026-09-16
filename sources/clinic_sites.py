"""Stage 2a — fetch each clinic's website (+ an about/team/doctors subpage if
findable) and run LLM extraction to pull out doctor names and contact info.

Idempotent via clinics.website_fetched: once a clinic's site has been
processed (success or failure), it's skipped on the next `extract` run.
"""

import json
import time
from collections import Counter

import config
import db
import dedupe
import extract


def _doctor_row_from_extraction(doc: dict, clinic: dict) -> dict:
    return {
        "name": doc.get("name"),
        "name_normalized": dedupe.normalize_name(doc.get("name") or ""),
        "qualification": doc.get("qualification"),
        "specialty": doc.get("specialty"),
        "experience_years": extract.parse_experience_years(doc.get("experience_years")),
        "designation": doc.get("designation"),
        "clinic_place_id": clinic["place_id"],
        "clinic_name": clinic["name"],
        "hospital_affiliation": None,
        "locality": clinic["locality"],
        "phone": clinic["phone"],
        "email": None,  # filled in from clinic_email at the call site
        "website": clinic["website"],
        "instagram_handle": None,
        "youtube_url": None,
        "has_own_practice": None,
    }


def run_clinic_extraction(conn, dry_run: bool = False, limit: int | None = None) -> dict:
    pending = db.clinics_pending_extraction(conn, limit=limit)
    doctors_created = 0
    clinics_processed = 0

    for clinic in pending:
        clinic = dict(clinic)
        if dry_run:
            print(f"[dry-run] would fetch website for {clinic['name']!r}: {clinic['website']}")
            continue

        try:
            html, error = extract.fetch_url(clinic["website"])
            if error:
                db.mark_website_fetched(conn, clinic["place_id"], error=error)
                time.sleep(config.REQUEST_DELAY_SECONDS)
                continue

            candidates = [(clinic["website"], html)]
            subpage_links = extract.find_subpage_links(
                html, clinic["website"], config.CLINIC_SUBPAGE_HINTS
            )
            for link in subpage_links[: config.MAX_SUBPAGES_PER_CLINIC]:
                time.sleep(config.REQUEST_DELAY_SECONDS)
                sub_html, sub_error = extract.fetch_url(link)
                if sub_html:
                    candidates.append((link, sub_html))

            seen_names = set()
            designations_seen = []
            for url, page_html in candidates:
                text = extract.html_to_text(page_html)
                result = extract.extract_structured(text, mode="clinic")
                if not result:
                    continue

                for doc in result.get("doctors") or []:
                    name = (doc.get("name") or "").strip()
                    key = dedupe.normalize_name(name)
                    if not name or key in seen_names:
                        continue
                    seen_names.add(key)

                    row = _doctor_row_from_extraction(doc, clinic)
                    row["email"] = result.get("clinic_email")
                    row["instagram_handle"] = result.get("instagram_url")
                    row["youtube_url"] = result.get("youtube_url")

                    doctor_id = db.insert_doctor(conn, row)
                    raw = {
                        **doc,
                        "clinic_email": result.get("clinic_email"),
                        "instagram_url": result.get("instagram_url"),
                        "youtube_url": result.get("youtube_url"),
                        "has_online_booking": result.get("has_online_booking"),
                    }
                    db.insert_source(conn, doctor_id, "clinic_site", url, json.dumps(raw))
                    doctors_created += 1
                    if doc.get("designation"):
                        designations_seen.append(doc["designation"])

                time.sleep(config.REQUEST_DELAY_SECONDS)

            # A source page's own bio/modal content can be genuinely bugged
            # (the same "read more" text shown for every team member) —
            # not something a smarter parser fixes, but worth flagging so a
            # human notices before trusting these designations at face value.
            for designation, count in Counter(designations_seen).items():
                if count >= 3:
                    print(
                        f"  ! {count} different doctors at {clinic['name']!r} share the exact "
                        f"designation {designation!r} — likely duplicated bio content on the "
                        f"source site, not a real fact. Worth a manual check."
                    )

            db.mark_website_fetched(conn, clinic["place_id"], error=None)
            clinics_processed += 1

        except Exception as exc:
            print(f"  ! unexpected error on clinic {clinic.get('name')!r}: {exc}")
            db.mark_website_fetched(conn, clinic["place_id"], error=f"unexpected: {exc}")

    return {"clinics_processed": clinics_processed, "doctors_created": doctors_created}
