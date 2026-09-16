"""CLI orchestrator.

    python run.py discover          # Stage 1
    python run.py extract           # Stage 2 (clinic sites + hospitals + conferences)
    python run.py dedupe            # Stage 3
    python run.py enrich            # Stage 4
    python run.py score             # Stage 5
    python run.py all               # everything, in order
    python run.py all --dry-run     # no writes, print what would happen
    python run.py extract --limit 10   # only process the first 10 pending clinic sites
    python run.py all --limit 10       # same limit applied wherever extract runs within "all"

Fails loudly and stops the run on a misconfigured API key. Per-record
failures (a bad website, a malformed page) are logged and skipped, never
allowed to kill the whole run.
"""

import argparse
import sys

import config
import db
import dedupe
import enrich
import score
from extract import ExtractionError
from sources import clinic_sites, conferences, hospitals, osm


def cmd_discover(conn, dry_run: bool, limit: int | None) -> None:
    print("=== Stage 1: Discover (OpenStreetMap Overpass) ===")
    result = osm.run_discovery(conn, dry_run=dry_run)
    print(result)
    if not dry_run:
        print(db.summary_counts(conn))


def cmd_extract(conn, dry_run: bool, limit: int | None) -> None:
    print("=== Stage 2a: Extract - clinic websites ===")
    print(clinic_sites.run_clinic_extraction(conn, dry_run=dry_run, limit=limit))

    print("=== Stage 2b: Extract - hospital directories ===")
    print(hospitals.run_hospital_extraction(conn, dry_run=dry_run))

    print("=== Stage 2c: Extract - conference pages (optional) ===")
    print(conferences.run_conference_extraction(conn, dry_run=dry_run))


def cmd_dedupe(conn, dry_run: bool, limit: int | None) -> None:
    print("=== Stage 3: Dedupe ===")
    print(dedupe.run_dedupe(conn, dry_run=dry_run))


def cmd_enrich(conn, dry_run: bool, limit: int | None) -> None:
    print("=== Stage 4: Enrich ===")
    print(enrich.run_enrichment(conn, dry_run=dry_run))


def cmd_score(conn, dry_run: bool, limit: int | None) -> None:
    print("=== Stage 5: Score ===")
    print(score.run_scoring(conn, dry_run=dry_run))


STAGES = {
    "discover": cmd_discover,
    "extract": cmd_extract,
    "dedupe": cmd_dedupe,
    "enrich": cmd_enrich,
    "score": cmd_score,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Doctor lead discovery pipeline")
    parser.add_argument("stage", choices=[*STAGES.keys(), "all"])
    parser.add_argument("--dry-run", action="store_true", help="No writes, print what would happen")
    parser.add_argument("--limit", type=int, default=None,
                         help="Cap how many pending clinic sites the extract stage processes")
    args = parser.parse_args()

    db.init_db()
    conn = db.get_connection()

    try:
        if args.stage == "all":
            for name, fn in STAGES.items():
                fn(conn, args.dry_run, args.limit)
        else:
            STAGES[args.stage](conn, args.dry_run, args.limit)
    except (osm.OverpassError, ExtractionError) as exc:
        print(f"\nFATAL: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
