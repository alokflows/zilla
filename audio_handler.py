# ============================================================
#  AUDIO HANDLER — Voice & Audio Transcription
# ============================================================
#  Handles voice messages and audio files from Telegram.
#
#  Flow:
#  1. Download audio from Telegram
#  2. Save to AGI-Brain/Inbox/audio
#  3. Convert to WAV using pydub + portable ffmpeg
#  4. Transcribe using Google Speech Recognition (free, cloud)
#  5. Save transcript to Knowledge/transcripts
#  6. Send result back via Telegram
# ============================================================

import os
import logging
import tempfile

from config import FFMPEG_PATH, AGI_INBOX_AUDIO

logger = logging.getLogger(__name__)

# Configure pydub to use our portable ffmpeg
_ffmpeg_dir = os.path.dirname(FFMPEG_PATH)
if os.path.exists(FFMPEG_PATH):
    # Set environment for pydub
    os.environ["PATH"] = _ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")

try:
    from pydub import AudioSegment
    PYDUB_AVAILABLE = True
except ImportError:
    PYDUB_AVAILABLE = False
    logger.warning("[AUDIO] pydub not installed — audio conversion disabled")

try:
    import speech_recognition as sr
    SR_AVAILABLE = True
except ImportError:
    SR_AVAILABLE = False
    logger.warning("[AUDIO] SpeechRecognition not installed — transcription disabled")


def is_audio_capable() -> bool:
    """Check if audio processing is available."""
    return PYDUB_AVAILABLE and SR_AVAILABLE


def convert_to_wav(input_path: str) -> str | None:
    """
    Convert any audio file to WAV format for speech recognition.
    Returns path to the WAV file, or None on failure.
    """
    if not PYDUB_AVAILABLE:
        logger.error("[AUDIO] pydub not available for conversion")
        return None

    try:
        # Detect format from extension
        ext = os.path.splitext(input_path)[1].lower().lstrip(".")
        format_map = {
            "ogg": "ogg",
            "oga": "ogg",
            "opus": "ogg",
            "mp3": "mp3",
            "m4a": "mp4",
            "mp4": "mp4",
            "wav": "wav",
            "webm": "webm",
            "flac": "flac",
            "aac": "aac",
        }

        fmt = format_map.get(ext, ext)

        # Load and convert
        audio = AudioSegment.from_file(input_path, format=fmt)

        # Convert to mono 16kHz WAV (optimal for speech recognition)
        audio = audio.set_channels(1).set_frame_rate(16000)

        wav_path = os.path.splitext(input_path)[0] + ".wav"
        audio.export(wav_path, format="wav")
        logger.info(f"[AUDIO] Converted {input_path} → {wav_path}")
        return wav_path

    except Exception as e:
        logger.error(f"[AUDIO] Conversion failed: {e}", exc_info=True)
        return None


def transcribe_audio(audio_path: str) -> str | None:
    """
    Transcribe an audio file to text using Google Speech Recognition.
    Free, cloud-based, no API key needed. Works on 8GB RAM machines.

    Returns transcript text, or None on failure.
    """
    if not SR_AVAILABLE:
        return None

    # Convert to WAV if needed
    if not audio_path.lower().endswith(".wav"):
        wav_path = convert_to_wav(audio_path)
        if not wav_path:
            return None
    else:
        wav_path = audio_path

    try:
        recognizer = sr.Recognizer()

        with sr.AudioFile(wav_path) as source:
            # Adjust for ambient noise
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            audio_data = recognizer.record(source)

        # Use Google's free speech recognition
        text = recognizer.recognize_google(audio_data, language="en-US")
        logger.info(f"[AUDIO] Transcribed {len(text)} chars from {audio_path}")
        return text

    except sr.UnknownValueError:
        logger.warning("[AUDIO] Could not understand audio")
        return "[Could not understand the audio. Try speaking more clearly.]"
    except sr.RequestError as e:
        logger.error(f"[AUDIO] Google SR API error: {e}")
        return f"[Speech recognition service error: {e}]"
    except Exception as e:
        logger.error(f"[AUDIO] Transcription failed: {e}", exc_info=True)
        return f"[Transcription error: {e}]"
    finally:
        # Clean up temporary WAV if we created one
        if wav_path != audio_path and os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except Exception:
                pass


def get_audio_status() -> str:
    """Return status of audio processing capabilities."""
    parts = []
    if PYDUB_AVAILABLE:
        parts.append("pydub: OK")
    else:
        parts.append("pydub: MISSING")

    if SR_AVAILABLE:
        parts.append("SpeechRecognition: OK")
    else:
        parts.append("SpeechRecognition: MISSING")

    if os.path.exists(FFMPEG_PATH):
        parts.append("ffmpeg: OK")
    else:
        parts.append(f"ffmpeg: MISSING ({FFMPEG_PATH})")

    return " | ".join(parts)
