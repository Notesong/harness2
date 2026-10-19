# SPDX-License-Identifier: Apache-2.0
"""Optional, immutable capability catalog; publishing never executes or adopts code.

The catalog is disabled until explicitly enabled. Global availability and each
profile's consent are independent. Administrative reads remain available while
disabled so old provenance is never hidden. Model-facing callers must also check
``effective_access`` before exposing entries. SHA256 objects and append-only,
hash-linked events share the profile archive's native locking/publication rules.

Evidence labels are declarations, not certifications. A selected configuration
is an association supplied by the publisher, not proof that it created the files.
"""

from __future__ import annotations

import re
from pathlib import Path

from .core import tree as HT
from .core.context import assert_tree_has_no_grader_verdicts
from .profiles import (
    EVIDENCE_STATUSES,
    MAX_EVENTS,
    MAX_TREE_BYTES,
    MAX_TREE_FILES,
    SCHEMA,
    CorruptProfileError,
    ProfileStore,
    _hash,
    _id,
    _json,
    _public,
    _sequence,
    _text,
)
from .substrate.opencode import OPENCODE_SURFACE

KINDS = frozenset({"script", "skill", "workflow", "guardrail", "plugin", "other"})
PORTABILITY = frozenset({"deterministic", "model-sensitive", "runtime-specific", "unknown"})
_EVENT = re.compile(r"([0-9]{8})-([a-f0-9]{64})\.json\Z")


class CorruptCapabilityError(CorruptProfileError):
    """A catalog object or event chain is incomplete or corrupt."""


class _Archive(ProfileStore):
    """Reuse bounded file I/O and lock ordering without exposing profile writes."""

    def __init__(self, workspace_root):
        super().__init__(workspace_root)
        self.root = self.workspace_root / ".harness2" / "capabilities"


def _files(tree: dict[str, str], paths) -> dict[str, str]:
    """Validate a standalone component, including explicit script dependencies."""
    if not isinstance(paths, (list, tuple)) or not 1 <= len(paths) <= MAX_TREE_FILES:
        raise ValueError("Select a bounded, nonempty list of capability files.")
    result, folded = {}, set()
    for path in paths:
        canonical = HT.normalize_rel(_text(path, "capability path", 240, empty=False))
        if canonical.casefold() in folded:
            raise ValueError("Capability paths collide on a case-insensitive filesystem.")
        folded.add(canonical.casefold())
        if not HT.is_allowed(OPENCODE_SURFACE, canonical) or canonical not in tree:
            raise ValueError("Capability files must exist in the selected harness revision.")
        result[canonical] = _text(tree[canonical], "capability file", 1_048_576)
    if sum(len(body.encode("utf-8")) for body in result.values()) > MAX_TREE_BYTES:
        raise ValueError("Capability files are too large.")
    # A registry item is not an active harness. Its skill can be valid without an
    # always-on trigger. Supply that trigger only to the existing validator; it
    # is neither saved nor installed. Missing script references still fail.
    validation = dict(result)
    body = HT.always_on_text(OPENCODE_SURFACE, validation)
    if len(body) > OPENCODE_SURFACE.always_on_max:
        raise ValueError("Capability always-on instructions exceed the harness context cap.")
    names = [name for name in HT.skill_names(OPENCODE_SURFACE, validation) if name not in body]
    injected = "\n" + " ".join(names) if names else ""
    if injected:
        validation[OPENCODE_SURFACE.autotrigger_file] = (
            validation.get(OPENCODE_SURFACE.autotrigger_file, "") + injected
        )
    HT.validate(OPENCODE_SURFACE, validation,
                always_on_max=OPENCODE_SURFACE.always_on_max + len(injected))
    assert_tree_has_no_grader_verdicts(result)
    return dict(sorted(result.items()))


