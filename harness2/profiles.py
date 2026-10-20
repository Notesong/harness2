# SPDX-License-Identifier: Apache-2.0
"""Durable model lineages, separate from disposable experiment working memory.

Schema 1 lives at ``<workspace>/.harness2/profiles/<slug>/``. ``objects/``
contains immutable, SHA256-addressed configuration and full harness revision JSON.
``events/`` contains immutable numbered JSON events linked by their SHA256 hashes.
An event publishes an object; an interrupted unpublished object is harmless. Active
configuration/revision pointers, notes, run references and retirement are derived
by replay, never by rewriting history. A workspace file lock serializes writers
and readers, including cross-profile imports. Atomic publication plus fsync avoids
partial records. Hashes detect corruption, not malicious rewriting by an owner.

Revisions form a graph with local parents and explicit foreign borrowing references.
Their full trees may grow or shrink; no old tree is deleted. Evidence classifications
and current adoption/retirement are independent. Credentials remain in workspace
settings, not this archive. This module never launches a model or edits a run.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .core import tree as HT
from .core.context import assert_tree_has_no_grader_verdicts
from .locking import atomic_replace, file_lock
from .substrate.opencode import OPENCODE_SURFACE

SCHEMA = 1
MAX_RECORD = 4 * 1024 * 1024
MAX_EVENTS = 100_000
MAX_TREE_FILES = 256
MAX_TREE_BYTES = 2 * 1024 * 1024
EVIDENCE_STATUSES = frozenset({"observed", "suspected", "replicated", "transferred"})
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_EVENT = re.compile(r"([0-9]{8})-([a-f0-9]{64})\.json\Z")
_SECRET_KEY = re.compile(
    r"(?:^|_)(?:api_?key|access_?token|refresh_?token|secret|password|credential|authorization)(?:_|$)",
    re.I,
)
_SETTINGS = frozenset({
    "project", "region", "solver", "improver", "judge", "local_url", "local_model",
    "local_context", "local_output", "local_runtime", "local_executable", "local_weights",
    "codex_url", "codex_model", "provider",
})


class CorruptProfileError(ValueError):
    """A stored record failed integrity or sequence validation."""


def _json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _hash(value) -> str:
    return hashlib.sha256(_json(value)).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value, field: str, limit: int = 16_384, *, empty: bool = True) -> str:
    if (not isinstance(value, str) or len(value) > limit or "\0" in value
            or (not empty and not value.strip())):
        raise ValueError(f"Invalid {field}.")
    return value


def _public(value, *, depth: int = 0):
    """Copy bounded JSON and reject credential fields/credential-bearing URLs."""
    if depth > 12:
        raise ValueError("Profile metadata is nested too deeply.")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Profile metadata needs finite numbers.")
        return value
    if isinstance(value, str):
        _text(value, "metadata text", 131_072)
        if value.startswith(("http://", "https://")):
            try:
                url = urlsplit(value)
                if url.username or url.password or url.query or url.fragment:
                    raise ValueError("Profile URLs cannot contain credentials, queries or fragments.")
            except ValueError:
                raise ValueError("Profile URL contains private or invalid information.") from None
        return value
    if isinstance(value, (list, tuple)):
        if len(value) > 1024:
            raise ValueError("Too many profile metadata entries.")
        return [_public(v, depth=depth + 1) for v in value]
    if isinstance(value, dict):
        if len(value) > 1024:
            raise ValueError("Too many profile metadata fields.")
        result = {}
        for key, item in value.items():
            _text(key, "metadata field", 256, empty=False)
            if _SECRET_KEY.search(key) or key.lower() in {"key", "token", "auth", "env"}:
                raise ValueError("Credentials and environment dumps cannot enter profile history.")
            result[key] = _public(item, depth=depth + 1)
        return result
    raise ValueError("Profile metadata must be JSON data.")


def _sequence(value, field: str) -> list:
    if not isinstance(value, (list, tuple)) or len(value) > 256:
        raise ValueError(f"Invalid {field}.")
    return _public(value)


def _id(value: str, *, digest: bool = False) -> str:
    if not isinstance(value, str) or not (_DIGEST if digest else _SLUG).fullmatch(value):
        raise ValueError("Invalid profile object ID." if digest else "Invalid profile ID.")
    if not digest:
        HT.normalize_rel(value)
    return value


def _tree(tree: dict[str, str]) -> dict[str, str]:
    if not isinstance(tree, dict) or len(tree) > MAX_TREE_FILES:
        raise ValueError("A harness revision needs a bounded file tree.")
    out, folded = {}, set()
    for name, body in tree.items():
        _text(name, "harness path", 240, empty=False)
        canonical = HT.normalize_rel(name)
        if canonical.casefold() in folded:
            raise ValueError("Harness paths collide on a case-insensitive filesystem.")
        folded.add(canonical.casefold())
        if not HT.is_allowed(OPENCODE_SURFACE, canonical):
            raise ValueError("Harness file is outside the OpenCode editable surface.")
        out[canonical] = _text(body, "harness file", 1_048_576)
    if sum(len(v.encode("utf-8")) for v in out.values()) > MAX_TREE_BYTES:
        raise ValueError("Harness revision is too large.")
    HT.validate(OPENCODE_SURFACE, out)
    assert_tree_has_no_grader_verdicts(out)
    return out


def _experience(value: dict | None) -> dict:
    if value is None:
        return {}
    if (not isinstance(value, dict)
            or set(value) - {"cue", "lesson", "evidence", "confidence", "edits"}):
        raise ValueError("Experience needs a single cue/lesson/evidence/confidence/edits entry.")
    result = {key: _text(body, "experience text") for key, body in value.items()}
    assert_tree_has_no_grader_verdicts({"experience.md": "\n".join(result.values())})
    return result


class ProfileStore:
    def __init__(self, workspace_root: Path | str):
        self.workspace_root = Path(workspace_root).resolve()
        self.root = self.workspace_root / ".harness2" / "profiles"

    def _safe(self, path: Path) -> Path:
        try:
            parts = path.relative_to(self.workspace_root).parts
        except ValueError:
            raise ValueError("Profile path leaves the workspace.") from None
        cursor = self.workspace_root
        for part in parts:
            cursor /= part
            try:
                info = cursor.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or (
                getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise ValueError("Profile storage cannot traverse symlinks or junctions.")
        return path

    def _mkdir(self, path: Path):
        self._safe(path).mkdir(parents=True, exist_ok=True)
        self._safe(path)

    @contextmanager
    def _locked(self):
        self._mkdir(self.root)
        with file_lock(self._safe(self.root / ".lock")):
            self._safe(self.root)
            yield

    def _dir(self, profile_id: str) -> Path:
        path = self._safe(self.root / _id(profile_id))
        if not path.is_dir():
            raise ValueError("Profile does not exist.")
        return path

    def _read(self, path: Path) -> dict:
        try:
            path = self._safe(path)
            if path.stat().st_size > MAX_RECORD:
                raise CorruptProfileError("Stored profile record is too large.")
            with path.open("rb") as handle:
                raw = handle.read(MAX_RECORD + 1)
            if len(raw) > MAX_RECORD:
                raise CorruptProfileError("Stored profile record is too large.")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise CorruptProfileError("Cannot read a valid profile record.") from exc

    def _publish(self, path: Path, value: dict):
        self._mkdir(path.parent)
        data = _json(value)
        if len(data) > MAX_RECORD:
            raise ValueError("Profile record is too large.")
        if self._safe(path).exists():
            if self._read(path) != value:
                raise CorruptProfileError("An immutable profile object has changed.")
            return
        temporary = self._safe(path.parent / f".{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            atomic_replace(temporary, self._safe(path))
        finally:
            temporary.unlink(missing_ok=True)

    def _object(self, directory: Path, object_id: str, kind: str) -> dict:
        value = self._read(directory / "objects" / f"{_id(object_id, digest=True)}.json")
        if _hash(value) != object_id or value.get("kind") != kind or value.get("schema") != SCHEMA:
            raise CorruptProfileError("Profile object failed its content hash or schema check.")
        return {"id": object_id, **value["data"]}

    def _append(self, directory: Path, events: list[dict], action: str, data: dict):
        if len(events) >= MAX_EVENTS:
            raise ValueError("Profile history needs archival before further additions.")
        value = {"schema": SCHEMA, "sequence": len(events) + 1, "previous":
                 events[-1]["id"] if events else None, "time": _now(), "action": action,
                 "data": data}
        digest = _hash(value)
        self._publish(directory / "events" / f"{len(events) + 1:08d}-{digest}.json", value)
        return digest

    def _view(self, profile_id: str) -> dict:
        directory = self._dir(profile_id)
        event_dir = self._safe(directory / "events")
        if not event_dir.is_dir():
            raise CorruptProfileError("Profile has no history.")
        paths = sorted(event_dir.iterdir())
        if len(paths) > MAX_EVENTS + 10:
            raise CorruptProfileError("Profile history exceeds its limit.")
        view = {"id": profile_id, "name": "", "description": "", "created_at": "",
                "configurations": [], "revisions": [], "events": [], "notes": [], "runs": [],
                "active_configuration_id": None, "active_revision_id": None}
        configurations, revisions, retired, runs = {}, {}, set(), {}
        for path in paths:
            if path.name.startswith(".") and path.name.endswith(".tmp"):
                continue
            match = _EVENT.fullmatch(path.name)
            if not match:
                raise CorruptProfileError("Unrecognized record in profile event history.")
            value = self._read(path)
            events = view["events"]
            if (int(match[1]) != len(events) + 1 or _hash(value) != match[2]
                    or value.get("sequence") != len(events) + 1
                    or value.get("schema") != SCHEMA
                    or value.get("previous") != (events[-1]["id"] if events else None)):
                raise CorruptProfileError("Profile event history failed its sequence or hash check.")
            action, data = value.get("action"), value.get("data")
            if not isinstance(data, dict) or (not events and action != "created"):
                raise CorruptProfileError("Profile event history is malformed.")
            try:
                if action == "created":
                    if events:
                        raise ValueError()
                    view.update(name=data["name"], description=data["description"],
                                created_at=value["time"])
                elif action in {"configuration_added", "revision_added"}:
                    kind = "configuration" if action == "configuration_added" else "revision"
                    obj = self._object(directory, data["id"], kind)
                    collection = configurations if kind == "configuration" else revisions
                    if obj["id"] in collection:
                        raise ValueError()
                    if kind == "revision" and any(p not in revisions for p in obj["parents"]):
                        raise ValueError()
                    obj["created_at"] = value["time"]
                    collection[obj["id"]] = obj
                elif action == "activated":
                    config_id, revision_id = data.get("configuration_id"), data.get("revision_id")
                    if config_id is not None:
                        if config_id not in configurations:
                            raise ValueError()
                        view["active_configuration_id"] = config_id
                    if revision_id is not None:
                        if revision_id not in revisions:
                            raise ValueError()
                        view["active_revision_id"] = revision_id
                        retired.discard(revision_id)
                elif action == "retired":
                    if data["revision_id"] not in revisions:
                        raise ValueError()
                    retired.add(data["revision_id"])
                    if view["active_revision_id"] == data["revision_id"]:
                        view["active_revision_id"] = None
                elif action in {"note_added", "run_recorded"}:
                    entry = {**data, "event_id": match[2], "created_at": value["time"]}
                    if action == "note_added":
                        view["notes"].append(entry)
                    else:
                        # A run publishes its start, then later observations. Its
                        # history remains intact; the browsing view shows latest.
                        key = ("job", data["job_id"]) if isinstance(data.get("job_id"), str) else (
                            ("output", data["output_dir"])
                            if isinstance(data.get("output_dir"), str) else ("event", match[2]))
                        runs[key] = {**runs.get(key, {}), **entry}
                else:
                    raise ValueError()
            except (KeyError, TypeError, ValueError) as exc:
                raise CorruptProfileError("Profile event refers to invalid state.") from exc
            events.append({"id": match[2], "type": action, **value})
        if not view["events"]:
            raise CorruptProfileError("Profile has no creation event.")
        view["configurations"] = list(configurations.values())
        view["runs"] = list(runs.values())
        for revision_id, revision in revisions.items():
            revision["retired"] = revision_id in retired
            revision["status"] = ("active" if view["active_revision_id"] == revision_id
                                  else "retired" if revision_id in retired else "candidate")
        view["revisions"] = list(revisions.values())
        return view

    def create(self, name: str, description: str = "", profile_id: str | None = None) -> dict:
        name = _text(name, "profile name", 120, empty=False).strip()
        description = _text(description, "profile description")
        if profile_id is None:
            ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
            profile_id = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:64] or "model"
        profile_id = _id(profile_id)
        with self._locked():
            directory = self._safe(self.root / profile_id)
            if directory.exists():
                raise ValueError("A profile with this ID already exists.")
            # Publish a complete initial history together. Interrupted staging
            # directories remain hidden and cannot reserve the requested ID.
            staging = self.root / f".create-{uuid.uuid4().hex}.tmp"
            self._mkdir(staging)
            self._append(staging, [], "created", {"name": name, "description": description})
            os.rename(self._safe(staging), self._safe(directory))
            return self._view(profile_id)

    def list(self) -> list[dict]:
        with self._locked():
            result = []
            for directory in sorted(self.root.iterdir()):
                if not _SLUG.fullmatch(directory.name):
                    continue
                self._safe(directory)
                if not directory.is_dir():
                    continue
                view = self._view(directory.name)
                result.append({key: view[key] for key in (
                    "id", "name", "description", "created_at", "active_configuration_id",
                    "active_revision_id")} | {f"{key}_count": len(view[key]) for key in (
                        "configurations", "revisions", "notes", "runs")})
            return result

    def get(self, profile_id: str) -> dict:
        with self._locked():
            return self._view(profile_id)

    def _add_object(self, profile_id: str, kind: str, data: dict) -> dict:
        view = self._view(profile_id)
        value = {"schema": SCHEMA, "kind": kind, "data": data}
        object_id = _hash(value)
        plural = "configurations" if kind == "configuration" else "revisions"
        for obj in view[plural]:
            if obj["id"] == object_id:
                return obj
        directory = self._dir(profile_id)
        self._publish(directory / "objects" / f"{object_id}.json", value)
        self._append(directory, view["events"], f"{kind}_added", {"id": object_id})
        return next(obj for obj in self._view(profile_id)[plural] if obj["id"] == object_id)

    def add_configuration(self, profile_id: str, snapshot: dict, *, label: str = "") -> dict:
        if (not isinstance(snapshot, dict) or set(snapshot) != {"settings", "identity"}
                or not isinstance(snapshot["settings"], dict)
                or not isinstance(snapshot["identity"], dict)
                or set(snapshot["settings"]) - _SETTINGS):
            raise ValueError("Configuration needs nonsecret settings and an identity snapshot.")
        data = {"label": _text(label, "configuration label", 240), "snapshot": _public(snapshot)}
        with self._locked():
            return self._add_object(profile_id, "configuration", data)

    def add_revision(self, profile_id: str, tree: dict[str, str], *, label: str = "",
                     parents=(), borrowed_from=(), why: str = "", evidence=(), domain: str = "",
                     operation: str = "adapt", motivated_by=(), experience: dict | None = None) -> dict:
        data = {"label": _text(label, "revision label", 240), "tree": _tree(tree),
                "parents": _sequence(parents, "revision parents"),
                "borrowed_from": _sequence(borrowed_from, "borrowing references"),
                "why": _text(why, "revision reason"), "evidence": _sequence(evidence, "evidence"),
                "domain": _text(domain, "domain", 240),
                "operation": _text(operation, "revision operation", 64, empty=False),
                "motivated_by": _sequence(motivated_by, "motivation references"),
                "experience": _experience(experience)}
        for parent in data["parents"]:
            _id(parent, digest=True)
        if len(set(data["parents"])) != len(data["parents"]):
            raise ValueError("A revision cannot repeat a parent.")
        with self._locked():
            local = {r["id"] for r in self._view(profile_id)["revisions"]}
            if any(_id(p, digest=True) not in local for p in data["parents"]):
                raise ValueError("A revision parent must already belong to this profile.")
            for ref in data["borrowed_from"]:
                if not isinstance(ref, dict) or set(ref) - {"profile_id", "revision_id", "component"}:
                    raise ValueError("Borrowed revisions need explicit profile and revision IDs.")
                foreign = self._view(_id(ref.get("profile_id")))
                foreign_id = _id(ref.get("revision_id"), digest=True)
                if not any(r["id"] == foreign_id for r in foreign["revisions"]):
                    raise ValueError("Borrowed revision does not exist.")
            return self._add_object(profile_id, "revision", data)

    def activate(self, profile_id: str, *, configuration_id: str | None = None,
                 revision_id: str | None = None, why: str = "") -> dict:
        if configuration_id is None and revision_id is None:
            raise ValueError("Choose a configuration or harness revision to activate.")
        data = {"configuration_id": configuration_id, "revision_id": revision_id,
                "why": _text(why, "activation reason")}
        with self._locked():
            view = self._view(profile_id)
            for value, collection in ((configuration_id, "configurations"), (revision_id, "revisions")):
                if value is not None and not any(obj["id"] == value for obj in view[collection]):
                    raise ValueError("Active selection must belong to this profile.")
            self._append(self._dir(profile_id), view["events"], "activated", data)
            return self._view(profile_id)

    def add_note(self, profile_id: str, *, text: str, evidence_status: str = "suspected",
                 references=(), motivated_by=()) -> dict:
        if evidence_status not in EVIDENCE_STATUSES:
            raise ValueError("Choose observed, suspected, replicated or transferred evidence.")
        data = {"text": _text(text, "profile note", empty=False), "evidence_status": evidence_status,
                "references": _sequence(references, "note references"),
                "motivated_by": _sequence(motivated_by, "motivation references")}
        with self._locked():
            view = self._view(profile_id)
            self._append(self._dir(profile_id), view["events"], "note_added", data)
            return self._view(profile_id)["notes"][-1]

    def retire(self, profile_id: str, revision_id: str, *, why: str) -> dict:
        data = {"revision_id": _id(revision_id, digest=True),
                "why": _text(why, "retirement reason", empty=False)}
        with self._locked():
            view = self._view(profile_id)
            if not any(r["id"] == revision_id for r in view["revisions"]):
                raise ValueError("Retired revision must belong to this profile.")
            self._append(self._dir(profile_id), view["events"], "retired", data)
            return self._view(profile_id)

    def record_run(self, profile_id: str, reference: dict) -> dict:
        if not isinstance(reference, dict) or not reference:
            raise ValueError("A run reference must be a nonempty object.")
        data = _public(reference)
        with self._locked():
            view = self._view(profile_id)
            event_id = self._append(self._dir(profile_id), view["events"], "run_recorded", data)
            return next(run for run in self._view(profile_id)["runs"] if run["event_id"] == event_id)
