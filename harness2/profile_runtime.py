# SPDX-License-Identifier: Apache-2.0
"""Freeze a profile into a run, outside the scheduler's mutable working library.

Only the pinned harness and its answer-free experience reach the adapter. Permanent
profile notes, grades, foreign profiles, and history remain outside model context.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Mapping
from pathlib import Path

from .core import context, tree
from .local_models import profile_defaults
from .locking import atomic_replace, file_lock
from .profiles import ProfileStore
from .substrate.opencode import OPENCODE_SURFACE

SNAPSHOT_ENV = "HARNESS2_PROFILE_SNAPSHOT"
SNAPSHOT_NAME = "profile-start.json"
MAX_SNAPSHOT = 4 * 1024 * 1024


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def _read(path: Path) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(MAX_SNAPSHOT + 1)
    if len(raw) > MAX_SNAPSHOT:
        raise ValueError("The profile snapshot is too large.")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("The profile snapshot must be an object.")
    return result


def pin_profile(
    store: ProfileStore, profile_id: str, output: Path, *,
    configuration_id: str | None = None, revision_id: str | None = None,
) -> dict:
    """Write a new run's immutable starting point; never follow active pointers later."""
    profile = store.get(profile_id)
    configuration_id = configuration_id or profile.get("active_configuration_id")
    revision_id = revision_id or profile.get("active_revision_id")
    configuration = next((row for row in profile["configurations"]
                          if row["id"] == configuration_id), None)
    revision = next((row for row in profile["revisions"] if row["id"] == revision_id), None)
    if not configuration or not revision:
        raise ValueError("Choose an active configuration and harness in this profile first.")
    if configuration["snapshot"]["settings"].get("provider", "local") != "local":
        raise ValueError("Profile experiments currently support local JobBench with OpenCode.")
    payload = {
        "version": 1, "profile_id": profile_id, "profile_name": profile["name"],
        "configuration_id": configuration_id, "revision_id": revision_id,
        "configuration": configuration["snapshot"], "harness": revision["tree"],
        "domain": revision.get("domain", ""), "experience": revision.get("experience", {}),
    }
    payload["snapshot_hash"] = _digest(payload)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    destination = output / SNAPSHOT_NAME
    with file_lock(output / ".profile-start.lock"):
        if destination.exists():
            if load_snapshot(destination) != payload:
                raise ValueError("This experiment already has a different profile starting point.")
            return payload
        temporary = output / f".{SNAPSHOT_NAME}-{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            atomic_replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return payload


def load_snapshot(path: Path) -> dict:
    path = Path(path)
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        raise ValueError("A profile snapshot must be a regular file inside its run.")
    result = _read(path)
    saved_hash = result.pop("snapshot_hash", None)
    if result.get("version") != 1 or saved_hash != _digest(result):
        raise ValueError("The pinned profile snapshot has changed or is unsupported.")
    result["snapshot_hash"] = saved_hash
    harness = result.get("harness")
    if not isinstance(harness, dict) or not all(
        isinstance(key, str) and isinstance(value, str) and tree.is_allowed(OPENCODE_SURFACE, key)
        for key, value in harness.items()
    ):
        raise ValueError("The pinned harness is not a valid OpenCode tree.")
    tree._normalized_tree(harness)
    context.assert_tree_has_no_grader_verdicts(harness)
    tree.validate(OPENCODE_SURFACE, harness)
    context.assert_tree_has_no_grader_verdicts({"EXPERIENCE.md": str(result.get("experience", {}))})
    return result


def snapshot_identity(environment: Mapping[str, str]) -> dict | None:
    name = environment.get(SNAPSHOT_ENV)
    if not name:
        return None
    pinned = load_snapshot(Path(name))
    return {key: pinned[key] for key in (
        "profile_id", "configuration_id", "revision_id", "snapshot_hash",
    )}


def configuration_environment(environment: Mapping[str, str], snapshot: dict) -> dict[str, str]:
    """Apply a saved recipe to a copy; authentication stays in local settings."""
    from .profile_import import SETTING_ENV

    result = dict(environment)
    settings = snapshot["settings"]
    # A different model must not inherit the previous model's resolved role aliases.
    for name in SETTING_ENV.values():
        # Empty values also prevent config.load_dotenv from filling a different
        # profile's absent fields from the workspace's default .env.
        result[name] = ""
    for name in ("HARNESS2_JUDGE_BASE_URL", "HARNESS2_VERTEX_PROJECT"):
        result[name] = ""
    for field, value in settings.items():
        if field in SETTING_ENV and value is not None:
            result[SETTING_ENV[field]] = str(value)
    return profile_defaults(result)


