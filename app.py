"""Streamlit UI — single-page lead list, SaaS-style. No auth, no editing.

One table plus a lead picker below it that opens the full lead card in a
dialog. Filters live in a compact bar at the top; anything beyond the
essentials is tucked behind "More filters" so the page reads as simple by
default. Dark theme lives in .streamlit/config.toml.
"""

import json

import pandas as pd
import streamlit as st

import db

st.set_page_config(page_title="Doctor Leads", layout="wide")

TIER_ORDER = {"Hot": 0, "Warm": 1, "Cold": 2}
TIER_COLOR = {"Hot": "red", "Warm": "orange", "Cold": "blue"}
TIER_FALLBACK = "violet"  # for a doctor `python run.py rank` hasn't reached yet
ROLE_COLOR = {"Founder": "green", "Senior Decision-Maker": "blue", "Staff": "gray"}

# Dark-mode-appropriate cell badges: muted saturated background + bright text
# (the light-mode pastel/dark-text combo would look washed out here).
TIER_CELL_STYLE = {
    "Hot": "background-color: #4C1D1D; color: #FCA5A5",
    "Warm": "background-color: #4A3411; color: #FCD34D",
    "Cold": "background-color: #1E3A5F; color: #93C5FD",
}
ROLE_CELL_STYLE = {
    "Founder": "background-color: #14432B; color: #6EE7B7",
    "Senior Decision-Maker": "background-color: #2A2A63; color: #A5B4FC",
    "Staff": "color: #9CA3AF",
}


@st.cache_data(ttl=30)
def load_doctors() -> pd.DataFrame:
    conn = db.get_connection()
    try:
        return pd.read_sql_query("SELECT * FROM doctors", conn)
    finally:
        conn.close()


