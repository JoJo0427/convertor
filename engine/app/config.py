"""Runtime configuration and workspace paths.

Everything the engine writes lives under WORKSPACE_ROOT so the privacy panel can
account for it honestly: what is stored, where, and what can be cleared.
"""

from __future__ import annotations

import os
from pathlib import Path

WORKSPACE_ROOT = Path(os.environ.get("CONVERTOR_WORKSPACE", "/data"))

INBOX_DIR = WORKSPACE_ROOT / "inbox"
OUTPUT_DIR = WORKSPACE_ROOT / "output"
TMP_DIR = WORKSPACE_ROOT / "tmp"
DB_PATH = WORKSPACE_ROOT / "convertor.db"

# Engine packs install here rather than inside the app bundle, so they survive an
# app update, can be removed without touching the install, and are accounted for
# by the storage panel like everything else.
PACKS_DIR = WORKSPACE_ROOT / "packs"

# Uploads larger than this are rejected with an actionable error rather than
# dying halfway through a write.
MAX_UPLOAD_BYTES = int(os.environ.get("CONVERTOR_MAX_UPLOAD_MB", "2048")) * 1024 * 1024

# How many conversion items run concurrently inside one job.
WORKER_CONCURRENCY = int(os.environ.get("CONVERTOR_CONCURRENCY", "3"))

# Phase 4 intelligent workflows are opt-in and stay unavailable without a key.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.environ.get("CONVERTOR_AI_MODEL", "claude-opus-5")

# "docker" ships every pack in the image; "desktop" ships a base tier and
# fetches the rest on demand. Set by the Tauri sidecar launcher.
BUILD_KIND = os.environ.get("CONVERTOR_BUILD", "docker")

VERSION = "1.0.0"


def ensure_dirs() -> None:
    for path in (INBOX_DIR, OUTPUT_DIR, TMP_DIR, PACKS_DIR):
        path.mkdir(parents=True, exist_ok=True)
