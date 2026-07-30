"""Convert browser mic blobs (WebM/Opus, mp4, ogg) to WAV for xAI STT.

xAI STT rejects WebM with: Unsupported or corrupt audio format: webm
Desktop Chrome MediaRecorder defaults to audio/webm;codecs=opus.
We always normalize to 16 kHz mono PCM WAV via ffmpeg before STT.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Prefer these when guessing extension from Content-Type / filename
_EXT_BY_HINT = {
    "webm": ".webm",
    "ogg": ".ogg",
    "opus": ".ogg",
    "mp4": ".mp4",
    "m4a": ".m4a",
    "mpeg": ".mp3",
    "mp3": ".mp3",
    "wav": ".wav",
    "x-wav": ".wav",
    "wave": ".wav",
}


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _guess_suffix(filename: str = "", content_type: str = "") -> str:
    name = (filename or "").lower()
    if "." in name:
        ext = "." + name.rsplit(".", 1)[-1]
        if ext in (".webm", ".ogg", ".opus", ".mp4", ".m4a", ".mp3", ".wav"):
            return ".ogg" if ext == ".opus" else ext
    ct = (content_type or "").lower()
    for key, ext in _EXT_BY_HINT.items():
        if key in ct:
            return ext
    return ".webm"


def to_wav_16k_mono(
    audio_bytes: bytes,
    filename: str = "audio.webm",
    content_type: str = "",
) -> Tuple[Optional[bytes], Optional[str]]:
    """
    Convert arbitrary browser audio to 16 kHz mono s16le WAV.

    Returns (wav_bytes, error_message). On success error is None.
    If input is already tiny/empty, returns an error without calling ffmpeg.
    """
    if not audio_bytes or len(audio_bytes) < 32:
        return None, "Empty or too-short audio recording"

    # Already WAV — still re-encode for sample rate consistency if ffmpeg present
    suffix = _guess_suffix(filename, content_type)
    if not ffmpeg_available():
        if suffix == ".wav":
            return audio_bytes, None
        return (
            None,
            "ffmpeg not installed in desk image — cannot convert WebM/Opus for xAI STT",
        )

    with tempfile.TemporaryDirectory(prefix="desk_audio_") as td:
        src = Path(td) / f"in{suffix}"
        dst = Path(td) / "out.wav"
        src.write_bytes(audio_bytes)
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(src),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(dst),
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=60,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return None, "ffmpeg timed out converting audio"
        except Exception as e:
            logger.warning("ffmpeg run failed: %s", e)
            return None, f"ffmpeg failed: {e}"

        if proc.returncode != 0 or not dst.is_file():
            err = (proc.stderr or b"").decode("utf-8", errors="replace")[:300]
            logger.warning("ffmpeg convert failed rc=%s %s", proc.returncode, err)
            return None, f"Could not decode audio ({suffix}): {err or 'ffmpeg error'}"

        wav = dst.read_bytes()
        if len(wav) < 44:
            return None, "Converted WAV is empty"
        return wav, None
