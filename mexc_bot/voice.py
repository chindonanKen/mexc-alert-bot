"""Voice notes → text (optional). Soft-fail; FEATURE_VOICE.

Uses OpenAI-compatible Whisper API when VOICE_STT_API_KEY is set.
Local fallback: none (reply with setup help).
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Optional

import requests

try:
    import certifi

    _CA = certifi.where()
except Exception:  # pragma: no cover
    _CA = True

logger = logging.getLogger(__name__)


def transcribe_ogg_bytes(
    data: bytes,
    *,
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
    model: str = "whisper-1",
    timeout: float = 60.0,
) -> Optional[str]:
    """Return transcript text or None."""
    key = (api_key or os.getenv("VOICE_STT_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        return None
    base = (api_base or os.getenv("VOICE_STT_API_BASE") or "https://api.openai.com/v1").rstrip(
        "/"
    )
    url = f"{base}/audio/transcriptions"
    try:
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=True) as tmp:
            tmp.write(data)
            tmp.flush()
            tmp.seek(0)
            files = {"file": ("voice.ogg", tmp, "audio/ogg")}
            data_form = {"model": model}
            headers = {"Authorization": f"Bearer {key}"}
            resp = requests.post(
                url,
                headers=headers,
                data=data_form,
                files=files,
                timeout=timeout,
                verify=_CA,
            )
            if resp.status_code != 200:
                logger.warning("STT status=%s %s", resp.status_code, resp.text[:200])
                return None
            payload = resp.json()
            text = (payload.get("text") or "").strip()
            return text or None
    except Exception as e:
        logger.warning("STT failed: %s", e)
        return None
