#!/usr/bin/env python3
"""Weather day fixture tests: validate editorial behavior with severe weather.

Tests a different editorial path than golden_day — severe weather alerts should
drive weather to full_segment, and weather-adjacent stories (cooling centers,
road closures) should be prioritized.

Usage:
    python -m pytest tests/test_weather_day.py -v
    python tests/test_weather_day.py  # standalone

All tests are deterministic (no API calls needed).
"""

import json
import os
import sys

import pytest
import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from pipeline.normalize import normalize_items
from pipeline.select_stories import pre_cluster, apply_overrides

WEATHER_DIR = os.path.join(PROJECT_ROOT, "tests", "golden_weather_day")
RAW_DIR = os.path.join(WEATHER_DIR, "raw")
EXPECTED_DIR = os.path.join(WEATHER_DIR, "expected")


def load_weather_raw() -> list[dict]:
    """Load all raw items from the weather day fixture."""
    all_items = []
    for filename in sorted(os.listdir(RAW_DIR)):
        if not filename.endswith(".json"):
            continue
        with open(os.path.join(RAW_DIR, filename)) as f:
            items = json.load(f)
        all_items.extend(items)
    return all_items


def load_editorial_checks() -> dict:
    with open(os.path.join(EXPECTED_DIR, "editorial_checks.yaml")) as f:
        return yaml.safe_load(f)


def _headline_matches(headline: str, pattern: str) -> bool:
    return pattern.lower() in headline.lower()


def _find_cluster_for_headline(clusters: list[dict], pattern: str) -> dict | None:
    for cluster in clusters:
        for member in cluster["members"]:
            if _headline_matches(member.get("headline", ""), pattern):
                return cluster
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_weather_day_normalization():
    """Verify normalization handles both news and weather items."""
    raw = load_weather_raw()
    normalized = normalize_items(raw, freshness_hours=8760)

    news_items = [i for i in normalized if i.get("section") != "weather"]
    weather_items = [i for i in normalized if i.get("section") == "weather"]

    assert len(news_items) > 0, "Should have news items"
    assert len(weather_items) >= 3, "Should have at least 3 weather items (forecast + alerts)"

    # Verify alerts have severity
    alerts = [w for w in weather_items if w.get("type") == "alert"]
    assert len(alerts) >= 2, f"Should have at least 2 weather alerts, got {len(alerts)}"
    for alert in alerts:
        assert alert.get("severity"), f"Alert missing severity: {alert.get('event')}"

    print(f"  Normalized: {len(news_items)} news, {len(weather_items)} weather "
          f"({len(alerts)} alerts)")


def test_weather_day_clustering():
    """Verify clustering groups duplicate stories on a quiet news day."""
    raw = load_weather_raw()
    normalized = normalize_items(raw, freshness_hours=8760)
    news_items = [i for i in normalized if i.get("section") != "weather"]
    checks = load_editorial_checks()

    clusters = pre_cluster(news_items, similarity_threshold=75, time_window_hours=6)

    print(f"  {len(news_items)} items → {len(clusters)} clusters")

    # Fewer items than golden day, but should still cluster
    assert len(clusters) < len(news_items), "Some items should cluster together"

    # Verify expected clusters
    for expected in checks["clustering"]["expected_clusters"]:
        story_snippets = expected["stories"]
        matching_clusters = set()
        for snippet in story_snippets:
            for cluster in clusters:
                for member in cluster["members"]:
                    if _headline_matches(member.get("headline", ""), snippet[:30]):
                        matching_clusters.add(cluster["cluster_id"])

        assert len(matching_clusters) <= 2, (
            f"Expected stories to cluster together but found in {len(matching_clusters)} "
            f"clusters: {expected['reason']}\n  Stories: {story_snippets}"
        )
        print(f"  ✓ Cluster verified: {expected['reason']}")


