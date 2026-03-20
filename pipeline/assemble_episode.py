"""Assemble a final episode from script + audio into episodes/{date}/."""

import json
import logging
import os
import shutil
from datetime import datetime, timezone

logger = logging.getLogger("briefing")


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

    # Write metadata
    selected_stories = editorial_decisions.get("selected_stories", [])
    weather = editorial_decisions.get("weather_decision", {})

    # Estimate runtime from word count (roughly 150 words per minute)
    with open(speech_script_path) as f:
        word_count = len(f.read().split())
    estimated_minutes = round(word_count / 150, 1)

    metadata = {
        "date": date_str,
        "assembled_at": datetime.now(timezone.utc).isoformat(),
        "story_count": len(selected_stories),
        "weather_level": weather.get("level", "unknown"),
        "word_count": word_count,
        "estimated_runtime_minutes": estimated_minutes,
        "has_audio": audio_dest is not None,
        "files": {
            "editor_script": "briefing_editor.md",
            "speech_script": "briefing_speech.md",
            "audio": "briefing.mp3" if audio_dest else None,
        },
        "stories": [
            {
                "rank": s.get("rank"),
                "headline": s.get("headline"),
                "source": s.get("source_name"),
                "section": s.get("section"),
            }
            for s in selected_stories
        ],
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
