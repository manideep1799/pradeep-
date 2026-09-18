"""Stage 2b — hospital consultant directories.

Seeded from config.HOSPITAL_DIRECTORIES. A hospital with a blank base_url is
skipped (we do not guess directory URLs). Paginates up to
config.MAX_HOSPITAL_PAGES per hospital.

Idempotent via the `sources` table: a directory page URL that has already
produced a source row is not re-fetched.
"""

import json
import time

import config
import db
import dedupe
import extract


def _doctor_row_from_extraction(doc: dict, hospital_name: str) -> dict:
    designation = doc.get("designation")
    department = doc.get("department")
    if designation and department:
        combined_designation = f"{designation} - {department}"
    else:
        combined_designation = designation or department

    return {
        "name": doc.get("name"),
        "name_normalized": dedupe.normalize_name(doc.get("name") or ""),
        "qualification": doc.get("qualification"),
        "specialty": doc.get("specialty"),
        "experience_years": extract.parse_experience_years(doc.get("experience_years")),
        "designation": combined_designation,
        "clinic_place_id": None,
        "clinic_name": None,
        "hospital_affiliation": hospital_name,
        "locality": None,
        "phone": None,
        "email": None,
        "website": None,
        "instagram_handle": None,
        "youtube_url": None,
        "has_own_practice": None,
    }


def _process_hospital(conn, hospital_name: str, base_url: str, dry_run: bool) -> int:
    doctors_created = 0
    url = base_url
    pages_visited = 0
    visited_urls = set()

    while url and pages_visited < config.MAX_HOSPITAL_PAGES:
        if url in visited_urls:
            break
        visited_urls.add(url)
        pages_visited += 1

        if dry_run:
            print(f"[dry-run] would fetch hospital directory page: {url}")
            break

        if db.source_url_already_processed(conn, url):
            print(f"  - already processed, skipping: {url}")
            break  # subsequent pages of a fully-processed directory are also done

        html, error = extract.fetch_url(url)
        if error:
            print(f"  ! fetch failed for {url}: {error}")
            break

        text = extract.html_to_text(html, base_url=url)
        result = extract.extract_structured(text, mode="hospital")

        if result:
            for doc in result.get("doctors") or []:
                if not doc.get("name"):
                    continue
                row = _doctor_row_from_extraction(doc, hospital_name)
                doctor_id = db.insert_doctor(conn, row)
                db.insert_source(conn, doctor_id, "hospital_dir", url, json.dumps(doc))
                doctors_created += 1

        next_links = extract.find_pagination_links(html, url)
        url = next_links[0] if next_links else None
        time.sleep(config.REQUEST_DELAY_SECONDS)

    return doctors_created


def run_hospital_extraction(conn, dry_run: bool = False) -> dict:
    hospitals_processed = 0
    doctors_created = 0

    for hospital_name, cfg in config.HOSPITAL_DIRECTORIES.items():
        base_url = cfg.get("base_url")
        if not base_url:
            print(f"  - {hospital_name}: no base_url configured, skipping")
            continue

        created = _process_hospital(conn, hospital_name, base_url, dry_run)
        doctors_created += created
        hospitals_processed += 1

    return {"hospitals_processed": hospitals_processed, "doctors_created": doctors_created}
