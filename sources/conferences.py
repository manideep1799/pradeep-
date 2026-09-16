"""Stage 2c — conference committee/faculty pages. Optional, lowest priority.

Seeded from config.CONFERENCE_PAGES. Committee pages are more valuable than
faculty pages, so they're processed first. Idempotent via the `sources` table
url check, same as hospitals.py.
"""

import json
import time

import config
import db
import dedupe
import extract


def _doctor_row_from_extraction(doc: dict) -> dict:
    return {
        "name": doc.get("name"),
        "name_normalized": dedupe.normalize_name(doc.get("name") or ""),
        "qualification": None,
        "specialty": None,
        "experience_years": None,
        "designation": doc.get("designation"),
        "clinic_place_id": None,
        "clinic_name": None,
        "hospital_affiliation": doc.get("affiliation"),
        "locality": None,
        "phone": None,
        "email": None,
        "website": None,
        "instagram_handle": None,
        "youtube_url": None,
        "has_own_practice": None,
    }


def run_conference_extraction(conn, dry_run: bool = False) -> dict:
    pages = sorted(config.CONFERENCE_PAGES, key=lambda p: p.get("kind") != "committee")
    pages_processed = 0
    doctors_created = 0

    for page in pages:
        url = page.get("url")
        if not url:
            continue

        if dry_run:
            print(f"[dry-run] would fetch conference page ({page.get('kind')}): {url}")
            continue

        if db.source_url_already_processed(conn, url):
            print(f"  - already processed, skipping: {url}")
            continue

        html, error = extract.fetch_url(url)
        if error:
            print(f"  ! fetch failed for {url}: {error}")
            continue

        text = extract.html_to_text(html)
        result = extract.extract_structured(text, mode="conference")
        if result:
            for doc in result.get("doctors") or []:
                if not doc.get("name"):
                    continue
                row = _doctor_row_from_extraction(doc)
                doctor_id = db.insert_doctor(conn, row)
                db.insert_source(conn, doctor_id, "conference", url, json.dumps(doc))
                doctors_created += 1

        pages_processed += 1
        time.sleep(config.REQUEST_DELAY_SECONDS)

    return {"pages_processed": pages_processed, "doctors_created": doctors_created}