def verify_configuration(snapshot: dict, environment: Mapping[str, str]) -> None:
    """Detect a different GGUF or executable behind an unchanged model alias."""
    identity = snapshot.get("identity", {})
    tools = identity.get("tools", {})
    declared_policy = tools.get("declared_configuration_sha256")
    if "declared_configuration_sha256" in tools:
        from .profile_import import tool_policy_fingerprint

        if tool_policy_fingerprint(environment)["declared_configuration_sha256"] != declared_policy:
            raise ValueError("The declared OpenCode tool policy changed. Capture a new configuration.")
    for key, variable in (("weights", "HARNESS2_LOCAL_WEIGHTS"),
                          ("runtime", "HARNESS2_LOCAL_EXECUTABLE")):
        recorded = identity.get(key, {})
        expected = recorded.get("sha256") if isinstance(recorded, dict) else None
        if not expected:
            continue  # Historic/external identities explicitly retain unknown fields.
        path = Path(environment.get(variable, ""))
        if not path.is_file():
            raise ValueError(f"The profile's {key} file is missing. Capture a new configuration.")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != expected:
            raise ValueError(f"The profile's {key} file changed. Capture a new configuration.")


def run_environment(environment: Mapping[str, str], output: Path) -> dict[str, str]:
    path = Path(output) / SNAPSHOT_NAME
    snapshot = load_snapshot(path)
    result = configuration_environment(environment, snapshot["configuration"])
    result.update({SNAPSHOT_ENV: str(path.resolve()), "HARNESS2_RUNS_DIR": str(Path(output).resolve())})
    return result


class ProfileAdapter:
    """The adapter boundary selects a pinned baseline; recursion stays unchanged."""

    def __init__(self, adapter, snapshot: dict, output: Path):
        self._adapter = adapter
        self.snapshot = snapshot
        self._base = Path(output) / "profile-start" / "harness"
        if not self._base.resolve().is_relative_to(Path(output).resolve()):
            raise ValueError("The materialized profile baseline leaves its experiment.")
        for path in (self._base.parent, self._base):
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise ValueError("The materialized profile baseline cannot traverse links.")
        self._base.parent.mkdir(parents=True, exist_ok=True)
        expected = {path: "" for path in OPENCODE_SURFACE.always_on}
        expected.update(snapshot["harness"])
        if self._base.exists():
            for entry in self._base.rglob("*"):
                if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
                    raise ValueError("The materialized profile baseline contains a link.")
            if tree.read_tree(OPENCODE_SURFACE, self._base) != expected:
                raise ValueError("The materialized profile baseline changed during this run.")
        else:
            tree.write_tree(OPENCODE_SURFACE, snapshot["harness"], self._base)

    def __getattr__(self, name):
        value = getattr(self._adapter, name)
        if name == "execute_many":
            def execute_many(task_id, harness_dir, run_ids, **kwargs):
                return value(task_id, self._base if harness_dir is None else harness_dir,
                             run_ids, **kwargs)
            return execute_many
        return value

    def base_harness(self) -> Path:
        return self._base

    def execute(self, task_id, harness_dir, run_id, **kwargs):
        return self._adapter.execute(
            task_id, self._base if harness_dir is None else harness_dir, run_id, **kwargs
        )

    def populate_profile_context(self, task_id: str, destination: Path) -> bool:
        """An explicit answer-free seed copied into disposable proposer context only."""
        experience = self.snapshot.get("experience", {})
        if (not all(experience.get(key) for key in ("cue", "lesson", "evidence"))
                or self.snapshot.get("domain") != self.domain_of(task_id)):
            return False
        destination.mkdir(parents=True, exist_ok=True)
        chapter = destination / "profile-start"
        tree.write_tree(OPENCODE_SURFACE, self.snapshot["harness"], chapter / "harness")
        content = "\n".join(f"{key}: {value}" for key, value in experience.items())
        context.assert_tree_has_no_grader_verdicts({"EXPERIENCE.md": content})
        (chapter / "EXPERIENCE.md").write_text(content + "\n", encoding="utf-8")
        with (destination / "INDEX.md").open("a", encoding="utf-8") as stream:
            stream.write(
                "\n## Pinned profile experience\n"
                f"Profile: {self.snapshot['profile_name']}; revision: {self.snapshot['revision_id']}.\n"
                "This is an explicitly selected earlier experience; its benefit on this task is unproven.\n"
                "See profile-start/EXPERIENCE.md and profile-start/harness/.\n"
            )
        return True


