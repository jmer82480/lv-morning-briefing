#!/usr/bin/env python3
"""Golden day test: validate clustering and editorial selection against expectations.

This test loads the golden day fixture data, runs normalization and pre-clustering
(deterministic, no API calls), and validates the results against human-written
editorial assertions.

Usage:
    python -m pytest tests/test_golden_day.py -v
    python tests/test_golden_day.py  # standalone

The pre-clustering tests run without API keys. The full editorial selection test
(test_editorial_selection) requires ANTHROPIC_API_KEY and is skipped without it.
"""

import json
import os
import sys

import pytest
import yaml

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from pipeline.normalize import normalize_items
from pipeline.select_stories import pre_cluster, apply_overrides, enforce_overrides

sys.path.insert(0, PROJECT_ROOT)
from run_briefing import validate_editorial_decisions, validate_script_file

GOLDEN_DIR = os.path.join(PROJECT_ROOT, "tests", "golden_day")
RAW_DIR = os.path.join(GOLDEN_DIR, "raw")
EXPECTED_DIR = os.path.join(GOLDEN_DIR, "expected")


def load_golden_raw() -> list[dict]:
    """Load all raw items from the golden day fixture."""
    all_items = []
    for filename in sorted(os.listdir(RAW_DIR)):
        if not filename.endswith(".json"):
            continue
        with open(os.path.join(RAW_DIR, filename)) as f:
            items = json.load(f)
        all_items.extend(items)
    return all_items


def load_editorial_checks() -> dict:
    """Load editorial expectations."""
    with open(os.path.join(EXPECTED_DIR, "editorial_checks.yaml")) as f:
        return yaml.safe_load(f)


def _headline_matches(headline: str, pattern: str) -> bool:
    """Check if a headline contains the given pattern (case-insensitive)."""
    return pattern.lower() in headline.lower()


def _find_cluster_for_headline(clusters: list[dict], pattern: str) -> dict | None:
    """Find the cluster containing a headline matching the pattern."""
    for cluster in clusters:
        for member in cluster["members"]:
            if _headline_matches(member.get("headline", ""), pattern):
                return cluster
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_normalization():
    """Verify normalization produces items with correct schema."""
    raw = load_golden_raw()
    # Use a large freshness window since fixture dates are synthetic
    normalized = normalize_items(raw, freshness_hours=8760)

    news_items = [i for i in normalized if i.get("section") != "weather"]
    weather_items = [i for i in normalized if i.get("section") == "weather"]

    assert len(news_items) > 0, "Should have news items"
    assert len(weather_items) > 0, "Should have weather items"

    # Check schema
    for item in news_items:
        assert "item_id" in item, f"Missing item_id: {item.get('headline')}"
        assert "source_name" in item
        assert "source_url" in item
        assert "headline" in item
        assert "published_at" in item

    print(f"  Normalized: {len(news_items)} news, {len(weather_items)} weather")


def test_deduplication():
    """Verify URL-based deduplication removes exact URL duplicates."""
    raw = load_golden_raw()
    normalized = normalize_items(raw, freshness_hours=8760)
    news_items = [i for i in normalized if i.get("section") != "weather"]

    urls = [i["source_url"] for i in news_items]
    assert len(urls) == len(set(urls)), "Duplicate URLs should be removed"
    print(f"  {len(news_items)} unique items (no URL duplicates)")


def test_local_place_tagging():
    """Verify local place names are correctly tagged."""
    raw = load_golden_raw()
    normalized = normalize_items(raw, freshness_hours=8760)
    news_items = [i for i in normalized if i.get("section") != "weather"]

    # Allentown tax story should have Allentown tagged
    allentown_items = [
        i for i in news_items
        if "allentown" in i.get("headline", "").lower()
        and "Allentown" in i.get("local_places", [])
    ]
    assert len(allentown_items) > 0, "Allentown stories should have Allentown tagged"

    # Bethlehem Steel story should have Bethlehem tagged
    bethlehem_items = [
        i for i in news_items
        if "bethlehem" in i.get("headline", "").lower()
        and "Bethlehem" in i.get("local_places", [])
    ]
    assert len(bethlehem_items) > 0, "Bethlehem stories should have Bethlehem tagged"

    print("  Place names correctly tagged")


