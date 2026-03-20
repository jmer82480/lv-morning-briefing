import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

logger = logging.getLogger("briefing")

# Lehigh Valley place names for local relevance tagging
LV_PLACES = {
    "allentown", "bethlehem", "easton", "emmaus", "macungie", "whitehall",
    "catasauqua", "coplay", "northampton", "hellertown", "nazareth",
    "bangor", "pen argyl", "wind gap", "bath", "saucon", "lower saucon",
    "upper saucon", "salisbury", "fountain hill", "freemansburg",
    "palmer", "wilson", "tatamy", "stockertown", "lehigh valley",
    "lehigh county", "northampton county", "upper macungie",
    "lower macungie", "south whitehall", "north whitehall",
    "hanover township", "bethlehem township", "lower nazareth",
    "upper nazareth", "plainfield", "bushkill", "forks township",
    "williams township", "moore township", "east allen",
    "schnecksville", "hokendauqua", "kutztown", "coopersburg",
    "quakertown", "slatington", "walnutport", "palmerton",
    "jim thorpe", "lehighton",
}


def canonical_url(url: str) -> str:
    """Normalize a URL for deduplication: strip tracking params, www, trailing slash."""
    parsed = urlparse(url)

    # Strip www prefix
    host = parsed.hostname or ""
    if host.startswith("www."):
        host = host[4:]

    # Strip common tracking query params
    tracking_params = {"utm_source", "utm_medium", "utm_campaign", "utm_content",
                       "utm_term", "fbclid", "gclid", "ref", "source"}
    qs = parse_qs(parsed.query)
    filtered_qs = {k: v for k, v in qs.items() if k.lower() not in tracking_params}
    clean_query = urlencode(filtered_qs, doseq=True)

    # Strip trailing slash from path
    path = parsed.path.rstrip("/")

    return urlunparse(("", host, path, "", clean_query, ""))


def generate_item_id(source_url: str) -> str:
    """Generate a stable item ID from the canonical URL."""
    return hashlib.sha256(canonical_url(source_url).encode()).hexdigest()[:16]


def tag_local_places(text: str) -> list[str]:
    """Extract Lehigh Valley place names from text."""
    text_lower = text.lower()
    found = []
    for place in LV_PLACES:
        if place in text_lower:
            # Return the properly cased version
            found.append(place.title())
    return sorted(set(found))


def normalize_items(raw_items: list[dict], freshness_hours: int = 24) -> list[dict]:
    """Transform raw collected items into the normalized schema.

    Handles both news items and weather data. Weather items pass through
    with their original structure plus normalized fields.
    """
    now = datetime.now(timezone.utc)
    normalized = []
    seen_ids = set()

    for item in raw_items:
        # Weather items don't need the same normalization
        if item.get("section") == "weather":
            normalized.append(item)
            continue

        source_url = item.get("source_url", "")
        if not source_url:
            continue

        item_id = generate_item_id(source_url)

        # Deduplicate by item_id
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)

        # Freshness filter
        published_at = item.get("published_at")
        if published_at:
            try:
                pub_dt = datetime.fromisoformat(published_at)
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                age_hours = (now - pub_dt).total_seconds() / 3600
                if age_hours > freshness_hours:
                    continue
            except (ValueError, TypeError):
                pass

        headline = (item.get("headline") or "")[:200].strip()
        summary = (item.get("summary") or "")[:500].strip()
        searchable_text = f"{headline} {summary}"

        normalized.append({
            "item_id": item_id,
            "source_name": item.get("source_name", ""),
            "source_url": source_url,
            "section": item.get("section", "local_news"),
            "headline": headline,
            "summary": summary,
            "body_text": item.get("body_text"),
            "published_at": published_at,
            "retrieved_at": now.isoformat(),
            "local_places": tag_local_places(searchable_text),
            "tags": [],
        })

    logger.info(f"Normalized {len(normalized)} items ({len(seen_ids)} news + weather data)")
    return normalized


def save_normalized(items: list[dict], date_str: str, base_dir: str) -> str:
    """Save normalized items to normalized/{date}/all_items.json."""
    out_dir = os.path.join(base_dir, "normalized", date_str)
    os.makedirs(out_dir, exist_ok=True)

    filepath = os.path.join(out_dir, "all_items.json")
    with open(filepath, "w") as f:
        json.dump(items, f, indent=2, default=str)

    # Also save per-section files
    sections: dict[str, list] = {}
    for item in items:
        section = item.get("section", "other")
        sections.setdefault(section, []).append(item)

    for section, section_items in sections.items():
        section_path = os.path.join(out_dir, f"{section}.json")
        with open(section_path, "w") as f:
            json.dump(section_items, f, indent=2, default=str)

    logger.info(f"Saved normalized items to {out_dir}")
    return filepath
