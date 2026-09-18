"""All tunable values for the pipeline live here. Nothing downstream should hardcode these."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- Paths ---
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "leads.db"

DATA_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# --- Secrets / API config ---
# Free-tier key from https://aistudio.google.com/apikey — used for LLM extraction (Stage 2).
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-flash-lite-latest")
REQUEST_DELAY_SECONDS = float(os.getenv("REQUEST_DELAY_SECONDS", "1.0"))
# Gemini's free tier has been observed capping gemini-3.6-flash at 5 requests
# per minute, which is far tighter than the page-fetch politeness delay above.
# 13s keeps us under that (60s / 5 = 12s minimum, +1s margin).
LLM_REQUEST_DELAY_SECONDS = float(os.getenv("LLM_REQUEST_DELAY_SECONDS", "13.0"))

# --- HTTP ---
USER_AGENT = "DoctorLeadDiscoveryBot/1.0 (internal agency research tool)"
REQUEST_TIMEOUT_SECONDS = 15

# --- Stage 1: OSM Overpass discovery (no key required) ---
LOCALITIES = [
    "Jubilee Hills", "Banjara Hills", "Gachibowli", "Madhapur", "Kondapur",
    "Somajiguda", "Begumpet", "Himayatnagar", "Kukatpally", "Secunderabad",
    "Ameerpet", "Hitec City", "Manikonda", "Attapur", "Uppal", "Miyapur",
    "Punjagutta", "Abids", "Koti", "Malakpet", "Dilsukhnagar", "LB Nagar",
    "Vanasthalipuram", "Nagole", "Tarnaka", "ECIL", "Alwal", "Bowenpally",
    "Trimulgherry", "Sainikpuri", "Kompally", "Chandanagar", "Nallagandla",
    "Kokapet", "Nanakramguda", "Tolichowki", "Mehdipatnam", "Charminar",
    "Falaknuma", "Karwan", "Golconda", "Erragadda", "SR Nagar", "Sanathnagar",
    "Moosapet", "Balanagar", "Bachupally", "Nizampet", "Pragathi Nagar",
    "Malkajgiri", "Habsiguda", "Nacharam", "Boduppal", "Shamshabad",
    "Rajendranagar", "Financial District",
]

CITY_SUFFIX = "Hyderabad"

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# (south, west, north, east) — a generous box around Greater Hyderabad that
# comfortably covers every locality above, including outer ones like Uppal
# and Miyapur.
HYDERABAD_BBOX = (17.15, 78.15, 17.65, 78.70)
OVERPASS_TIMEOUT_SECONDS = 90

# Approximate locality centroids (lat, lon), used to bucket a clinic into one
# of LOCALITIES by nearest distance when OSM has no addr:suburb tag for it
# (common in India), and to build a small per-locality query box for
# `python run.py discover --locality "..."`. These are approximate
# neighbourhood centers, not precise boundaries — a fuzzy nearest-match is
# the intent, not a claim of exact locality boundaries.
LOCALITY_CENTROIDS = {
    "Jubilee Hills": (17.4325, 78.4071),
    "Banjara Hills": (17.4156, 78.4347),
    "Gachibowli": (17.4401, 78.3489),
    "Madhapur": (17.4483, 78.3915),
    "Kondapur": (17.4615, 78.3627),
    "Somajiguda": (17.4239, 78.4611),
    "Begumpet": (17.4400, 78.4650),
    "Himayatnagar": (17.4008, 78.4867),
    "Kukatpally": (17.4849, 78.4138),
    "Secunderabad": (17.4399, 78.4983),
    "Ameerpet": (17.4374, 78.4482),
    "Hitec City": (17.4435, 78.3772),
    "Manikonda": (17.4062, 78.3831),
    "Attapur": (17.3654, 78.4321),
    "Uppal": (17.4058, 78.5591),
    "Miyapur": (17.4966, 78.3648),
    "Punjagutta": (17.4239, 78.4489),
    "Abids": (17.3902, 78.4767),
    "Koti": (17.3833, 78.4808),
    "Malakpet": (17.3739, 78.4991),
    "Dilsukhnagar": (17.3687, 78.5247),
    "LB Nagar": (17.3466, 78.5531),
    "Vanasthalipuram": (17.3266, 78.5533),
    "Nagole": (17.3730, 78.5555),
    "Tarnaka": (17.4239, 78.5136),
    "ECIL": (17.4667, 78.5667),
    "Alwal": (17.4833, 78.5083),
    "Bowenpally": (17.4667, 78.4833),
    "Trimulgherry": (17.4667, 78.4972),
    "Sainikpuri": (17.4889, 78.5528),
    "Kompally": (17.5333, 78.4833),
    "Chandanagar": (17.4933, 78.3372),
    "Nallagandla": (17.4633, 78.3372),
    "Kokapet": (17.4133, 78.3389),
    "Nanakramguda": (17.4189, 78.3419),
    "Tolichowki": (17.3961, 78.4275),
    "Mehdipatnam": (17.3959, 78.4392),
    "Charminar": (17.3616, 78.4747),
    "Falaknuma": (17.3389, 78.4794),
    "Karwan": (17.3775, 78.4589),
    "Golconda": (17.3833, 78.4011),
    "Erragadda": (17.4519, 78.4322),
    "SR Nagar": (17.4394, 78.4444),
    "Sanathnagar": (17.4489, 78.4419),
    "Moosapet": (17.4644, 78.4222),
    "Balanagar": (17.4658, 78.4514),
    "Bachupally": (17.5222, 78.3639),
    "Nizampet": (17.5133, 78.3822),
    "Pragathi Nagar": (17.5083, 78.3931),
    "Malkajgiri": (17.4519, 78.5308),
    "Habsiguda": (17.4142, 78.5433),
    "Nacharam": (17.4394, 78.5589),
    "Boduppal": (17.3833, 78.5644),
    "Shamshabad": (17.2403, 78.4294),
    "Rajendranagar": (17.3239, 78.4022),
    "Financial District": (17.4139, 78.3406),
}
MAX_LOCALITY_DISTANCE_KM = 3.5

# --- Stage 2a: Clinic website extraction ---
CLINIC_SUBPAGE_HINTS = ["about", "team", "doctor", "our-doctors", "meet", "profile"]
MAX_SUBPAGES_PER_CLINIC = 3

# --- Stage 2b: Hospital consultant directories ---
# Fill in each group's real consultant-directory URL before running
# `python run.py extract`. A blank base_url is skipped, not guessed.
HOSPITAL_DIRECTORIES = {
    "Apollo": {"base_url": "", "pagination": True},
    "KIMS": {"base_url": "", "pagination": True},
    "Yashoda": {"base_url": "", "pagination": True},
    "Continental": {"base_url": "", "pagination": True},
    "Care": {"base_url": "", "pagination": True},
    "AIG": {"base_url": "", "pagination": True},
    "Citizens": {"base_url": "", "pagination": True},
    "Star": {"base_url": "", "pagination": True},
    "Rainbow": {"base_url": "", "pagination": True},
    "Sunshine": {"base_url": "", "pagination": True},
}
MAX_HOSPITAL_PAGES = 20

# --- Stage 2c: Conference/committee pages (optional, lowest priority) ---
# List of {"url": ..., "kind": "committee" | "faculty"} dicts. Committee pages
# are more valuable than faculty pages per the PRD; fill in as found.
CONFERENCE_PAGES = []

# --- Stage 3: Dedupe ---
FUZZY_MATCH_THRESHOLD = 88
TITLE_PREFIXES = ["dr.", "dr", "prof.", "prof", "mr.", "mr", "mrs.", "mrs", "ms.", "ms"]

# --- Stage 4: Enrich ---
HOSPITAL_ONLY_LOOKUP_SUFFIX = "clinic Hyderabad"
# A CEO/Owner/Proprietor/Managing Director title only counts as owning the
# practice below this many doctors at the clinic — above it, the title
# almost certainly means a hired executive at a corporate chain, not
# personal commercial autonomy over marketing spend.
OWNERSHIP_TITLE_KEYWORDS = ["ceo", "chief executive officer", "owner", "proprietor", "managing director"]
SMALL_CLINIC_MAX_DOCTORS = 5

# Role categorization (Founder / Senior Decision-Maker / Staff): a doctor who
# isn't a founder (has_own_practice) but whose designation implies real
# organizational authority anyway — e.g. the CEO of a chain too big to clear
# the founder bar above. Full phrases only, not bare "head" — a naive
# substring match wrongly caught "Head and Neck Surgeon" (a specialty, not
# an org title) before this was tightened.
SENIOR_DESIGNATION_KEYWORDS = [
    "director", "chairman", "chairperson", "vice chairman", "dean",
    "head of department", "head of the department", "department head",
    "hod", "professor & head", "professor and head", "prof & head", "unit head",
    "ceo", "chief executive officer", "chief medical officer", "cmo",
    "owner", "proprietor",
]

# --- Stage 5: Score ---
PRIME_LOCALITIES = {
    "Jubilee Hills", "Banjara Hills", "Gachibowli", "Somajiguda", "Hitec City",
    "Financial District", "Secunderabad", "Punjagutta",
}

SCORE_WEIGHTS = {
    "has_own_practice": 3,
    "senior_decision_maker": 3,
    "reachable": 3,
    "has_website": 2,
    "experience_band": 2,
    "has_instagram": 2,
    "online_booking": 1,
    "high_review_volume": 1,
    "prime_locality": 1,
    "no_contact": -5,
    "junior": -2,
}
EXPERIENCE_BAND_MIN = 10
EXPERIENCE_BAND_MAX = 30
JUNIOR_EXPERIENCE_THRESHOLD = 8
HIGH_REVIEW_COUNT_THRESHOLD = 100


def locality_order() -> list[str]:
    """LOCALITIES ordered prime (hospital-dense/premium) areas first, for
    scraping runs that go locality-by-locality — see
    `python run.py discover --locality all`."""
    prime = [loc for loc in LOCALITIES if loc in PRIME_LOCALITIES]
    rest = [loc for loc in LOCALITIES if loc not in PRIME_LOCALITIES]
    return prime + rest


# --- Stage 6: Research (Gemini + Google Search grounding) ---
# Domains that must never be used as evidence for a research finding, even if
# Google's grounding search surfaces them as a citation. Matches the PRD's
# prohibited-source list, plus the social platforms themselves — their
# profile *links* are still fine to store as a contact channel (that's a
# separate, existing feature); this only blocks treating their page content
# as a sales "fact".
PROHIBITED_FINDING_DOMAINS = [
    "practo.com", "justdial.com", "lybrate.com", "eka.care",
    "linkedin.com", "instagram.com", "facebook.com",
]
RESEARCH_MAX_FINDINGS_PER_DOCTOR = 5
# Deterministic weight per finding category — mirrors SCORE_WEIGHTS. The LLM
# only ever picks a category from this fixed vocabulary; it never assigns its
# own numeric weight, since that would be unreliable. Tune these, not the
# LLM's judgement.
RESEARCH_FINDING_WEIGHTS = {
    "buying_trigger": 4,
    "prestige_credential": 2,
    "growth_signal": 2,
    "authority_signal": 1,
    "digital_presence_gap": 1,
    "red_flag": -3,
    "other": 0,
}

# --- Stage 7: Rank (tiering) ---
# Tiers bucket the Stage 5 score plus any Stage 6 finding weights combined
# ("adjusted score"). A doctor with no research yet is tiered on score alone.
TIER_HOT_MIN = 14
TIER_WARM_MIN = 8
