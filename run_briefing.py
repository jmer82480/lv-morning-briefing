#!/usr/bin/env python3
"""Lehigh Valley Morning Briefing — pipeline orchestrator.

Usage:
    python run_briefing.py                     # Full pipeline for today
    python run_briefing.py --date 2026-03-19   # Run for a specific date
    python run_briefing.py --collect-only       # Only fetch sources
    python run_briefing.py --skip-collect       # Re-use existing raw data
    python run_briefing.py --skip-review        # Skip human review gate
    python run_briefing.py --skip-audio         # Script only, no TTS
    python run_briefing.py --audio-only         # Re-use existing speech script, only run TTS
"""

import argparse
import json
import os
import sys
from datetime import date

import yaml
from dotenv import load_dotenv

from pipeline.logger import setup_logger
from pipeline.collectors.rss_collector import RSSCollector
from pipeline.collectors.weather_collector import WeatherCollector
from pipeline.normalize import normalize_items, save_normalized
from pipeline.select_stories import select_stories
from pipeline.generate_script import generate_editor_script, generate_speech_script
from pipeline.review import review_script
from pipeline.generate_audio import generate_audio
from pipeline.assemble_episode import assemble_episode


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

COLLECTOR_MAP = {
    "rss": RSSCollector,
    "weather_api": WeatherCollector,
}


def load_config(filename: str) -> dict:
    """Load a YAML config file from config/."""
    path = os.path.join(BASE_DIR, "config", filename)
    with open(path) as f:
        return yaml.safe_load(f) or {}


def run_collect(sources: list[dict], date_str: str) -> list[dict]:
    """Stage 1: Collect from all enabled sources."""
    logger = setup_logger(date_str)
    logger.info(f"=== COLLECT ({date_str}) ===")

    all_items = []
    enabled = [s for s in sources if s.get("enabled", True)]
    logger.info(f"Collecting from {len(enabled)} enabled sources")

    for source in enabled:
        source_type = source.get("type", "")
        collector_cls = COLLECTOR_MAP.get(source_type)

        if not collector_cls:
            logger.warning(f"  Unknown source type '{source_type}' for {source['name']}, skipping")
            continue

        try:
            collector = collector_cls(source)
            items = collector.collect()
            collector.save_raw(items, date_str, BASE_DIR)
            all_items.extend(items)
        except Exception as e:
            logger.warning(f"  FAILED: {source['name']}: {e}")
            continue

    logger.info(f"Collected {len(all_items)} total items from {len(enabled)} sources")

    if not all_items:
        logger.error("All sources failed. No items collected.")
        sys.exit(1)

    return all_items


def run_normalize(raw_items: list[dict], date_str: str, settings: dict) -> list[dict]:
    """Stage 2: Normalize collected items."""
    logger = setup_logger(date_str)
    logger.info(f"=== NORMALIZE ({date_str}) ===")

    freshness_hours = settings.get("briefing", {}).get("freshness_hours", 24)
    normalized = normalize_items(raw_items, freshness_hours=freshness_hours)
    save_normalized(normalized, date_str, BASE_DIR)

    return normalized


def run_select(
    normalized_items: list[dict],
    date_str: str,
    settings: dict,
    prompt_templates: dict,
    overrides: dict,
) -> dict:
    """Stage 3: Select and rank stories."""
    logger = setup_logger(date_str)
    logger.info(f"=== SELECT STORIES ({date_str}) ===")

    return select_stories(
        normalized_items, date_str, BASE_DIR, settings, prompt_templates, overrides
    )


def run_generate_editor_script(
    editorial_decisions: dict,
    date_str: str,
    settings: dict,
    prompt_templates: dict,
) -> str:
    """Stage 4a: Generate editor script (pre-review)."""
    logger = setup_logger(date_str)
    logger.info(f"=== GENERATE EDITOR SCRIPT ({date_str}) ===")

    return generate_editor_script(
        editorial_decisions, date_str, BASE_DIR, settings, prompt_templates
    )


def run_generate_speech_script(
    editor_path: str,
    date_str: str,
    settings: dict,
) -> str:
    """Stage 4b: Generate speech script from reviewed editor script (post-review)."""
    logger = setup_logger(date_str)
    logger.info(f"=== GENERATE SPEECH SCRIPT ({date_str}) ===")

    return generate_speech_script(editor_path, date_str, BASE_DIR, settings)


def run_review(editor_path: str) -> bool:
    """Stage 5: Human review gate."""
    return review_script(editor_path)


def run_audio(speech_path: str, date_str: str, settings: dict) -> str:
    """Stage 6: Generate audio from speech script."""
    logger = setup_logger(date_str)
    logger.info(f"=== GENERATE AUDIO ({date_str}) ===")

    return generate_audio(speech_path, date_str, BASE_DIR, settings)


def run_assemble(
    date_str: str,
    editor_path: str,
    speech_path: str,
    audio_path: str | None,
    editorial_decisions: dict,
) -> str:
    """Stage 7: Assemble final episode."""
    logger = setup_logger(date_str)
    logger.info(f"=== ASSEMBLE EPISODE ({date_str}) ===")

    return assemble_episode(
        date_str, editor_path, speech_path, audio_path, editorial_decisions, BASE_DIR
    )


