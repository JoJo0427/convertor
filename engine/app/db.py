"""SQLite persistence for files, history, saved presets and settings.

Everything lives in one file inside the workspace so "delete my data" is a real,
verifiable action rather than a promise.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from . import config

_lock = threading.Lock()
_connection: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    format_id TEXT,
    size INTEGER NOT NULL DEFAULT 0,
    detection TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    finished_at REAL,
    status TEXT NOT NULL,
    target TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'each',
    preset TEXT,
    options TEXT NOT NULL DEFAULT '{}',
    inputs TEXT NOT NULL DEFAULT '[]',
    route TEXT NOT NULL DEFAULT '{}',
    summary TEXT NOT NULL DEFAULT '{}',
    output_dir TEXT
);

CREATE INDEX IF NOT EXISTS jobs_created_at ON jobs (created_at DESC);

CREATE TABLE IF NOT EXISTS presets (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    source_format TEXT,
    target_format TEXT NOT NULL,
    values_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

DEFAULT_SETTINGS: dict[str, Any] = {
    "keepHistory": True,
    "autoCleanTemp": True,
    "tempRetentionHours": 24,
    "outputRetentionDays": 0,  # 0 means keep outputs until cleared by hand
    "defaultPreset": "",
}


def _connect() -> sqlite3.Connection:
    global _connection
    if _connection is None:
        config.ensure_dirs()
        _connection = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _connection.row_factory = sqlite3.Row
        _connection.executescript(SCHEMA)
        _connection.commit()
    return _connection


def _execute(sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
    with _lock:
        conn = _connect()
        cursor = conn.execute(sql, tuple(params))
        rows = cursor.fetchall()
        conn.commit()
        return rows


async def execute(sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
    return await asyncio.to_thread(_execute, sql, params)


def init() -> None:
    _connect()


# ------------------------------------------------------------------------ files


async def save_file(record: dict) -> None:
    await execute(
        "INSERT OR REPLACE INTO files (id, name, path, format_id, size, detection, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (record["id"], record["name"], record["path"], record.get("formatId"),
         record.get("size", 0), json.dumps(record.get("detection", {})), time.time()),
    )


async def get_file(file_id: str) -> dict | None:
    rows = await execute("SELECT * FROM files WHERE id = ?", (file_id,))
    if not rows:
        return None
    row = rows[0]
    return {
        "id": row["id"], "name": row["name"], "path": row["path"],
        "formatId": row["format_id"], "size": row["size"],
        "detection": json.loads(row["detection"]), "createdAt": row["created_at"],
    }


async def delete_file(file_id: str) -> None:
    await execute("DELETE FROM files WHERE id = ?", (file_id,))


async def all_files() -> list[dict]:
    rows = await execute("SELECT * FROM files ORDER BY created_at DESC")
    return [
        {"id": r["id"], "name": r["name"], "path": r["path"],
         "formatId": r["format_id"], "size": r["size"],
         "detection": json.loads(r["detection"]), "createdAt": r["created_at"]}
        for r in rows
    ]


# ------------------------------------------------------------------- job history


async def record_job(job: dict) -> None:
    if not await get_setting("keepHistory"):
        return
    await execute(
        "INSERT OR REPLACE INTO jobs (id, created_at, finished_at, status, target, "
        "mode, preset, options, inputs, route, summary, output_dir) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            job["id"], job["createdAt"], job.get("finishedAt"), job["status"],
            job["target"], job.get("mode", "each"), job.get("preset"),
            json.dumps(job.get("options", {})),
            json.dumps(job.get("inputs", [])),
            json.dumps(job.get("route", {})),
            json.dumps(job.get("summary", {})),
            job.get("outputDir"),
        ),
    )


def _job_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "createdAt": row["created_at"],
        "finishedAt": row["finished_at"], "status": row["status"],
        "target": row["target"], "mode": row["mode"], "preset": row["preset"],
        "options": json.loads(row["options"]), "inputs": json.loads(row["inputs"]),
        "route": json.loads(row["route"]), "summary": json.loads(row["summary"]),
        "outputDir": row["output_dir"],
    }


async def history(
    *, query: str = "", target: str = "", status: str = "", limit: int = 100
) -> list[dict]:
    sql = "SELECT * FROM jobs WHERE 1 = 1"
    params: list = []
    if target:
        sql += " AND target = ?"
        params.append(target)
    if status:
        sql += " AND status = ?"
        params.append(status)
    if query:
        sql += " AND (inputs LIKE ? OR target LIKE ?)"
        params += [f"%{query}%", f"%{query}%"]
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return [_job_row(r) for r in await execute(sql, params)]


async def recent_targets(limit: int = 6) -> list[dict]:
    """Output formats used most recently. Derived from history rather than kept
    as separate state, so clearing history genuinely clears this too."""
    if not await get_setting("keepHistory"):
        return []
    rows = await execute(
        "SELECT target, COUNT(*) AS uses, MAX(created_at) AS last_used "
        "FROM jobs GROUP BY target ORDER BY last_used DESC LIMIT ?",
        (limit,),
    )
    return [
        {"formatId": r["target"], "uses": r["uses"], "lastUsed": r["last_used"]}
        for r in rows
    ]


async def get_job_record(job_id: str) -> dict | None:
    rows = await execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    return _job_row(rows[0]) if rows else None


async def delete_job_record(job_id: str) -> None:
    await execute("DELETE FROM jobs WHERE id = ?", (job_id,))


async def clear_history() -> int:
    rows = await execute("SELECT COUNT(*) AS n FROM jobs")
    count = rows[0]["n"] if rows else 0
    await execute("DELETE FROM jobs")
    return count


# ----------------------------------------------------------------- user presets


async def save_preset(preset: dict) -> None:
    await execute(
        "INSERT OR REPLACE INTO presets (id, label, description, source_format, "
        "target_format, values_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (preset["id"], preset["label"], preset.get("description", ""),
         preset.get("sourceFormat"), preset["targetFormat"],
         json.dumps(preset.get("values", {})), time.time()),
    )


async def user_presets(target: str | None = None) -> list[dict]:
    if target:
        rows = await execute(
            "SELECT * FROM presets WHERE target_format = ? ORDER BY created_at DESC",
            (target,),
        )
    else:
        rows = await execute("SELECT * FROM presets ORDER BY created_at DESC")
    return [
        {"id": r["id"], "label": r["label"], "description": r["description"],
         "sourceFormat": r["source_format"], "targetFormat": r["target_format"],
         "values": json.loads(r["values_json"]), "createdAt": r["created_at"],
         "custom": True}
        for r in rows
    ]


async def delete_preset(preset_id: str) -> None:
    await execute("DELETE FROM presets WHERE id = ?", (preset_id,))


# --------------------------------------------------------------------- settings


async def get_settings() -> dict:
    rows = await execute("SELECT key, value FROM settings")
    stored = {r["key"]: json.loads(r["value"]) for r in rows}
    return {**DEFAULT_SETTINGS, **stored}


async def get_setting(key: str) -> Any:
    return (await get_settings()).get(key)


async def update_settings(values: dict) -> dict:
    for key, value in values.items():
        if key not in DEFAULT_SETTINGS:
            continue
        await execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, json.dumps(value)),
        )
    return await get_settings()
