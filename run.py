"""CLI orchestrator.

    python run.py discover          # Stage 1
    python run.py extract           # Stage 2 (clinic sites + hospitals + conferences)
    python run.py dedupe            # Stage 3
    python run.py enrich            # Stage 4
    python run.py score             # Stage 5
    python run.py research          # Stage 6 (Gemini + Google Search grounding)
    python run.py rank              # Stage 7 (Hot/Warm/Cold tiering)
    python run.py all               # everything, in order
    python run.py all --dry-run     # no writes, print what would happen
    python run.py extract --limit 10   # only process the first 10 pending clinic sites
    python run.py research --limit 10  # only research the top 10 un-researched doctors
    python run.py all --limit 10       # same limit applied wherever extract/research run within "all"
    python run.py discover --locality "Jubilee Hills"   # one locality, small bbox (most reliable)
    python run.py discover --locality all               # every locality, prime-first, then citywide catch-all

Fails loudly and stops the run on a misconfigured API key. Per-record
failures (a bad website, a malformed page) are logged and skipped, never
allowed to kill the whole run.
"""

import argparse
import sys

import db
import dedupe
import enrich
import rank
import research
import score
from extract import ExtractionError
from sources import clinic_sites, conferences, hospitals, osm


def cmd_discover(conn, args) -> None:
    print("=== Stage 1: Discover (OpenStreetMap Overpass) ===")
    result = osm.run_discovery(conn, dry_run=args.dry_run, locality=args.locality)
    print(result)
    if not args.dry_run:
        print(db.summary_counts(conn))


def cmd_extract(conn, args) -> None:
    print("=== Stage 2a: Extract - clinic websites ===")
    print(clinic_sites.run_clinic_extraction(conn, dry_run=args.dry_run, limit=args.limit))

    print("=== Stage 2b: Extract - hospital directories ===")
    print(hospitals.run_hospital_extraction(conn, dry_run=args.dry_run))

    print("=== Stage 2c: Extract - conference pages (optional) ===")
    print(conferences.run_conference_extraction(conn, dry_run=args.dry_run))


def cmd_dedupe(conn, args) -> None:
    print("=== Stage 3: Dedupe ===")
    print(dedupe.run_dedupe(conn, dry_run=args.dry_run))


def cmd_enrich(conn, args) -> None:
    print("=== Stage 4: Enrich ===")
    print(enrich.run_enrichment(conn, dry_run=args.dry_run))


def cmd_score(conn, args) -> None:
    print("=== Stage 5: Score ===")
    print(score.run_scoring(conn, dry_run=args.dry_run))


def cmd_research(conn, args) -> None:
    print("=== Stage 6: Research ===")
    print(research.run_research(conn, dry_run=args.dry_run, limit=args.limit))


def cmd_rank(conn, args) -> None:
    print("=== Stage 7: Rank ===")
    print(rank.run_ranking(conn, dry_run=args.dry_run))


STAGES = {
    "discover": cmd_discover,
    "extract": cmd_extract,
    "dedupe": cmd_dedupe,
    "enrich": cmd_enrich,
    "score": cmd_score,
    "research": cmd_research,
    "rank": cmd_rank,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Doctor lead discovery pipeline")
    parser.add_argument("stage", choices=[*STAGES.keys(), "all"])
    parser.add_argument("--dry-run", action="store_true", help="No writes, print what would happen")
    parser.add_argument("--limit", type=int, default=None,
                         help="Cap how many pending clinic sites (extract) or un-researched "
                              "doctors (research) get processed")
    parser.add_argument("--locality", type=str, default=None,
                         help="discover only: a single locality name (e.g. 'Jubilee Hills'), "
                              "or 'all' to sweep every locality prime-first. Omit for the "
                              "default single citywide sweep.")
    args = parser.parse_args()

    db.init_db()
    conn = db.get_connection()

    try:
        if args.stage == "all":
            for name, fn in STAGES.items():
                fn(conn, args)
        else:
            STAGES[args.stage](conn, args)
    except (osm.OverpassError, ExtractionError) as exc:
        print(f"\nFATAL: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
