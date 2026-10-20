# SPDX-License-Identifier: Apache-2.0
"""Configuration fingerprints and an explicit, read-only import of Owen's first run.

The import reads saved public metadata and harness files. It never loads a model,
changes the old experiment, or infers its configuration from today's ``.env``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import ntpath
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .core import tree as HT
from .core.context import assert_tree_has_no_grader_verdicts
from .local_models import profile, profile_defaults, validate_api_base_url
from .run_story import public_text
from .substrate.opencode import OPENCODE_SURFACE

MAX_SOURCE_BYTES = 2 * 1024 * 1024
CHUNK_BYTES = 8 * 1024 * 1024
OWEN_RUN = "runs/local-model-64k"
SUMMARY = "jb/adapt/jb-oc-agg1/technical_writers__task1/summary.json"
HASH_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
LABEL_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")

# These are public Studio fields, not an unrestricted environment snapshot. Keep
# this module independent of the UI package so CLI imports do not create UI state.
SETTING_ENV = {
    "project": "GOOGLE_CLOUD_PROJECT",
    "region": "GOOGLE_CLOUD_LOCATION",
    "solver": "HARNESS2_SOLVER_MODEL",
    "improver": "HARNESS2_IMPROVER_MODEL",
    "judge": "HARNESS2_JUDGE_MODEL",
    "local_url": "HARNESS2_LOCAL_BASE_URL",
    "local_model": "HARNESS2_LOCAL_MODEL",
    "local_context": "HARNESS2_LOCAL_CONTEXT",
    "local_output": "HARNESS2_LOCAL_OUTPUT",
    "local_runtime": "HARNESS2_LOCAL_RUNTIME",
    "local_executable": "HARNESS2_LOCAL_EXECUTABLE",
    "local_weights": "HARNESS2_LOCAL_WEIGHTS",
    "codex_url": "HARNESS2_CODEX_BASE_URL",
    "codex_model": "HARNESS2_CODEX_MODEL",
    "provider": "HARNESS2_UI_PROVIDER",
}


def _text(value, limit=2048):
    return public_text(value, limit) if isinstance(value, str) else ""


def _stat_identity(value):
    return tuple(getattr(value, key) for key in (
        "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"
    ))


def _unchanged_file(path_before, opened_before, opened_after, path_after):
    # On Python 3.12 Windows, path.stat() and os.fstat() can expose different
    # st_ctime semantics (creation time versus handle change time). Compare each
    # source against itself, then compare stable cross-source identity fields.
    return (_stat_identity(path_before) == _stat_identity(path_after)
            and _stat_identity(opened_before) == _stat_identity(opened_after)
            and _stat_identity(path_before)[:4] == _stat_identity(opened_before)[:4])


def _fingerprint(value: str) -> dict:
    """Hash one actual file, rejecting replacement or mutation during the read."""
    result = {"path": value or None, "size_bytes": None, "sha256": None,
              "status": "unknown"}
    if not value:
        result["reason"] = "No local file was specified."
        return result
    try:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError("Not an absolute path")
        path = path.resolve(strict=True)
        path_before = path.stat()
        if not stat.S_ISREG(path_before.st_mode):
            raise ValueError("Not a regular file")
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("Not a regular file")
            digest = hashlib.sha256()
            while block := stream.read(CHUNK_BYTES):
                digest.update(block)
            after = os.fstat(stream.fileno())
        if not _unchanged_file(path_before, before, after, path.stat()):
            result["reason"] = "The file changed while it was being fingerprinted."
            return result
        result.update({"path": str(path), "size_bytes": before.st_size,
                       "sha256": digest.hexdigest(), "status": "hashed",
                       "mtime_ns": before.st_mtime_ns})
    except (OSError, ValueError):
        result["reason"] = "The specified local file could not be fingerprinted."
    return result


def _annotation(path: str) -> dict:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    match = re.search(r"(?i)(?:UD-)?(?:IQ|Q)\d[\w-]*(?=\.gguf$)", name)
    return {"filename": name or None,
            "quantization_label": match.group(0) if match else None,
            "source": "filename annotation only; quantization metadata was not inspected"}


def _settings(environment: Mapping[str, str]) -> dict:
    settings = {}
    for field, name in SETTING_ENV.items():
        value = environment.get(name, "") or ""
        if (not isinstance(value, str) or len(value) > 2048
                or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise ValueError("Configuration contains an invalid public setting.")
        settings[field] = value.strip()
    # Reject credential-bearing URLs before a snapshot or an error can echo them.
    for field in ("local_url", "codex_url"):
        if settings[field]:
            settings[field] = validate_api_base_url(settings[field])
    secrets = _private_values(environment)
    if any(secret in value for value in settings.values() for secret in secrets):
        raise ValueError("A public configuration setting contains authentication material.")
    return settings


def _private_values(environment: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(sorted({value for key, value in environment.items()
                         if isinstance(value, str) and len(value) >= 4 and value != "local"
                         and any(word in key.upper() for word in (
                             "API_KEY", "ACCESS_TOKEN", "AUTH_TOKEN", "SECRET", "PASSWORD"
                         ))}, key=len, reverse=True))


def _policy_public(value, private_values: tuple[str, ...], *, depth=0):
    """Redact before hashing: authentication rotation is not a new tool policy."""
    if depth > 10:
        raise ValueError("Declared OpenCode configuration is nested too deeply.")
    if value is None or isinstance(value, bool):
        return value
    if type(value) in (int, float):
        if type(value) is float and not math.isfinite(value):
            raise ValueError("Declared OpenCode configuration needs finite numbers.")
        return value
    if isinstance(value, str):
        if len(value) > 32768:
            raise ValueError("Declared OpenCode configuration text exceeds the capture limit.")
        for secret in private_values:
            value = value.replace(secret, "[redacted]")
        if value.startswith(("http://", "https://")):
            try:
                parsed = urlsplit(value)
                host = parsed.hostname or ""
                if ":" in host:
                    host = "[" + host + "]"
                if parsed.port:
                    host += ":" + str(parsed.port)
                value = urlunsplit((parsed.scheme, host, parsed.path, "", ""))
            except ValueError:
                value = "[invalid endpoint]"
        return value
    if isinstance(value, list):
        if len(value) > 1024:
            raise ValueError("Declared OpenCode configuration has too many entries.")
        return [_policy_public(row, private_values, depth=depth + 1) for row in value]
    if isinstance(value, dict):
        if len(value) > 1024:
            raise ValueError("Declared OpenCode configuration has too many fields.")
        result = {}
        for name, row in value.items():
            normalized = re.sub(r"[^a-z]", "", name.lower())
            private = (normalized in {"key", "token", "auth", "authorization", "cookie", "headers"}
                       or any(part in normalized for part in (
                           "apikey", "accesstoken", "refreshtoken", "authtoken", "secret",
                           "password", "credential"
                       )))
            public_name = _policy_public(name, private_values, depth=depth + 1)
            result[public_name] = ("[redacted]" if private else
                                   _policy_public(row, private_values, depth=depth + 1))
        return result
    raise ValueError("Declared OpenCode configuration must contain JSON data.")


def _canonical_digest(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _public_permission(value):
    # Only decision modes are public here. Command/path patterns and arbitrary
    # string contents remain represented by the private-safe configuration hash.
    if isinstance(value, str):
        return value if value in {"allow", "ask", "deny"} else None
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if re.fullmatch(r"[a-zA-Z0-9_*-]{1,80}", key):
                mode = _public_permission(item)
                if mode is not None:
                    result[key] = mode
        return result
    return None


def tool_policy_fingerprint(environment: Mapping[str, str]) -> dict:
    """Fingerprint declared OpenCode overrides without storing their secret text.

    OpenCode's global configuration, discovered plugins, and their dependencies
    are outside this bounded capture. The digest describes the declared sources,
    not a claim that every effective runtime tool has been observed.
    """
    private = _private_values(environment)
    sources = {}
    file_info = {"path": None, "sha256": None, "status": "not_declared",
                 "hash_kind": "canonical public JSON with authentication redacted"}
    declared_file = environment.get("OPENCODE_CONFIG")
    if declared_file:
        if (not isinstance(declared_file, str) or len(declared_file) > 2048
                or any(ord(char) < 32 for char in declared_file)):
            raise ValueError("The declared OpenCode configuration path is invalid.")
        path = Path(declared_file).expanduser().resolve()
        file_info["path"] = _policy_public(str(path), private)
        try:
            path_before = path.stat()
            if not stat.S_ISREG(path_before.st_mode):
                raise ValueError("Not a regular file")
            with path.open("rb") as stream:
                before = os.fstat(stream.fileno())
                raw = stream.read(128 * 1024 + 1)
                after = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or len(raw) > 128 * 1024
                    or not _unchanged_file(path_before, before, after, path.stat())):
                raise ValueError("Unstable or oversized file")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("Not an object")
            sources["file"] = _policy_public(value, private)
            file_info.update({"sha256": _canonical_digest(sources["file"]), "status": "captured"})
        except (OSError, ValueError, UnicodeError):
            # No file content or parser exception (which can quote secrets) escapes.
            raise ValueError("The declared OpenCode configuration file could not be captured.") from None
    inline = environment.get("OPENCODE_CONFIG_CONTENT")
    if inline:
        try:
            if not isinstance(inline, str) or len(inline.encode("utf-8")) > 128 * 1024:
                raise ValueError("Oversized inline configuration")
            value = json.loads(inline)
            if not isinstance(value, dict):
                raise ValueError("Not an object")
            sources["inline"] = _policy_public(value, private)
        except (ValueError, UnicodeError):
            raise ValueError("The declared inline OpenCode configuration could not be captured.") from None
    tools, permission = {}, None
    for value in sources.values():
        gate = value.get("tools")
        if isinstance(gate, dict):
            tools.update({name: enabled for name, enabled in gate.items()
                          if re.fullmatch(r"[a-zA-Z0-9_*-]{1,80}", name) and type(enabled) is bool})
        if "permission" in value:
            permission = _public_permission(value["permission"])
    return {"substrate": "OpenCode",
            "declared_configuration_sha256": _canonical_digest(sources) if sources else None,
            "configuration_file": file_info, "policy": {"tools": tools, "permission": permission}
            if sources else None,
            "status": "declared_sources_captured" if sources else "not_declared",
            "limitations": "Global OpenCode configuration and plugin file contents/dependencies "
                           "were not captured; endpoint tool availability was not inspected."}


def capture_configuration(environment: Mapping[str, str]) -> dict:
    """Capture public settings and local file bytes, without executing anything.

    Missing external/cloud weights, runtime versions, and tool policies remain
    unknown. A filename's quantization label is not verified model metadata.
    Credentials, arbitrary environment variables, and provider JSON are omitted.
    """
    settings = _settings(environment)
    local = profile(environment)
    resolved = profile_defaults(environment) if local else dict(environment)
    settings["provider"] = settings["provider"] or (
        "vertex" if settings["solver"].startswith("google-vertex/") else
        "local" if local else "custom"
    )
    settings["local_runtime"] = settings["local_runtime"] or "external"
    settings["region"] = settings["region"] or "global"
    if local:
        settings["local_context"] = str(local.context)
        settings["local_output"] = str(local.output)
        for role in ("solver", "improver", "judge"):
            settings[role] = resolved.get(SETTING_ENV[role], "")
    managed = settings["provider"] == "local" and settings["local_runtime"] == "managed"
    weights = _fingerprint(settings["local_weights"])
    weights["annotation"] = _annotation(settings["local_weights"])
    runtime = _fingerprint(settings["local_executable"])
    runtime.update({"version": None,
                    "version_status": "unknown; executable was not invoked",
                    "dependencies_status": "shared-library bytes were not captured"})
    identity = {
        "capture_kind": "current_setup_files",
        "weights": weights,
        "runtime": runtime,
        "roles": {role: settings[role] or None for role in ("solver", "improver", "judge")},
        "budgets": {"context": local.context if local else None,
                    "output": local.output if local else None},
        "reasoning": {"mode": "off" if managed else None,
                      "source": "managed llama.cpp startup recipe" if managed else
                      "unknown; the external provider was not inspected"},
        "tools": tool_policy_fingerprint(environment),
    }
    return {"settings": settings, "identity": identity}


def _safe_path(root: Path, relative: str) -> Path:
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Historical source is outside the selected workspace.")
    current = path
    while current != root:
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise ValueError("Historical import refuses linked source paths.")
        current = current.parent
    return path


def _source(root: Path, relative: str, *, optional=False) -> tuple[dict, dict | None]:
    path = _safe_path(root, relative)
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_SOURCE_BYTES + 1)
        if len(raw) > MAX_SOURCE_BYTES:
            raise ValueError("Historical metadata exceeds the import size limit.")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("Historical metadata must contain a JSON object.")
        return value, {"path": relative, "sha256": hashlib.sha256(raw).hexdigest()}
    except FileNotFoundError:
        if optional:
            return {}, None
        raise ValueError("Required historical metadata is missing.") from None
    except (OSError, json.JSONDecodeError, UnicodeError):
        raise ValueError("Historical metadata could not be read.") from None


def _recorded_file(value, fallback_path="") -> dict:
    value = value if isinstance(value, dict) else {}
    digest = value.get("sha256")
    recorded_path = _text(value.get("resolved_path"))
    path_agrees = (not fallback_path or bool(recorded_path)
                   and ntpath.normcase(ntpath.normpath(recorded_path))
                   == ntpath.normcase(ntpath.normpath(fallback_path)))
    verified = (isinstance(digest, str) and bool(HASH_PATTERN.fullmatch(digest))
                and value.get("pre_post_stat_unchanged") is True and path_agrees)
    size = value.get("size_bytes")
    return {"path": recorded_path if path_agrees and recorded_path else fallback_path or None,
            "size_bytes": size if type(size) is int and size >= 0 and path_agrees else None,
            "sha256": digest if verified else None,
            "status": "recorded_during_run" if verified else "unknown",
            "captured_at": _text(value.get("captured_utc")) or None}


def _historic_configuration(manifest: dict, hashes: dict, config: dict) -> dict:
    recorded = manifest.get("profile_identity")
    if not isinstance(recorded, dict):
        raise ValueError("The historical server identity is missing.")
    base, model = recorded.get("base_url"), recorded.get("model")
    context, output = recorded.get("context"), recorded.get("output")
    if (not isinstance(base, str) or not isinstance(model, str) or not model
            or any(char.isspace() for char in model)
            or type(context) is not int or type(output) is not int
            or not 0 < output < context):
        raise ValueError("The historical server identity is invalid.")
    expected_identity = {"base_url": validate_api_base_url(base), "model": model,
                         "context": context, "output": output}
    for source, key in ((hashes, "profile_identity"), (config, "local_profile")):
        if key in source:
            identity = source[key]
            if not isinstance(identity, dict):
                raise ValueError("Historical model identity evidence disagrees between sources.")
            candidate = {field: identity.get(field) for field in expected_identity}
            if isinstance(candidate["base_url"], str):
                candidate["base_url"] = validate_api_base_url(candidate["base_url"])
            if candidate != expected_identity:
                raise ValueError("Historical model identity evidence disagrees between sources.")
    # This environment is assembled exclusively from old, saved metadata.
    saved = {"HARNESS2_UI_PROVIDER": "local", "HARNESS2_LOCAL_RUNTIME": "external",
             "HARNESS2_LOCAL_BASE_URL": base, "HARNESS2_LOCAL_MODEL": model,
             "HARNESS2_LOCAL_CONTEXT": str(context), "HARNESS2_LOCAL_OUTPUT": str(output),
             "HARNESS2_LOCAL_WEIGHTS": _text(manifest.get("model_file")),
             "HARNESS2_LOCAL_EXECUTABLE": _text(manifest.get("executable")),
             "GOOGLE_CLOUD_LOCATION": _text(config.get("location")) or "global"}
    for role, key in (("solver", "executor"), ("improver", "improver"), ("judge", "judge")):
        value = _text(config.get(key))
        if value:
            saved[SETTING_ENV[role]] = value
    # Resolve public defaults, without calling capture_configuration: importing old
    # bytes must never substitute the files that happen to exist on disk today.
    resolved = profile_defaults(saved)
    settings = _settings(resolved)
    files = hashes.get("files")
    files = [row for row in files if isinstance(row, dict)] if isinstance(files, list) else []
    model_file = next((row for row in files if row.get("kind") == "model"), {})
    runtime_file = next((row for row in files if row.get("kind") == "runtime"), {})
    weights = _recorded_file(model_file, settings["local_weights"])
    weights["annotation"] = _annotation(settings["local_weights"])
    runtime = _recorded_file(runtime_file, settings["local_executable"])
    runtime.update({"version": _text(manifest.get("version")) or None,
                    "version_source": "saved server manifest",
                    "dependencies": [_recorded_file(row) for row in files
                                     if row.get("kind") == "runtime_dll"]})
    reasoning = _text(manifest.get("thinking")) or None
    return {"settings": settings, "identity": {
        "capture_kind": "historical_saved_evidence",
        "captured_at": _text(hashes.get("captured_utc")) or None,
        "weights": weights, "runtime": runtime,
        "roles": {role: settings[role] or None for role in ("solver", "improver", "judge")},
        "budgets": {"context": context, "output": output},
        "reasoning": {"mode": reasoning, "source": "saved server manifest"},
        "tools": {"substrate": "OpenCode", "policy": None,
                  "status": "exact historical tool policy was not captured here"},
        "limitations": ["Historical file hashes are saved observations, not a new verification "
                        "of the entire runtime at launch.",
                        "Settings describe the original external server; automatic managed "
                        "lifetime is a separate future configuration."],
    }}


def _tree(root: Path, relative: str) -> dict[str, str]:
    directory = _safe_path(root, relative)
    if not directory.is_dir():
        raise ValueError("A historical harness snapshot is missing.")
    result = {}
    total = 0
    for parent, directories, files in os.walk(directory, followlinks=False):
        if len(result) + len(directories) + len(files) > 1000:
            raise ValueError("Historical harness exceeds the file limit.")
        for name in directories + files:
            path = Path(parent) / name
            _safe_path(root, path.relative_to(root).as_posix())
        for name in sorted(files):
            path = Path(parent) / name
            rel = path.relative_to(directory).as_posix()
            if rel in {stub for stub, _ in OPENCODE_SURFACE.stubs}:
                continue
            if not HT.is_allowed(OPENCODE_SURFACE, rel):
                raise ValueError("Historical harness contains a file outside its editable surface.")
            with path.open("rb") as stream:
                raw = stream.read(MAX_SOURCE_BYTES + 1)
            total += len(raw)
            if total > MAX_SOURCE_BYTES:
                raise ValueError("Historical harness exceeds the size limit.")
            try:
                result[rel] = raw.decode("utf-8")
            except UnicodeError:
                raise ValueError("Historical harness contains a non-text file.") from None
    if not result:
        raise ValueError("A historical harness snapshot is empty.")
    HT.validate(OPENCODE_SURFACE, result)
    assert_tree_has_no_grader_verdicts(result)
    return result


def _tree_refs(relative: str, tree: dict[str, str]) -> list[dict]:
    return [{"path": relative + "/" + name,
             "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()}
            for name, body in sorted(tree.items())]


def _note_once(store, profile_id, *, text, references, evidence_status="observed"):
    if not any(row.get("text") == text for row in store.get(profile_id).get("notes", [])):
        store.add_note(profile_id, text=text, evidence_status=evidence_status,
                       references=references)


def import_owen(workspace_root: Path, store) -> dict:
    """Explicitly seed Owen's lineage from the completed 64K experiment.

    Originals remain read-only. Repeated imports retain current selections and
    reuse immutable snapshots rather than append duplicate notes or run records.
    """
    root = Path(workspace_root).resolve()
    run = OWEN_RUN
    manifest, manifest_ref = _source(root, run + "/evidence/server-manifest.json")
    hashes, hashes_ref = _source(root, run + "/evidence/runtime-model-bytes.json", optional=True)
    completed, completed_ref = _source(root, run + "/evidence/completed-workflow-status.json")
    config, config_ref = _source(root, run + "/jb/adapt/jb-oc-agg1/config.json", optional=True)
    summary, summary_ref = _source(root, run + "/" + SUMMARY)
    summary_ref["evidence_status"] = "observed"
    if (summary.get("task_id") != "technical_writers/task1" or summary.get("k") != 1
            or summary.get("status") != "ok" or completed.get("workflow_stage") != "completed"):
        raise ValueError("Historical sources do not identify the completed first 64K experiment.")
    selections = summary.get("selections")
    selection = selections.get("agg", {}) if isinstance(selections, dict) else {}
    if not isinstance(selection, dict):
        raise ValueError("The historical selected harness is missing.")
    chosen = selection.get("chosen_label")
    if not isinstance(chosen, str) or not LABEL_PATTERN.fullmatch(chosen):
        raise ValueError("The historical selected harness label is invalid.")
    snapshot = _historic_configuration(manifest, hashes, config)
    refs = [row for row in (manifest_ref, hashes_ref, completed_ref, config_ref, summary_ref) if row]
    snapshot["identity"]["sources"] = refs
    harness_root = run + "/" + SUMMARY.rsplit("/", 1)[0] + "/harnesses/"
    # Read every required source before creating any profile state.
    baseline_tree = _tree(root, harness_root + "base")
    selected_tree = _tree(root, harness_root + chosen)
    modes = summary.get("modes")
    mode = modes.get("agg", {}) if isinstance(modes, dict) else {}
    stages = mode.get("stages", []) if isinstance(mode, dict) else []
    stages = [row for row in stages if isinstance(row, dict)] if isinstance(stages, list) else []
    domain_stage = next((row for row in stages if row.get("label") == "dom1"), None)
    invariant_stage = next((row for row in stages if row.get("label") == "inv1"), {})
    domain_tree = (_tree(root, harness_root + "dom1") if domain_stage
                   and domain_stage.get("fell_back") is False else None)
    matches = [row for row in store.list() if row.get("id") == "owen"
               or str(row.get("name", "")).casefold() == "owen"]
    if len(matches) > 1:
        raise ValueError("More than one Owen profile exists; historical import is ambiguous.")
    created = not matches
    record = (store.create("Owen", profile_id="owen", description=(
        "A continuing local-model lineage, beginning with the first completed 64K "
        "technical-writing experiment. Configurations remain separate from identity."
    )) if created else store.get(matches[0]["id"]))
    if str(record.get("name", "")).casefold() != "owen":
        raise ValueError("The Owen profile ID already belongs to another identity.")
    profile_id = record["id"]
    configuration = store.add_configuration(profile_id, snapshot,
                                            label="First 64K experiment · external server")
    baseline = store.add_revision(profile_id, baseline_tree, label="Chapter one · original baseline",
                                  operation="baseline", domain="technical_writers",
                                  evidence=refs + _tree_refs(harness_root + "base", baseline_tree),
                                  why="Preserved original baseline from the first completed 64K run.")
    domain = None
    if domain_tree is not None:
        domain = store.add_revision(profile_id, domain_tree,
                                    label="Chapter one · domain proposal", parents=[baseline["id"]],
                                    domain="technical_writers", operation="adapt",
                                    evidence=refs + _tree_refs(harness_root + "dom1", domain_tree),
                                    why="Accepted domain proposal within this run. Its probe retained "
                                        "partial output; cross-task benefit is unproven.")
    experience = selection.get("experience")
    experience = ({
        key: _text(experience.get(key), 16000)
        for key in ("cue", "lesson", "evidence", "confidence", "edits")
    } if isinstance(experience, dict) else {})
    parents = [baseline["id"]] + ([domain["id"]] if domain else [])
    invariant_fact = ("the invariant proposal timed out and fell back to baseline"
                      if invariant_stage.get("fell_back") is True
                      and "timeout" in str(invariant_stage.get("error", "")).lower()
                      else "the invariant proposal fell back to baseline"
                      if invariant_stage.get("fell_back") is True
                      else "the invariant proposal's disposition remains in the saved run")
    final = store.add_revision(profile_id, selected_tree,
                               label="Chapter one · composed " + chosen, parents=parents,
                               domain="technical_writers", operation="compose",
                               evidence=refs + _tree_refs(harness_root + chosen, selected_tree),
                               experience=experience,
                               why="Adopted within one run. Composition retained domain guidance; "
                                   + invariant_fact + ". "
                                   "Benefit across tasks is unproven.")
    note_refs = [*refs, {"run": run, "revision_id": final["id"],
                         "configuration_id": configuration["id"]}]
    _note_once(store, profile_id, references=note_refs, text=(
        "Chapter one: the completed first 64K technical-writing experiment is preserved with "
        "its baseline, selected composition, public outcome metadata, and experience reflection. "
        "The accepted domain proposal and invariant fallback are retained separately from the "
        "final selection. Intermediate probe results must be read with their partial-output "
        "qualifications. These are observations from one task, not replicated or transferred "
        "improvements."
    ))
    quality = completed.get("quality_result")
    if isinstance(quality, dict) and quality.get("graded") is True:
        score = quality.get("weighted_percent")
        score_text = f"{score:.2f}%" if type(score) in (int, float) and 0 <= score <= 100 else "recorded"
        _note_once(store, profile_id, references=note_refs, text=(
            f"Historical evaluation: selected output received {score_text} from the same local "
            "model acting as judge. The baseline was not graded, so benefit over baseline "
            "is unknown. The original Windows judge failed on fcntl, then completed after "
            "a guarded native portability repair. This was an engineering smoke test with "
            "possible exposure to earlier public task history, not a controlled comparison. "
            "Evaluation information is historical annotation and is not injected into the harness."
        ))
    _note_once(store, profile_id, references=note_refs, evidence_status="suspected", text=(
        "Unresolved hypothesis: source-hierarchy and verification guidance may transfer to other "
        "technical-writing tasks. Test on new tasks against the preserved baseline, including "
        "time and context costs. Remove or simplify components that do not earn their cost."
    ))
    reference = {"import_key": "owen-first-64k", "output_dir": str(root / run),
                 "job_id": hashlib.sha256(os.path.normcase(str((root / run).resolve()))
                                          .encode("utf-8")).hexdigest()[:12],
                 "title": "Chapter one · first completed 64K technical-writing experiment",
                 "configuration_id": configuration["id"], "revision_id": final["id"],
                 "baseline_revision_id": baseline["id"], "source": "explicit historical import",
                 "status": "completed", "task_id": "technical_writers/task1", "sources": refs,
                 "benefit_over_baseline": "unknown; baseline was not graded",
                 "cross_task_benefit": "unproven"}
    if not any(row.get("import_key") == reference["import_key"]
               for row in store.get(profile_id).get("runs", [])):
        store.record_run(profile_id, reference)
    if created:
        store.activate(profile_id, configuration_id=configuration["id"], revision_id=final["id"],
                       why="Explicitly imported Owen's first chapter; adopted within one run, "
                           "with cross-task benefit unproven.")
    warnings = []
    if not hashes_ref:
        warnings.append("Historical model/runtime hashes are missing; those identities remain unknown.")
    elif any(snapshot["identity"][kind]["status"] == "unknown" for kind in ("weights", "runtime")):
        warnings.append("Some historical file hashes do not verify the manifest paths; "
                        "those file identities remain unknown.")
    return {"profile_id": profile_id, "created": created, "configuration_id": configuration["id"],
            "baseline_revision_id": baseline["id"],
            "domain_revision_id": domain["id"] if domain else None, "revision_id": final["id"],
            "source_run": run, "source_files_unchanged": True,
            "active_selection_preserved": not created, "warnings": warnings}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owen", action="store_true", required=True,
                        help="Import the saved first 64K experiment as Owen's first chapter")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    arguments = parser.parse_args(argv)
    from .profiles import ProfileStore

    try:
        report = import_owen(arguments.workspace, ProfileStore(arguments.workspace))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Historical import failed: {exc}\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
