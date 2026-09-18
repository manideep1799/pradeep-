"""Shared HTTP fetch + LLM extraction used by sources/clinic_sites.py,
sources/hospitals.py, and sources/conferences.py.

Do not write per-site HTML parsers here. Fetch page -> plain text -> LLM ->
structured JSON. That's the whole pattern, for every source.
"""

import json
import time
import urllib.robotparser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import config

_robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}
_last_llm_call_at = 0.0


def _throttle_llm() -> None:
    """Gemini's free tier caps requests per minute per model (as low as 5/min
    on some models) — much tighter than the page-fetch politeness delay.
    Enforce a minimum gap between LLM calls regardless of caller."""
    global _last_llm_call_at
    elapsed = time.monotonic() - _last_llm_call_at
    wait = config.LLM_REQUEST_DELAY_SECONDS - elapsed
    if wait > 0:
        time.sleep(wait)
    _last_llm_call_at = time.monotonic()

_SYSTEM_PROMPT = """You extract structured facts about doctors from a webpage's text \
content for a lead-generation tool. Follow these rules strictly:

- Return ONLY valid JSON. No markdown fences, no commentary, no explanation.
- If a field is not explicitly stated on the page, return null for it. Never \
infer, guess, or estimate a value.
- For experience_years: if the page says a number like "15 years" or "15+ years", \
parse it to the integer 15. If experience is not stated at all, return null. \
Never round up, average, or estimate.
- Only extract doctors who are named individuals with some professional detail \
(qualification, specialty, designation, or experience). Do not invent entries.
- Links in the page text appear inline as "label (url)". Where a field asks for \
a social/profile URL (instagram_url, facebook_url, linkedin_url, youtube_url), \
copy the exact url from one of these — never invent or guess one, and never \
return a bare label with no url. If a doctor has their own personal profile \
link, prefer it over the clinic's shared one for that doctor's own fields.
"""


class ExtractionError(Exception):
    pass


# --------------------------------------------------------------------------
# HTTP fetch
# --------------------------------------------------------------------------

def _robots_allowed(url: str) -> bool:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    rp = _robots_cache.get(origin)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        # Fetch robots.txt with our own requests session + real User-Agent,
        # not rp.read()'s built-in urllib fetch. rp.read() sends urllib's
        # generic user-agent, which some WAFs 403 outright — and robotparser
        # treats a 401/403 as "disallow everything", producing false
        # positives against sites that are actually fully crawlable.
        try:
            resp = requests.get(
                urljoin(origin, "/robots.txt"),
                headers={"User-Agent": config.USER_AGENT},
                timeout=config.REQUEST_TIMEOUT_SECONDS,
            )
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
            else:
                # No readable robots.txt -> treat as allow-all.
                rp.parse([])
        except requests.RequestException:
            rp.parse([])
        _robots_cache[origin] = rp
    try:
        return rp.can_fetch(config.USER_AGENT, url)
    except Exception:
        return True


def fetch_url(url: str) -> tuple[str | None, str | None]:
    """Fetch a URL and return (html, error). One retry on failure. Respects robots.txt."""
    if not url or not url.strip():
        return None, "empty_url"
    url = url.strip()
    if "://" not in url:
        # Some stored website values (from OSM tags) are bare domains like
        # "www.kimshospitals.in" with no scheme — requests.get() raises
        # MissingSchema on those and the fetch silently fails. Assume https;
        # this also has to happen before the robots.txt check below, which
        # needs a real origin to look up.
        url = f"https://{url}"

    if not _robots_allowed(url):
        return None, "blocked_by_robots_txt"

    headers = {"User-Agent": config.USER_AGENT}
    last_error = None
    for attempt in range(2):
        try:
            resp = requests.get(url, headers=headers, timeout=config.REQUEST_TIMEOUT_SECONDS)
            if resp.status_code == 200:
                return resp.text, None
            last_error = f"http_{resp.status_code}"
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
    return None, last_error


