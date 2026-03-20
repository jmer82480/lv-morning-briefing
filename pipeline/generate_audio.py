"""Generate audio from the speech script using ElevenLabs TTS."""

import logging
import os
import time

from elevenlabs import ElevenLabs

logger = logging.getLogger("briefing")


def generate_audio(
    speech_script_path: str,
    date_str: str,
    base_dir: str,
    settings: dict,
) -> str:
    """Convert the speech script to audio using ElevenLabs TTS.

    Returns the path to the generated audio file.
    """
    with open(speech_script_path) as f:
        text = f.read().strip()

    if not text:
        raise ValueError(f"Speech script is empty: {speech_script_path}")

    voice = settings.get("tts", {}).get("voice", "onyx")

    logger.info(f"Generating audio ({len(text)} chars, voice: {voice})...")

    client = ElevenLabs()

    # Retry once on failure
    for attempt in range(2):
        try:
            audio_generator = client.text_to_speech.convert(
                text=text,
                voice_id=voice,
                model_id="eleven_multilingual_v2",
                output_format="mp3_44100_128",
            )

            # Collect all audio chunks
            audio_bytes = b"".join(audio_generator)
            break

        except Exception as e:
            if attempt == 0:
                logger.warning(f"TTS failed (attempt 1): {e}. Retrying in 5s...")
                time.sleep(5)
            else:
                logger.error(f"TTS failed after retry: {e}")
                raise

    # Save audio file
    out_dir = os.path.join(base_dir, "audio")
    os.makedirs(out_dir, exist_ok=True)

    filepath = os.path.join(out_dir, f"{date_str}_briefing.mp3")
    with open(filepath, "wb") as f:
        f.write(audio_bytes)

    size_mb = len(audio_bytes) / (1024 * 1024)
    logger.info(f"Saved audio to {filepath} ({size_mb:.1f} MB)")
    return filepath