def test_pre_clustering():
    """Verify pre-clustering groups related stories correctly."""
    raw = load_golden_raw()
    normalized = normalize_items(raw, freshness_hours=8760)
    news_items = [i for i in normalized if i.get("section") != "weather"]
    checks = load_editorial_checks()

    clusters = pre_cluster(news_items, similarity_threshold=75, time_window_hours=6)

    print(f"  {len(news_items)} items → {len(clusters)} clusters")

    # Verify expected clusters exist
    for expected in checks["clustering"]["expected_clusters"]:
        # Find a cluster that contains at least 2 of the expected stories
        story_snippets = expected["stories"]

        # Find all clusters containing any of these stories
        matching_clusters = set()
        for snippet in story_snippets:
            # Find which cluster contains a headline matching this snippet
            for cluster in clusters:
                for member in cluster["members"]:
                    if _headline_matches(member.get("headline", ""), snippet[:30]):
                        matching_clusters.add(cluster["cluster_id"])

        # All matching stories should be in the same cluster (or at most 2)
        assert len(matching_clusters) <= 2, (
            f"Expected stories to cluster together but found in {len(matching_clusters)} "
            f"clusters: {expected['reason']}\n"
            f"  Stories: {story_snippets}\n"
            f"  Cluster IDs: {matching_clusters}"
        )
        print(f"  ✓ Cluster verified: {expected['reason']}")


def test_clustering_separates_unrelated():
    """Verify unrelated stories are NOT clustered together."""
    raw = load_golden_raw()
    normalized = normalize_items(raw, freshness_hours=8760)
    news_items = [i for i in normalized if i.get("section") != "weather"]

    clusters = pre_cluster(news_items, similarity_threshold=75, time_window_hours=6)

    # The Allentown tax cluster and Bethlehem Steel cluster should be separate
    tax_cluster = _find_cluster_for_headline(clusters, "tax increase")
    steel_cluster = _find_cluster_for_headline(clusters, "Bethlehem Steel")

    assert tax_cluster is not None, "Should find a tax increase cluster"
    assert steel_cluster is not None, "Should find a Bethlehem Steel cluster"
    assert tax_cluster["cluster_id"] != steel_cluster["cluster_id"], (
        "Tax increase and Bethlehem Steel should NOT be in the same cluster"
    )
    print("  ✓ Unrelated stories correctly separated")


def test_overrides_force_exclude():
    """Verify force-exclude overrides remove matching clusters."""
    raw = load_golden_raw()
    normalized = normalize_items(raw, freshness_hours=8760)
    news_items = [i for i in normalized if i.get("section") != "weather"]
    clusters = pre_cluster(news_items)

    overrides = {
        "force_include": [],
        "force_exclude": [{"headline_pattern": "Obituaries"}],
        "pin_rank": [],
    }

    filtered, log = apply_overrides(clusters, overrides)

    # Obituaries should be excluded
    for cluster in filtered:
        for member in cluster["members"]:
            assert "obituaries" not in member.get("headline", "").lower(), (
                "Obituaries should have been excluded by override"
            )

    assert len(log["force_excluded"]) > 0, "Should have logged the exclusion"
    print(f"  ✓ Force-excluded {len(log['force_excluded'])} items")


def test_enforce_force_include():
    """Verify enforce_overrides adds back a force-included story that Claude omitted."""
    raw = load_golden_raw()
    normalized = normalize_items(raw, freshness_hours=8760)
    news_items = [i for i in normalized if i.get("section") != "weather"]
    clusters = pre_cluster(news_items)

    # Mark the Bethlehem Steel cluster as force-included
    steel_cluster = _find_cluster_for_headline(clusters, "Bethlehem Steel")
    assert steel_cluster is not None, "Should find a Bethlehem Steel cluster"
    steel_cluster["force_included"] = True

    # Simulate Claude's decisions that OMIT the force-included cluster
    fake_decisions = {
        "selected_stories": [
            {
                "rank": 1,
                "cluster_id": 999,  # some other cluster
                "headline": "Unrelated story",
            },
        ],
        "weather_decision": {"level": "one_liner"},
        "cut_stories": [],
        "near_misses": [],
    }

    result = enforce_overrides(fake_decisions, clusters)
    selected_ids = {s["cluster_id"] for s in result["selected_stories"]}

    assert steel_cluster["cluster_id"] in selected_ids, (
        "Force-included Bethlehem Steel cluster should be in selected_stories "
        "even when Claude omitted it"
    )

    # Verify the injected story has correct metadata
    injected = [
        s for s in result["selected_stories"]
        if s["cluster_id"] == steel_cluster["cluster_id"]
    ]
    assert len(injected) == 1
    assert "Bethlehem Steel" in injected[0]["headline"]
    assert injected[0]["editorial_note"] == "Force-included by manual override"
    print(f"  ✓ Force-included story added back after Claude omitted it")


