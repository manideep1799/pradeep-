"""Schema init, connection, and upsert helpers. No ORM."""

import sqlite3
from datetime import datetime, timezone

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS clinics (
    place_id        TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT,
    address         TEXT,
    locality        TEXT,
    phone           TEXT,
    website         TEXT,
    email           TEXT,
    rating          REAL,
    review_count    INTEGER,
    business_status TEXT,
    maps_url        TEXT,
    found_via       TEXT,
    website_fetched INTEGER DEFAULT 0,
    fetch_error     TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP
);

CREATE TABLE IF NOT EXISTS doctors (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL,
    name_normalized     TEXT NOT NULL,
    qualification       TEXT,
    specialty           TEXT,
    experience_years    INTEGER,
    designation         TEXT,
    clinic_place_id     TEXT,
    clinic_name         TEXT,
    hospital_affiliation TEXT,
    locality            TEXT,
    phone               TEXT,
    email               TEXT,
    website             TEXT,
    instagram_handle    TEXT,
    facebook_url        TEXT,
    linkedin_url        TEXT,
    youtube_url         TEXT,
    has_own_practice    INTEGER,
    role_category       TEXT,
    score               INTEGER DEFAULT 0,
    score_reasons       TEXT,
    adjusted_score      INTEGER,
    tier                TEXT,
    rank_reason         TEXT,
    opening_line        TEXT,
    researched_at       TIMESTAMP,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP,
    FOREIGN KEY (clinic_place_id) REFERENCES clinics(place_id)
);

CREATE TABLE IF NOT EXISTS sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    doctor_id   INTEGER NOT NULL,
    source_type TEXT NOT NULL,
    source_url  TEXT,
    raw_data    TEXT,
    fetched_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (doctor_id) REFERENCES doctors(id)
);

CREATE INDEX IF NOT EXISTS idx_doctors_name_normalized ON doctors(name_normalized);
CREATE INDEX IF NOT EXISTS idx_doctors_clinic_place_id ON doctors(clinic_place_id);
CREATE INDEX IF NOT EXISTS idx_sources_doctor_id ON sources(doctor_id);
CREATE INDEX IF NOT EXISTS idx_sources_source_url ON sources(source_url);
"""

# Columns added after the tool's first release. CREATE TABLE above already
# includes them for a brand-new DB; these ALTERs bring an existing DB (like
# the one already deployed) up to date. Each is idempotent: a "duplicate
# column" failure means a previous run already applied it, so it's ignored.
_MIGRATIONS = [
    "ALTER TABLE doctors ADD COLUMN facebook_url TEXT",
    "ALTER TABLE doctors ADD COLUMN linkedin_url TEXT",
    "ALTER TABLE doctors ADD COLUMN adjusted_score INTEGER",
    "ALTER TABLE doctors ADD COLUMN tier TEXT",
    "ALTER TABLE doctors ADD COLUMN rank_reason TEXT",
    "ALTER TABLE doctors ADD COLUMN opening_line TEXT",
    "ALTER TABLE doctors ADD COLUMN researched_at TIMESTAMP",
    "ALTER TABLE doctors ADD COLUMN role_category TEXT",
]


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        for stmt in _MIGRATIONS:
            try:
                conn.execute(stmt)
                conn.commit()
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_clinic(conn: sqlite3.Connection, data: dict) -> str:
    """Insert a clinic, or update it in place if place_id already exists."""
    place_id = data["place_id"]
    existing = conn.execute(
        "SELECT place_id FROM clinics WHERE place_id = ?", (place_id,)
    ).fetchone()

    fields = [
        "name", "category", "address", "locality", "phone", "website",
        "email", "rating", "review_count", "business_status", "maps_url",
        "found_via",
    ]
    values = {f: data.get(f) for f in fields}

    if existing:
        set_clause = ", ".join(f"{f} = ?" for f in fields)
        conn.execute(
            f"UPDATE clinics SET {set_clause}, updated_at = ? WHERE place_id = ?",
            [*values.values(), _now(), place_id],
        )
    else:
        columns = ["place_id", *fields, "updated_at"]
        placeholders = ", ".join("?" for _ in columns)
        conn.execute(
            f"INSERT INTO clinics ({', '.join(columns)}) VALUES ({placeholders})",
            [place_id, *values.values(), _now()],
        )
    conn.commit()
    return place_id


def mark_website_fetched(conn: sqlite3.Connection, place_id: str, error: str | None = None) -> None:
    conn.execute(
        "UPDATE clinics SET website_fetched = 1, fetch_error = ?, updated_at = ? WHERE place_id = ?",
        (error, _now(), place_id),
    )
    conn.commit()


def clinics_pending_extraction(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM clinics WHERE website IS NOT NULL AND website != '' AND website_fetched = 0"
    if limit is not None:
        sql += " LIMIT ?"
        return conn.execute(sql, (limit,)).fetchall()
    return conn.execute(sql).fetchall()


def insert_doctor(conn: sqlite3.Connection, data: dict) -> int:
    fields = [
        "name", "name_normalized", "qualification", "specialty", "experience_years",
        "designation", "clinic_place_id", "clinic_name", "hospital_affiliation",
        "locality", "phone", "email", "website", "instagram_handle", "facebook_url",
        "linkedin_url", "youtube_url", "has_own_practice",
    ]
    values = [data.get(f) for f in fields]
    columns = [*fields, "updated_at"]
    placeholders = ", ".join("?" for _ in columns)
    cur = conn.execute(
        f"INSERT INTO doctors ({', '.join(columns)}) VALUES ({placeholders})",
        [*values, _now()],
    )
    conn.commit()
    return cur.lastrowid


def update_doctor(conn: sqlite3.Connection, doctor_id: int, data: dict) -> None:
    if not data:
        return
    set_clause = ", ".join(f"{k} = ?" for k in data)
    conn.execute(
        f"UPDATE doctors SET {set_clause}, updated_at = ? WHERE id = ?",
        [*data.values(), _now(), doctor_id],
    )
    conn.commit()


def delete_doctor(conn: sqlite3.Connection, doctor_id: int) -> None:
    conn.execute("DELETE FROM doctors WHERE id = ?", (doctor_id,))
    conn.commit()


def insert_source(conn: sqlite3.Connection, doctor_id: int, source_type: str,
                   source_url: str | None, raw_data: str | None) -> int:
    cur = conn.execute(
        "INSERT INTO sources (doctor_id, source_type, source_url, raw_data) VALUES (?, ?, ?, ?)",
        (doctor_id, source_type, source_url, raw_data),
    )
    conn.commit()
    return cur.lastrowid


def source_url_already_processed(conn: sqlite3.Connection, source_url: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sources WHERE source_url = ? LIMIT 1", (source_url,)
    ).fetchone()
    return row is not None


def reassign_sources(conn: sqlite3.Connection, from_doctor_id: int, to_doctor_id: int) -> None:
    conn.execute(
        "UPDATE sources SET doctor_id = ? WHERE doctor_id = ?",
        (to_doctor_id, from_doctor_id),
    )
    conn.commit()


def all_doctors(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM doctors").fetchall()


def doctors_pending_research(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    """Highest-scoring un-researched doctors first — Gemini's free-tier quota
    is tight enough that ordering which doctor gets today's calls matters."""
    sql = "SELECT * FROM doctors WHERE researched_at IS NULL ORDER BY score DESC"
    if limit is not None:
        sql += " LIMIT ?"
        return conn.execute(sql, (limit,)).fetchall()
    return conn.execute(sql).fetchall()


