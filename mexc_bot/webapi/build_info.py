"""Build identity for GET /api/health.

Lets later checks compare “live already has X” without printing secrets.
Prefer env injected at image build (GIT_SHA / IMAGE_TAG). Optional git
rev-parse for local desk. Never reads tokens.
"""

from __future__ import annotations

import os
import subprocess
from typing import Optional


def git_sha() -> Optional[str]:
    env = (
        os.getenv("GIT_SHA")
        or os.getenv("GIT_COMMIT")
        or os.getenv("SOURCE_COMMIT")
        or ""
    ).strip()
    if env:
        return env
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        sha = out.decode("utf-8", errors="replace").strip()
        return sha or None
    except Exception:
        return None


def image_tag() -> Optional[str]:
    tag = (
        os.getenv("IMAGE_TAG")
        or os.getenv("DESK_IMAGE_TAG")
        or os.getenv("DESK_IMAGE")
        or ""
    ).strip()
    return tag or None


def build_identity() -> dict:
    return {"git_sha": git_sha(), "image_tag": image_tag()}
