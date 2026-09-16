"""Streamlit UI. Single page, two internal users. No charts, no auth, no editing."""

import json

import pandas as pd
import streamlit as st

import db

st.set_page_config(page_title="Doctor Leads", layout="wide")


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


st.title("Doctor Leads")

if st.button("Refresh data"):
    st.cache_data.clear()

df = load_doctors()

if df.empty:
    st.info("No doctors in the database yet. Run `python run.py all` first.")
    st.stop()

# A phone/email on a doctor who owns their practice reaches them directly.
# The same field on a doctor who doesn't own their practice is really the
# hospital's switchboard — you're not reaching that specific doctor.
has_contact = (df["phone"].fillna("") != "") | (df["email"].fillna("") != "")
df["contact_type"] = "none"
df.loc[has_contact & (df["has_own_practice"] == 1), "contact_type"] = "direct"
df.loc[has_contact & (df["has_own_practice"] != 1), "contact_type"] = "hospital"

# --- Header stats ---
total_doctors = len(df)
total_with_contact = int(has_contact.sum())
total_direct_contact = int((df["contact_type"] == "direct").sum())
total_scoring_8plus = int((df["score"] >= 8).sum())

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total doctors", total_doctors)
c2.metric("With contact", total_with_contact)
c3.metric("Direct contact", total_direct_contact)
c4.metric("Scoring 8+", total_scoring_8plus)

st.divider()

# --- Filters ---
st.sidebar.header("Filters")

search_text = st.sidebar.text_input("Search name or clinic")

specialties = sorted(s for s in df["specialty"].dropna().unique().tolist() if s)
selected_specialties = st.sidebar.multiselect("Specialty", specialties)

localities = sorted(l for l in df["locality"].dropna().unique().tolist() if l)
selected_localities = st.sidebar.multiselect("Locality", localities)

exp_series = df["experience_years"].dropna()
exp_lo, exp_hi = (int(exp_series.min()), int(exp_series.max())) if not exp_series.empty else (0, 50)
if exp_lo == exp_hi:
    exp_hi += 1
experience_range = st.sidebar.slider("Experience (years)", exp_lo, exp_hi, (exp_lo, exp_hi))

contact_filter = st.sidebar.selectbox(
    "Contact type",
    ["Any", "Has any contact", "Has direct contact (owns practice)", "Has hospital-only contact"],
)
has_own_practice_only = st.sidebar.checkbox("Has own practice only")

score_lo, score_hi = int(df["score"].min()), int(df["score"].max())
if score_lo == score_hi:
    score_hi += 1
min_score = st.sidebar.slider("Minimum score", score_lo, score_hi, score_lo)

# --- Apply filters ---
filtered = df.copy()

if search_text:
    mask = (
        filtered["name"].str.contains(search_text, case=False, na=False)
        | filtered["clinic_name"].str.contains(search_text, case=False, na=False)
    )
    filtered = filtered[mask]

if selected_specialties:
    filtered = filtered[filtered["specialty"].isin(selected_specialties)]

if selected_localities:
    filtered = filtered[filtered["locality"].isin(selected_localities)]

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

filtered = filtered[filtered["score"] >= min_score]
filtered = filtered.sort_values("score", ascending=False)

st.subheader(f"Doctors ({len(filtered)})")

csv_bytes = filtered.drop(columns=["score_reasons"], errors="ignore").to_csv(index=False).encode("utf-8")
st.download_button("Export CSV (current filtered view)", csv_bytes, file_name="leads.csv", mime="text/csv")

display_cols = [
    "name", "specialty", "designation", "experience_years", "clinic_name",
    "hospital_affiliation", "locality", "phone", "email", "website",
    "has_own_practice", "contact_type", "score",
]
st.dataframe(filtered[display_cols], use_container_width=True, hide_index=True)

st.divider()
st.subheader("Record detail")

if filtered.empty:
    st.write("No records match the current filters.")
else:
    labels = [
        f"{row['name']} — {row['clinic_name'] or row['hospital_affiliation'] or 'no clinic'} — score {row['score']}"
        for _, row in filtered.iterrows()
    ]
    choice = st.selectbox("Select a doctor", options=range(len(filtered)), format_func=lambda i: labels[i])
    selected_row = filtered.iloc[choice]

    with st.expander("Full record", expanded=True):
        record = selected_row.to_dict()
        reasons = record.pop("score_reasons", None)
        st.json(record)
        if reasons:
            try:
                st.write("Score reasons:", ", ".join(json.loads(reasons)))
            except (json.JSONDecodeError, TypeError):
                pass

        st.write("**Sources:**")
        sources = load_sources(int(selected_row["id"]))
        if not sources:
            st.write("No source rows recorded.")
        for s in sources:
            url_display = s["source_url"] or "(no url)"
            st.markdown(f"- `{s['source_type']}` — {url_display} — fetched {s['fetched_at']}")
