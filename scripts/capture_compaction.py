#!/usr/bin/env python3
"""Capture one OpenCode session without changing its live SQLite database.

The SQLite backup API produces an internally consistent, in-memory snapshot,
including committed WAL records. Only the requested session is exported. Model
reasoning parts are represented by metadata in session.json. An explicit opt-in
can additionally preserve a verbatim, session-scoped raw JSON export locally.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def visible(value):
    """Keep exposed content while excluding separate or tagged reasoning."""
    if isinstance(value, dict):
        if value.get("type") == "reasoning":
            return {
                "type": "reasoning",
                "omitted": True,
                "characters": len(value.get("text", "")),
                "time": value.get("time"),
            }
        return {key: visible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [visible(item) for item in value]
    if isinstance(value, str):
        return re.sub(
            r"<(?:think|thinking)>.*?(?:</(?:think|thinking)>|\Z)",
            "[model reasoning omitted]",
            value,
            flags=re.DOTALL | re.IGNORECASE,
        )
    return value


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def capture(
    db: Path,
    session_id: str,
    output: Path,
    boundary: str | None,
    sources: list[Path],
    workspace: Path | None = None,
    preserve_raw_session: bool = False,
) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    stamp = datetime.now(timezone.utc).isoformat()
    source = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    frozen = sqlite3.connect(":memory:")
    try:
        source.backup(frozen)
    finally:
        source.close()
    frozen.row_factory = sqlite3.Row
    try:
        session = frozen.execute("SELECT * FROM session WHERE id=?", (session_id,)).fetchone()
        if session is None:
            raise ValueError(f"Session not present: {session_id}")
        messages = []
        raw_messages = []
        for row in frozen.execute(
            "SELECT * FROM message WHERE session_id=? ORDER BY time_created,id", (session_id,)
        ):
            message = dict(row)
            raw_data = json.loads(message["data"])
            message["data"] = visible(raw_data)
            message["parts"] = []
            raw_message = {**message, "data": raw_data, "parts": []}
            for part_row in frozen.execute(
                "SELECT * FROM part WHERE session_id=? AND message_id=? ORDER BY time_created,id",
                (session_id, message["id"]),
            ):
                part = dict(part_row)
                raw_part_data = json.loads(part["data"])
                raw_message["parts"].append({**part, "data": raw_part_data})
                part["data"] = visible(raw_part_data)
                message["parts"].append(part)
            messages.append(message)
            raw_messages.append(raw_message)
        todos = [
            dict(row)
            for row in frozen.execute(
                "SELECT * FROM todo WHERE session_id=? ORDER BY position", (session_id,)
            )
        ]
    finally:
        frozen.close()

    payload = {
        "captured_utc": stamp,
        "session": dict(session),
        "messages": messages,
        "todos": todos,
    }
    write_json(output / "session.json", payload)
    if preserve_raw_session:
        write_json(output / "session.raw.json", {**payload, "messages": raw_messages})
    if workspace:
        inventory = []
        for path in sorted(workspace.rglob("*")):
            if path.is_file() and not path.is_symlink():
                data = path.read_bytes()
                inventory.append(
                    {
                        "path": path.relative_to(workspace).as_posix(),
                        "bytes": len(data),
                        "sha256": digest(data),
                        "mtime_ns": path.stat().st_mtime_ns,
                    }
                )
                if path.is_relative_to(workspace / "output"):
                    copied = output / "workspace-output" / path.relative_to(workspace / "output")
                    copied.parent.mkdir(parents=True, exist_ok=True)
                    copied.write_bytes(data)
        write_json(
            output / "workspace-state.json",
            {
                "captured_utc": datetime.now(timezone.utc).isoformat(),
                "workspace": str(workspace.resolve()),
                "files": inventory,
                "output_directory_exists": (workspace / "output").is_dir(),
                "note": "Filesystem observed immediately after database backup, not an atomic cross-process snapshot. Output bytes copied; other workspace files inventoried only.",
            },
        )
    compactions = [message for message in messages if message["data"].get("summary") is True]
    selected = (
        next((message for message in compactions if message["id"] == boundary), None)
        if boundary
        else next(iter(compactions), None)
    )
    timeline = []
    for message in messages:
        data = message["data"]
        timeline.append(
            {
                "id": message["id"],
                "created": message["time_created"],
                "updated": message["time_updated"],
                "role": data.get("role"),
                "agent": data.get("agent"),
                "summary": data.get("summary", False),
                "provider": data.get("providerID", data.get("model", {}).get("providerID")),
                "model": data.get("modelID", data.get("model", {}).get("modelID")),
                "finish": data.get("finish"),
                "time": data.get("time"),
                "tokens": data.get("tokens"),
                "error": data.get("error"),
                "tools": [
                    part["data"] for part in message["parts"] if part["data"].get("type") == "tool"
                ],
            }
        )
    write_json(output / "timeline.json", timeline)
    status = {
        "boundary": selected["id"] if selected else None,
        "summary_generation_finished": False,
        "resumed_assistant_messages": 0,
    }
    if selected:
        index = messages.index(selected)
        trigger_index = max(0, index - 1)
        write_json(
            output / "pre-boundary.json",
            {
                "messages": messages[max(0, trigger_index - 3) : index],
                "original_request": messages[0],
            },
        )
        write_json(output / "compaction.json", selected)
        summary = "\n\n".join(
            part["data"].get("text", "")
            for part in selected["parts"]
            if part["data"].get("type") == "text"
        )
        (output / "compacted-context.txt").write_text(summary, encoding="utf-8")
        resumed = messages[index + 1 :]
        write_json(output / "resumed-actions.json", {"messages": resumed[:12]})
        status.update(
            {
                "summary_generation_finished": bool(
                    selected["data"].get("time", {}).get("completed")
                ),
                "summary_characters": len(summary),
                "summary_finish": selected["data"].get("finish"),
                "summary_error": selected["data"].get("error"),
                "summary_accepted_by_filter": bool(selected["data"].get("finish"))
                and not selected["data"].get("error"),
                "summary_ended_normally": selected["data"].get("finish") == "stop"
                and not selected["data"].get("error"),
                "resumed_assistant_messages": sum(
                    message["data"].get("role") == "assistant" for message in resumed
                ),
            }
        )

    source_records = []
    for index, path in enumerate(sources):
        if not path.is_file():
            source_records.append({"source": str(path), "missing": True})
            continue
        # Capture a byte prefix of a growing file, with the exact length recorded.
        with path.open("rb") as handle:
            data = handle.read(path.stat().st_size)
        destination = output / "sources" / f"{index:02d}-{path.name}"
        destination.parent.mkdir(exist_ok=True)
        destination.write_bytes(data)
        source_records.append(
            {
                "source": str(path.resolve()),
                "captured_file": destination.relative_to(output).as_posix(),
                "bytes": len(data),
                "sha256": digest(data),
                "capture": "complete file or contemporaneous prefix if source was growing",
            }
        )

    write_json(
        output / "provenance.json",
        {
            "captured_utc": stamp,
            "source_database": str(db.resolve()),
            "source_session": session_id,
            "snapshot_method": "SQLite read-only URI connection -> sqlite3.Connection.backup into an in-memory database -> session-scoped export",
            "database_mutated": False,
            "reasoning_text_omitted_from_session_export": True,
            "raw_session_file": "session.raw.json" if preserve_raw_session else None,
            "reasoning_text_retained_in_raw_session_export": preserve_raw_session,
            "raw_provenance_sources_filtered": False,
            "raw_provenance_source_note": "Explicit --source files are preserved verbatim and may contain material omitted from the session export; choose only authorized, non-secret source files.",
            "scope": "session, its messages/parts/todos only; account, permission and unrelated session tables excluded",
            "sources": source_records,
            "status": status,
        },
    )
    manifest = []
    for path in sorted(output.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            manifest.append(
                {
                    "path": path.relative_to(output).as_posix(),
                    "bytes": len(data),
                    "sha256": digest(data),
                }
            )
    write_json(output / "SHA256.json", manifest)
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--session", required=True)
    parser.add_argument(
        "--output", required=True, type=Path, help="New output directory; never overwritten"
    )
    parser.add_argument(
        "--boundary", help="Compaction assistant message ID; defaults to first summary"
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        help="Explicit experiment workspace to inventory; copies output/ only",
    )
    parser.add_argument(
        "--preserve-raw-session",
        action="store_true",
        help="Also preserve unfiltered session.raw.json, including model reasoning; local authorized experiments only",
    )
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        type=Path,
        help="Explicit non-secret provenance file to preserve (repeatable)",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            capture(
                args.db,
                args.session,
                args.output,
                args.boundary,
                args.source,
                args.workspace,
                args.preserve_raw_session,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
