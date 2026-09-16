"""Stage 1 — Discover clinics via OpenStreetMap's Overpass API.

Free forever, no signup, no card. Swapped in for Google Places (New) because
that requires a billing account even for its free monthly credit.

Coverage tradeoff: OSM's clinic/hospital data for Hyderabad is far less
complete than Google's business index. Expect meaningfully fewer than the
800-1,500 clinics the original Google-Places design targeted, and phone/
website fields will be missing more often. Ratings and review counts don't
exist in OSM at all — those fields stay null and the scoring rules that key
off them (`high review volume`) simply won't fire for OSM-sourced clinics.
"""

import math
import time

import requests

import config
import db


class OverpassError(Exception):
    """Raised on a hard failure talking to the Overpass API. Should stop the run."""


_HEALTHCARE_AMENITIES = ["clinic", "hospital", "doctors", "dentist"]


def _bbox_str() -> str:
    s, w, n, e = config.HYDERABAD_BBOX
    return f"{s},{w},{n},{e}"


def _run_query(ql: str, retries: int = 1) -> list[dict]:
    """One Overpass call, with one retry on failure (the free public instance
    is prone to transient 504s under load)."""
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                config.OVERPASS_URL,
                data={"data": ql},
                headers={"User-Agent": config.USER_AGENT},
                timeout=config.OVERPASS_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            last_error = OverpassError(f"Overpass request failed: {exc}")
            time.sleep(config.REQUEST_DELAY_SECONDS * 3)
            continue

        if resp.status_code != 200:
            last_error = OverpassError(f"Overpass API {resp.status_code}: {resp.text[:300]}")
            time.sleep(config.REQUEST_DELAY_SECONDS * 3)
            continue

        try:
            return resp.json().get("elements", [])
        except ValueError as exc:
            last_error = OverpassError(f"Overpass returned non-JSON response: {exc}")
            time.sleep(config.REQUEST_DELAY_SECONDS * 3)
            continue

    raise last_error


def _discovery_queries() -> list[tuple[str, str]]:
    """One smaller query per tag, rather than one giant combined query —
    each is far less likely to time out on the public Overpass instance, and
    a failure on one tag doesn't cost the others. Returns [(label, ql), ...]."""
    bbox = _bbox_str()
    queries = []
    for amenity in _HEALTHCARE_AMENITIES:
        ql = (
            f"[out:json][timeout:{config.OVERPASS_TIMEOUT_SECONDS}];\n"
            f"(\n  node[\"amenity\"=\"{amenity}\"]({bbox});\n"
            f"  way[\"amenity\"=\"{amenity}\"]({bbox});\n);\nout center tags;"
        )
        queries.append((f"amenity={amenity}", ql))
    healthcare_ql = (
        f"[out:json][timeout:{config.OVERPASS_TIMEOUT_SECONDS}];\n"
        f"(\n  node[\"healthcare\"]({bbox});\n  way[\"healthcare\"]({bbox});\n);\nout center tags;"
    )
    queries.append(("healthcare=*", healthcare_ql))
    return queries


def _tags_get(tags: dict, *keys: str) -> str | None:
    for k in keys:
        v = tags.get(k)
        if v:
            return v
    return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _nearest_locality(lat: float | None, lon: float | None) -> str | None:
    if lat is None or lon is None:
        return None
    best_loc, best_dist = None, None
    for loc, (clat, clon) in config.LOCALITY_CENTROIDS.items():
        d = _haversine_km(lat, lon, clat, clon)
        if best_dist is None or d < best_dist:
            best_loc, best_dist = loc, d
    if best_dist is not None and best_dist <= config.MAX_LOCALITY_DISTANCE_KM:
        return best_loc
    return None


def _guess_locality(tags: dict, address: str | None, lat: float | None, lon: float | None) -> str | None:
    suburb = _tags_get(tags, "addr:suburb", "addr:neighbourhood")
    if suburb:
        for loc in config.LOCALITIES:
            if loc.lower() in suburb.lower():
                return loc

    haystack = (address or "").lower()
    for loc in config.LOCALITIES:
        if loc.lower() in haystack:
            return loc

    nearest = _nearest_locality(lat, lon)
    if nearest:
        return nearest

    return suburb or _tags_get(tags, "addr:district", "addr:city")