def test_enforce_pin_rank():
    """Verify enforce_overrides corrects the rank when Claude ignores a pin."""
    raw = load_golden_raw()
    normalized = normalize_items(raw, freshness_hours=8760)
    news_items = [i for i in normalized if i.get("section") != "weather"]
    clusters = pre_cluster(news_items)

    # Mark the tax increase cluster as pinned to rank 1
    tax_cluster = _find_cluster_for_headline(clusters, "tax increase")
    assert tax_cluster is not None, "Should find a tax increase cluster"
    tax_cluster["pinned_rank"] = 1

    # Simulate Claude putting the tax story at rank 5 instead of 1
    fake_decisions = {
        "selected_stories": [
            {"rank": 1, "cluster_id": 900, "headline": "Some other lead"},
            {"rank": 2, "cluster_id": 901, "headline": "Another story"},
            {"rank": 3, "cluster_id": 902, "headline": "Third story"},
            {"rank": 4, "cluster_id": 903, "headline": "Fourth story"},
            {
                "rank": 5,
                "cluster_id": tax_cluster["cluster_id"],
                "headline": tax_cluster["representative"]["headline"],
            },
        ],
        "weather_decision": {"level": "one_liner"},
        "cut_stories": [],
        "near_misses": [],
    }

    result = enforce_overrides(fake_decisions, clusters)

    # The tax story should now be rank 1
    tax_story = [
        s for s in result["selected_stories"]
        if s["cluster_id"] == tax_cluster["cluster_id"]
    ]
    assert len(tax_story) == 1
    assert tax_story[0]["rank"] == 1, (
        f"Pinned story should be rank 1, got rank {tax_story[0]['rank']}"
    )

    # All ranks should be sequential with no gaps
    ranks = [s["rank"] for s in result["selected_stories"]]
    assert ranks == list(range(1, len(ranks) + 1)), (
        f"Ranks should be sequential after re-sort, got {ranks}"
    )
    print(f"  ✓ Pinned story moved to rank 1 (Claude had it at rank 5)")


def test_enforce_pin_rank_mid_position():
    """Verify pin_rank=3 is honored (not just rank 1)."""
    raw = load_golden_raw()
    normalized = normalize_items(raw, freshness_hours=8760)
    news_items = [i for i in normalized if i.get("section") != "weather"]
    clusters = pre_cluster(news_items)

    steel_cluster = _find_cluster_for_headline(clusters, "Bethlehem Steel")
    assert steel_cluster is not None
    steel_cluster["pinned_rank"] = 3

    fake_decisions = {
        "selected_stories": [
            {"rank": 1, "cluster_id": 900, "headline": "Lead story"},
            {"rank": 2, "cluster_id": 901, "headline": "Second story"},
            {"rank": 3, "cluster_id": 902, "headline": "Third story"},
            {"rank": 4, "cluster_id": 903, "headline": "Fourth story"},
            {
                "rank": 5,
                "cluster_id": steel_cluster["cluster_id"],
                "headline": steel_cluster["representative"]["headline"],
            },
        ],
        "weather_decision": {"level": "one_liner"},
        "cut_stories": [],
        "near_misses": [],
    }

    result = enforce_overrides(fake_decisions, clusters)

    steel_story = [
        s for s in result["selected_stories"]
        if s["cluster_id"] == steel_cluster["cluster_id"]
    ]
    assert len(steel_story) == 1
    assert steel_story[0]["rank"] == 3, (
        f"Pinned story should be rank 3, got rank {steel_story[0]['rank']}"
    )

    ranks = [s["rank"] for s in result["selected_stories"]]
    assert ranks == list(range(1, len(ranks) + 1)), (
        f"Ranks should be sequential, got {ranks}"
    )
    print(f"  ✓ Pinned story at rank 3 (Claude had it at rank 5)")


