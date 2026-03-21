"""Pronunciation normalization for TTS.

Loads phonetic replacements from config/pronunciation.yaml and applies
them to text before it reaches the TTS engine.  Only used in the audio
generation path — saved speech scripts stay human-readable.
"""

import os
import re

import yaml


def load_pronunciation(base_dir: str) -> dict[str, str]:
    """Load pronunciation replacements from config/pronunciation.yaml."""
    path = os.path.join(base_dir, "config", "pronunciation.yaml")
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return data.get("replacements", {})
    except FileNotFoundError:
        return {}


def apply_pronunciation(text: str, replacements: dict[str, str]) -> str:
    """Replace local place names with pronunciation-friendly versions.

    Uses word boundary markers to avoid replacing inside larger words.
    Applied only to in-memory TTS input, not to saved artifacts.
    """
    for original, pronunciation in replacements.items():
        pattern = re.compile(r"\b" + re.escape(original) + r"\b", re.IGNORECASE)
        text = pattern.sub(pronunciation, text)
    return text
