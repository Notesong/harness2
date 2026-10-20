"""Bounded, read-only discovery of experiments created outside Studio."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from ..local_models import validate_api_base_url

MAX_ENTRIES = 1000
MAX_JSON_BYTES = 1024 * 1024
_PRUNE = {"dataset", "sources", "context", "reflection", "executions", "harnesses",
          "rollouts", "_audit", "library", "evidence", "compaction-captures"}
_LABELS = {"readiness": "Check setup", "check": "Check setup", "sweep": "Refine harnesses",
           "refine": "Refine harnesses", "judge": "Judge results", "report": "Build report"}


def _safe(path: Path, root: Path) -> bool:
    try:
        if not path.resolve().is_relative_to(root):
            return False
        relative = path.relative_to(root)
        for parent in (root.joinpath(*relative.parts[:index])
                       for index in range(1, len(relative.parts) + 1)):
            if parent.is_symlink() or parent.is_junction():
                return False
        return True
    except (OSError, ValueError, RuntimeError):
        return False


def _read(path: Path, root: Path) -> dict:
    if not _safe(path, root):
        return {}
    try:
        with path.open("rb") as stream:
            body = stream.read(MAX_JSON_BYTES + 1)
        value = json.loads(body) if len(body) <= MAX_JSON_BYTES else None
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, RecursionError):
        return {}


def _text(value, maximum=512) -> str:
    return value if (isinstance(value, str) and len(value) <= maximum
                     and not any(ord(char) < 32 or ord(char) == 127 for char in value)) else ""


def _date(value) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def _time(data: dict, *keys) -> str | None:
    return next((date for key in keys if (date := _date(data.get(key)))), None)


def _children(path: Path, root: Path) -> list[Path]:
    if not _safe(path, root):
        return []
    try:
        # Bound enumeration itself, rather than sorting an unbounded generator.
        with os.scandir(path) as entries:
            result = []
            for index, entry in enumerate(entries):
                if index >= MAX_ENTRIES:
                    break
                child = Path(entry.path)
                if not entry.name.startswith(".") and _safe(child, root):
                    result.append(child)
        return sorted(result)
    except OSError:
        return []


def _summaries(output: Path, root: Path) -> list[tuple[Path, dict]]:
    found, pending, visited = [], [output], 0
    while pending and visited < MAX_ENTRIES:
        directory = pending.pop()
        # Known evidence trees contain transcripts, not task summaries. Never
        # traverse them while looking for summaries in benchmark adapt roots.
        for child in _children(directory, root):
            visited += 1
            if visited > MAX_ENTRIES:
                break
            try:
                if child.is_dir() and child.name not in _PRUNE:
                    pending.append(child)
                elif child.name == "summary.json":
                    data = _read(child, root)
                    if _text(data.get("task_id")):
                        found.append((child, data))
            except OSError:
                continue
    return sorted(found)


def _phases(data: dict) -> list[dict]:
    stages = data.get("stages", data.get("phases", []))
    if isinstance(stages, dict):
        stages = [dict(value, name=key) for key, value in list(stages.items())[:20]
                  if isinstance(value, dict)]
    if not isinstance(stages, list):
        return []
    result = []
    for stage in stages[:20]:
        if not isinstance(stage, dict):
            continue
        name = stage.get("name", stage.get("id"))
        if name not in _LABELS:
            continue
        code = stage.get("exit_code", stage.get("returncode"))
        status = ("succeeded" if code == 0 else "failed" if type(code) is int
                  else stage.get("status"))
        if status not in ("succeeded", "failed", "cancelled", "interrupted"):
            status = "interrupted"
        result.append({"id": name, "label": _LABELS[name], "status": status,
                       "started_at": _time(stage, "started_at", "started_utc"),
                       "finished_at": _time(stage, "finished_at", "ended_utc"),
                       "exit_code": code if type(code) is int else None})
    return result


def _models(identity: dict, workflow: dict) -> dict:
    result = {key: "" for key in ("project", "region", "solver", "improver", "judge",
                                  "local_url", "local_model", "local_context", "local_output",
                                  "codex_url", "codex_model")}
    for source, target in (("executor", "solver"), ("improver", "improver"),
                           ("judge", "judge"), ("project", "project"), ("location", "region")):
        result[target] = _text(identity.get(source))
    local = identity.get("local_profile", workflow.get("profile", {}))
    if isinstance(local, dict):
        result["local_model"] = _text(local.get("model"))
        for source, target in (("context", "local_context"), ("output", "local_output")):
            value = local.get(source)
            if type(value) is int and value > 0:
                result[target] = str(value)
        try:
            result["local_url"] = validate_api_base_url(_text(local.get("base_url")))
        except ValueError:
            pass
        for name in ("solver", "improver", "judge"):
            if not result[name] and result["local_model"]:
                result[name] = ("local/" if name != "judge" else "") + result["local_model"]
    return result


def _record(output: Path, root: Path) -> dict | None:
    summaries = _summaries(output, root)
    demo = _read(output / "demo.json", root) or _read(output / "demo-result.json", root)
    workflow = _read(output / "evidence/workflow-status.json", root)
    current = _read(output / "current.json", root)
    recovery = _read(output / "evidence/unattended-follow-through/status.json", root)
    stopped = _read(output / "manual-stop.json", root)
    if current.get("status") not in ("starting", "running", "completed", "succeeded", "failed",
                                     "cancelled", "interrupted"):
        current = {}
    if not (type(demo.get("n_tasks")) is int and type(demo.get("done")) is int
            and type(demo.get("failed")) is int):
        demo = {}
    if not summaries and not demo and not workflow and not current and not stopped:
        return None
    identity = _read(summaries[0][0].parent.parent / "config.json", root) if summaries else {}
    if not identity and stopped:
        saved = _read(output / "first-profile-sanitized.json", root)
        identity = saved.get("recorded_run_identity", {})
        if not isinstance(identity, dict):
            identity = {}
    task_rows = [data for _, data in summaries]
    domains = sorted({_text(data.get("domain"), 160) for data in task_rows} - {""})
    if not domains and isinstance(identity.get("domains"), list):
        domains = [_text(value, 160) for value in identity["domains"][:1000] if _text(value, 160)]
    names = [part for path, _ in summaries for part in path.relative_to(output).parts]
    benchmark = next((name for name in names if name in ("jb", "lab", "wb")), "jb")
    if output.name in ("jb", "lab", "wb"):
        benchmark = output.name
    namespace = summaries[0][0].parent.parent.name if summaries else ""
    matched = re.match(r"^(jb|lab|wb-(office|web|code))-(oc|cx)-", namespace)
    subset = None
    if matched:
        benchmark = matched[1].split("-")[0]
        subset = matched[2]
    substrate = matched[3] if matched else "oc"
    modes = set()
    for row in task_rows:
        value = row.get("modes", row.get("selections", {}))
        if isinstance(value, dict):
            modes.update(set(value) & {"agg", "seq"})
    mode = "both" if modes == {"agg", "seq"} else "sequential" if modes == {"seq"} else "parallel"
    k = next((row["k"] for row in task_rows if type(row.get("k")) is int and row["k"] > 0), 1)
    params = {"benchmark": benchmark, "subset": subset, "substrate": substrate, "mode": mode,
              "domains": domains, "pipeline": bool(workflow or current), "k": k,
              "max_tasks": identity.get("max_tasks") if type(identity.get("max_tasks")) is int
              and identity["max_tasks"] > 0 else max(1, len(task_rows)), "workers": 1}
    phases = _phases(workflow) or _phases(current)
    recovered = (recovery.get("status") == "completed"
                 and recovery.get("all_recovery_stages_succeeded") is True
                 and bool(_time(recovery, "ended_utc", "finished_at")))
    if recovered:
        replacements = {phase["id"]: phase for phase in _phases(recovery)}
        phases = [replacements.pop(phase["id"], phase) for phase in phases]
        phases.extend(replacements.values())
    done = sum(row.get("status") == "ok" for row in task_rows)
    failed = sum(row.get("status") not in ("ok", "running", "partial") for row in task_rows)
    total = len(task_rows)
    if demo:
        total = demo.get("n_tasks") if type(demo.get("n_tasks")) is int else total
        done = demo.get("done") if type(demo.get("done")) is int else done
        failed = demo.get("failed") if type(demo.get("failed")) is int else failed
    ended = _time(workflow, "ended_utc", "finished_at") or _time(current, "finished_at", "ended_utc")
    if recovered:
        ended = _time(recovery, "ended_utc", "finished_at")
        status = "succeeded" if not failed else "failed"
    elif stopped:
        status = "cancelled"
        ended = _time(stopped, "stopped_utc", "finished_at")
    elif workflow or current:
        source = workflow or current
        terminal = source.get("status")
        if ended and (source.get("all_stage_exit_codes_zero") is True
                      or terminal in ("completed", "succeeded")) and not failed:
            status = "succeeded"
        elif terminal in ("failed", "cancelled") or (ended and any(
                phase["status"] == "failed" for phase in phases)):
            status = "cancelled" if terminal == "cancelled" else "failed"
        else:
            status = "interrupted"
    else:
        status = "failed" if failed else "succeeded" if total > 0 and done == total else "interrupted"
    try:
        fallback = datetime.fromtimestamp(output.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return None
    started = _time(workflow, "started_utc", "started_at") or _time(current, "started_at", "started_utc")
    created = _time(current, "created_at") or started or fallback
    title = "Local demo" if demo else {"jb": "JobBench", "lab": "LAB", "wb": "WorkBuddy"}[benchmark]
    record = {"id": hashlib.sha256(os.path.normcase(str(output.resolve())).encode("utf-8")).hexdigest()[:12],
            "kind": "demo" if demo else "benchmark", "title": title + " · " + output.name,
            "status": status, "params": params, "created_at": created, "started_at": started,
            "finished_at": ended or (fallback if status in ("succeeded", "failed", "cancelled") else None),
            "phase": {"succeeded": "Complete", "failed": "Failed", "cancelled": "Stopped",
                      "interrupted": "Interrupted"}[status],
            "phase_index": len(phases), "phases": phases, "output_dir": str(output.resolve()),
            "models": _models(identity, workflow), "total_tasks": max(0, total),
            "progress": {"done": max(0, done), "failed": max(0, failed), "total": max(0, total)},
            "imported": True, "error": "", "exit_code": 0 if status == "succeeded" else None,
            "resumed_from": None}
    if identity.get("profile"):
        # Read-only association; original CLI evidence and active profile pointers
        # are never rewritten by history discovery.
        from ..profile_runtime import load_snapshot

        try:
            pinned = load_snapshot(output / "profile-start.json")
            binding = {key: pinned[key] for key in (
                "profile_id", "profile_name", "configuration_id", "revision_id", "snapshot_hash",
            )}
            expected = {key: value for key, value in binding.items() if key != "profile_name"}
            if identity["profile"] == expected:
                record["profile"] = binding
        except (OSError, ValueError, KeyError):
            pass
    return record


def discover_history(workspace) -> list[dict]:
    """Import recognized immediate run roots and orphaned Studio children, without writes.

    Imported records never claim a live process and cannot authorize an automatic
    resume. Metadata is allowlisted; command lines, notes, tokens, process details,
    transcripts, and selected file contents are not included in these records.
    """
    root = Path(workspace.runs_root).resolve()
    result, seen = [], set()
    for child in _children(root, root):
        candidates = _children(child, root) if child.name == "ui" else [child]
        for output in candidates:
            try:
                if not output.is_dir() or output.resolve() in seen:
                    continue
                seen.add(output.resolve())
                if record := _record(output, root):
                    result.append(record)
            except (OSError, ValueError, TypeError, RecursionError):
                continue
            if len(result) >= MAX_ENTRIES:
                return result
    return result
