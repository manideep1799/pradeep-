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
# (common in India). These are approximate neighbourhood centers, not
# precise boundaries — a fuzzy nearest-match is the intent, not a claim of
# exact locality boundaries.
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

# --- Stage 5: Score ---
PRIME_LOCALITIES = {"Jubilee Hills", "Banjara Hills", "Gachibowli", "Somajiguda", "Hitec City"}

SCORE_WEIGHTS = {
    "has_own_practice": 3,
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
