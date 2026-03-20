"""Generate briefing scripts from editorial decisions.

Two-phase process:
  1. generate_editor_script(): Claude produces the editor version from editorial decisions.
     Operator reviews and edits this file.
  2. generate_speech_script(): Claude converts the reviewed editor script into TTS-ready
     plain text. Pronunciation substitutions are applied after.

This ensures human edits to the editor script flow into the spoken version.
"""

import json
import logging
import os
import re
import time
from datetime import datetime

import anthropic
import yaml

from pipeline.model_io import call_claude_json

logger = logging.getLogger("briefing")


def _load_pronunciation(base_dir: str) -> dict[str, str]:
    """Load pronunciation replacements from config/pronunciation.yaml."""
    path = os.path.join(base_dir, "config", "pronunciation.yaml")
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return data.get("replacements", {})
    except FileNotFoundError:
        return {}


def _apply_pronunciation(text: str, replacements: dict[str, str]) -> str:
    """Replace local place names with pronunciation-friendly versions.

    Uses word boundary markers to avoid replacing inside larger words.
    """
    for original, pronunciation in replacements.items():
        pattern = re.compile(r"\b" + re.escape(original) + r"\b", re.IGNORECASE)
        text = pattern.sub(pronunciation, text)
    return text


def generate_editor_script(
    editorial_decisions: dict,
    date_str: str,
    base_dir: str,
    settings: dict,
    prompt_templates: dict,
) -> str:
    """Phase 1: Generate the editor script from editorial decisions.

    Returns the path to the saved editor script.
    """
    templates = prompt_templates["script_generation"]
    system_prompt = templates["system"]
    user_template = templates["user"]

    target_words = settings.get("briefing", {}).get("target_words", 750)
    system_prompt = system_prompt.format(target_words=target_words)

    dt = datetime.strptime(date_str, "%Y-%m-%d")
    day_of_week = dt.strftime("%A")

    selected_stories_json = json.dumps(
        editorial_decisions.get("selected_stories", []), indent=2
    )
    weather_decision_json = json.dumps(
        editorial_decisions.get("weather_decision", {}), indent=2
    )

    user_prompt = user_template.format(
        date=date_str,
        day_of_week=day_of_week,
        selected_stories_json=selected_stories_json,
        weather_decision_json=weather_decision_json,
    )

    model = settings.get("ai", {}).get("model", "claude-sonnet-4-20250514")
    max_tokens = settings.get("ai", {}).get("max_tokens", 4096)

    logger.info(f"Generating editor script ({target_words} word target)...")

    required_keys = {"editor_script", "word_count"}
    result = call_claude_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=model,
        max_tokens=max_tokens,
        required_keys=required_keys,
        label="editor script generation",
    )

    editor_script = result.get("editor_script", "")
    word_count = result.get("word_count", len(editor_script.split()))
    logger.info(f"Editor script generated: {word_count} words")

    editor_path = _save_script(editor_script, date_str, "editor", base_dir)
    return editor_path


def generate_speech_script(
    editor_script_path: str,
    date_str: str,
    base_dir: str,
    settings: dict,
) -> str:
    """Phase 2: Convert the reviewed editor script into a TTS-ready speech script.

    Called AFTER human review so that any edits to the editor script are reflected.
    Returns the path to the saved speech script.
    """
    with open(editor_script_path) as f:
        editor_text = f.read()

    model = settings.get("ai", {}).get("model", "claude-sonnet-4-20250514")
    max_tokens = settings.get("ai", {}).get("max_tokens", 4096)

    system_prompt = (
        "You convert a news briefing editor script into a speech-ready version "
        "for text-to-speech. Rules:\n"
        "- Remove all markdown formatting (headers, bold, links, blockquotes).\n"
        "- Remove all URLs and bracketed citations.\n"
        "- Remove editorial notes and annotations.\n"
        "- Write out numbers in words (e.g., '5-2' becomes 'five to two').\n"
        "- Use natural spoken source attribution sparingly ('According to the "
        "Morning Call...' — only where it sounds natural, not every sentence).\n"
        "- No filler transitions.\n"
        "- Preserve the content and structure of the editor script exactly — "
        "do not add, remove, or rephrase stories. Only adapt the formatting "
        "for spoken delivery.\n"
        "- Respond with JSON: {\"speech_script\": \"<text>\"}"
    )

    user_prompt = (
        f"Convert this editor script to a TTS-ready speech script:\n\n{editor_text}"
    )

    logger.info("Generating speech script from reviewed editor script...")

    required_keys = {"speech_script"}
    result = call_claude_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=model,
        max_tokens=max_tokens,
        required_keys=required_keys,
        label="speech script generation",
    )

    speech_script = result.get("speech_script", "")

    # Apply pronunciation substitutions
    pronunciations = _load_pronunciation(base_dir)
    if pronunciations:
        speech_script = _apply_pronunciation(speech_script, pronunciations)
        logger.info(f"Applied {len(pronunciations)} pronunciation substitutions")

    speech_path = _save_script(speech_script, date_str, "speech", base_dir)
    return speech_path


def _save_script(content: str, date_str: str, variant: str, base_dir: str) -> str:
    """Save a script to scripts/{date}_{variant}.md."""
    out_dir = os.path.join(base_dir, "scripts")
    os.makedirs(out_dir, exist_ok=True)

    filepath = os.path.join(out_dir, f"{date_str}_{variant}.md")
    with open(filepath, "w") as f:
        f.write(content)

    logger.info(f"Saved {variant} script to {filepath}")
    return filepath