def test_weather_day_superintendent_clusters():
    """Verify the superintendent story (2 outlets) clusters together."""
    raw = load_weather_raw()
    normalized = normalize_items(raw, freshness_hours=8760)
    news_items = [i for i in normalized if i.get("section") != "weather"]
    clusters = pre_cluster(news_items)

    super_cluster = _find_cluster_for_headline(clusters, "superintendent")
    assert super_cluster is not None, "Should find superintendent cluster"
    assert len(super_cluster["members"]) >= 2, (
        f"Superintendent story covered by 2 outlets should cluster — "
        f"got {len(super_cluster['members'])} members"
    )
    print(f"  ✓ Superintendent stories clustered ({len(super_cluster['members'])} members)")


def test_weather_day_route22_clusters():
    """Verify the Route 22 story (2 outlets) clusters together."""
    raw = load_weather_raw()
    normalized = normalize_items(raw, freshness_hours=8760)
    news_items = [i for i in normalized if i.get("section") != "weather"]
    clusters = pre_cluster(news_items)

    route22_cluster = _find_cluster_for_headline(clusters, "Route 22")
    assert route22_cluster is not None, "Should find Route 22 cluster"
    assert len(route22_cluster["members"]) >= 2, (
        f"Route 22 story covered by 2 outlets should cluster — "
        f"got {len(route22_cluster['members'])} members"
    )
    print(f"  ✓ Route 22 stories clustered ({len(route22_cluster['members'])} members)")


def test_weather_day_unique_stories_separate():
    """Verify unique stories are not clustered with unrelated ones."""
    raw = load_weather_raw()
    normalized = normalize_items(raw, freshness_hours=8760)
    news_items = [i for i in normalized if i.get("section") != "weather"]
    clusters = pre_cluster(news_items)

    cooling_cluster = _find_cluster_for_headline(clusters, "cooling centers")
    ironpigs_cluster = _find_cluster_for_headline(clusters, "IronPigs")

    assert cooling_cluster is not None, "Should find cooling centers cluster"
    assert ironpigs_cluster is not None, "Should find IronPigs cluster"
    assert cooling_cluster["cluster_id"] != ironpigs_cluster["cluster_id"], (
        "Cooling centers and IronPigs should NOT be in the same cluster"
    )
    print("  ✓ Unrelated stories correctly separated")


def test_weather_day_item_counts():
    """Verify expected item counts for a quieter news day."""
    raw = load_weather_raw()
    normalized = normalize_items(raw, freshness_hours=8760)
    news_items = [i for i in normalized if i.get("section") != "weather"]
    weather_items = [i for i in normalized if i.get("section") == "weather"]
    clusters = pre_cluster(news_items)

    assert 6 <= len(news_items) <= 12, f"Expected 6-12 news items, got {len(news_items)}"
    assert 4 <= len(clusters) <= 10, f"Expected 4-10 clusters, got {len(clusters)}"
    assert len(weather_items) >= 3, f"Expected ≥3 weather items, got {len(weather_items)}"

    print(f"  ✓ Counts: {len(news_items)} news, {len(weather_items)} weather, "
          f"{len(clusters)} clusters")


def test_weather_day_place_tagging():
    """Verify weather-adjacent stories get proper place tagging."""
    raw = load_weather_raw()
    normalized = normalize_items(raw, freshness_hours=8760)
    news_items = [i for i in normalized if i.get("section") != "weather"]

    # Cooling centers story mentions Allentown, Whitehall, Emmaus
    cooling_items = [
        i for i in news_items
        if "cooling" in i.get("headline", "").lower()
    ]
    assert len(cooling_items) > 0, "Should find cooling centers story"
    places = cooling_items[0].get("local_places", [])
    assert "Allentown" in places, f"Cooling centers should tag Allentown, got {places}"
    assert "Whitehall" in places, f"Cooling centers should tag Whitehall, got {places}"
    assert "Emmaus" in places, f"Cooling centers should tag Emmaus, got {places}"

    print(f"  ✓ Cooling centers tagged: {', '.join(places)}")