def _assert_no_verdict_metadata(value) -> None:
    # JSON escaping can conceal a quoted marker inside free text. Scan the raw
    # leaf strings as well as the serialized packet, without interpreting it.
    strings, pending = [], [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            strings.append(item)
        elif isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    assert_tree_has_no_grader_verdicts({"packet.json": _json(value).decode("utf-8"),
                                      **{f"field-{i}.md": text for i, text in enumerate(strings)}})


class CapabilityStore:
    """Workspace-wide catalog. No model calls, imports, or execution occur here."""

    def __init__(self, workspace_root: Path | str):
        self._archive = _Archive(workspace_root)
        self.workspace_root = self._archive.workspace_root
        self.root = self._archive.root
        self.profiles = ProfileStore(self.workspace_root)

    def _object(self, entry_id: str) -> dict:
        value = self._archive._read(self.root / "objects" / f"{_id(entry_id, digest=True)}.json")
        if (value.get("schema") != SCHEMA or value.get("kind") != "capability"
                or not isinstance(value.get("data"), dict) or _hash(value) != entry_id):
            raise CorruptCapabilityError("Capability object failed its content hash or schema check.")
        data = value["data"]
        if (not isinstance(data.get("files"), dict)
                or data.get("content_hash") != _hash(data["files"])):
            raise CorruptCapabilityError("Capability files failed their content hash check.")
        return {"id": entry_id, **data}

    def _view(self) -> dict:
        view = {"enabled": False, "profile_access": {}, "effective_access": {},
                "entries": [], "events": []}
        event_dir = self._archive._safe(self.root / "events")
        if not event_dir.exists():
            return view
        if not event_dir.is_dir():
            raise CorruptCapabilityError("Capability event history is not a directory.")
        paths = sorted(event_dir.iterdir())
        if len(paths) > MAX_EVENTS + 10:
            raise CorruptCapabilityError("Capability history exceeds its limit.")
        entries = {}
        for path in paths:
            if path.name.startswith(".") and path.name.endswith(".tmp"):
                continue
            match = _EVENT.fullmatch(path.name)
            if not match:
                raise CorruptCapabilityError("Unrecognized capability event record.")
            event = self._archive._read(path)
            events = view["events"]
            if (int(match[1]) != len(events) + 1 or _hash(event) != match[2]
                    or event.get("schema") != SCHEMA or event.get("sequence") != len(events) + 1
                    or event.get("previous") != (events[-1]["id"] if events else None)):
                raise CorruptCapabilityError("Capability event history failed its sequence or hash check.")
            action, data = event.get("action"), event.get("data")
            try:
                if not isinstance(data, dict):
                    raise ValueError()
                if action == "enabled_changed":
                    if type(data["enabled"]) is not bool:
                        raise ValueError()
                    view["enabled"] = data["enabled"]
                elif action == "profile_access_changed":
                    profile_id = _id(data["profile_id"])
                    if type(data["allowed"]) is not bool:
                        raise ValueError()
                    view["profile_access"][profile_id] = data["allowed"]
                elif action == "published":
                    if data["id"] in entries:
                        raise ValueError()
                    entry = self._object(data["id"])
                    if any(parent not in entries for parent in entry["derived_from"]):
                        raise ValueError()
                    entry["created_at"] = event["time"]
                    entries[entry["id"]] = entry
                else:
                    raise ValueError()
            except (KeyError, TypeError, ValueError) as exc:
                raise CorruptCapabilityError("Capability event refers to invalid state.") from exc
            events.append({"id": match[2], "type": action, **event})
        view["entries"] = list(entries.values())
        view["effective_access"] = {
            profile_id: view["enabled"] and allowed
            for profile_id, allowed in view["profile_access"].items()
        }
        return view

    @staticmethod
    def _summary(entry: dict) -> dict:
        return {key: value for key, value in entry.items() if key != "files"} | {
            "paths": list(entry["files"]), "file_count": len(entry["files"]),
            "content_bytes": sum(len(body.encode("utf-8")) for body in entry["files"].values()),
        }

    def state(self) -> dict:
        """Administrative metadata; file bodies are returned only by ``get``."""
        with self._archive._locked():
            view = self._view()
            view["entries"] = [self._summary(entry) for entry in view["entries"]]
            return view

    def references_for(self, profile_id: str) -> list[dict] | None:
        """One coherent model-facing snapshot, or ``None`` when access is off."""
        profile_id = _id(profile_id)
        with self._archive._locked():
            view = self._view()
            if not view["enabled"] or not view["profile_access"].get(profile_id, False):
                return None
            return view["entries"]

    def set_enabled(self, enabled: bool) -> dict:
        if type(enabled) is not bool:
            raise ValueError("Catalog availability must be true or false.")
        with self._archive._locked():
            view = self._view()
            if view["enabled"] != enabled:
                self._archive._append(self.root, view["events"], "enabled_changed", {"enabled": enabled})
        return self.state()

    def set_profile_access(self, profile_id: str, allowed: bool) -> dict:
        if type(allowed) is not bool:
            raise ValueError("Profile catalog consent must be true or false.")
        profile_id = _id(profile_id)
        self.profiles.get(profile_id)
        with self._archive._locked():
            view = self._view()
            if profile_id not in view["profile_access"] or view["profile_access"][profile_id] != allowed:
                self._archive._append(self.root, view["events"], "profile_access_changed",
                                      {"profile_id": profile_id, "allowed": allowed})
        return self.state()

    def get(self, entry_id: str) -> dict:
        entry_id = _id(entry_id, digest=True)
        with self._archive._locked():
            view = self._view()
            for entry in view["entries"]:
                if entry["id"] == entry_id:
                    entry["events"] = [event for event in view["events"]
                                       if event["data"].get("id") == entry_id]
                    return entry
        raise ValueError("Capability does not exist.")

    def _revision_ancestors(self, profile: dict, revision: dict) -> list[str]:
        """Keep declared catalog transmission through later local/foreign edits."""
        profiles = {profile["id"]: profile}
        pending, seen, parents = [(profile["id"], revision["id"])], set(), []
        while pending:
            profile_id, revision_id = pending.pop()
            if (profile_id, revision_id) in seen:
                continue
            seen.add((profile_id, revision_id))
            if len(seen) > MAX_EVENTS:
                raise ValueError("Capability revision ancestry exceeds its traversal limit.")
            if profile_id not in profiles:
                profiles[profile_id] = self.profiles.get(profile_id)
            source = next((r for r in profiles[profile_id]["revisions"] if r["id"] == revision_id), None)
            if source is None:
                raise ValueError("Capability source revision ancestry is incomplete.")
            parents.extend(ref["capability_id"] for ref in source["evidence"]
                           if isinstance(ref, dict) and "capability_id" in ref)
            pending.extend((profile_id, parent) for parent in source["parents"])
            pending.extend((ref["profile_id"], ref["revision_id"]) for ref in source["borrowed_from"])
        return parents

    def publish(self, profile_id: str, revision_id: str, *, configuration_id: str,
                name: str, description: str = "", paths=(), kind: str,
                portability: str, why: str = "", evidence=(), evidence_status: str = "suspected",
                costs: dict | None = None, incompatibilities=(), failures=(), derived_from=(),
                motivated_by=()) -> dict:
        """Archive selected files and declarations; never activate or run them."""
        profile_id = _id(profile_id)
        revision_id, configuration_id = _id(revision_id, digest=True), _id(configuration_id, digest=True)
        if kind not in KINDS or portability not in PORTABILITY:
            raise ValueError("Choose a supported capability kind and portability class.")
        if evidence_status not in EVIDENCE_STATUSES:
            raise ValueError("Choose observed, suspected, replicated or transferred evidence.")
        evidence = _sequence(evidence, "capability evidence")
        reference_keys = {"path", "run_id", "job_id", "output_dir", "url", "capability_id",
                          "revision_id", "profile_id", "sha256", "reference"}
        if evidence_status != "suspected" and not any(
            isinstance(ref, dict) and any(isinstance(ref.get(key), str) and ref[key].strip()
                                         for key in reference_keys) for ref in evidence):
            raise ValueError("Stronger evidence labels require explicit supporting reference objects.")
        if costs is not None and not isinstance(costs, dict):
            raise ValueError("Capability costs must be an object.")
        data = {
            "name": _text(name, "capability name", 240, empty=False).strip(),
            "description": _text(description, "capability description"),
            "kind": kind, "portability": portability,
            "why": _text(why, "capability motivation"), "evidence": evidence,
            "evidence_status": evidence_status,
            "evidence_qualification": "Publisher claim; not independently verified by the catalog.",
            "costs": _public(costs or {}),
            "incompatibilities": _sequence(incompatibilities, "known incompatibilities"),
            "failures": _sequence(failures, "known failures"),
            "motivated_by": _sequence(motivated_by, "motivation references"),
            "configuration_association": (
                "Declared origin association; a configuration selection alone does not prove causality."
            ),
        }
        parents = _sequence(derived_from, "capability ancestors")
        for parent in parents:
            _id(parent, digest=True)
        with self._archive._locked():
            view = self._view()
            if not view["enabled"]:
                raise ValueError("Enable the capability catalog before publishing.")
            profile = self.profiles.get(profile_id)
            if not any(configuration["id"] == configuration_id
                       for configuration in profile["configurations"]):
                raise ValueError("Origin configuration must belong to the selected profile.")
            revision = next((r for r in profile["revisions"] if r["id"] == revision_id), None)
            if revision is None:
                raise ValueError("Origin revision must belong to the selected profile.")
            # Catalog forks record source IDs in revision evidence. Republishing
            # them cannot silently relabel their declared ancestry as independent.
            parents.extend(self._revision_ancestors(profile, revision))
            parents = list(dict.fromkeys(_id(parent, digest=True) for parent in parents))
            if any(parent not in {entry["id"] for entry in view["entries"]} for parent in parents):
                raise ValueError("Capability ancestors must already be published in this catalog.")
            files = _files(revision["tree"], paths)
            data.update(origin={"profile_id": profile_id, "profile_name": profile["name"],
                                "configuration_id": configuration_id, "revision_id": revision_id},
                        files=files, content_hash=_hash(files), derived_from=parents,
                        ancestry="derived" if parents else "declared-independent",
                        invention_qualification="Declared ancestry; independent invention is not certified.")
            data = _public(data)
            _assert_no_verdict_metadata(data)
            value = {"schema": SCHEMA, "kind": "capability", "data": data}
            entry_id = _hash(value)
            if not any(entry["id"] == entry_id for entry in view["entries"]):
                self._archive._publish(self.root / "objects" / f"{entry_id}.json", value)
                self._archive._append(self.root, view["events"], "published", {"id": entry_id})
        return self.get(entry_id)

    def fork(self, entry_id: str, target_profile_id: str, *, parent_revision_id: str | None = None,
             label: str = "", why: str = "", replace_files: dict | None = None,
             replace_paths=()) -> dict:
        """Copy into an unadopted candidate; imported claims never become local proof.

        The catalog lock stays held through profile publication, serializing this
        operation with disabling. Different target bytes require explicit consent
        for each path. ``replace_files`` can modify incoming files, not add code.
        """
        entry_id, target_profile_id = _id(entry_id, digest=True), _id(target_profile_id)
        label = _text(label, "fork label", 240)
        why = _text(why, "fork reason")
        if replace_files is not None and not isinstance(replace_files, dict):
            raise ValueError("Modified capability files must be an object.")
        replacements, folded = {}, set()
        for path, body in (replace_files or {}).items():
            canonical = HT.normalize_rel(_text(path, "modified capability path", 240, empty=False))
            if canonical.casefold() in folded:
                raise ValueError("Modified capability paths collide.")
            folded.add(canonical.casefold())
            replacements[canonical] = _text(body, "modified capability file", 1_048_576)
        overwrite = [HT.normalize_rel(_text(path, "replacement path", 240, empty=False))
                     for path in _sequence(replace_paths, "replacement paths")]
        if len(set(overwrite)) != len(overwrite):
            raise ValueError("Replacement paths cannot repeat.")
        with self._archive._locked():
            view = self._view()
            if not view["enabled"]:
                raise ValueError("Enable the capability catalog before forking.")
            entry = next((e for e in view["entries"] if e["id"] == entry_id), None)
            if entry is None:
                raise ValueError("Capability does not exist.")
            incoming = entry["files"]
            if set(replacements) - set(incoming) or set(overwrite) - set(incoming):
                raise ValueError("Modifications and replacement consent must name incoming files.")
            profile = self.profiles.get(target_profile_id)
            parent_revision_id = parent_revision_id or profile["active_revision_id"]
            parent = next((r for r in profile["revisions"] if r["id"] == parent_revision_id), None)
            if parent is None:
                raise ValueError("Choose an existing target profile harness revision to fork into.")
            files = _files(incoming | replacements, list(incoming))
            tree = dict(parent["tree"])
            existing = {path.casefold(): path for path in tree}
            for path, body in files.items():
                prior_path = existing.get(path.casefold())
                if prior_path is not None and tree[prior_path] != body and path not in overwrite:
                    raise ValueError("Capability conflicts with a target file; explicitly select replacement paths.")
                if prior_path is not None:
                    del tree[prior_path]
                tree[path] = body
            before_integration = dict(tree)
            repaired_skills = HT.autotrigger(OPENCODE_SURFACE, tree)
            integrated_paths = [path for path in tree if tree[path] != before_integration.get(path)]
            modified = sorted({path for path in files if files[path] != incoming[path]}
                              | set(integrated_paths))
            return self.profiles.add_revision(target_profile_id, tree,
                label=label or f"Try {entry['name']}", parents=[parent["id"]],
                borrowed_from=[{"profile_id": entry["origin"]["profile_id"],
                                "revision_id": entry["origin"]["revision_id"], "component": entry_id}],
                why=why or "A foreign capability hypothesis to test locally.",
                evidence=[*parent["evidence"], {
                    "capability_id": entry_id, "origin": entry["origin"],
                    "evidence_status": "suspected", "local_validation": "unvalidated",
                    "modified_files": modified,
                    "repaired_skills": repaired_skills,
                    "integration_changes": [{"path": path,
                        "reason": "Added skill discovery hint to the unadopted candidate."}
                        for path in integrated_paths],
                    "qualification": "Foreign capability; unvalidated locally.",
                }], domain=parent["domain"], operation="capability-fork",
                experience=parent["experience"])