def test_enforce_multiple_pins():
    """Verify multiple pinned stories are all honored simultaneously."""
    raw = load_golden_raw()
    normalized = normalize_items(raw, freshness_hours=8760)
    news_items = [i for i in normalized if i.get("section") != "weather"]
    clusters = pre_cluster(news_items)

    tax_cluster = _find_cluster_for_headline(clusters, "tax increase")
    steel_cluster = _find_cluster_for_headline(clusters, "Bethlehem Steel")
    assert tax_cluster is not None
    assert steel_cluster is not None

    tax_cluster["pinned_rank"] = 1
    steel_cluster["pinned_rank"] = 3

    fake_decisions = {
        "selected_stories": [
            {"rank": 1, "cluster_id": 900, "headline": "Lead story"},
            {"rank": 2, "cluster_id": 901, "headline": "Second story"},
            {
                "rank": 3,
                "cluster_id": steel_cluster["cluster_id"],
                "headline": steel_cluster["representative"]["headline"],
            },
            {
                "rank": 4,
                "cluster_id": tax_cluster["cluster_id"],
                "headline": tax_cluster["representative"]["headline"],
            },
            {"rank": 5, "cluster_id": 903, "headline": "Fifth story"},
        ],
        "weather_decision": {"level": "one_liner"},
        "cut_stories": [],
        "near_misses": [],
    }

    result = enforce_overrides(fake_decisions, clusters)

    tax_story = [s for s in result["selected_stories"]
                 if s["cluster_id"] == tax_cluster["cluster_id"]]
    steel_story = [s for s in result["selected_stories"]
                   if s["cluster_id"] == steel_cluster["cluster_id"]]

    assert tax_story[0]["rank"] == 1, (
        f"Tax story should be rank 1, got {tax_story[0]['rank']}"
    )
    assert steel_story[0]["rank"] == 3, (
        f"Steel story should be rank 3, got {steel_story[0]['rank']}"
    )

    ranks = [s["rank"] for s in result["selected_stories"]]
    assert ranks == list(range(1, len(ranks) + 1)), (
        f"Ranks should be sequential, got {ranks}"
    )
    print(f"  ✓ Two pinned stories at ranks 1 and 3 simultaneously")


def test_enforce_force_include_with_pin():
    """Verify force_include + pin_rank together: injected story gets pinned rank."""
    raw = load_golden_raw()
    normalized = normalize_items(raw, freshness_hours=8760)
    news_items = [i for i in normalized if i.get("section") != "weather"]
    clusters = pre_cluster(news_items)

    steel_cluster = _find_cluster_for_headline(clusters, "Bethlehem Steel")
    assert steel_cluster is not None
    steel_cluster["force_included"] = True
    steel_cluster["pinned_rank"] = 2

    # Claude omits the steel story entirely
    fake_decisions = {
        "selected_stories": [
            {"rank": 1, "cluster_id": 900, "headline": "Lead story"},
            {"rank": 2, "cluster_id": 901, "headline": "Second story"},
            {"rank": 3, "cluster_id": 902, "headline": "Third story"},
        ],
        "weather_decision": {"level": "one_liner"},
        "cut_stories": [],
        "near_misses": [],
    }

    result = enforce_overrides(fake_decisions, clusters)
    selected = result["selected_stories"]

    # Steel story must be present
    steel_story = [s for s in selected if s["cluster_id"] == steel_cluster["cluster_id"]]
    assert len(steel_story) == 1, "Force-included story should be present"
    assert steel_story[0]["rank"] == 2, (
        f"Force-included + pinned story should be rank 2, got {steel_story[0]['rank']}"
    )

    ranks = [s["rank"] for s in selected]
    assert ranks == list(range(1, len(ranks) + 1)), (
        f"Ranks should be sequential, got {ranks}"
    )
    assert len(selected) == 4, f"Should have 4 total stories (3 + 1 injected), got {len(selected)}"
    print(f"  ✓ Force-included story injected at pinned rank 2")


