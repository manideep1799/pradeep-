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


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
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
     "experience_years": null, "designation": null, "is_founder": null}
  ],
  "clinic_email": null,
  "instagram_url": null,
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


def _get_client():
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

    client = _get_client()

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