def apply_profile(adapter, environment: Mapping[str, str] | None = None):
    environment = os.environ if environment is None else environment
    name = environment.get(SNAPSHOT_ENV)
    if not name:
        return adapter
    if adapter.spec.name != "jb" or adapter.substrate.name != "oc":
        raise ValueError("Profile experiments currently support local JobBench with OpenCode.")
    path = Path(name).resolve()
    output = Path(environment.get("HARNESS2_RUNS_DIR", "")).resolve()
    if path != output / SNAPSHOT_NAME:
        raise ValueError("Use the profile snapshot belonging to this experiment directory.")
    pinned = load_snapshot(path)
    from .profile_import import SETTING_ENV

    expected = configuration_environment(environment, pinned["configuration"])
    keys = {*SETTING_ENV.values(), "HARNESS2_JUDGE_BASE_URL"}
    if any((environment.get(key) or "") != (expected.get(key) or "") for key in keys):
        raise ValueError("The model settings differ from this experiment's pinned configuration.")
    return ProfileAdapter(adapter, pinned, output)


def capture_run_reference(store: ProfileStore, snapshot: dict, output: Path, **metadata) -> None:
    store.record_run(snapshot["profile_id"], {
        "output_dir": str(Path(output).resolve()),
        "configuration_id": snapshot["configuration_id"], "revision_id": snapshot["revision_id"],
        "snapshot_hash": snapshot["snapshot_hash"], **metadata,
    })