def test_validation_rejects_empty_selection():
    """Verify validation catches zero selected stories."""
    bad_decisions = {
        "selected_stories": [],
        "weather_decision": {"level": "one_liner"},
        "cut_stories": [],
        "near_misses": [],
    }
    try:
        validate_editorial_decisions(bad_decisions)
        assert False, "Should have raised ValueError for empty selection"
    except ValueError as e:
        assert "zero selected stories" in str(e).lower()
        print(f"  ✓ Empty selection rejected: {e}")


def test_validation_rejects_missing_headline():
    """Verify validation catches stories without headlines."""
    bad_decisions = {
        "selected_stories": [
            {"rank": 1, "cluster_id": 1, "headline": ""},  # empty headline
        ],
        "weather_decision": {"level": "one_liner"},
        "cut_stories": [],
        "near_misses": [],
    }
    try:
        validate_editorial_decisions(bad_decisions)
        assert False, "Should have raised ValueError for missing headline"
    except ValueError as e:
        assert "no headline" in str(e).lower()
        print(f"  ✓ Missing headline rejected: {e}")


def test_validation_rejects_empty_script():
    """Verify script validation catches empty files."""
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("short")
        tmp_path = f.name

    try:
        validate_script_file(tmp_path, "test script", min_chars=100)
        assert False, "Should have raised ValueError for short script"
    except ValueError as e:
        assert "characters" in str(e).lower()
        print(f"  ✓ Empty script rejected: {e}")
    finally:
        os.unlink(tmp_path)


def test_clustering_determinism():
    """Verify the same fixture produces identical clusters regardless of file load order.

    This catches non-determinism from dict/set iteration order or unsorted listdir.
    """
    raw = load_golden_raw()
    normalized = normalize_items(raw, freshness_hours=8760)
    news_items = [i for i in normalized if i.get("section") != "weather"]

    clusters_a = pre_cluster(news_items, similarity_threshold=75, time_window_hours=6)

    # Reverse the input order — should produce the same clusters
    reversed_items = list(reversed(news_items))
    clusters_b = pre_cluster(reversed_items, similarity_threshold=75, time_window_hours=6)

    assert len(clusters_a) == len(clusters_b), (
        f"Cluster count changed with input order: {len(clusters_a)} vs {len(clusters_b)}"
    )

    # Compare cluster contents by headline sets
    def cluster_signature(clusters):
        sigs = set()
        for c in clusters:
            headlines = frozenset(m.get("headline", "") for m in c["members"])
            sigs.add(headlines)
        return sigs

    sigs_a = cluster_signature(clusters_a)
    sigs_b = cluster_signature(clusters_b)

    assert sigs_a == sigs_b, (
        f"Cluster contents changed with input order.\n"
        f"Only in A: {sigs_a - sigs_b}\n"
        f"Only in B: {sigs_b - sigs_a}"
    )
    print(f"  ✓ {len(clusters_a)} clusters stable across input orderings")


def test_logger_date_switch():
    """Verify logger correctly switches file handler when date changes."""
    from pipeline.logger import setup_logger, _current_log_file
    import tempfile

    # We can't easily test file paths without mocking, but we can verify
    # that calling setup_logger twice with different dates doesn't duplicate handlers
    logger_a = setup_logger("2026-01-01")
    handler_count_a = len(logger_a.handlers)

    logger_b = setup_logger("2026-01-02")
    handler_count_b = len(logger_b.handlers)

    assert handler_count_a == handler_count_b, (
        f"Handler count changed from {handler_count_a} to {handler_count_b} on date switch. "
        f"Logger is accumulating duplicate handlers."
    )

    # Same logger instance
    assert logger_a is logger_b, "setup_logger should return the same logger instance"

    # propagate should be False
    assert not logger_b.propagate, "Logger propagate should be False"

    print(f"  ✓ Logger handles date switch cleanly ({handler_count_b} handlers)")


