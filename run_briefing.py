#!/usr/bin/env python3
"""Lehigh Valley Morning Briefing — pipeline orchestrator.

Usage:
    python run_briefing.py                     # Full pipeline for today
    python run_briefing.py --date 2026-03-19   # Run for a specific date
    python run_briefing.py --date golden        # Run golden day test fixture
    python run_briefing.py --dry-run            # Collect → select → scorecard (no scripts/audio)
    python run_briefing.py --collect-only       # Only fetch sources
    python run_briefing.py --skip-collect       # Re-use existing raw data
    python run_briefing.py --select-only        # Re-use normalized data, re-run selection only
    python run_briefing.py --script-only        # Re-use editorial decisions, re-generate scripts
    python run_briefing.py --skip-review        # Skip human review gate
    python run_briefing.py --skip-audio         # Script only, no TTS
    python run_briefing.py --audio-only         # Re-use existing speech script, only run TTS
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timezone

import yaml
from dotenv import load_dotenv

from pipeline.logger import setup_logger
from pipeline.collectors.rss_collector import RSSCollector
from pipeline.collectors.weather_collector import WeatherCollector
from pipeline.normalize import normalize_items, save_normalized
from pipeline.select_stories import select_stories
from pipeline.generate_script import generate_editor_script, generate_speech_script
from pipeline.review import review_script
from pipeline.generate_audio import generate_audio, get_tts_usage, reset_tts_usage
from pipeline.assemble_episode import assemble_episode
from pipeline.model_io import get_usage_log, get_total_usage, reset_usage


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

COLLECTOR_MAP = {
    "rss": RSSCollector,
    "weather_api": WeatherCollector,
}

# Module-level stage timing accumulator
_stage_timings: dict[str, dict] = {}
_current_stage_start: float | None = None
_current_stage_name: str | None = None


def _start_stage(name: str):
    """Mark the start of a pipeline stage for timing."""
    global _current_stage_start, _current_stage_name
    _current_stage_start = time.time()
    _current_stage_name = name


def _end_stage(name: str | None = None, **extra):
    """Mark the end of a pipeline stage. Records duration and optional metadata."""
    global _current_stage_start, _current_stage_name
    stage = name or _current_stage_name
    if stage and _current_stage_start is not None:
        _stage_timings[stage] = {
            "duration_seconds": round(time.time() - _current_stage_start, 1),
            **extra,
        }
    _current_stage_start = None
    _current_stage_name = None


def _reset_timings():
    """Clear stage timings for a new run."""
    _stage_timings.clear()


def load_config(filename: str) -> dict:
    """Load a YAML config file from config/."""
    path = os.path.join(BASE_DIR, "config", filename)
    with open(path) as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_editorial_decisions(decisions: dict, stage_label: str = "selection"):
    """Validate that editorial decisions contain usable data."""
    selected = decisions.get("selected_stories")
    if not selected or len(selected) == 0:
        raise ValueError(
            f"{stage_label}: Claude returned zero selected stories. "
            f"Check the prompt, candidates, or model response."
        )

    for i, story in enumerate(selected):
        if not story.get("headline"):
            raise ValueError(
                f"{stage_label}: selected_stories[{i}] has no headline. "
                f"Model response may be malformed."
            )
        if not story.get("cluster_id"):
            raise ValueError(
                f"{stage_label}: selected_stories[{i}] ({story.get('headline', '?')}) "
                f"has no cluster_id. Model response may be malformed."
            )

    weather = decisions.get("weather_decision")
    if not weather or not weather.get("level"):
        raise ValueError(
            f"{stage_label}: Missing or empty weather_decision. "
            f"Model response may be malformed."
        )


def validate_script_file(filepath: str, label: str, min_chars: int = 100):
    """Validate that a script file exists and has meaningful content."""
    if not os.path.exists(filepath):
        raise ValueError(f"{label}: file does not exist at {filepath}")

    with open(filepath) as f:
        content = f.read().strip()

    if len(content) < min_chars:
        raise ValueError(
            f"{label}: file has only {len(content)} characters (minimum {min_chars}). "
            f"Content may be empty or truncated. File: {filepath}"
        )

    return content


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------

def print_scorecard(decisions: dict, date_str: str, base_dir: str) -> str:
    """Print an editorial scorecard and save it as an artifact."""
    selected = decisions.get("selected_stories", [])
    cut = decisions.get("cut_stories", [])
    near = decisions.get("near_misses", [])
    weather = decisions.get("weather_decision", {})

    force_included = [s for s in selected if "Force-included" in s.get("editorial_note", "")]

    lines = [
        "=" * 60,
        f"EDITORIAL SCORECARD — {date_str}",
        "=" * 60,
        f"  Raw news items:      {decisions.get('raw_news_item_count', '?')}",
        f"  After overrides:     {decisions.get('candidate_news_item_count', '?')}",
        f"  Clusters:            {decisions.get('cluster_count', '?')}",
        f"  Selected:            {len(selected)}",
        f"  Cut:                 {len(cut)}",
        f"  Near misses:         {len(near)}",
        f"  Force-includes used: {len(force_included)}",
        f"  Weather:             {weather.get('level', '?')}",
        "",
        "SELECTED STORIES:",
    ]

    for s in sorted(selected, key=lambda x: x.get("rank", 99)):
        rank = s.get("rank", "?")
        headline = s.get("headline", "?")
        source = s.get("source_name", "?")
        note = s.get("editorial_note", "")
        lines.append(f"  {rank}. {headline}")
        lines.append(f"     [{source}] {note}")

    if cut:
        lines.append("")
        lines.append("CUT:")
        for c in cut:
            lines.append(f"  ✗ {c.get('headline', '?')} — {c.get('reason', '')}")

    if near:
        lines.append("")
        lines.append("NEAR MISSES:")
        for n in near:
            lines.append(f"  ~ {n.get('headline', '?')} — {n.get('reason', '')}")

    lines.append("")
    lines.append(f"  Weather rationale: {weather.get('rationale', '—')}")
    lines.append("=" * 60)

    text = "\n".join(lines)
    print("\n" + text)

    scorecard_dir = os.path.join(base_dir, "selected", date_str)
    os.makedirs(scorecard_dir, exist_ok=True)
    scorecard_path = os.path.join(scorecard_dir, "scorecard.txt")
    with open(scorecard_path, "w") as f:
        f.write(text + "\n")

    return text


# ---------------------------------------------------------------------------
# Review packet
# ---------------------------------------------------------------------------

def save_review_packet(
    editorial_decisions: dict,
    date_str: str,
    base_dir: str,
) -> str:
    """Save a review packet alongside the editor script.

    The review packet is a markdown reference document the reviewer can
    open alongside the editor script. It contains:
      - Selected stories with source URLs and editorial notes
      - Cut stories with reasons
      - Near misses
      - Weather decision with rationale
      - Override effects
    """
    selected = sorted(
        editorial_decisions.get("selected_stories", []),
        key=lambda x: x.get("rank", 99),
    )
    cut = editorial_decisions.get("cut_stories", [])
    near = editorial_decisions.get("near_misses", [])
    weather = editorial_decisions.get("weather_decision", {})

    dt = datetime.strptime(date_str, "%Y-%m-%d")
    day_name = dt.strftime("%A, %B %d, %Y")

    lines = [
        f"# Review Packet — {day_name}",
        "",
        "## Summary",
        f"- **Selected:** {len(selected)} stories",
        f"- **Cut:** {len(cut)} stories",
        f"- **Near misses:** {len(near)}",
        f"- **Weather:** {weather.get('level', '?')}",
        "",
        "---",
        "",
        "## Selected Stories",
        "",
    ]

    for s in selected:
        rank = s.get("rank", "?")
        headline = s.get("headline", "?")
        source = s.get("source_name", "")
        url = s.get("source_url", "")
        note = s.get("editorial_note", "")
        why = s.get("why_it_matters", "")
        other = s.get("other_sources", [])

        lines.append(f"### {rank}. {headline}")
        lines.append(f"- **Source:** [{source}]({url})")

        if other:
            other_links = ", ".join(
                f"[{o.get('name', '?')}]({o.get('url', '')})" for o in other
            )
            lines.append(f"- **Also covered by:** {other_links}")

        if why:
            lines.append(f"- **Why it matters:** {why}")
        if note:
            lines.append(f"- **Editorial note:** {note}")

        override_note = ""
        if note and "Force-included" in note:
            override_note = "FORCE-INCLUDED"
        if override_note:
            lines.append(f"- **Override:** {override_note}")

        lines.append("")

    if cut:
        lines.append("---")
        lines.append("")
        lines.append("## Cut Stories")
        lines.append("")
        for c in cut:
            lines.append(f"- **{c.get('headline', '?')}** — {c.get('reason', '')}")
        lines.append("")

    if near:
        lines.append("---")
        lines.append("")
        lines.append("## Near Misses")
        lines.append("")
        for n in near:
            lines.append(f"- **{n.get('headline', '?')}** — {n.get('reason', '')}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Weather Decision")
    lines.append(f"- **Level:** {weather.get('level', '?')}")
    lines.append(f"- **Rationale:** {weather.get('rationale', '—')}")
    forecast = weather.get("forecast_summary", "")
    if forecast:
        lines.append(f"- **Forecast:** {forecast}")
    alerts = weather.get("alerts", [])
    if alerts:
        lines.append(f"- **Active alerts:** {', '.join(str(a) for a in alerts)}")
    lines.append("")

    text = "\n".join(lines)

    out_dir = os.path.join(base_dir, "scripts")
    os.makedirs(out_dir, exist_ok=True)
    packet_path = os.path.join(out_dir, f"{date_str}_review_packet.md")
    with open(packet_path, "w") as f:
        f.write(text)

    logger = setup_logger(date_str)
    logger.info(f"Review packet saved to {packet_path}")
    return packet_path


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------

def run_collect(sources: list[dict], date_str: str) -> list[dict]:
    """Stage 1: Collect from all enabled sources."""
    logger = setup_logger(date_str)
    logger.info(f"=== COLLECT ({date_str}) ===")

    _start_stage("collect")
    all_items = []
    enabled = [s for s in sources if s.get("enabled", True)]
    source_failures = []
    logger.info(f"Collecting from {len(enabled)} enabled sources")

    for source in enabled:
        source_type = source.get("type", "")
        collector_cls = COLLECTOR_MAP.get(source_type)

        if not collector_cls:
            logger.warning(f"  Unknown source type '{source_type}' for {source['name']}, skipping")
            source_failures.append(source["name"])
            continue

        try:
            collector = collector_cls(source)
            items = collector.collect()
            collector.save_raw(items, date_str, BASE_DIR)
            all_items.extend(items)
        except Exception as e:
            logger.warning(f"  FAILED: {source['name']}: {e}")
            source_failures.append(source["name"])
            continue

    _end_stage("collect", item_count=len(all_items), source_failures=source_failures)
    logger.info(f"Collected {len(all_items)} total items from {len(enabled)} sources")

    if not all_items:
        logger.error("All sources failed. No items collected.")
        sys.exit(1)

    return all_items


def run_normalize(raw_items: list[dict], date_str: str, settings: dict) -> list[dict]:
    """Stage 2: Normalize collected items."""
    logger = setup_logger(date_str)
    logger.info(f"=== NORMALIZE ({date_str}) ===")

    _start_stage("normalize")
    freshness_hours = settings.get("briefing", {}).get("freshness_hours", 24)
    normalized = normalize_items(raw_items, freshness_hours=freshness_hours)
    save_normalized(normalized, date_str, BASE_DIR)
    _end_stage("normalize", item_count=len(normalized))

    if not normalized:
        logger.error("Normalization produced zero items. Nothing to brief on.")
        sys.exit(1)

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

    _start_stage("select")
    decisions = select_stories(
        normalized_items, date_str, BASE_DIR, settings, prompt_templates, overrides
    )
    validate_editorial_decisions(decisions)
    _end_stage("select",
               cluster_count=decisions.get("cluster_count"),
               selected_count=len(decisions.get("selected_stories", [])))

    return decisions


def run_generate_editor_script(
    editorial_decisions: dict,
    date_str: str,
    settings: dict,
    prompt_templates: dict,
) -> str:
    """Stage 4a: Generate editor script (pre-review)."""
    logger = setup_logger(date_str)
    logger.info(f"=== GENERATE EDITOR SCRIPT ({date_str}) ===")

    _start_stage("editor_script")
    editor_path = generate_editor_script(
        editorial_decisions, date_str, BASE_DIR, settings, prompt_templates
    )
    content = validate_script_file(editor_path, "Editor script")
    _end_stage("editor_script", word_count=len(content.split()))

    return editor_path


def run_generate_speech_script(
    editor_path: str,
    date_str: str,
    settings: dict,
) -> str:
    """Stage 4b: Generate speech script from reviewed editor script (post-review)."""
    logger = setup_logger(date_str)
    logger.info(f"=== GENERATE SPEECH SCRIPT ({date_str}) ===")

    _start_stage("speech_script")
    speech_path = generate_speech_script(editor_path, date_str, BASE_DIR, settings)
    content = validate_script_file(speech_path, "Speech script")
    _end_stage("speech_script", word_count=len(content.split()))

    return speech_path


def run_review(editor_path: str, editorial_decisions: dict | None = None) -> bool:
    """Stage 5: Human review gate."""
    _start_stage("review")
    result = review_script(editor_path, editorial_decisions)
    _end_stage("review", approved=result)
    return result


def run_audio(speech_path: str, date_str: str, settings: dict) -> str:
    """Stage 6: Generate audio from speech script."""
    logger = setup_logger(date_str)
    logger.info(f"=== GENERATE AUDIO ({date_str}) ===")

    validate_script_file(speech_path, "Speech script (pre-TTS)")

    _start_stage("audio")
    audio_path = generate_audio(speech_path, date_str, BASE_DIR, settings)
    tts = get_tts_usage()
    _end_stage("audio",
               characters=tts.get("characters", 0),
               file_size_mb=tts.get("file_size_mb", 0))

    return audio_path


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

    _start_stage("assemble")
    episode_dir = assemble_episode(
        date_str, editor_path, speech_path, audio_path, editorial_decisions, BASE_DIR
    )
    _end_stage("assemble")

    return episode_dir


# ---------------------------------------------------------------------------
# Data loaders for stage reruns
# ---------------------------------------------------------------------------

def load_existing_raw(date_str: str, raw_dir: str | None = None) -> list[dict]:
    """Load previously collected raw data from a directory."""
    if raw_dir is None:
        raw_dir = os.path.join(BASE_DIR, "raw", date_str)
    if not os.path.isdir(raw_dir):
        print(f"Error: No raw data found at {raw_dir}")
        sys.exit(1)

    all_items = []
    for filename in sorted(os.listdir(raw_dir)):
        if not filename.endswith(".json"):
            continue
        filepath = os.path.join(raw_dir, filename)
        with open(filepath) as f:
            items = json.load(f)
        all_items.extend(items)

    logger = setup_logger(date_str)
    logger.info(f"Loaded {len(all_items)} items from existing raw data at {raw_dir}")
    return all_items


def load_existing_normalized(date_str: str) -> list[dict]:
    """Load previously normalized data for a date."""
    filepath = os.path.join(BASE_DIR, "normalized", date_str, "all_items.json")
    if not os.path.exists(filepath):
        print(f"Error: No normalized data found at {filepath}")
        print(f"Run the full pipeline or --collect-only first for {date_str}.")
        sys.exit(1)

    with open(filepath) as f:
        items = json.load(f)

    logger = setup_logger(date_str)
    logger.info(f"Loaded {len(items)} normalized items from {filepath}")
    return items


def load_existing_decisions(date_str: str) -> dict:
    """Load previously saved editorial decisions for a date."""
    filepath = os.path.join(BASE_DIR, "selected", date_str, "editorial_decisions.json")
    if not os.path.exists(filepath):
        print(f"Error: No editorial decisions found at {filepath}")
        print(f"Run --select-only or the full pipeline first for {date_str}.")
        sys.exit(1)

    with open(filepath) as f:
        decisions = json.load(f)

    logger = setup_logger(date_str)
    logger.info(f"Loaded editorial decisions from {filepath}")
    return decisions


# ---------------------------------------------------------------------------
# Run summary
# ---------------------------------------------------------------------------

def save_run_summary(
    date_str: str,
    mode: str,
    start_time: float,
    output_files: dict[str, str | None],
    editorial_decisions: dict | None = None,
):
    """Save a structured run summary to logs/{date}_summary.json.

    Includes per-stage timing, Claude token usage, TTS usage, and editorial stats.
    """
    elapsed = time.time() - start_time
    model_usage = get_total_usage()
    model_log = get_usage_log()
    tts = get_tts_usage()

    summary = {
        "date": date_str,
        "mode": mode,
        "started_at": datetime.fromtimestamp(start_time, tz=timezone.utc).isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(elapsed, 1),
        "stages": dict(_stage_timings),
        "model_usage": {
            "calls": model_usage["calls"],
            "input_tokens": model_usage["input_tokens"],
            "output_tokens": model_usage["output_tokens"],
            "total_tokens": model_usage["total_tokens"],
            "per_call": model_log,
        },
        "tts_usage": tts if tts else None,
        "output_files": {k: v for k, v in output_files.items() if v},
    }

    if editorial_decisions:
        summary["editorial"] = {
            "raw_news_items": editorial_decisions.get("raw_news_item_count"),
            "candidate_news_items": editorial_decisions.get("candidate_news_item_count"),
            "clusters": editorial_decisions.get("cluster_count"),
            "selected": len(editorial_decisions.get("selected_stories", [])),
            "cut": len(editorial_decisions.get("cut_stories", [])),
            "near_misses": len(editorial_decisions.get("near_misses", [])),
            "weather_level": editorial_decisions.get("weather_decision", {}).get("level"),
        }

    log_dir = os.path.join(BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    summary_path = os.path.join(log_dir, f"{date_str}_summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    logger = setup_logger(date_str)
    logger.info(f"Run summary saved to {summary_path}")

    # Print usage summary
    if model_usage["calls"] > 0:
        print(f"  Claude: {model_usage['calls']} calls, "
              f"{model_usage['input_tokens']} in / {model_usage['output_tokens']} out tokens")
    if tts:
        print(f"  TTS: {tts.get('characters', 0)} chars → "
              f"{tts.get('file_size_mb', 0)} MB audio")

    return summary_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Lehigh Valley Morning Briefing")
    parser.add_argument("--date", default=date.today().isoformat(),
                        help="Date for the briefing (YYYY-MM-DD or 'golden')")

    # Mode flags (mutually exclusive shortcuts)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--collect-only", action="store_true",
                            help="Only fetch sources, don't process")
    mode_group.add_argument("--dry-run", action="store_true",
                            help="Collect → normalize → select → scorecard (no scripts/audio)")
    mode_group.add_argument("--select-only", action="store_true",
                            help="Re-use existing normalized data, re-run selection only")
    mode_group.add_argument("--script-only", action="store_true",
                            help="Re-use existing editorial decisions, re-generate scripts")
    mode_group.add_argument("--audio-only", action="store_true",
                            help="Re-use existing speech script, only run TTS")

    # Modifier flags
    parser.add_argument("--skip-collect", action="store_true",
                        help="Skip collection, re-use existing raw data")
    parser.add_argument("--skip-review", action="store_true",
                        help="Skip human review gate")
    parser.add_argument("--skip-audio", action="store_true",
                        help="Generate script only, no TTS")
    args = parser.parse_args()

    date_str = args.date
    is_golden = date_str == "golden"
    if is_golden:
        date_str = "2026-03-20"  # Fixed date for golden day fixture

    load_dotenv(os.path.join(BASE_DIR, ".env"))

    logger = setup_logger(date_str)
    start_time = time.time()

    # Reset all accumulators for a clean run
    reset_usage()
    reset_tts_usage()
    _reset_timings()

    if is_golden:
        logger.info(f"Running GOLDEN DAY test fixture (date: {date_str})")
    else:
        logger.info(f"Starting briefing pipeline for {date_str}")

    # Load config
    sources_config = load_config("sources.yaml")
    settings = load_config("settings.yaml")
    prompt_templates = load_config("prompt_templates.yaml")
    overrides = load_config("overrides.yaml")
    sources = sources_config.get("sources", [])

    # --audio-only: just re-run TTS from existing speech script
    if args.audio_only:
        speech_path = os.path.join(BASE_DIR, "scripts", f"{date_str}_speech.md")
        if not os.path.exists(speech_path):
            print(f"Error: No speech script found at {speech_path}")
            sys.exit(1)
        audio_path = run_audio(speech_path, date_str, settings)
        save_run_summary(date_str, "audio-only", start_time, {"audio": audio_path})
        _print_duration(start_time)
        print(f"\nAudio generated: {audio_path}")
        return

    # --script-only: re-use editorial decisions, regenerate scripts
    if args.script_only:
        editorial_decisions = load_existing_decisions(date_str)
        print_scorecard(editorial_decisions, date_str, BASE_DIR)

        try:
            editor_path = run_generate_editor_script(
                editorial_decisions, date_str, settings, prompt_templates
            )
        except Exception as e:
            logger.error(f"Script generation failed: {e}")
            sys.exit(1)

        # Save review packet before review
        packet_path = save_review_packet(editorial_decisions, date_str, BASE_DIR)

        if not args.skip_review:
            approved = run_review(editor_path, editorial_decisions)
            if not approved:
                print(f"\nBriefing aborted. Editor script saved at: {editor_path}")
                return

        try:
            speech_path = run_generate_speech_script(editor_path, date_str, settings)
        except Exception as e:
            logger.error(f"Speech script generation failed: {e}\nEditor script: {editor_path}")
            sys.exit(1)

        save_run_summary(date_str, "script-only", start_time,
                         {"editor_script": editor_path, "speech_script": speech_path,
                          "review_packet": packet_path},
                         editorial_decisions)
        _print_duration(start_time)
        print(f"\nScripts regenerated:")
        print(f"  Editor: {editor_path}")
        print(f"  Speech: {speech_path}")
        print(f"  Review packet: {packet_path}")
        print(f"  Re-run with --audio-only to generate audio.")
        return

    # --select-only: re-use normalized data, re-run selection
    if args.select_only:
        normalized = load_existing_normalized(date_str)

        try:
            editorial_decisions = run_select(
                normalized, date_str, settings, prompt_templates, overrides
            )
        except Exception as e:
            logger.error(f"Story selection failed: {e}")
            sys.exit(1)

        print_scorecard(editorial_decisions, date_str, BASE_DIR)
        save_run_summary(date_str, "select-only", start_time,
                         {"scorecard": f"selected/{date_str}/scorecard.txt"},
                         editorial_decisions)
        _print_duration(start_time)
        print(f"\nSelection complete. Re-run with --script-only to generate scripts.")
        return

    # --- Full pipeline (or --dry-run, --collect-only) ---

    # Stage 1: Collect
    if is_golden:
        golden_raw_dir = os.path.join(BASE_DIR, "tests", "golden_day", "raw")
        raw_items = load_existing_raw(date_str, raw_dir=golden_raw_dir)
        logger.info("Loaded golden day fixture data (skipping live collection)")
    elif args.skip_collect:
        raw_items = load_existing_raw(date_str)
    else:
        raw_items = run_collect(sources, date_str)

    if args.collect_only:
        save_run_summary(date_str, "collect-only", start_time,
                         {"raw_data": f"raw/{date_str}/"})
        _print_duration(start_time)
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
            f"Re-run with --select-only to retry selection."
        )
        sys.exit(1)

    # Scorecard (always printed after selection)
    print_scorecard(editorial_decisions, date_str, BASE_DIR)

    # --dry-run: stop after selection and scorecard
    if args.dry_run:
        save_run_summary(date_str, "dry-run", start_time,
                         {"scorecard": f"selected/{date_str}/scorecard.txt"},
                         editorial_decisions)
        _print_duration(start_time)
        print(f"\nDry run complete. No scripts or audio generated.")
        print(f"  Editorial decisions: selected/{date_str}/editorial_decisions.json")
        print(f"  Scorecard:           selected/{date_str}/scorecard.txt")
        print(f"  Re-run with --script-only to generate scripts.")
        return

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
            f"Re-run with --script-only to retry."
        )
        sys.exit(1)

    # Save review packet before review
    packet_path = save_review_packet(editorial_decisions, date_str, BASE_DIR)

    # Stage 5: Human review (edits to editor script flow into speech version)
    if not args.skip_review:
        approved = run_review(editor_path, editorial_decisions)
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
            f"Re-run with --script-only to retry."
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

    # Run summary
    save_run_summary(date_str, "full", start_time,
                     {"episode": episode_dir, "editor_script": editor_path,
                      "speech_script": speech_path, "review_packet": packet_path,
                      "audio": audio_path},
                     editorial_decisions)

    # Final output
    _print_duration(start_time)
    print("\n" + "=" * 60)
    print("BRIEFING COMPLETE")
    print("=" * 60)
    print(f"Episode:        {episode_dir}")
    print(f"Editor script:  {editor_path}")
    print(f"Speech script:  {speech_path}")
    print(f"Review packet:  {packet_path}")
    if audio_path:
        print(f"Audio:          {audio_path}")
    print("=" * 60)


def _print_duration(start_time: float):
    """Print pipeline duration."""
    elapsed = time.time() - start_time
    if elapsed < 60:
        print(f"\n  ⏱  {elapsed:.1f}s")
    else:
        minutes = int(elapsed // 60)
        seconds = elapsed % 60
        print(f"\n  ⏱  {minutes}m {seconds:.0f}s")


if __name__ == "__main__":
    main()
