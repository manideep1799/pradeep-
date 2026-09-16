"""Stage 3 — Dedupe. The hardest part of the build; over-invest here.

Normalises doctor names, clusters exact + fuzzy matches, merges clusters into
a single surviving row per real doctor, reassigns their sources, and emits a
CSV of near-matches that were NOT auto-merged for manual review.
"""

import csv
import json
import re
import string

from rapidfuzz import fuzz

import config
import db

_TITLE_RE = re.compile(
    r"^(" + "|".join(re.escape(t) for t in config.TITLE_PREFIXES) + r")\b\.?\s*",
    re.IGNORECASE,
)
_PUNCT_TABLE = str.maketrans("", "", string.punctuation)


def normalize_name(name: str) -> str:
    if not name:
        return ""
    name = name.lower().strip()
    # Strip a leading title repeatedly (handles "dr. prof. ramesh")
    prev = None
    while prev != name:
        prev = name
        name = _TITLE_RE.sub("", name).strip()
    name = name.translate(_PUNCT_TABLE)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _refresh_normalized_names(conn) -> None:
    for doc in db.all_doctors(conn):
        normalized = normalize_name(doc["name"])
        if normalized != doc["name_normalized"]:
            db.update_doctor(conn, doc["id"], {"name_normalized": normalized})


def _fields_equal_ci(a, b) -> bool:
    if not a or not b:
        return False
    return str(a).strip().lower() == str(b).strip().lower()


def _completeness(doc: dict) -> int:
    fields = [
        "qualification", "specialty", "experience_years", "designation",
        "clinic_place_id", "clinic_name", "hospital_affiliation", "locality",
        "phone", "email", "website", "instagram_handle", "youtube_url",
    ]
    return sum(1 for f in fields if doc.get(f) not in (None, ""))


def _source_type_for_doctor(conn, doctor_id: int) -> str | None:
    row = conn.execute(
        "SELECT source_type FROM sources WHERE doctor_id = ? ORDER BY id LIMIT 1",
        (doctor_id,),
    ).fetchone()
    return row["source_type"] if row else None


def _merge_cluster(conn, cluster: list[dict]) -> int:
    """Merge a cluster of doctor rows into one surviving row. Returns survivor id."""
    # Prefer clinic_site-sourced rows for contact fields (phone/email/website).
    contact_fields = {"phone", "email", "website"}
    other_fields = [
        "name", "qualification", "specialty", "experience_years", "designation",
        "clinic_place_id", "clinic_name", "hospital_affiliation", "locality",
        "instagram_handle", "youtube_url", "has_own_practice",
    ]

    survivor = max(cluster, key=_completeness)
    survivor_id = survivor["id"]
    merged: dict = dict(survivor)

    clinic_site_rows = [
        d for d in cluster if _source_type_for_doctor(conn, d["id"]) == "clinic_site"
    ]

    for field in contact_fields:
        if merged.get(field):
            continue
        preferred_pool = clinic_site_rows or cluster
        for d in preferred_pool:
            if d.get(field):
                merged[field] = d[field]
                break

    for field in other_fields:
        if merged.get(field) in (None, ""):
            for d in cluster:
                if d.get(field) not in (None, ""):
                    merged[field] = d[field]
                    break

    update_payload = {k: v for k, v in merged.items() if k not in ("id", "created_at", "updated_at")}
    db.update_doctor(conn, survivor_id, update_payload)

    for d in cluster:
        if d["id"] == survivor_id:
            continue
        db.reassign_sources(conn, d["id"], survivor_id)
        db.delete_doctor(conn, d["id"])

    return survivor_id


def run_dedupe(conn, dry_run: bool = False) -> dict:
    _refresh_normalized_names(conn)
    doctors = [dict(d) for d in db.all_doctors(conn)]

    # Union-Find over doctor ids present in `doctors`.
    parent = {d["id"]: d["id"] for d in doctors}

    def find(x):
        while parent[x] != x:
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    review_rows = []

    n = len(doctors)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = doctors[i], doctors[j]
            if find(a["id"]) == find(b["id"]):
                continue

            if a["name_normalized"] and a["name_normalized"] == b["name_normalized"]:
                union(a["id"], b["id"])
                continue

            ratio = fuzz.token_sort_ratio(a["name_normalized"], b["name_normalized"])
            if ratio < config.FUZZY_MATCH_THRESHOLD:
                continue

            same_specialty = _fields_equal_ci(a.get("specialty"), b.get("specialty"))
            same_locality = _fields_equal_ci(a.get("locality"), b.get("locality"))

            if same_specialty and same_locality:
                union(a["id"], b["id"])
            else:
                review_rows.append({
                    "doctor_a_id": a["id"], "doctor_a_name": a["name"],
                    "doctor_b_id": b["id"], "doctor_b_name": b["name"],
                    "fuzzy_ratio": ratio,
                    "same_specialty": same_specialty, "same_locality": same_locality,
                    "reason": "fuzzy match but specialty/locality mismatch - needs manual review",
                })

    clusters: dict[int, list[dict]] = {}
    for d in doctors:
        clusters.setdefault(find(d["id"]), []).append(d)

    merges_performed = 0
    if not dry_run:
        for root, cluster in clusters.items():
            if len(cluster) > 1:
                _merge_cluster(conn, cluster)
                merges_performed += 1
    else:
        merges_performed = sum(1 for c in clusters.values() if len(c) > 1)
        print(f"[dry-run] would perform {merges_performed} merges")

    if review_rows and not dry_run:
        review_path = config.OUTPUT_DIR / "dedupe_review.csv"
        with open(review_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(review_rows[0].keys()))
            writer.writeheader()
            writer.writerows(review_rows)
        print(f"Wrote {len(review_rows)} near-matches to review: {review_path}")

    return {
        "doctors_before": n,
        "clusters_merged": merges_performed,
        "flagged_for_review": len(review_rows),
    }