def candidate_from_run(store: ProfileStore, profile_id: str, record: dict, *, label: str = "",
                       why: str = "", parents=(), task_id=None, mode=None) -> dict:
    """Archive a chosen harness, never a report/grade, without changing the run."""
    if record.get("kind") != "benchmark" or record.get("status") in ("starting", "running"):
        raise ValueError("Choose a finished benchmark run.")
    params = record.get("params", {})
    if params.get("benchmark") != "jb" or params.get("substrate") != "oc":
        raise ValueError("Harness capture currently supports JobBench with OpenCode.")
    output = Path(record["output_dir"]).resolve()
    candidates = []
    for path in sorted((output / "jb" / "adapt").glob("*/*/summary.json"))[:1000]:
        if path.is_symlink() or not path.resolve().is_relative_to(output):
            raise ValueError("A saved task summary escapes its experiment directory.")
        summary = _read(path)
        if task_id and summary.get("task_id") != task_id:
            continue
        for selected_mode, selection in (summary.get("selections") or {}).items():
            if mode and selected_mode != mode:
                continue
            if selection.get("ok") and selection.get("chosen_label"):
                candidates.append((path, summary, selection))
    if len(candidates) != 1:
        raise ValueError("Choose one task and mode from a run with a completed selection.")
    path, summary, selection = candidates[0]
    chosen_label = selection["chosen_label"]
    if not isinstance(chosen_label, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", chosen_label):
        raise ValueError("The selected harness label must be one portable directory name.")
    chosen = path.parent / "harnesses" / chosen_label
    if not chosen.resolve().is_relative_to(output) or not chosen.is_dir():
        raise ValueError("The selected harness is missing or outside its run.")
    if chosen.is_symlink() or getattr(chosen, "is_junction", lambda: False)():
        raise ValueError("The selected harness cannot be a link.")
    seen = set()
    for entry in chosen.rglob("*"):
        if (entry.is_symlink() or getattr(entry, "is_junction", lambda: False)()
                or not entry.resolve().is_relative_to(chosen.resolve())):
            raise ValueError("The selected harness contains an external link.")
        if entry.is_file():
            name = tree.normalize_rel(entry.relative_to(chosen).as_posix()).casefold()
            if name in seen:
                raise ValueError("The saved harness has case-insensitive path collisions.")
            seen.add(name)
    harness = tree.read_tree(OPENCODE_SURFACE, chosen)
    if selection.get("harness_fingerprint"):
        from .core.runmark import fingerprint

        if fingerprint(chosen) != selection["harness_fingerprint"]:
            raise ValueError("The selected harness has changed since its run.")
    evidence = [{"path": path.relative_to(output).as_posix(),
                 "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                 "output_dir": str(output), "job_id": record.get("id", ""),
                 "evidence_status": "observed",
                 "qualification": "Chosen within one run; benefit across tasks unproven."}]
    return store.add_revision(
        profile_id, harness, label=label or "Candidate from " + record.get("title", "run"),
        parents=parents, why=why or "Chosen within this run; benefit across tasks remains unproven.",
        evidence=evidence, domain=summary.get("domain", ""), operation="adapt",
        experience=selection.get("experience") or {},
    )


def profile_run_outcome(store: ProfileStore, binding: dict, output: Path, record: dict) -> dict:
    """Freeze result facts and selected candidates; adoption remains a separate event.

    Run directories can later be resumed. Their final public state is therefore
    copied into the archive now, rather than relying on mutable file references.
    Grader results and raw model/session text are deliberately excluded.
    """
    output = Path(output).resolve()
    outcomes, candidates, warnings = [], [], []
    if record.get("kind") == "benchmark":
        summaries = sorted((output / "jb" / "adapt").glob("*/*/summary.json"))
        if len(summaries) > 128:
            warnings.append("Only the first 128 task summaries were copied into profile history; the run retains the rest.")
        for path in summaries[:128]:
            if path.is_symlink() or not path.resolve().is_relative_to(output):
                warnings.append("A task summary outside its experiment was not archived.")
                continue
            summary = _read(path)
            task = str(summary.get("task_id", ""))[:240]
            modes = summary.get("modes") or {}
            if not isinstance(modes, dict):
                modes = {}
            outcome = {
                "task_id": task, "domain": str(summary.get("domain", ""))[:240],
                "status": str(summary.get("status", "unknown"))[:64],
                "source": path.relative_to(output).as_posix(),
                "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "modes": [],
            }
            for mode, facts in list(modes.items())[:2]:
                if not isinstance(facts, dict) or mode not in ("agg", "seq"):
                    continue
                stages = []
                source_stages = facts.get("stages") or []
                if not isinstance(source_stages, list):
                    source_stages = []
                for stage in source_stages[:64]:
                    if not isinstance(stage, dict):
                        continue
                    error = str(stage.get("error") or "").lower()
                    stages.append({
                        "label": str(stage.get("label", ""))[:64],
                        "fell_back": stage.get("fell_back") is True,
                        "kept_base": stage.get("kept_base") is True,
                        "attempts": stage.get("attempts") if type(stage.get("attempts")) is int else None,
                        "failure": "timeout" if "timeout" in error or "timed out" in error else
                        "failed" if error else None,
                    })
                outcome["modes"].append({"mode": mode, "ok": facts.get("ok") is True, "stages": stages})
            outcomes.append(outcome)
            selections = summary.get("selections") or {}
            if not isinstance(selections, dict):
                continue
            for mode, selection in list(selections.items())[:2]:
                if mode not in ("agg", "seq") or not isinstance(selection, dict) or not selection.get("ok"):
                    continue
                try:
                    revision = candidate_from_run(
                        store, binding["profile_id"], record,
                        label=f"{task} · {mode} · {record.get('id', output.name)}"[:240],
                        why="Chosen in this experiment; archived as a candidate without changing the active harness.",
                        parents=[binding["revision_id"]], task_id=task, mode=mode,
                    )
                    candidates.append(revision["id"])
                except (OSError, ValueError):
                    warnings.append(f"The selected {mode} harness for {task} could not be archived.")
    reference = {
        "job_id": record.get("id"), "title": record.get("title", "Profile experiment"),
        "status": record.get("status", "unknown"), "outcomes": outcomes,
        "candidate_revision_ids": candidates, "archive_warnings": warnings,
    }
    for key in ("created_at", "finished_at"):
        if record.get(key):
            reference[key] = record[key]
    capture_run_reference(store, binding, output, **reference)
    return reference