def load_sources(doctor_id: int) -> list[dict]:
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT source_type, source_url, raw_data, fetched_at FROM sources "
            "WHERE doctor_id = ? ORDER BY fetched_at",
            (doctor_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def load_findings(doctor_id: int) -> list[dict]:
    findings = []
    for s in load_sources(doctor_id):
        if s["source_type"] != "research_finding":
            continue
        try:
            data = json.loads(s["raw_data"]) if s["raw_data"] else {}
        except json.JSONDecodeError:
            data = {}
        data["source_url"] = s["source_url"]
        findings.append(data)
    return findings


@st.dialog("Lead", width="large")
def show_lead_dialog(record: dict) -> None:
    tier = record.get("tier")
    tier_color = TIER_COLOR.get(tier, TIER_FALLBACK)
    role = record.get("role_category")
    role_color = ROLE_COLOR.get(role, "gray")

    badges = [f":{tier_color}[**{tier or 'NOT RANKED'}**]"]
    if role:
        badges.append(f":{role_color}[{role}]")
    header_bits = [b for b in [record.get("clinic_name") or record.get("hospital_affiliation"), record.get("locality")] if b]

    st.markdown(f"## {record['name']}")
    st.markdown("  ·  ".join(badges + header_bits))
    st.caption(record.get("rank_reason") or "no strong signals yet")

    if not record.get("researched_at"):
        st.info("Not researched yet — tiered on base score only. Still shown, still on the list.")

    contact_bits = []
    if record.get("phone"):
        contact_bits.append(f"Phone: {record['phone']}")
    if record.get("email"):
        contact_bits.append(f"Email: {record['email']}")
    if record.get("website"):
        contact_bits.append(f"[Website]({record['website']})")
    if record.get("instagram_handle"):
        contact_bits.append(f"[Instagram]({record['instagram_handle']})")
    if record.get("facebook_url"):
        contact_bits.append(f"[Facebook]({record['facebook_url']})")
    if record.get("linkedin_url"):
        contact_bits.append(f"[LinkedIn]({record['linkedin_url']})")
    if record.get("youtube_url"):
        contact_bits.append(f"[YouTube]({record['youtube_url']})")
    st.markdown("  |  ".join(contact_bits) if contact_bits else "*No contact channel captured yet.*")

    if record.get("opening_line"):
        st.success(record["opening_line"])

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**Why this doctor**")
        try:
            base_reasons = json.loads(record["score_reasons"]) if record.get("score_reasons") else []
        except (json.JSONDecodeError, TypeError):
            base_reasons = []
        st.markdown("\n".join(f"- {r}" for r in base_reasons) if base_reasons else "*No base-score signals recorded.*")

    with col_b:
        st.markdown("**Research findings**")
        findings = load_findings(int(record["id"]))
        if findings:
            for f in findings:
                line = f"- **{f.get('headline', '')}**"
                if f.get("detail"):
                    line += f" — {f['detail']}"
                if f.get("source_url"):
                    line += f" ([source]({f['source_url']}))"
                st.markdown(line)
        else:
            st.caption("No research findings yet.")

    with st.expander("Full record (raw)"):
        full = {k: v for k, v in record.items() if k not in ("score_reasons", "tier_rank", "sort_score", "researched", "organization")}
        st.json(full)
        st.write("**All source records (audit trail):**")
        sources = load_sources(int(record["id"]))
        if not sources:
            st.write("No source rows recorded.")
        for s in sources:
            st.markdown(f"- `{s['source_type']}` — {s['source_url'] or '(no url)'} — fetched {s['fetched_at']}")


# --- Header ---
title_col, refresh_col = st.columns([5, 1])
with title_col:
    st.title("Doctor Leads")
with refresh_col:
    st.write("")
    if st.button("Refresh", use_container_width=True):
        st.cache_data.clear()

df = load_doctors()

if df.empty:
    st.info("No doctors in the database yet. Run `python run.py all` first.")
    st.stop()

has_contact = (df["phone"].fillna("") != "") | (df["email"].fillna("") != "")
df["contact_type"] = "none"
df.loc[has_contact & (df["has_own_practice"] == 1), "contact_type"] = "direct"
df.loc[has_contact & (df["has_own_practice"] != 1), "contact_type"] = "hospital"

df["organization"] = df["clinic_name"].fillna(df["hospital_affiliation"])
df["sort_score"] = df["adjusted_score"].fillna(df["score"])
df["tier_rank"] = df["tier"].map(TIER_ORDER).fillna(len(TIER_ORDER))
df["researched"] = df["researched_at"].notna()

total_doctors = len(df)
tier_counts = df["tier"].value_counts()
role_counts = df["role_category"].value_counts()
total_researched = int(df["researched"].sum())

st.caption(
    f"**{total_doctors}** leads &nbsp;·&nbsp; "
    f":red[**{int(tier_counts.get('Hot', 0))}** Hot] &nbsp;·&nbsp; "
    f":orange[**{int(tier_counts.get('Warm', 0))}** Warm] &nbsp;·&nbsp; "
    f":green[**{int(role_counts.get('Founder', 0))}** Founders] &nbsp;·&nbsp; "
    f"{total_researched}/{total_doctors} researched"
)

# --- Compact filter bar ---
f1, f2, f3, f4, f5 = st.columns([2.5, 1.3, 1.7, 1.5, 1])

with f1:
    search_text = st.text_input("Search", placeholder="Search name or clinic", label_visibility="collapsed")
with f2:
    tier_options = [t for t in ["Hot", "Warm", "Cold"] if t in df["tier"].dropna().unique()]
    selected_tiers = st.multiselect("Tier", tier_options, placeholder="Tier", label_visibility="collapsed")
with f3:
    role_options = [r for r in ["Founder", "Senior Decision-Maker", "Staff"] if r in df["role_category"].dropna().unique()]
    selected_roles = st.multiselect("Role", role_options, placeholder="Role category", label_visibility="collapsed")
with f4:
    localities = sorted(l for l in df["locality"].dropna().unique().tolist() if l)
    selected_localities = st.multiselect("Locality", localities, placeholder="Locality", label_visibility="collapsed")
with f5:
    with st.popover("More filters", use_container_width=True):
        specialties = sorted(s for s in df["specialty"].dropna().unique().tolist() if s)
        selected_specialties = st.multiselect("Specialty", specialties)

        researched_filter = st.selectbox("Research status", ["Any", "Researched", "Not researched yet"])

        exp_series = df["experience_years"].dropna()
        exp_lo, exp_hi = (int(exp_series.min()), int(exp_series.max())) if not exp_series.empty else (0, 50)
        if exp_lo == exp_hi:
            exp_hi += 1
        experience_range = st.slider("Experience (years)", exp_lo, exp_hi, (exp_lo, exp_hi))

        contact_filter = st.selectbox(
            "Contact type",
            ["Any", "Has any contact", "Has direct contact (owns practice)", "Has hospital-only contact"],
        )
        has_own_practice_only = st.checkbox("Has own practice only")

        score_lo, score_hi = int(df["sort_score"].min()), int(df["sort_score"].max())
        if score_lo == score_hi:
            score_hi += 1
        min_score = st.slider("Minimum score", score_lo, score_hi, score_lo)

# --- Apply filters ---
filtered = df.copy()

if search_text:
    mask = (
        filtered["name"].str.contains(search_text, case=False, na=False)
        | filtered["clinic_name"].str.contains(search_text, case=False, na=False)
    )
    filtered = filtered[mask]

if selected_tiers:
    filtered = filtered[filtered["tier"].isin(selected_tiers)]

if selected_roles:
    filtered = filtered[filtered["role_category"].isin(selected_roles)]

if selected_localities:
    filtered = filtered[filtered["locality"].isin(selected_localities)]

if selected_specialties:
    filtered = filtered[filtered["specialty"].isin(selected_specialties)]

if researched_filter == "Researched":
    filtered = filtered[filtered["researched"]]
elif researched_filter == "Not researched yet":
    filtered = filtered[~filtered["researched"]]

filtered = filtered[
    filtered["experience_years"].isna()
    | filtered["experience_years"].between(experience_range[0], experience_range[1])
]

if contact_filter == "Has any contact":
    filtered = filtered[filtered["contact_type"] != "none"]
elif contact_filter == "Has direct contact (owns practice)":
    filtered = filtered[filtered["contact_type"] == "direct"]
elif contact_filter == "Has hospital-only contact":
    filtered = filtered[filtered["contact_type"] == "hospital"]

if has_own_practice_only:
    filtered = filtered[filtered["has_own_practice"] == 1]

filtered = filtered[filtered["sort_score"] >= min_score]
filtered = filtered.sort_values(["tier_rank", "sort_score"], ascending=[True, False]).reset_index(drop=True)

# --- Table ---
list_col, export_col = st.columns([5, 1])
with list_col:
    st.caption(f"{len(filtered)} of {total_doctors} shown")
with export_col:
    export_cols = [c for c in df.columns if c not in ("tier_rank", "sort_score", "researched", "organization")]
    csv_bytes = filtered[export_cols].to_csv(index=False).encode("utf-8")
    st.download_button("Export CSV", csv_bytes, file_name="leads.csv", mime="text/csv", use_container_width=True)

if filtered.empty:
    st.info("No leads match the current filters.")
else:
    TABLE_COLS = ["tier", "role_category", "name", "organization", "locality", "specialty", "phone", "sort_score"]
    table_view = filtered[TABLE_COLS].rename(columns={"role_category": "role", "sort_score": "score"})

    styled = (
        table_view.style
        .map(lambda v: TIER_CELL_STYLE.get(v, ""), subset=["tier"])
        .map(lambda v: ROLE_CELL_STYLE.get(v, ""), subset=["role"])
    )
    st.dataframe(styled, use_container_width=True, hide_index=True, height=460)

    lead_labels = [
        f"{row['name']} — {row['organization'] or 'no organization'} ({row['tier'] or 'not ranked'})"
        for _, row in filtered.iterrows()
    ]
    picked_idx = st.selectbox(
        "Open a lead",
        options=range(len(filtered)),
        format_func=lambda i: lead_labels[i],
        index=None,
        placeholder="Search and select a doctor to see the full lead...",
    )

    if picked_idx is not None:
        picked = filtered.iloc[picked_idx]
        doctor_id = int(picked["id"])
        if st.session_state.get("_last_opened_lead") != doctor_id:
            st.session_state["_last_opened_lead"] = doctor_id
            show_lead_dialog(picked.to_dict())
    else:
        st.session_state["_last_opened_lead"] = None
