"""Stage 7 — Rank. Turns the Stage 5 base score plus any Stage 6 research
findings into a Hot / Warm / Cold tier and a one-line reason, for EVERY
doctor — not just the ones research.py has gotten to yet. Pure computation,
no network calls, so it's cheap to re-run any time the score or findings
change and is never blocked by API quota.

Everyone gets a tier. A doctor with no research findings yet still gets one
from their base score alone — findings only ever push it up or down from
there. app.py uses doctors.researched_at to label those "not researched yet"
without hiding them, per the product rule that nobody is filtered out.
"""

import json

import config
import db


def _findings_for(conn, doctor_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT source_url, raw_data FROM sources WHERE doctor_id = ? AND source_type = 'research_finding'",
        (doctor_id,),
    ).fetchall()
    findings = []
    for row in rows:
        if not row["raw_data"]:
            continue
        try:
            data = json.loads(row["raw_data"])
        except json.JSONDecodeError:
            continue
        data["source_url"] = row["source_url"]
        findings.append(data)
    return findings


def compute_tier(base_score: int, findings: list[dict]) -> tuple[str, int]:
    adjusted = base_score + sum(
        config.RESEARCH_FINDING_WEIGHTS.get(f.get("category"), 0) for f in findings
    )
    if adjusted >= config.TIER_HOT_MIN:
        return "Hot", adjusted
    if adjusted >= config.TIER_WARM_MIN:
        return "Warm", adjusted
    return "Cold", adjusted


def _rank_reason(base_reasons: list[str], findings: list[dict]) -> str:
    parts = [f["headline"] for f in findings[:1] if f.get("headline")]
    parts.extend(base_reasons[:2])
    return "; ".join(parts) if parts else "no strong signals yet"


def run_ranking(conn, dry_run: bool = False) -> dict:
    doctors = [dict(d) for d in db.all_doctors(conn)]
    tier_counts = {"Hot": 0, "Warm": 0, "Cold": 0}
    ranked = 0

    for doctor in doctors:
        findings = _findings_for(conn, doctor["id"])
        base_reasons = json.loads(doctor["score_reasons"]) if doctor.get("score_reasons") else []
        tier, adjusted_score = compute_tier(doctor.get("score") or 0, findings)
        rank_reason = _rank_reason(base_reasons, findings)
        tier_counts[tier] += 1

        if dry_run:
            continue
        db.update_doctor(conn, doctor["id"], {
            "tier": tier,
            "adjusted_score": adjusted_score,
            "rank_reason": rank_reason,
        })
        ranked += 1

    return {
        "doctors_ranked": ranked,
        "tier_hot": tier_counts["Hot"],
        "tier_warm": tier_counts["Warm"],
        "tier_cold": tier_counts["Cold"],
    }
