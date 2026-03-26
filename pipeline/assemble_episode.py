"""Assemble a final episode from script + audio into episodes/{date}/.

The metadata.json schema is designed as the future publishing seam:
downstream distribution (podcast hosting, social posting, email) should
only need to read this file to know what to publish.

Schema version: 1.0
"""

import json
import logging
import os
import shutil
from datetime import datetime, timezone

from pipeline.generate_audio import get_tts_usage
from pipeline.model_io import get_total_usage

logger = logging.getLogger("briefing")

# Bump this when the metadata schema changes in a breaking way.
METADATA_SCHEMA_VERSION = "1.0"


def assemble_episode(
    date_str: str,
    editor_script_path: str,
    speech_script_path: str,
    audio_path: str | None,
    editorial_decisions: dict,
    base_dir: str,
) -> str:
    """Package the final episode into episodes/{date}/.

    Copies script(s) and audio, writes metadata.json.
    Returns the episode directory path.

    metadata.json is the publishing seam — it contains everything a
    downstream consumer needs to distribute the episode:
      - Episode identity (date, version)
      - Content summary (stories with source URLs, weather level)
      - File manifest (which files are present)
      - Runtime estimate
      - Production metadata (model, voice, costs)
    """
    episode_dir = os.path.join(base_dir, "episodes", date_str)
    os.makedirs(episode_dir, exist_ok=True)

    # Copy scripts
    editor_dest = os.path.join(episode_dir, "briefing_editor.md")
    speech_dest = os.path.join(episode_dir, "briefing_speech.md")
    shutil.copy2(editor_script_path, editor_dest)
    shutil.copy2(speech_script_path, speech_dest)

    # Copy audio if available
    audio_dest = None
    if audio_path and os.path.exists(audio_path):
        audio_dest = os.path.join(episode_dir, "briefing.mp3")
        shutil.copy2(audio_path, audio_dest)

    # Read speech script for word count
    with open(speech_script_path) as f:
        speech_text = f.read()
    word_count = len(speech_text.split())
    estimated_minutes = round(word_count / 150, 1)

    # Build story manifest with full source info
    selected_stories = editorial_decisions.get("selected_stories", [])
    weather = editorial_decisions.get("weather_decision", {})

    stories_manifest = []
    for s in sorted(selected_stories, key=lambda x: x.get("rank", 99)):
        entry = {
            "rank": s.get("rank"),
            "headline": s.get("headline"),
            "summary": s.get("summary", ""),
            "source_name": s.get("source_name"),
            "source_url": s.get("source_url"),
            "other_sources": s.get("other_sources", []),
            "section": s.get("section"),
            "why_it_matters": s.get("why_it_matters", ""),
        }
        if s.get("editorial_note", "").startswith("Force-included"):
            entry["override"] = "force-included"
        stories_manifest.append(entry)

    # Production metadata
    tts_usage = get_tts_usage()
    model_usage = get_total_usage()

    metadata = {
        "schema_version": METADATA_SCHEMA_VERSION,
        "date": date_str,
        "assembled_at": datetime.now(timezone.utc).isoformat(),

        # Content
        "story_count": len(selected_stories),
        "stories": stories_manifest,
        "weather": {
            "level": weather.get("level", "unknown"),
            "rationale": weather.get("rationale", ""),
            "forecast_summary": weather.get("forecast_summary", ""),
            "alerts": weather.get("alerts", []),
        },

        # Runtime
        "word_count": word_count,
        "estimated_runtime_minutes": estimated_minutes,

        # Files
        "has_audio": audio_dest is not None,
        "files": {
            "editor_script": "briefing_editor.md",
            "speech_script": "briefing_speech.md",
            "audio": "briefing.mp3" if audio_dest else None,
            "metadata": "metadata.json",
        },

        # Production (for cost tracking and debugging)
        "production": {
            "model": editorial_decisions.get("model", "unknown"),
            "model_tokens": {
                "input": model_usage.get("input_tokens", 0),
                "output": model_usage.get("output_tokens", 0),
                "total": model_usage.get("total_tokens", 0),
            },
            "tts_voice": tts_usage.get("voice", "unknown"),
            "tts_characters": tts_usage.get("characters", 0),
            "audio_file_size_mb": tts_usage.get("file_size_mb", 0),
        },

        # Distribution — empty for now. Future integrations plug in here:
        # podcast RSS, email newsletter, social media, etc.
        "distribution": None,
    }

    metadata_path = os.path.join(episode_dir, "metadata.json")
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)

    logger.info(
        f"Episode assembled: {episode_dir} "
        f"({len(selected_stories)} stories, ~{estimated_minutes} min, "
        f"audio: {'yes' if audio_dest else 'no'})"
    )
    return episode_dir
