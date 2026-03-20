"""Generate briefing scripts from editorial decisions.

Produces two outputs:
  - Editor script (scripts/{date}_editor.md): markdown with source links, editorial notes
  - Speech script (scripts/{date}_speech.md): TTS-ready plain text with pronunciation subs
"""

import json
import logging
import os
import re
import time
from datetime import datetime

import anthropic
import yaml

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
    """Replace local place names with pronunciation-friendly versions."""
    for original, pronunciation in replacements.items():
        # Case-insensitive replacement, preserving word boundaries
        pattern = re.compile(re.escape(original), re.IGNORECASE)
        text = pattern.sub(pronunciation, text)
    return text


def generate_scripts(
    editorial_decisions: dict,
    date_str: str,
    base_dir: str,
    settings: dict,
    prompt_templates: dict,
) -> tuple[str, str]:
    """Generate editor and speech scripts from editorial decisions.

    Returns (editor_script_path, speech_script_path).
    """
    templates = prompt_templates["script_generation"]
    system_prompt = templates["system"]
    user_template = templates["user"]

    target_words = settings.get("briefing", {}).get("target_words", 750)

    # Format the system prompt with target words
    system_prompt = system_prompt.format(target_words=target_words)

    # Build date info
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

    logger.info(f"Generating briefing script ({target_words} word target)...")

    client = anthropic.Anthropic()

    # Retry once on failure
    for attempt in range(2):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )

            response_text = response.content[0].text

            # Extract JSON from response
            json_match = re.search(
                r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL
            )
            if json_match:
                response_text = json_match.group(1)

            result = json.loads(response_text)
            break

        except (json.JSONDecodeError, anthropic.APIError, KeyError, IndexError) as e:
            if attempt == 0:
                logger.warning(f"Script generation failed (attempt 1): {e}. Retrying in 5s...")
                time.sleep(5)
            else:
                logger.error(f"Script generation failed after retry: {e}")
                raise

    editor_script = result.get("editor_script", "")
    speech_script = result.get("speech_script", "")
    word_count = result.get("word_count", len(speech_script.split()))

    logger.info(f"Script generated: {word_count} words")

    # Apply pronunciation substitutions to speech script
    pronunciations = _load_pronunciation(base_dir)
    if pronunciations:
        speech_script = _apply_pronunciation(speech_script, pronunciations)
        logger.info(f"Applied {len(pronunciations)} pronunciation substitutions")

    # Save both scripts
    editor_path = _save_script(editor_script, date_str, "editor", base_dir)
    speech_path = _save_script(speech_script, date_str, "speech", base_dir)

    return editor_path, speech_path


def _save_script(content: str, date_str: str, variant: str, base_dir: str) -> str:
    """Save a script to scripts/{date}_{variant}.md."""
    out_dir = os.path.join(base_dir, "scripts")
    os.makedirs(out_dir, exist_ok=True)

    filepath = os.path.join(out_dir, f"{date_str}_{variant}.md")
    with open(filepath, "w") as f:
        f.write(content)

    logger.info(f"Saved {variant} script to {filepath}")
    return filepath