def html_to_text(html: str, base_url: str | None = None) -> str:
    """HTML -> plain text, with each link's destination kept inline as
    "label (url)". Plain get_text() silently drops every href, which is the
    confirmed reason social-media/profile links were never reaching the LLM
    (measured 0% Instagram capture despite most real clinic sites having one).
    base_url resolves relative hrefs to absolute ones so the LLM sees a link
    it can actually use, not "/team/dr-x"."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        absolute = urljoin(base_url, href) if base_url else href
        label = a.get_text(strip=True)
        a.replace_with(f" {label} ({absolute}) " if label else f" ({absolute}) ")

    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def find_subpage_links(html: str, base_url: str, hints: list[str]) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    found = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        lowered = href.lower()
        if any(hint in lowered for hint in hints):
            full_url = urljoin(base_url, href)
            if full_url not in seen and urlparse(full_url).netloc == urlparse(base_url).netloc:
                seen.add(full_url)
                found.append(full_url)
    return found


def find_pagination_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    found = []
    seen = set()
    for a in soup.find_all("a", href=True):
        text = (a.get_text() or "").strip().lower()
        if text in {"next", "»", ">", "next page"} or "page=" in a["href"].lower():
            full_url = urljoin(base_url, a["href"])
            if full_url not in seen:
                seen.add(full_url)
                found.append(full_url)
    return found


# --------------------------------------------------------------------------
# LLM extraction
# --------------------------------------------------------------------------

_SCHEMAS = {
    "clinic": """{
  "doctors": [
    {"name": null, "qualification": null, "specialty": null,
     "experience_years": null, "designation": null, "is_founder": null,
     "instagram_url": null, "facebook_url": null, "linkedin_url": null}
  ],
  "clinic_email": null,
  "instagram_url": null,
  "facebook_url": null,
  "linkedin_url": null,
  "youtube_url": null,
  "has_online_booking": null
}""",
    "hospital": """{
  "doctors": [
    {"name": null, "qualification": null, "specialty": null,
     "experience_years": null, "designation": null, "department": null}
  ]
}""",
    "conference": """{
  "doctors": [
    {"name": null, "affiliation": null, "designation": null}
  ]
}""",
}


def get_client():
    if not config.GEMINI_API_KEY:
        raise ExtractionError("GEMINI_API_KEY is not set. Add it to .env before running extract.")
    from google import genai
    return genai.Client(api_key=config.GEMINI_API_KEY)


def _call_llm(client, text: str, mode: str, strict: bool = False) -> str:
    from google.genai import types

    schema = _SCHEMAS[mode]
    instruction = (
        f"Extract doctor information from this webpage text. "
        f"Respond with JSON matching exactly this shape:\n{schema}\n\n"
        f"Page text:\n{text[:12000]}"
    )
    if strict:
        instruction = (
            "Your previous response was not valid JSON. Return ONLY valid JSON, "
            "with no markdown fences and no commentary, matching this shape:\n"
            f"{schema}\n\nPage text:\n{text[:12000]}"
        )

    _throttle_llm()
    response = client.models.generate_content(
        model=config.LLM_MODEL,
        contents=instruction,
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            temperature=0,
            response_mime_type="application/json",
        ),
    )
    return response.text or ""


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
    return raw.strip()


def extract_structured(text: str, mode: str) -> dict | None:
    """Fetch -> already done by caller. This does text -> LLM -> parsed JSON.
    Retries once with a stricter instruction on malformed JSON, then gives up."""
    if mode not in _SCHEMAS:
        raise ValueError(f"unknown extraction mode: {mode}")
    if not text.strip():
        return None

    client = get_client()

    for strict in (False, True):
        try:
            raw = _call_llm(client, text, mode, strict=strict)
            parsed = json.loads(_strip_fences(raw))
            return parsed
        except json.JSONDecodeError:
            continue
        except Exception as exc:
            print(f"  ! LLM extraction call failed: {exc}")
            return None

    print("  ! LLM returned malformed JSON twice, skipping this page")
    return None


def parse_experience_years(value) -> int | None:
    """Defensive parse in case the LLM returns a string like '15+' despite instructions."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        digits = "".join(ch for ch in value if ch.isdigit())
        return int(digits) if digits else None
    return None


# --------------------------------------------------------------------------
# LLM extraction with Google Search grounding (used by research.py, Stage 6)
# --------------------------------------------------------------------------

_RESEARCH_SYSTEM_PROMPT = """You are a sales research assistant for a social-media \
marketing agency that pitches high-ticket services to senior doctors in Hyderabad. \
Given a doctor's name and clinic/hospital, use search to find PUBLIC, VERIFIABLE \
facts useful for a salesperson deciding who to prioritize and how to open a \
conversation. Follow these rules strictly:

- Never use practo.com, justdial.com, lybrate.com, eka.care, or linkedin.com as a \
source, even if search surfaces them. Skip any fact you can only support from \
one of those domains.
- Do not use any source that requires login to view.
- Only report something if you found actual evidence for it on a real page. \
Never invent, guess, or pad out a finding.
- A doctor's specialty is never relevant to how they should be prioritized — \
do not comment on which specialty is "better" or "worse".
- Return ONLY valid JSON, no markdown fences, no commentary, matching exactly \
the shape given in the instruction.
"""


def call_grounded_research(client, prompt: str) -> tuple[dict | None, list[dict]]:
    """One Gemini call with the Google Search tool enabled, for research.py.
    Returns (parsed_json_or_None, citations), where citations is a list of
    {"url", "domain", "title"} dicts pulled from the response's grounding
    metadata — this is how a finding's source_url gets verified against
    config.PROHIBITED_FINDING_DOMAINS rather than trusted on the model's word.

    Deliberately does not pass response_mime_type="application/json": the
    Gemini Developer API (free tier) has been observed rejecting some
    combinations of a built-in tool (google_search) with forced JSON mode.
    The prompt asks for JSON directly instead, parsed the same lenient way as
    extract_structured() above."""
    from google.genai import types

    _throttle_llm()
    response = client.models.generate_content(
        model=config.LLM_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=_RESEARCH_SYSTEM_PROMPT,
            temperature=0,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )

    citations = []
    try:
        candidate = response.candidates[0]
        grounding = getattr(candidate, "grounding_metadata", None)
        chunks = grounding.grounding_chunks if grounding else None
        for chunk in chunks or []:
            web = getattr(chunk, "web", None)
            if web is not None and web.uri:
                citations.append({"url": web.uri, "domain": web.domain, "title": web.title})
    except (AttributeError, IndexError):
        pass

    try:
        return json.loads(_strip_fences(response.text or "")), citations
    except json.JSONDecodeError:
        return None, citations