def test_golden_day_item_counts():
    """Verify item counts are in expected ranges."""
    raw = load_golden_raw()
    normalized = normalize_items(raw, freshness_hours=8760)
    news_items = [i for i in normalized if i.get("section") != "weather"]
    clusters = pre_cluster(news_items)

    # We created 22 items across 4 sources; after URL dedup we should have 22 unique
    # After clustering, should have roughly 10-18 clusters
    assert 10 <= len(news_items) <= 25, f"Expected 10-25 news items, got {len(news_items)}"
    assert 8 <= len(clusters) <= 20, f"Expected 8-20 clusters, got {len(clusters)}"
    print(f"  ✓ Counts in range: {len(news_items)} items, {len(clusters)} clusters")


# ---------------------------------------------------------------------------
# Full editorial selection test (requires API key)
# ---------------------------------------------------------------------------

def test_editorial_selection():
    """Full editorial selection test — requires ANTHROPIC_API_KEY.

    Runs the complete selection pipeline and validates against editorial checks.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY not set")

    from pipeline.select_stories import select_stories

    raw = load_golden_raw()
    normalized = normalize_items(raw, freshness_hours=8760)
    checks = load_editorial_checks()

    settings = {"ai": {"model": "claude-sonnet-4-20250514", "max_tokens": 4096},
                "clustering": {"title_similarity_threshold": 75, "time_window_hours": 6}}

    with open(os.path.join(PROJECT_ROOT, "config", "prompt_templates.yaml")) as f:
        prompt_templates = yaml.safe_load(f)

    overrides = {"force_include": [], "force_exclude": [], "pin_rank": []}

    # Use a temp directory for output
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        decisions = select_stories(
            normalized, "2026-03-20", tmpdir, settings, prompt_templates, overrides
        )

    selected = decisions.get("selected_stories", [])
    selected_headlines = [s.get("headline", "") for s in selected]

    # Check story count
    min_count = checks["expected_story_count"]["min"]
    max_count = checks["expected_story_count"]["max"]
    assert min_count <= len(selected) <= max_count, (
        f"Expected {min_count}-{max_count} stories, got {len(selected)}"
    )
    print(f"  ✓ Story count: {len(selected)}")

    # Check must_select
    for rule in checks["must_select"]:
        pattern = rule["headline_contains"]
        found = any(_headline_matches(h, pattern) for h in selected_headlines)
        assert found, f"Must-select story missing: '{pattern}' — {rule['reason']}"
        print(f"  ✓ Must-select present: {pattern}")

    # Check must_cut
    for rule in checks["must_cut"]:
        pattern = rule["headline_contains"]
        found = any(_headline_matches(h, pattern) for h in selected_headlines)
        assert not found, f"Must-cut story was selected: '{pattern}' — {rule['reason']}"
        print(f"  ✓ Must-cut absent: {pattern}")

    # Check weather decision
    weather = decisions.get("weather_decision", {})
    expected_level = checks["weather"]["expected_level"]
    assert weather.get("level") == expected_level, (
        f"Expected weather level '{expected_level}', got '{weather.get('level')}'"
    )
    print(f"  ✓ Weather: {weather.get('level')}")

    print(f"\n  EDITORIAL SELECTION PASSED — {len(selected)} stories selected")


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        ("Normalization", test_normalization),
        ("Deduplication", test_deduplication),
        ("Local place tagging", test_local_place_tagging),
        ("Pre-clustering", test_pre_clustering),
        ("Clustering separates unrelated", test_clustering_separates_unrelated),
        ("Overrides force-exclude", test_overrides_force_exclude),
        ("Enforce force-include post-Claude", test_enforce_force_include),
        ("Enforce pin-rank post-Claude", test_enforce_pin_rank),
        ("Enforce pin-rank=3", test_enforce_pin_rank_mid_position),
        ("Enforce multiple pins", test_enforce_multiple_pins),
        ("Enforce force-include + pin", test_enforce_force_include_with_pin),
        ("Validation: empty selection", test_validation_rejects_empty_selection),
        ("Validation: missing headline", test_validation_rejects_missing_headline),
        ("Validation: empty script", test_validation_rejects_empty_script),
        ("Clustering determinism", test_clustering_determinism),
        ("Logger date switch", test_logger_date_switch),
        ("Golden day item counts", test_golden_day_item_counts),
        ("Editorial selection (API)", test_editorial_selection),
    ]

    passed = 0
    failed = 0
    skipped = 0

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