def load_existing_raw(date_str: str) -> list[dict]:
    """Load previously collected raw data from raw/{date}/."""
    raw_dir = os.path.join(BASE_DIR, "raw", date_str)
    if not os.path.isdir(raw_dir):
        print(f"Error: No raw data found at {raw_dir}")
        sys.exit(1)

    all_items = []
    for filename in os.listdir(raw_dir):
        if not filename.endswith(".json"):
            continue
        filepath = os.path.join(raw_dir, filename)
        with open(filepath) as f:
            items = json.load(f)
        all_items.extend(items)

    logger = setup_logger(date_str)
    logger.info(f"Loaded {len(all_items)} items from existing raw data at {raw_dir}")
    return all_items


def main():
    parser = argparse.ArgumentParser(description="Lehigh Valley Morning Briefing")
    parser.add_argument("--date", default=date.today().isoformat(),
                        help="Date for the briefing (YYYY-MM-DD, default: today)")
    parser.add_argument("--collect-only", action="store_true",
                        help="Only fetch sources, don't process")
    parser.add_argument("--skip-collect", action="store_true",
                        help="Skip collection, re-use existing raw data")
    parser.add_argument("--skip-review", action="store_true",
                        help="Skip human review gate")
    parser.add_argument("--skip-audio", action="store_true",
                        help="Generate script only, no TTS")
    parser.add_argument("--audio-only", action="store_true",
                        help="Re-use existing speech script, only run TTS")
    args = parser.parse_args()

    date_str = args.date
    load_dotenv(os.path.join(BASE_DIR, ".env"))

    logger = setup_logger(date_str)
    logger.info(f"Starting briefing pipeline for {date_str}")

    # Load config
    sources_config = load_config("sources.yaml")
    settings = load_config("settings.yaml")
    prompt_templates = load_config("prompt_templates.yaml")
    overrides = load_config("overrides.yaml")
    sources = sources_config.get("sources", [])

    # --audio-only: just re-run TTS
    if args.audio_only:
        speech_path = os.path.join(BASE_DIR, "scripts", f"{date_str}_speech.md")
        if not os.path.exists(speech_path):
            print(f"Error: No speech script found at {speech_path}")
            sys.exit(1)
        audio_path = run_audio(speech_path, date_str, settings)
        print(f"\nAudio generated: {audio_path}")
        return

    # Stage 1: Collect
    if args.skip_collect:
        raw_items = load_existing_raw(date_str)
    else:
        raw_items = run_collect(sources, date_str)

    if args.collect_only:
        print(f"\nCollection complete. Raw data saved to raw/{date_str}/")
        return

    # Stage 2: Normalize
    normalized = run_normalize(raw_items, date_str, settings)

    # Stage 3: Select stories
    try:
        editorial_decisions = run_select(
            normalized, date_str, settings, prompt_templates, overrides
        )
    except Exception as e:
        normalized_path = os.path.join(BASE_DIR, "normalized", date_str, "all_items.json")
        logger.error(
            f"Story selection failed: {e}\n"
            f"Normalized items saved at {normalized_path}\n"
            f"Re-run with --skip-collect to retry without re-fetching sources."
        )
        sys.exit(1)

    # Stage 4a: Generate editor script (pre-review)
    try:
        editor_path = run_generate_editor_script(
            editorial_decisions, date_str, settings, prompt_templates
        )
    except Exception as e:
        decisions_path = os.path.join(BASE_DIR, "selected", date_str, "editorial_decisions.json")
        logger.error(
            f"Script generation failed: {e}\n"
            f"Editorial decisions saved at {decisions_path}\n"
            f"Re-run with --skip-collect to retry."
        )
        sys.exit(1)

    # Stage 5: Human review (edits to editor script flow into speech version)
    if not args.skip_review:
        approved = run_review(editor_path)
        if not approved:
            print(f"\nBriefing aborted. Editor script saved at: {editor_path}")
            return

    # Stage 4b: Generate speech script from (possibly edited) editor script
    try:
        speech_path = run_generate_speech_script(editor_path, date_str, settings)
    except Exception as e:
        logger.error(
            f"Speech script generation failed: {e}\n"
            f"Editor script saved at: {editor_path}\n"
            f"Re-run with --skip-collect to retry."
        )
        sys.exit(1)

    # Stage 6: Generate audio
    audio_path = None
    if not args.skip_audio:
        try:
            audio_path = run_audio(speech_path, date_str, settings)
        except Exception as e:
            logger.error(
                f"Audio generation failed: {e}\n"
                f"Scripts saved at:\n"
                f"  Editor: {editor_path}\n"
                f"  Speech: {speech_path}\n"
                f"Re-run with --audio-only to retry TTS."
            )
            sys.exit(1)

    # Stage 7: Assemble episode
    episode_dir = run_assemble(
        date_str, editor_path, speech_path, audio_path, editorial_decisions
    )

    # Summary
    print("\n" + "=" * 60)
    print("BRIEFING COMPLETE")
    print("=" * 60)
    print(f"Episode: {episode_dir}")
    print(f"Editor script: {editor_path}")
    print(f"Speech script: {speech_path}")
    if audio_path:
        print(f"Audio: {audio_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
