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

# ---------------------------------------------------------------- live settings
#
# These four were environment variables, which meant changing any of them needed a
# .env edit and a restart — reasonable for a server, useless in a desktop app where
# there is no terminal and "restart the engine" is not a thing a user can do.
#
# They are now settings held in the database, with the environment variable as the
# default. A deployment that sets CONVERTOR_CONCURRENCY still gets it; a user who
# changes the value in Settings gets it immediately, because every read goes
# through the accessors below rather than a constant captured at import.
#
# `_live` is a plain dict rather than a database read per call: these are consulted
# on hot paths (every upload chunk, every job), and it is refreshed whenever
# settings are written.

ENV_DEFAULTS: dict[str, object] = {
    "maxUploadMb": int(os.environ.get("CONVERTOR_MAX_UPLOAD_MB", "2048")),
    "workerConcurrency": int(os.environ.get("CONVERTOR_CONCURRENCY", "3")),
    "anthropicApiKey": os.environ.get("ANTHROPIC_API_KEY", "").strip(),
    "aiModel": os.environ.get("CONVERTOR_AI_MODEL", "claude-opus-5"),
}

_live: dict[str, object] = dict(ENV_DEFAULTS)


def apply_settings(settings: dict) -> None:
    """Adopt stored settings. Called at startup and after every write."""
    for key in ENV_DEFAULTS:
        if key in settings and settings[key] not in (None, ""):
            _live[key] = settings[key]
        else:
            _live[key] = ENV_DEFAULTS[key]


def max_upload_bytes() -> int:
    """Uploads larger than this are rejected with an actionable error rather than
    dying halfway through a write."""
    try:
        return max(1, int(_live["maxUploadMb"])) * 1024 * 1024
    except (TypeError, ValueError):
        return int(ENV_DEFAULTS["maxUploadMb"]) * 1024 * 1024  # type: ignore[arg-type]


def worker_concurrency() -> int:
    """How many conversion items run concurrently inside one job."""
    try:
        return max(1, min(16, int(_live["workerConcurrency"])))
    except (TypeError, ValueError):
        return 3


def anthropic_api_key() -> str:
    """Intelligent workflows are opt-in and stay unavailable without a key."""
    return str(_live.get("anthropicApiKey") or "").strip()


def ai_model() -> str:
    return str(_live.get("aiModel") or ENV_DEFAULTS["aiModel"])

# "docker" ships every pack in the image; "desktop" ships a base tier and
# fetches the rest on demand. Set by the Tauri sidecar launcher.
BUILD_KIND = os.environ.get("CONVERTOR_BUILD", "docker")

VERSION = "1.0.0"


def ensure_dirs() -> None:
    for path in (INBOX_DIR, OUTPUT_DIR, TMP_DIR, PACKS_DIR):
        path.mkdir(parents=True, exist_ok=True)
