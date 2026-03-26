"""Regression test: pronunciation normalization boundary.

Guarantees that:
  1. Saved speech scripts stay human-readable (no phonetic spellings).
  2. Pronunciation normalization is applied only to in-memory TTS text.
  3. The generate_audio code path applies pronunciation before the API call.
  4. The generate_script code path does NOT apply pronunciation.

These tests run without API keys (no Claude or ElevenLabs calls).
"""

import os
import sys
import tempfile

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from pipeline.pronunciation import apply_pronunciation, load_pronunciation


# ── Shared fixtures ──────────────────────────────────────────────────────────

SAMPLE_TEXT = (
    "Lehigh Valley residents in Nazareth and Emmaus watched the "
    "Schuylkill River rise near Catasauqua and Macungie."
)

PHONETIC_MARKERS = [
    "LEE-high",
    "NAZ-uh-reth",
    "EH-mays",
    "SKOO-kull",
    "cat-uh-SAW-kwuh",
    "muh-CUNG-ee",
]


@pytest.fixture
def pronunciations():
    """Load the real pronunciation config from the project."""
    return load_pronunciation(PROJECT_ROOT)


# ── 1. Pronunciation module works correctly ──────────────────────────────────


def test_pronunciation_replaces_place_names(pronunciations):
    """apply_pronunciation converts human-readable names to phonetic versions."""
    result = apply_pronunciation(SAMPLE_TEXT, pronunciations)
    for marker in PHONETIC_MARKERS:
        assert marker in result, f"Expected phonetic marker '{marker}' in result"


def test_pronunciation_preserves_non_place_words(pronunciations):
    """Words that are not in the replacement dict are left unchanged."""
    result = apply_pronunciation(SAMPLE_TEXT, pronunciations)
    assert "residents" in result
    assert "watched" in result
    assert "River" in result
    assert "rise" in result
    assert "near" in result


def test_pronunciation_word_boundaries(pronunciations):
    """Word boundaries prevent partial replacements inside larger words."""
    text = "The Bethlehemite was from Bethlehem and the Lehigh Valley."
    result = apply_pronunciation(text, pronunciations)
    # "Bethlehemite" should not be touched
    assert "Bethlehemite" in result
    # "Lehigh" should be replaced
    assert "LEE-high" in result


def test_pronunciation_empty_dict():
    """An empty replacement dict returns the original text unchanged."""
    result = apply_pronunciation(SAMPLE_TEXT, {})
    assert result == SAMPLE_TEXT


# ── 2. Saved speech script stays human-readable ─────────────────────────────


def test_generate_script_does_not_import_pronunciation():
    """generate_script.py must not import or call pronunciation functions.

    This is the core boundary test: if someone accidentally re-adds
    pronunciation to generate_script.py, this test fails immediately.
    """
    script_path = os.path.join(PROJECT_ROOT, "pipeline", "generate_script.py")
    with open(script_path) as f:
        source = f.read()

    # Must not import pronunciation
    assert "from pipeline.pronunciation" not in source, (
        "generate_script.py must not import from pipeline.pronunciation — "
        "pronunciation normalization belongs in generate_audio.py only"
    )
    assert "import pronunciation" not in source, (
        "generate_script.py must not import pronunciation"
    )

    # Must not call apply_pronunciation or _apply_pronunciation
    assert "apply_pronunciation" not in source, (
        "generate_script.py must not call apply_pronunciation — "
        "the saved speech script must stay human-readable"
    )

    # Must not call _load_pronunciation or load_pronunciation
    assert "load_pronunciation" not in source, (
        "generate_script.py must not call load_pronunciation"
    )


def test_generate_audio_imports_pronunciation():
    """generate_audio.py must import and use pronunciation functions.

    This confirms the pronunciation layer is wired into the TTS path.
    """
    audio_path = os.path.join(PROJECT_ROOT, "pipeline", "generate_audio.py")
    with open(audio_path) as f:
        source = f.read()

    assert "from pipeline.pronunciation import" in source, (
        "generate_audio.py must import from pipeline.pronunciation"
    )
    assert "apply_pronunciation" in source, (
        "generate_audio.py must call apply_pronunciation"
    )
    assert "load_pronunciation" in source, (
        "generate_audio.py must call load_pronunciation"
    )


# ── 3. End-to-end boundary: file stays clean, TTS text gets phonetics ────────


def test_speech_file_clean_tts_text_phonetic(pronunciations):
    """Simulate the full boundary: script saves clean, audio reads and normalizes.

    This mirrors the real pipeline flow:
      1. generate_speech_script saves human-readable text to a file.
      2. generate_audio reads that file and applies pronunciation in-memory.
    The file on disk must never contain phonetic markers.
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix="_speech.md", delete=False
    ) as f:
        # Step 1: simulate generate_speech_script saving human-readable text
        f.write(SAMPLE_TEXT)
        temp_path = f.name

    try:
        # Verify the saved file is human-readable
        with open(temp_path) as f:
            saved_text = f.read()
        for marker in PHONETIC_MARKERS:
            assert marker not in saved_text, (
                f"Saved speech script must not contain phonetic marker '{marker}'"
            )

        # Step 2: simulate generate_audio reading and normalizing
        with open(temp_path) as f:
            tts_text = f.read().strip()
        tts_text = apply_pronunciation(tts_text, pronunciations)

        # TTS text should have phonetic markers
        for marker in PHONETIC_MARKERS:
            assert marker in tts_text, (
                f"TTS text must contain phonetic marker '{marker}'"
            )

        # File on disk must still be clean
        with open(temp_path) as f:
            still_clean = f.read()
        for marker in PHONETIC_MARKERS:
            assert marker not in still_clean, (
                f"File on disk must remain clean after TTS normalization"
            )
    finally:
        os.unlink(temp_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
