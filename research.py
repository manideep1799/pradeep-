"""Stage 6 — Research. For each doctor (highest-scoring first), run one
Gemini call with Google Search grounding to find sales-relevant facts: a
buying trigger, a prestige/credential signal, a growth signal, an authority
signal, a digital-presence gap, or a red flag — about the doctor OR their
hospital/clinic. Every finding is stored as a `sources` row (source_type=
'research_finding') with its citation URL, never invented, and a source on
config.PROHIBITED_FINDING_DOMAINS is dropped even if the model cited it.

Idempotent via doctors.researched_at: a doctor researched once is not
re-researched by a later run. Gemini's free tier is quota-tight (as low as
20 requests/day observed on some models) — this stage burns one call per
doctor, so `--limit` matters here more than anywhere else in the pipeline.
Tiering itself happens separately in rank.py (Stage 7), which runs over
every doctor including ones this stage hasn't reached yet.
"""

import json

import config
import db
import extract


_RESPONSE_SHAPE = """{
  "findings": [
    {"category": "buying_trigger|prestige_credential|growth_signal|authority_signal|digital_presence_gap|red_flag|other",
     "headline": "one short, specific, concrete sentence",
     "detail": "1-2 sentences of supporting detail",
     "source_url": "the exact URL this fact came from"}
  ],
  "opening_line": "one specific personalized outreach opening line, or null if there are no findings"
}"""


def _prompt_for(doctor: dict) -> str:
    clinic = doctor.get("clinic_name") or doctor.get("hospital_affiliation") or ""
    where = f" at {clinic}" if clinic else ""
    locality = f", {doctor['locality']}" if doctor.get("locality") else ""
    return (
        f"Research Dr. {doctor['name']}{where} in Hyderabad{locality} for a social-media "
        f"marketing agency deciding who to prioritize for outreach and how to open the "
        f"conversation. Look for public evidence of:\n"
        f"- buying_trigger: a new center/branch/wing being built, a recent expansion, a "
        f"renovation, new equipment, or anything suggesting fresh budget or ambition\n"
        f"- prestige_credential: where they trained/studied if it's a notably prestigious "
        f"institution, major awards (e.g. Padma awards), rare distinctions\n"
        f"- growth_signal: the practice/hospital visibly scaling (new branches, hiring, "
        f"media coverage of growth)\n"
        f"- authority_signal: frequent conference speaking, published research, media "
        f"quotes, thought leadership\n"
        f"- digital_presence_gap: strong real-world reputation but weak or outdated online "
        f"presence (an opening for a marketing agency specifically)\n"
        f"- red_flag: anything suggesting this is a poor prospect (e.g. hospital closed, "
        f"doctor relocated away from Hyderabad, active controversy)\n\n"
        f"Report at most {config.RESEARCH_MAX_FINDINGS_PER_DOCTOR} findings, each with the "
        f"exact source URL you found it on. If you find nothing credible, return an empty "
        f"findings list — never invent one. Also write one specific, personalized opening "
        f"line a salesperson could use in a first outreach message, grounded in the single "
        f"most compelling finding (or null if there are no findings).\n\n"
        f"Respond with ONLY JSON matching this shape:\n{_RESPONSE_SHAPE}"
    )


def _is_prohibited_source(url: str | None, domain: str | None) -> bool:
    haystack = ((domain or "") + " " + (url or "")).lower()
    return any(bad in haystack for bad in config.PROHIBITED_FINDING_DOMAINS)


def _citation_for(source_url: str | None, citations: list[dict]) -> dict | None:
    for c in citations:
        if c.get("url") == source_url:
            return c
    # The model sometimes paraphrases the citation url slightly; a single
    # grounding citation for an otherwise-uncited finding is still a
    # reasonable match, since most calls here only surface one source.
    return citations[0] if len(citations) == 1 else None


def run_research(conn, dry_run: bool = False, limit: int | None = None) -> dict:
    pending = db.doctors_pending_research(conn, limit=limit)

    if dry_run:
        for doctor in pending:
            print(f"[dry-run] would research {doctor['name']!r} (score {doctor['score']})")
        return {"doctors_researched": 0, "findings_stored": 0, "dropped_prohibited_sources": 0}

    client = extract.get_client()

    researched = 0
    findings_stored = 0
    dropped_prohibited = 0

    for row in pending:
        doctor = dict(row)
        prompt = _prompt_for(doctor)
        try:
            parsed, citations = extract.call_grounded_research(client, prompt)
        except Exception as exc:
            # A quota/rate-limit error won't clear up for the next doctor in
            # this same run — Gemini's free tier has a hard per-day cap as
            # low as 20 requests on some models. Stop the stage instead of
            # burning the ~13s throttle on every remaining doctor only to
            # fail the same way each time; whatever's left stays
            # researched_at=NULL and picks up on the next run (or next day).
            message = str(exc)
            if "RESOURCE_EXHAUSTED" in message or "429" in message or "quota" in message.lower():
                print(f"  ! quota exhausted after {researched} doctor(s) researched this run: {exc}")
                print(f"  ! stopping Stage 6 early - {len(pending) - researched} doctor(s) still pending, will pick up next run")
                break
            print(f"  ! research call failed for {doctor['name']!r}: {exc}")
            continue

        findings = []
        for f in ((parsed or {}).get("findings") or [])[: config.RESEARCH_MAX_FINDINGS_PER_DOCTOR]:
            if not f.get("headline"):
                continue
            category = f.get("category")
            if category not in config.RESEARCH_FINDING_WEIGHTS:
                category = "other"

            citation = _citation_for(f.get("source_url"), citations)
            source_url = f.get("source_url") or (citation or {}).get("url")
            domain = (citation or {}).get("domain")
            if _is_prohibited_source(source_url, domain):
                dropped_prohibited += 1
                continue

            findings.append({
                "category": category,
                "headline": f["headline"],
                "detail": f.get("detail"),
                "source_url": source_url,
            })

        for f in findings:
            db.insert_source(
                conn, doctor["id"], "research_finding", f.get("source_url"),
                json.dumps({"category": f["category"], "headline": f["headline"], "detail": f.get("detail")}),
            )
            findings_stored += 1

        opening_line = (parsed or {}).get("opening_line") if findings else None
        db.mark_researched(conn, doctor["id"], {"opening_line": opening_line})
        researched += 1
        print(f"  - {doctor['name']}: {len(findings)} finding(s)")

    return {
        "doctors_researched": researched,
        "findings_stored": findings_stored,
        "dropped_prohibited_sources": dropped_prohibited,
    }
