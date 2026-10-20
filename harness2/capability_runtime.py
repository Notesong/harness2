# SPDX-License-Identifier: Apache-2.0
"""Opted-in, frozen catalog references for proposer sessions; never auto-installed."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .capabilities import KINDS, PORTABILITY, CapabilityStore, _files
from .core.context import assert_tree_has_no_grader_verdicts
from .core.tree import normalize_rel
from .profiles import EVIDENCE_STATUSES, _hash, _id, _public

MAX_ENTRIES = 32
MAX_CATALOG_BYTES = 1024 * 1024
_SOURCE = re.compile(r"(?im)^\s*CAPABILITY_SOURCE:\s*([a-f0-9]{64})\s*$")
FIELDS = frozenset({
    "id", "name", "description", "why", "kind", "portability", "origin", "derived_from",
    "evidence_status", "evidence_qualification", "configuration_association", "ancestry",
    "invention_qualification", "costs", "incompatibilities", "failures", "files", "content_hash",
    "references",
})
REFERENCE_FIELDS = frozenset({
    "run_id", "job_id", "profile_id", "configuration_id", "revision_id", "task_id", "sha256", "path",
})


def _project(entry: dict) -> dict:
    packet = {key: value for key, value in entry.items() if key in FIELDS and key != "references"}
    references = []
    for ref in entry.get("evidence", []):
        if isinstance(ref, dict):
            public = {key: value for key, value in ref.items()
                      if key in REFERENCE_FIELDS and isinstance(value, str)}
            if public:
                references.append(public)
    packet["references"] = references
    return packet


def validate_catalog_snapshot(value: dict) -> dict:
    if (not isinstance(value, dict) or set(value) != {"version", "entries"}
            or value.get("version") != 1 or not isinstance(value.get("entries"), list)
            or not 1 <= len(value["entries"]) <= MAX_ENTRIES):
        raise ValueError("The pinned capability catalog is malformed or exceeds its entry limit.")
    public = _public(value)
    serialized = json.dumps(public, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > MAX_CATALOG_BYTES:
        raise ValueError("The opted-in catalog exceeds its context budget; select a smaller catalog first.")
    seen = set()
    for entry in public["entries"]:
        if not isinstance(entry, dict) or set(entry) - FIELDS:
            raise ValueError("The pinned capability packet has unsupported fields.")
        entry_id = _id(entry.get("id"), digest=True)
        if entry_id in seen:
            raise ValueError("The pinned catalog repeats a capability.")
        seen.add(entry_id)
        if (not isinstance(entry.get("name"), str) or not entry["name"].strip()
                or len(entry["name"]) > 240):
            raise ValueError("A pinned capability needs a nonempty name.")
        for field in ("description", "why", "evidence_qualification", "configuration_association",
                      "ancestry", "invention_qualification"):
            if field in entry and not isinstance(entry[field], str):
                raise ValueError("Pinned capability descriptions must be text.")
        if entry.get("kind") not in KINDS or entry.get("portability") not in PORTABILITY:
            raise ValueError("The pinned capability has an unsupported kind or portability class.")
        if entry.get("evidence_status") not in EVIDENCE_STATUSES:
            raise ValueError("The pinned capability has an unsupported evidence label.")
        origin = entry.get("origin")
        if (not isinstance(origin, dict) or set(origin) != {
            "profile_id", "profile_name", "configuration_id", "revision_id",
        }):
            raise ValueError("A pinned capability needs explicit source ancestry.")
        _id(origin["profile_id"])
        _id(origin["configuration_id"], digest=True)
        _id(origin["revision_id"], digest=True)
        if not isinstance(origin["profile_name"], str) or not origin["profile_name"].strip():
            raise ValueError("A pinned capability needs its source profile name.")
        references = entry.get("references", [])
        if not isinstance(references, list) or len(references) > 256:
            raise ValueError("Pinned capability references must be a bounded list.")
        for reference in references:
            if (not isinstance(reference, dict) or set(reference) - REFERENCE_FIELDS
                    or not all(isinstance(item, str) for item in reference.values())):
                raise ValueError("Pinned capability references contain unsupported metadata.")
            if "sha256" in reference:
                _id(reference["sha256"], digest=True)
            if "path" in reference:
                path = normalize_rel(reference["path"])
                if any(word in path.lower() for word in ("judged", "rubric", "answer_key", "grader")):
                    raise ValueError("Evaluation-report paths cannot enter model-facing catalog references.")
        files = entry.get("files")
        if not isinstance(files, dict) or not files:
            raise ValueError("A pinned capability needs its selected files.")
        _files(files, list(files))
        if entry.get("content_hash") != _hash(files):
            raise ValueError("The pinned capability files changed.")
        for parent in entry.get("derived_from", []):
            _id(parent, digest=True)
    def leaves(item):
        if isinstance(item, str):
            yield item
        elif isinstance(item, dict):
            for key, child in item.items():
                yield key
                yield from leaves(child)
        elif isinstance(item, list):
            for child in item:
                yield from leaves(child)

    assert_tree_has_no_grader_verdicts({"catalog.json": serialized,
                                      "catalog-text.md": "\n".join(leaves(public))})
    return public


def catalog_snapshot(workspace_root: Path, profile_id: str) -> dict | None:
    store = CapabilityStore(workspace_root)
    entries = store.references_for(profile_id)
    if not entries:
        return None
    if len(entries) > MAX_ENTRIES:
        raise ValueError("The opted-in catalog exceeds 32 entries; narrow its selection before a model run.")
    # Both exposure gates and the selected objects were read under one lock.
    # The result is a frozen starting condition, not a live subscription.
    return validate_catalog_snapshot({
        "version": 1, "entries": [_project(entry) for entry in entries],
    })


def populate_catalog_context(snapshot: dict, destination: Path) -> bool:
    value = snapshot.get("capabilities")
    if value is None:
        return False
    packet = validate_catalog_snapshot(value)
    destination.mkdir(parents=True, exist_ok=True)
    catalog = destination / "catalog"
    catalog.mkdir(exist_ok=True)
    index = [
        "# Optional capability references", "",
        "This profile explicitly opted into this frozen catalog for this experiment.",
        "Foreign capabilities are reference material, unvalidated locally. Nothing is installed automatically.",
        "You may ignore, copy, or adapt a component if the task evidence supports doing so.",
        "If you borrow one, include `CAPABILITY_SOURCE: <full capability ID>` in your final decision notes.",
        "That records a declaration of borrowing; availability alone does not establish use or benefit.", "",
    ]
    for entry in packet["entries"]:
        directory = catalog / "entries" / entry["id"]
        directory.mkdir(parents=True, exist_ok=True)
        metadata = {key: value for key, value in entry.items() if key != "files"}
        (directory / "CAPABILITY.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        for rel, body in entry["files"].items():
            path = directory / "files" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8", newline="\n")
        index.extend([
            f"## {entry['name']}", f"- ID: {entry['id']}",
            f"- Origin: {entry['origin']['profile_name']}",
            f"- Form: {entry['kind']}; portability: {entry['portability']}",
            f"- Publisher evidence claim: {entry['evidence_status']}; unvalidated for this profile.",
            f"- Packet: entries/{entry['id']}/CAPABILITY.json",
            f"- Files: entries/{entry['id']}/files/", "",
        ])
    (catalog / "INDEX.md").write_text("\n".join(index), encoding="utf-8")
    with (destination / "INDEX.md").open("a", encoding="utf-8") as stream:
        stream.write(
            "\n## Optional capability catalog\n"
            "Browse catalog/INDEX.md for explicitly opted-in foreign reference components.\n"
            "These are hypotheses, not inherited instructions or evidence of local benefit.\n"
        )
    return True


def declared_sources(snapshot: dict, notes: str) -> list[dict]:
    """Only explicit declarations for actually exposed entries establish borrowing."""
    catalog = snapshot.get("capabilities")
    if catalog is None:
        return []
    entries = {entry["id"]: entry for entry in validate_catalog_snapshot(catalog)["entries"]}
    result = []
    for entry_id in dict.fromkeys(_SOURCE.findall(notes)):
        if entry_id in entries:
            entry = entries[entry_id]
            result.append({
                "capability_id": entry_id, "origin": entry["origin"],
                "evidence_status": "suspected", "local_validation": "unvalidated",
                "qualification": "Model-declared borrowing; local benefit has not been established.",
            })
    return result
