"""Generate audio from the speech script using ElevenLabs TTS."""

import logging
import os
import time

from elevenlabs import ElevenLabs

logger = logging.getLogger("briefing")

# ElevenLabs built-in voice IDs.
# Config can use either the friendly name or the raw ID.
# These are ElevenLabs' default voices; custom/cloned voices must use their ID directly.
VOICE_ID_MAP = {
    "rachel": "21m00Tcm4TlvDq8ikWAM",
    "drew": "29vD33N1CtxCmqQRPOHJ",
    "clyde": "2EiwWnXFnvU5JabPnv8n",
    "paul": "5Q0t7uMcjvnagumLfvZi",
    "domi": "AZnzlk1XvdvUeBnXmlld",
    "dave": "CYw3kZ02Hs0563khs1Fj",
    "fin": "D38z5RcWu1voky8WS1ja",
    "sarah": "EXAVITQu4vr4xnSDxMaL",
    "antoni": "ErXwobaYiN019PkySvjV",
    "thomas": "GBv7mTt0atIp3Br8iCZE",
    "charlie": "IKne3meq5aSn9XLyUdCD",
    "george": "JBFqnCBsd6RMkjVDRZzb",
    "emily": "LcfcDJNUP1GQjkzn1xUU",
    "elli": "MF3mGyEYCl7XYWbV9V6O",
    "callum": "N2lVS1w4EtoT3dr4eOWO",
    "patrick": "ODq5zmih8GrVes37Dizd",
    "harry": "SOYHLrjzK2X1ezoPC6cr",
    "liam": "TX3LPaxmHKxFdv7VOQHJ",
    "dorothy": "ThT5KcBeYPX3keUQqHPh",
    "josh": "TxGEqnHWrfWFTfGW9XjX",
    "arnold": "VR6AewLTigWG4xSOukaG",
    "charlotte": "XB0fDUnXU5powFXDhCwa",
    "alice": "Xb7hH8MSUJpSbSDYk0k2",
    "matilda": "XrExE9yKIg1WjnnlVkGX",
    "james": "ZQe5CZNOzWyzPSCn5a3c",
    "joseph": "Zlb1dXrM653N07WRdFW3",
    "jeremy": "bVMeCyTHy58xNoL34h3p",
    "michael": "flq6f7yk4E4fJM5XTYuZ",
    "ethan": "g5CIjZEefAph4nQFvHAz",
    "chris": "iP95p4xoKVk53GoZ742B",
    "brian": "nPczCjzI2devNBz1zQrb",
    "daniel": "onwK4e9ZLuTAKqWW03F9",
    "lily": "pFZP5JQG7iQjIQuC4Bku",
    "bill": "pqHfZKP75CvOlQylNhV4",
    "jessie": "t0jbNlBVZ17f02VDIeMI",
    "nicole": "piTKgcLEGmPE4e6mEKli",
    "adam": "pNInz6obpgDQGcFmaJgB",
    "sam": "yoZ06aMxZJJ28mfd3POQ",
}


def resolve_voice_id(voice: str) -> str:
    """Resolve a friendly voice name to an ElevenLabs voice ID.

    Accepts either a friendly name (e.g., 'rachel') or a raw voice ID.
    """
    # Check if it's a friendly name
    voice_id = VOICE_ID_MAP.get(voice.lower())
    if voice_id:
        return voice_id

    # Assume it's already a raw voice ID
    return voice


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

    voice_setting = settings.get("tts", {}).get("voice", "rachel")
    voice_id = resolve_voice_id(voice_setting)

    logger.info(f"Generating audio ({len(text)} chars, voice: {voice_setting} → {voice_id})...")

    client = ElevenLabs()

    # Retry once on failure
    for attempt in range(2):
        try:
            audio_generator = client.text_to_speech.convert(
                text=text,
                voice_id=voice_id,
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
