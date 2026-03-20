"""Editorial story selection: pre-cluster (heuristic) + editorial select (Claude).

This is the most important file in the pipeline. It decides what goes in the briefing.

Phase A: Deterministic pre-clustering
  - URL dedup, title similarity, place-name matching, time grouping
  - Reduces 60+ raw items to 20-40 clusters

Phase A.5: Manual overrides
  - Force-include, force-exclude, pin-rank from config/overrides.yaml

Phase B: Claude editorial selection
  - Picks top 5-8 stories, weather decision, cut list, near misses

Outputs:
  - selected/{date}/pre_clusters.json (clustering audit trail)
  - selected/{date}/editorial_decisions.json (Claude's editorial output)
"""

import json
import logging
import os
import re
from datetime import datetime, timezone

from rapidfuzz import fuzz

from pipeline.model_io import call_claude_json

logger = logging.getLogger("briefing")


# ---------------------------------------------------------------------------
# Phase A: Deterministic pre-clustering
# ---------------------------------------------------------------------------

def _normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for comparison."""
    title = title.lower()
    title = re.sub(r"[^\w\s]", "", title)
    return re.sub(r"\s+", " ", title).strip()


def pre_cluster(
    news_items: list[dict],
    similarity_threshold: int = 75,
    time_window_hours: int = 6,
) -> list[dict]:
    """Group normalized news items into topic clusters using heuristics.

    Returns a list of cluster dicts, each with:
      cluster_id, representative, members, similarity_score, local_places, source_count
    """
    clusters: list[dict] = []
    assigned = set()

    for i, item in enumerate(news_items):
        if i in assigned:
            continue

        cluster_members = [item]
        assigned.add(i)

        item_title = _normalize_title(item.get("headline", ""))
        item_url = item.get("source_url", "")
        item_pub = item.get("published_at", "")

        best_similarity = 0.0

        for j, other in enumerate(news_items):
            if j in assigned:
                continue

            other_title = _normalize_title(other.get("headline", ""))
            other_url = other.get("source_url", "")
            other_pub = other.get("published_at", "")

            # Check title similarity
            score = fuzz.token_sort_ratio(item_title, other_title)

            # Check time proximity — boost similarity for temporally close items
            time_close = _within_time_window(item_pub, other_pub, time_window_hours)

            if score >= similarity_threshold:
                cluster_members.append(other)
                assigned.add(j)
                best_similarity = max(best_similarity, score)
            elif score >= similarity_threshold - 15 and time_close:
                # Lower threshold if items are published close together
                cluster_members.append(other)
                assigned.add(j)
                best_similarity = max(best_similarity, score)

        # Pick representative: prefer the member with the longest summary
        representative = max(cluster_members, key=lambda x: len(x.get("summary", "")))

        # Collect local places across all members
        all_places = set()
        for m in cluster_members:
            all_places.update(m.get("local_places", []))

        # Collect unique sources
        source_names = set(m.get("source_name", "") for m in cluster_members)

        cluster_id = len(clusters) + 1
        clusters.append({
            "cluster_id": cluster_id,
            "representative": representative,
            "members": cluster_members,
            "similarity_score": round(best_similarity / 100, 2) if best_similarity else 0.0,
            "local_places": sorted(all_places),
            "source_count": len(source_names),
        })

    logger.info(
        f"Pre-clustering: {len(news_items)} items → {len(clusters)} clusters"
    )
    return clusters


def _within_time_window(pub1: str, pub2: str, hours: int) -> bool:
    """Check if two publication times are within the given window."""
    try:
        dt1 = datetime.fromisoformat(pub1)
        dt2 = datetime.fromisoformat(pub2)
        if dt1.tzinfo is None:
            dt1 = dt1.replace(tzinfo=timezone.utc)
        if dt2.tzinfo is None:
            dt2 = dt2.replace(tzinfo=timezone.utc)
        return abs((dt1 - dt2).total_seconds()) < hours * 3600
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Phase A.5: Manual overrides
# ---------------------------------------------------------------------------

def apply_overrides(
    clusters: list[dict],
    overrides: dict,
) -> tuple[list[dict], dict]:
    """Apply manual editorial overrides to clusters.

    Returns (filtered_clusters, overrides_applied_log).
    """
    force_include = overrides.get("force_include") or []
    force_exclude = overrides.get("force_exclude") or []
    pin_rank = overrides.get("pin_rank") or []

    log = {
        "force_included": [],
        "force_excluded": [],
        "rank_pinned": [],
    }

    # Force-exclude: remove clusters matching URL or headline patterns
    filtered = []
    for cluster in clusters:
        rep = cluster["representative"]
        url = rep.get("source_url", "")
        headline = rep.get("headline", "")
        excluded = False

        for rule in force_exclude:
            url_pat = rule.get("url_pattern", "")
            headline_pat = rule.get("headline_pattern", "")

            if url_pat and url_pat.lower() in url.lower():
                log["force_excluded"].append(url)
                excluded = True
                break
            if headline_pat and headline_pat.lower() in headline.lower():
                log["force_excluded"].append(headline)
                excluded = True
                break

        if not excluded:
            filtered.append(cluster)

    # Force-include: mark clusters that match (they'll be passed to Claude with a flag)
    for cluster in filtered:
        rep = cluster["representative"]
        url = rep.get("source_url", "")

        for rule in force_include:
            if rule.get("url", "") == url:
                cluster["force_included"] = True
                cluster["pinned_rank"] = rule.get("rank")
                log["force_included"].append(url)

    # Pin-rank: mark clusters matching headline patterns
    for cluster in filtered:
        rep = cluster["representative"]
        headline = rep.get("headline", "")

        for rule in pin_rank:
            pattern = rule.get("headline_contains", "")
            if pattern and pattern.lower() in headline.lower():
                cluster["pinned_rank"] = rule.get("rank")
                log["rank_pinned"].append(headline)

    logger.info(
        f"Overrides: {len(log['force_excluded'])} excluded, "
        f"{len(log['force_included'])} force-included, "
        f"{len(log['rank_pinned'])} rank-pinned"
    )
    return filtered, log


# ---------------------------------------------------------------------------
# Phase B: Claude editorial selection
# ---------------------------------------------------------------------------

def _build_clusters_for_prompt(clusters: list[dict]) -> str:
    """Format clusters as JSON for the Claude prompt."""
    prompt_clusters = []
    for c in clusters:
        rep = c["representative"]
        members_info = []
        for m in c["members"]:
            if m.get("source_url") != rep.get("source_url"):
                members_info.append({
                    "source_name": m.get("source_name", ""),
                    "source_url": m.get("source_url", ""),
                    "headline": m.get("headline", ""),
                })

        entry = {
            "cluster_id": c["cluster_id"],
            "headline": rep.get("headline", ""),
            "summary": rep.get("summary", ""),
            "source_name": rep.get("source_name", ""),
            "source_url": rep.get("source_url", ""),
            "section": rep.get("section", ""),
            "published_at": rep.get("published_at", ""),
            "local_places": c.get("local_places", []),
            "source_count": c["source_count"],
            "other_sources": members_info,
        }

        if c.get("force_included"):
            entry["MUST_INCLUDE"] = True
        if c.get("pinned_rank"):
            entry["PINNED_RANK"] = c["pinned_rank"]

        prompt_clusters.append(entry)

    return json.dumps(prompt_clusters, indent=2)


def _build_weather_for_prompt(weather_items: list[dict]) -> str:
    """Format weather data for the Claude prompt."""
    if not weather_items:
        return json.dumps({"available": False})

    forecasts = [w for w in weather_items if w.get("type") == "forecast"]
    alerts = [w for w in weather_items if w.get("type") == "alert"]

    weather_data = {
        "available": True,
        "forecasts": [
            {
                "period": f.get("period_name", ""),
                "temperature": f.get("temperature"),
                "unit": f.get("temperature_unit", "F"),
                "wind": f"{f.get('wind_speed', '')} {f.get('wind_direction', '')}".strip(),
                "short_forecast": f.get("short_forecast", ""),
                "detailed_forecast": f.get("detailed_forecast", ""),
            }
            for f in forecasts
        ],
        "alerts": [
            {
                "event": a.get("event", ""),
                "severity": a.get("severity", ""),
                "headline": a.get("headline", ""),
                "description": a.get("description", "")[:300],
                "areas": a.get("areas", ""),
            }
            for a in alerts
        ],
    }
    return json.dumps(weather_data, indent=2)


def editorial_select(
    clusters: list[dict],
    weather_items: list[dict],
    date_str: str,
    prompt_templates: dict,
    settings: dict,
) -> dict:
    """Send pre-clustered candidates to Claude for editorial selection.

    Returns the parsed editorial decisions dict.
    """
    templates = prompt_templates["story_selection"]
    system_prompt = templates["system"]
    user_template = templates["user"]

    clusters_json = _build_clusters_for_prompt(clusters)
    weather_json = _build_weather_for_prompt(weather_items)

    user_prompt = user_template.format(
        date=date_str,
        cluster_count=len(clusters),
        clusters_json=clusters_json,
        weather_json=weather_json,
    )

    model = settings.get("ai", {}).get("model", "claude-sonnet-4-20250514")
    max_tokens = settings.get("ai", {}).get("max_tokens", 4096)

    logger.info(f"Sending {len(clusters)} clusters to Claude for editorial selection...")

    required_keys = {"selected_stories", "weather_decision", "cut_stories", "near_misses"}
    decisions = call_claude_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=model,
        max_tokens=max_tokens,
        required_keys=required_keys,
        label="editorial selection",
    )

    # Add metadata
    decisions["date"] = date_str
    decisions["model"] = model
    decisions["cluster_count"] = len(clusters)
    decisions["raw_item_count"] = sum(len(c["members"]) for c in clusters)

    logger.info(
        f"Editorial selection complete: {len(decisions.get('selected_stories', []))} selected, "
        f"{len(decisions.get('cut_stories', []))} cut, "
        f"{len(decisions.get('near_misses', []))} near misses"
    )
    return decisions


# ---------------------------------------------------------------------------
# Phase C: Post-selection override enforcement
# ---------------------------------------------------------------------------

def enforce_overrides(decisions: dict, clusters: list[dict]) -> dict:
    """Enforce manual overrides after Claude returns its selection.

    Guarantees that:
      - force_included clusters are present in selected_stories
      - pinned_rank clusters have the correct rank
    Claude's output is treated as a suggestion; overrides are deterministic.
    """
    selected = decisions.get("selected_stories", [])
    selected_cluster_ids = {s.get("cluster_id") for s in selected}
    patched = False

    for cluster in clusters:
        cid = cluster["cluster_id"]
        rep = cluster["representative"]

        # Enforce force-include: add missing stories
        if cluster.get("force_included") and cid not in selected_cluster_ids:
            logger.warning(
                f"Override enforcement: force-including cluster {cid} "
                f"({rep.get('headline', '')!r}) — Claude omitted it"
            )
            pinned = cluster.get("pinned_rank")
            entry = {
                "rank": pinned or len(selected) + 1,
                "cluster_id": cid,
                "headline": rep.get("headline", ""),
                "summary": rep.get("summary", ""),
                "source_name": rep.get("source_name", ""),
                "source_url": rep.get("source_url", ""),
                "other_sources": [
                    {"name": m.get("source_name", ""), "url": m.get("source_url", "")}
                    for m in cluster["members"]
                    if m.get("source_url") != rep.get("source_url")
                ],
                "section": rep.get("section", ""),
                "editorial_note": "Force-included by manual override",
                "why_it_matters": "Operator flagged this story as must-include",
            }
            selected.append(entry)
            patched = True

        # Enforce pinned rank
        if cluster.get("pinned_rank") and cid in selected_cluster_ids:
            target_rank = cluster["pinned_rank"]
            for story in selected:
                if story.get("cluster_id") == cid and story.get("rank") != target_rank:
                    logger.warning(
                        f"Override enforcement: pinning cluster {cid} "
                        f"({rep.get('headline', '')!r}) to rank {target_rank} "
                        f"(Claude assigned rank {story.get('rank')})"
                    )
                    story["rank"] = target_rank
                    patched = True

    if patched:
        # Re-sort by rank and fix any rank collisions
        selected.sort(key=lambda s: s.get("rank", 999))
        for i, story in enumerate(selected):
            story["rank"] = i + 1
        decisions["selected_stories"] = selected

    return decisions


# ---------------------------------------------------------------------------
# Orchestration: run the full selection pipeline
# ---------------------------------------------------------------------------

def save_pre_clusters(
    clusters: list[dict],
    overrides_log: dict,
    raw_item_count: int,
    date_str: str,
    base_dir: str,
) -> str:
    """Save pre-cluster results to selected/{date}/pre_clusters.json."""
    out_dir = os.path.join(base_dir, "selected", date_str)
    os.makedirs(out_dir, exist_ok=True)

    filepath = os.path.join(out_dir, "pre_clusters.json")

    data = {
        "date": date_str,
        "raw_item_count": raw_item_count,
        "cluster_count": len(clusters),
        "clusters": clusters,
        "unclustered": [c for c in clusters if len(c["members"]) == 1],
        "overrides_applied": overrides_log,
    }

    with open(filepath, "w") as f:
        json.dump(data, f, indent=2, default=str)

    logger.info(f"Saved pre-clusters to {filepath}")
    return filepath


def save_editorial_decisions(decisions: dict, date_str: str, base_dir: str) -> str:
    """Save editorial decisions to selected/{date}/editorial_decisions.json."""
    out_dir = os.path.join(base_dir, "selected", date_str)
    os.makedirs(out_dir, exist_ok=True)

    filepath = os.path.join(out_dir, "editorial_decisions.json")

    with open(filepath, "w") as f:
        json.dump(decisions, f, indent=2, default=str)

    logger.info(f"Saved editorial decisions to {filepath}")
    return filepath


def select_stories(
    normalized_items: list[dict],
    date_str: str,
    base_dir: str,
    settings: dict,
    prompt_templates: dict,
    overrides: dict,
) -> dict:
    """Run the full story selection pipeline.

    1. Separate news items from weather data
    2. Pre-cluster news items (deterministic)
    3. Apply manual overrides
    4. Save pre-clusters for audit trail
    5. Send to Claude for editorial selection
    6. Save editorial decisions

    Returns the editorial decisions dict.
    """
    # Separate weather from news
    weather_items = [i for i in normalized_items if i.get("section") == "weather"]
    news_items = [i for i in normalized_items if i.get("section") != "weather"]

    logger.info(f"Story selection: {len(news_items)} news items, {len(weather_items)} weather items")

    if not news_items:
        logger.error("No news items to select from. Cannot produce a briefing.")
        raise ValueError("No news items available for story selection")

    # Phase A: Pre-cluster
    clustering_config = settings.get("clustering", {})
    clusters = pre_cluster(
        news_items,
        similarity_threshold=clustering_config.get("title_similarity_threshold", 75),
        time_window_hours=clustering_config.get("time_window_hours", 6),
    )

    # Phase A.5: Manual overrides
    clusters, overrides_log = apply_overrides(clusters, overrides)

    # Save pre-clusters (audit trail)
    save_pre_clusters(clusters, overrides_log, len(news_items), date_str, base_dir)

    # Phase B: Claude editorial selection
    decisions = editorial_select(
        clusters, weather_items, date_str, prompt_templates, settings,
    )

    # Phase C: Enforce overrides post-Claude
    decisions = enforce_overrides(decisions, clusters)

    # Save editorial decisions
    save_editorial_decisions(decisions, date_str, base_dir)

    return decisions