def test_weather_day_severe_alerts_present():
    """Verify the fixture has the right alert structure to drive full_segment.

    This is the deterministic prerequisite: if the normalized weather data
    doesn't have severe alerts, no model will ever produce full_segment.
    """
    raw = load_weather_raw()
    normalized = normalize_items(raw, freshness_hours=8760)
    weather_items = [i for i in normalized if i.get("section") == "weather"]
    checks = load_editorial_checks()

    alerts = [w for w in weather_items if w.get("type") == "alert"]
    severities = [a.get("severity", "").lower() for a in alerts]

    # Must have at least one severe-level alert
    assert any(s in ("severe", "extreme") for s in severities), (
        f"Fixture needs at least one severe/extreme alert to test full_segment path. "
        f"Got severities: {severities}"
    )

    # Must have multiple alerts (the fixture has 3)
    assert len(alerts) >= 2, (
        f"Expected ≥2 weather alerts for full_segment test, got {len(alerts)}"
    )

    # Verify expected editorial outcome
    assert checks["weather"]["expected_level"] == "full_segment", (
        "Weather day fixture must expect full_segment"
    )

    alert_events = [a.get("event", "") for a in alerts]
    print(f"  ✓ {len(alerts)} severe alerts present: {', '.join(alert_events)}")
    print(f"  ✓ Expected editorial outcome: full_segment")


def test_weather_day_editorial_selection():
    """Full editorial selection test for the weather day — requires ANTHROPIC_API_KEY.

    Validates that severe weather drives a full_segment weather decision
    and that weather-adjacent stories (cooling centers) are selected.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY not set")

    from pipeline.select_stories import select_stories

    raw = load_weather_raw()
    normalized = normalize_items(raw, freshness_hours=8760)
    checks = load_editorial_checks()

    settings = {"ai": {"model": "claude-sonnet-4-20250514", "max_tokens": 4096},
                "clustering": {"title_similarity_threshold": 75, "time_window_hours": 6}}

    with open(os.path.join(PROJECT_ROOT, "config", "prompt_templates.yaml")) as f:
        prompt_templates = yaml.safe_load(f)

    overrides = {"force_include": [], "force_exclude": [], "pin_rank": []}

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        decisions = select_stories(
            normalized, "2026-06-15", tmpdir, settings, prompt_templates, overrides
        )

    # Weather decision must be full_segment
    weather = decisions.get("weather_decision", {})
    expected_level = checks["weather"]["expected_level"]
    assert weather.get("level") == expected_level, (
        f"Expected weather level '{expected_level}', got '{weather.get('level')}'. "
        f"With 3 severe alerts, weather must be full_segment."
    )
    print(f"  ✓ Weather: {weather.get('level')} (correct for severe alerts)")

    # Cooling centers must be selected (weather-adjacent, public safety)
    selected = decisions.get("selected_stories", [])
    selected_headlines = [s.get("headline", "") for s in selected]
    for rule in checks["must_select"]:
        pattern = rule["headline_contains"]
        found = any(_headline_matches(h, pattern) for h in selected_headlines)
        assert found, f"Must-select story missing: '{pattern}' — {rule['reason']}"
        print(f"  ✓ Must-select present: {pattern}")

    # Story count within expected range
    min_count = checks["expected_story_count"]["min"]
    max_count = checks["expected_story_count"]["max"]
    assert min_count <= len(selected) <= max_count, (
        f"Expected {min_count}-{max_count} stories, got {len(selected)}"
    )
    print(f"  ✓ Story count: {len(selected)}")
    print(f"\n  WEATHER DAY EDITORIAL SELECTION PASSED")


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        ("Weather day normalization", test_weather_day_normalization),
        ("Weather day clustering", test_weather_day_clustering),
        ("Superintendent clusters", test_weather_day_superintendent_clusters),
        ("Route 22 clusters", test_weather_day_route22_clusters),
        ("Unique stories separate", test_weather_day_unique_stories_separate),
        ("Item counts", test_weather_day_item_counts),
        ("Place tagging", test_weather_day_place_tagging),
        ("Severe alerts present", test_weather_day_severe_alerts_present),
        ("Editorial selection (API)", test_weather_day_editorial_selection),
    ]

    passed = 0
    failed = 0

    for name, test_fn in tests:
        print(f"\n{'=' * 50}")
        print(f"TEST: {name}")
        print("=" * 50)
        try:
            test_fn()
            passed += 1
            print(f"  PASSED")
        except AssertionError as e:
            failed += 1
            print(f"  FAILED: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR: {e}")

    print(f"\n{'=' * 50}")
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 50)
    sys.exit(1 if failed else 0)