def mark_researched(conn: sqlite3.Connection, doctor_id: int, updates: dict) -> None:
    update_doctor(conn, doctor_id, {**updates, "researched_at": _now()})


def doctors_at_clinic_count(conn: sqlite3.Connection, clinic_place_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM doctors WHERE clinic_place_id = ?", (clinic_place_id,)
    ).fetchone()
    return row["n"] if row else 0


def summary_counts(conn: sqlite3.Connection) -> dict:
    clinics_total = conn.execute("SELECT COUNT(*) AS n FROM clinics").fetchone()["n"]
    clinics_with_website = conn.execute(
        "SELECT COUNT(*) AS n FROM clinics WHERE website IS NOT NULL AND website != ''"
    ).fetchone()["n"]
    clinics_with_phone = conn.execute(
        "SELECT COUNT(*) AS n FROM clinics WHERE phone IS NOT NULL AND phone != ''"
    ).fetchone()["n"]
    clinics_with_both = conn.execute(
        "SELECT COUNT(*) AS n FROM clinics WHERE website IS NOT NULL AND website != '' "
        "AND phone IS NOT NULL AND phone != ''"
    ).fetchone()["n"]
    doctors_total = conn.execute("SELECT COUNT(*) AS n FROM doctors").fetchone()["n"]
    doctors_with_contact = conn.execute(
        "SELECT COUNT(*) AS n FROM doctors WHERE (phone IS NOT NULL AND phone != '') "
        "OR (email IS NOT NULL AND email != '')"
    ).fetchone()["n"]
    return {
        "clinics_total": clinics_total,
        "clinics_with_website": clinics_with_website,
        "clinics_with_phone": clinics_with_phone,
        "clinics_with_both": clinics_with_both,
        "doctors_total": doctors_total,
        "doctors_with_contact": doctors_with_contact,
    }