def _element_to_clinic_row(el: dict) -> dict | None:
    tags = el.get("tags") or {}
    name = tags.get("name")
    if not name:
        return None  # unnamed nodes aren't useful leads

    osm_type = el.get("type")
    osm_id = el.get("id")
    place_id = f"osm:{osm_type}:{osm_id}"

    addr_parts = [
        tags.get("addr:housenumber"), tags.get("addr:street"),
        tags.get("addr:suburb"), tags.get("addr:city"), tags.get("addr:postcode"),
    ]
    address = ", ".join(p for p in addr_parts if p) or None

    category = tags.get("healthcare:speciality") or tags.get("healthcare") or tags.get("amenity")

    lat = el.get("lat")
    lon = el.get("lon")
    if lat is None or lon is None:
        center = el.get("center") or {}
        lat, lon = center.get("lat"), center.get("lon")

    return {
        "place_id": place_id,
        "name": name,
        "category": category,
        "address": address,
        "locality": _guess_locality(tags, address, lat, lon),
        "phone": _tags_get(tags, "phone", "contact:phone"),
        "website": _tags_get(tags, "website", "contact:website"),
        "email": _tags_get(tags, "email", "contact:email"),
        "rating": None,
        "review_count": None,
        "business_status": None,
        "maps_url": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
        "found_via": f"osm:{category or 'healthcare'}",
    }


def run_discovery(conn, dry_run: bool = False) -> dict:
    """Pull every clinic/hospital/doctors/dentist node+way in the Hyderabad
    bbox from OSM, as several smaller per-tag queries rather than one giant
    one — each is far less likely to time out, and one tag failing doesn't
    cost the others. Idempotent: re-running upserts the same osm:type:id
    rows rather than duplicating. Only raises (stopping the run) if every
    single query failed, since that likely means the service itself is down."""
    queries = _discovery_queries()

    if dry_run:
        print(f"[dry-run] would run {len(queries)} Overpass queries covering the Hyderabad bbox:")
        for label, ql in queries:
            print(f"--- {label} ---\n{ql}")
        return {"elements_fetched": 0, "unique_clinics_upserted": 0}

    total_elements = 0
    upserted = 0
    skipped_unnamed = 0
    queries_failed = 0

    for label, ql in queries:
        try:
            elements = _run_query(ql)
        except OverpassError as exc:
            print(f"  ! query for {label!r} failed, skipping: {exc}")
            queries_failed += 1
            time.sleep(config.REQUEST_DELAY_SECONDS)
            continue

        total_elements += len(elements)
        for el in elements:
            row = _element_to_clinic_row(el)
            if row is None:
                skipped_unnamed += 1
                continue
            db.upsert_clinic(conn, row)
            upserted += 1

        time.sleep(config.REQUEST_DELAY_SECONDS)

    if queries_failed == len(queries):
        raise OverpassError("every Overpass discovery query failed — the service may be down")

    return {
        "elements_fetched": total_elements,
        "unique_clinics_upserted": upserted,
        "skipped_unnamed": skipped_unnamed,
        "queries_failed": queries_failed,
    }


def find_single_clinic(name_hint: str, locality: str | None = None) -> dict | None:
    """Best-effort single lookup used by enrich.py to find a hospital-only
    doctor's private clinic: regex-match the doctor's surname against OSM
    `name` tags within the Hyderabad bbox. Per-record best-effort — any
    failure is logged and swallowed, never raised, since a public Overpass
    server hiccup on one lookup shouldn't stop a run of hundreds."""
    surname = name_hint.strip().split()[-1] if name_hint.strip() else None
    if not surname:
        return None

    escaped = surname.replace('"', "")
    amenity_pattern = "|".join(_HEALTHCARE_AMENITIES)
    bbox = _bbox_str()
    ql = (
        f'[out:json][timeout:{config.OVERPASS_TIMEOUT_SECONDS}];\n'
        f"(\n"
        f'  node["name"~"{escaped}",i]["amenity"~"{amenity_pattern}"]({bbox});\n'
        f'  way["name"~"{escaped}",i]["amenity"~"{amenity_pattern}"]({bbox});\n'
        f'  node["name"~"{escaped}",i]["healthcare"]({bbox});\n'
        f'  way["name"~"{escaped}",i]["healthcare"]({bbox});\n'
        f");\nout center tags;"
    )

    try:
        elements = _run_query(ql)
    except OverpassError as exc:
        print(f"  ! private-clinic lookup failed for {name_hint!r}: {exc}")
        return None
    finally:
        time.sleep(config.REQUEST_DELAY_SECONDS)

    for el in elements:
        row = _element_to_clinic_row(el)
        if row:
            row["locality"] = row.get("locality") or locality
            return row
    return None
