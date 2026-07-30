#!/usr/bin/env python3
"""Unit tests for desk audio → WAV conversion (ffmpeg when available)."""

import struct
import sys
import unittest
import wave
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mexc_bot.webapi.audio_convert import (  # noqa: E402
    ffmpeg_available,
    to_wav_16k_mono,
)


def _minimal_wav(seconds: float = 0.1, rate: int = 8000) -> bytes:
    n = int(rate * seconds)
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        # quiet sine-ish noise (not silence zeros only — still fine for convert)
        frames = b"".join(struct.pack("<h", int(1000 * ((i % 20) - 10))) for i in range(n))
        w.writeframes(frames)
    return buf.getvalue()


class TestAudioConvert(unittest.TestCase):
    def test_empty_rejected(self):
        wav, err = to_wav_16k_mono(b"", filename="x.webm")
        self.assertIsNone(wav)
        self.assertIsNotNone(err)

    def test_tiny_rejected(self):
        wav, err = to_wav_16k_mono(b"RIFF", filename="x.wav")
        self.assertIsNone(wav)
        self.assertIsNotNone(err)

    def test_wav_roundtrip_or_passthrough(self):
        raw = _minimal_wav()
        out, err = to_wav_16k_mono(raw, filename="tone.wav", content_type="audio/wav")
        if not ffmpeg_available():
            # Without ffmpeg, WAV bytes pass through
            self.assertIsNone(err)
            self.assertEqual(out, raw)
            return
        self.assertIsNone(err, err)
        self.assertIsNotNone(out)
        self.assertGreater(len(out), 44)
        # Valid WAV header
        self.assertTrue(out[:4] == b"RIFF")
        self.assertTrue(out[8:12] == b"WAVE")

    def test_garbage_webm_fails_cleanly_when_ffmpeg(self):
        if not ffmpeg_available():
            self.skipTest("ffmpeg not installed")
        junk = b"not a real webm container" + b"\x00" * 64
        out, err = to_wav_16k_mono(junk, filename="voice.webm")
        self.assertIsNone(out)
        self.assertIsNotNone(err)


if __name__ == "__main__":
    unittest.main()
