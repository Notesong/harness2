"""Owned background CLI jobs and read-only result views for the local UI."""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .. import config, processes
from .workspace import BENCHMARKS, Workspace

ACTIVE = {"running", "starting"}
KINDS = {"demo", "benchmark", "check", "setup", "preview", "judge", "report", "authenticate"}
MODEL_FIELDS = (
    "project",
    "region",
    "solver",
    "improver",
    "judge",
    "local_url",
    "local_model",
    "codex_url",
    "codex_model",
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def within(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def bounded_text(path: Path, limit: int = 32000) -> str:
    with path.open("rb") as stream:
        body = stream.read(limit + 1)
    return body[:limit].decode("utf-8", errors="replace") + (
        "\n… preview truncated" if len(body) > limit else ""
    )


class BusyError(ValueError):
    pass


class JobManager:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.directory = workspace.state_dir / "jobs"
        self.directory.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.jobs = {}
        self.procs = {}
        self.cancelled = set()
        self.threads = {}
        self.active_id = None
        for path in self.directory.glob("*.json"):
            record = read_json(path)
            if not isinstance(record, dict) or not re.fullmatch(
                r"[a-f0-9]{12}", str(record.get("id", ""))
            ):
                continue
            if record.get("status") in ACTIVE:
                record.update(
                    status="interrupted",
                    finished_at=now(),
                    error="The app closed before this operation finished. You can resume an experiment.",
                )
                self._write(record)
            self.jobs[record["id"]] = record

    def _write(self, record: dict):
        target = self.directory / f"{record['id']}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        os.replace(temporary, target)

    def list(self) -> list[dict]:
        with self.lock:
            records = [copy.deepcopy(record) for record in self.jobs.values()]
        return sorted(records, key=lambda row: row["created_at"], reverse=True)

    def parameters(self, data: dict) -> dict:
        if not isinstance(data, dict):
            raise ValueError("Run settings should be an object.")
        benchmark = data.get("benchmark", "jb")
        subset = data.get("subset") if benchmark == "wb" else None
        if benchmark not in ("jb", "lab", "wb") or (
            benchmark == "wb" and subset not in ("office", "web", "code")
        ):
            raise ValueError("Choose an available benchmark and subset.")
        substrate, mode = data.get("substrate", "oc"), data.get("mode", "parallel")
        if substrate not in ("oc", "cx") or mode not in ("parallel", "sequential", "both"):
            raise ValueError("Choose a valid executor and refinement mode.")
        numbers = {}
        for key, default, maximum in (("k", 1, 8), ("max_tasks", 1, 10000), ("workers", 1, 16)):
            value = data.get(key, default)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{key} must be between 1 and {maximum}.")
            numbers[key] = value
        domains = data.get("domains", [])
        if (
            not isinstance(domains, list)
            or len(domains) > 1000
            or any(
                not isinstance(domain, str)
                or not re.fullmatch(r"[\w .&()'\-]+", domain)
                or len(domain) > 160
                for domain in domains
            )
        ):
            raise ValueError("Choose valid domains from the benchmark list.")
        return {
            "benchmark": benchmark,
            "subset": subset,
            "substrate": substrate,
            "mode": mode,
            "domains": list(dict.fromkeys(domains)),
            "pipeline": bool(data.get("pipeline", True)),
            **numbers,
        }

    def _cell(self, params: dict) -> list[str]:
        args = ["--bench", params["benchmark"], "--substrate", params["substrate"]]
        if params["subset"]:
            args += ["--subset", params["subset"]]
        return args

    def _plan(self, kind: str, params: dict, output: Path | None) -> list[dict]:
        def phase(key, label, module, args):
            return {
                "id": key,
                "label": label,
                "command": [sys.executable, "-u", "-m", module, *map(str, args)],
                "status": "pending",
            }

        modes = "parallel,sequential" if params["mode"] == "both" else params["mode"]
        cell = self._cell(params)
        identity = [*cell, "--k", str(params["k"]), "--mode", modes]
        if kind == "demo":
            return [
                phase(
                    "demo",
                    "Refine two sample tasks",
                    "harness2.cli.demo",
                    ["--output", output, "--k", params["k"], "--mode", params["mode"]],
                )
            ]
        if kind == "authenticate":
            return [
                {
                    "id": "authenticate",
                    "label": "Sign in with Google",
                    "command": config.command(["gcloud", "auth", "application-default", "login"]),
                    "status": "pending",
                }
            ]
        if kind == "setup":
            args = [params["benchmark"], "--substrate", params["substrate"]]
            if params["subset"]:
                args += ["--subset", params["subset"]]
            return [phase("setup", "Install tools and task data", "harness2.cli.setup", args)]
        if kind == "check":
            return [phase("check", "Check benchmark setup", "harness2.cli.check", cell)]
        if kind in ("judge", "report"):
            return [
                phase(
                    kind,
                    "Judge adopted results" if kind == "judge" else "Build score report",
                    f"harness2.cli.{kind}",
                    identity,
                )
            ]
        args = [
            *identity,
            "--max-tasks",
            str(params["max_tasks"]),
            "--workers",
            str(params["workers"]),
        ]
        if params["domains"]:
            args += ["--only-domains", ",".join(params["domains"])]
        if kind == "preview":
            return [
                phase(
                    "preview",
                    "Preview task selection",
                    "harness2.cli.run_sweep",
                    [*args, "--dry-run"],
                )
            ]
        run = phase("refine", "Run and refine harnesses", "harness2.cli.run_sweep", args)
        return (
            [
                phase("check", "Check benchmark setup", "harness2.cli.check", cell),
                run,
                phase("judge", "Judge adopted results", "harness2.cli.judge", identity),
                phase("report", "Build score report", "harness2.cli.report", identity),
            ]
            if params["pipeline"]
            else [run]
        )

    def start(
        self, kind: str, data: dict, *, confirmed: bool = False, resume_id: str | None = None
    ) -> dict:
        if kind not in KINDS:
            raise ValueError("Unknown operation.")
        if kind in ("benchmark", "judge") and not confirmed:
            raise ValueError("Confirm that you want to use the configured model before starting.")
        params = self.parameters(data)
        source_id = data.get("source_id")
        with self.lock:
            if self.active_id:
                raise BusyError("Another operation is running. Let it finish or stop it first.")
            if resume_id:
                prior = self.jobs.get(resume_id)
                if (
                    not prior
                    or prior["kind"] not in ("demo", "benchmark")
                    or prior["status"] in ACTIVE
                ):
                    raise ValueError("This operation cannot be resumed.")
                current = self.workspace.settings()
                if prior["kind"] == "benchmark" and any(
                    current.get(key) != prior.get("models", {}).get(key) for key in MODEL_FIELDS
                ):
                    raise ValueError(
                        "The model connection changed. Restore the original settings to resume, or start a new experiment."
                    )
                kind, params = prior["kind"], prior["params"]
                output = Path(prior["output_dir"])
            else:
                output = None
            if kind in ("judge", "report"):
                source = self.jobs.get(source_id)
                if not source or source["kind"] != "benchmark" or not source.get("output_dir"):
                    raise ValueError("Choose the experiment to judge or report.")
                params, output = source["params"], Path(source["output_dir"])
            if (
                kind in ("benchmark", "judge")
                and self.workspace.settings()["provider"] == "local"
                and (params["substrate"] != "oc" or params["benchmark"] != "jb")
            ):
                raise ValueError(
                    "The local model profile currently supports the full JobBench pipeline with OpenCode. "
                    "Choose that combination, or configure the other benchmark's judge separately."
                )
            identifier = uuid.uuid4().hex[:12]
            if kind in ("demo", "benchmark") and output is None:
                output = (
                    self.workspace.runs_root
                    / "ui"
                    / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{identifier[:6]}"
                )
            # Standalone judge/report are intentionally tied to an existing
            # experiment via resume; the primary UI runs these in its pipeline.
            if kind in ("judge", "report") and output is None:
                raise ValueError(
                    "Open an experiment and use its pipeline to judge or report the same run."
                )
            if output is not None:
                output.mkdir(parents=True, exist_ok=True)
            phases = self._plan(kind, params, output)
            benchmark_name = next(
                (
                    row[1]
                    for row in BENCHMARKS
                    if row[3] == params["benchmark"] and row[4] == params["subset"]
                ),
                params["benchmark"],
            )
            total = 2 if kind == "demo" else 0
            if kind in ("benchmark", "preview"):
                catalog = next(
                    (
                        row
                        for row in self.workspace.benchmarks()
                        if row["id"]
                        == (
                            "wb-" + params["subset"]
                            if params["benchmark"] == "wb"
                            else params["benchmark"]
                        )
                    ),
                    {},
                )
                total = sum(
                    min(row["count"], params["max_tasks"])
                    for row in catalog.get("domains", [])
                    if not params["domains"] or row["id"] in params["domains"]
                )
            record = {
                "id": identifier,
                "title": "Local demo"
                if kind == "demo"
                else benchmark_name
                + " · "
                + {
                    "benchmark": "Experiment",
                    "preview": "Preview",
                    "check": "Setup check",
                    "setup": "Installation",
                    "authenticate": "Google sign-in",
                }.get(kind, kind.title()),
                "kind": kind,
                "status": "starting",
                "params": params,
                "created_at": now(),
                "started_at": None,
                "finished_at": None,
                "phase": "Preparing",
                "phase_index": 0,
                "phases": phases,
                "output_dir": str(output) if output else None,
                "error": "",
                "exit_code": None,
                "total_tasks": total,
                "progress": {"done": 0, "failed": 0, "total": total},
                "models": {key: self.workspace.settings().get(key) for key in MODEL_FIELDS},
                "resumed_from": resume_id,
            }
            self.jobs[identifier] = record
            self.active_id = identifier
            self._write(record)
            thread = threading.Thread(
                target=self._execute,
                args=(identifier,),
                daemon=True,
                name=f"harness-ui-{identifier}",
            )
            self.threads[identifier] = thread
            thread.start()
            return copy.deepcopy(record)

    def cancel(self, identifier: str) -> dict:
        with self.lock:
            record = self.jobs.get(identifier)
            if not record:
                raise KeyError(identifier)
            if record["status"] not in ACTIVE:
                return copy.deepcopy(record)
            self.cancelled.add(identifier)
            record["phase"] = "Stopping…"
            self._write(record)
            proc = self.procs.get(identifier)
        if proc is not None:
            processes.terminate_tree(proc, grace=1)
        return self.get(identifier)

    def _execute(self, identifier: str):
        record = self.jobs[identifier]
        env = self.workspace.environment()
        if record["output_dir"]:
            env["HARNESS2_RUNS_DIR"] = record["output_dir"]
        secret_values = self.workspace.secrets()

        def redact(text):
            for secret in secret_values:
                text = text.replace(secret, "[redacted]")
            return text

        record.update(status="running", started_at=now())
        exit_code = 0
        log_path = self.directory / f"{identifier}.log"
        try:
            with log_path.open("a", encoding="utf-8", newline="\n") as log:
                for index, phase in enumerate(record["phases"]):
                    with self.lock:
                        if identifier in self.cancelled:
                            break
                        record.update(phase=phase["label"], phase_index=index)
                        phase["status"] = "running"
                        self._write(record)
                    log.write(f"\n── {phase['label']} ──\n")
                    log.flush()
                    proc = processes.start(
                        phase["command"],
                        cwd=self.workspace.root,
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                    )
                    with self.lock:
                        self.procs[identifier] = proc
                        stopping = identifier in self.cancelled
                    if stopping:
                        processes.terminate_tree(proc)
                    try:
                        for line in proc.stdout:
                            log.write(redact(line))
                            log.flush()
                        exit_code = proc.wait()
                    finally:
                        processes.terminate_tree(proc)
                        proc.stdout.close()
                        with self.lock:
                            self.procs.pop(identifier, None)
                    with self.lock:
                        phase["status"] = (
                            "cancelled"
                            if identifier in self.cancelled
                            else "succeeded"
                            if exit_code == 0
                            else "failed"
                        )
                        self._write(record)
                    if exit_code:
                        break
        except Exception as exc:
            exit_code = 1
            record["error"] = redact(str(exc))
            with log_path.open("a", encoding="utf-8") as log:
                log.write("\n" + record["error"] + "\n")
        finally:
            with self.lock:
                cancelled = identifier in self.cancelled
                status = "cancelled" if cancelled else "succeeded" if exit_code == 0 else "failed"
                record.update(
                    status=status,
                    exit_code=exit_code,
                    finished_at=now(),
                    phase="Stopped"
                    if cancelled
                    else "Complete"
                    if exit_code == 0
                    else "Needs attention",
                )
                if status == "failed" and not record["error"]:
                    record["error"] = (
                        "This step did not complete. The activity log has the details; completed results are preserved."
                    )
                for phase in record["phases"]:
                    if phase["status"] == "running":
                        phase["status"] = status
                try:
                    tasks, _ = self.results(record)
                    record["progress"] = {
                        "done": sum(task["status"] == "ok" for task in tasks),
                        "failed": sum(task["status"] not in ("ok", "running") for task in tasks),
                        "total": record["total_tasks"],
                    }
                except OSError:
                    pass
                self._write(record)
                self.active_id = None

    def get(self, identifier: str) -> dict:
        with self.lock:
            if identifier not in self.jobs:
                raise KeyError(identifier)
            record = copy.deepcopy(self.jobs[identifier])
        path = self.directory / f"{identifier}.log"
        if path.is_file():
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 128000))
                record["log"] = stream.read().decode("utf-8", errors="replace")
        else:
            record["log"] = ""
        try:
            record["tasks"], _ = self.results(record)
        except OSError:
            record["tasks"] = []
        done = sum(task["status"] == "ok" for task in record["tasks"])
        failed = sum(task["status"] not in ("ok", "running") for task in record["tasks"])
        record["progress"] = {"done": done, "failed": failed, "total": record["total_tasks"]}
        return record

    def results(self, record: dict) -> tuple[list[dict], dict[str, Path]]:
        tasks, artifacts = [], {}
        if not record.get("output_dir"):
            return tasks, artifacts
        root = Path(record["output_dir"])
        if not root.is_dir():
            return tasks, artifacts
        for path in sorted(root.rglob("summary.json"))[:1000]:
            summary = read_json(path)
            if not isinstance(summary, dict) or "task_id" not in summary:
                continue
            selections = []
            judged = read_json(path.parent.parent / "judged.json", [])
            rows = (
                judged
                if isinstance(judged, list)
                else judged.get("rows", [])
                if isinstance(judged, dict)
                else []
            )
            selected = summary.get("selections") or {}
            if not isinstance(rows, list):
                rows = []
            if not isinstance(selected, dict):
                continue
            for mode, selection in selected.items():
                if not isinstance(selection, dict):
                    continue
                files = []
                directory = selection.get("chosen_run_dir")
                chosen = (
                    Path(directory)
                    if isinstance(directory, str) and directory
                    else root / "missing"
                )
                if chosen.is_dir() and (
                    within(chosen, root) or within(chosen, self.workspace.root / "dependencies")
                ):
                    benchmark = record["params"]["benchmark"]
                    for artifact in self.artifact_paths(chosen, record["params"])[:2000]:
                        if (
                            not artifact.is_file()
                            or "__pycache__" in artifact.parts
                            or any(part.startswith(".") for part in artifact.relative_to(chosen).parts)
                            or artifact.is_symlink()
                        ):
                            continue
                        if not within(artifact, chosen):
                            continue
                        if benchmark == "jb" and not within(artifact, root) and not any(
                            part in artifact.parts
                            for part in ("output", "model_output", "artifacts", "deliverables")
                        ):
                            continue
                        if artifact.name.startswith(".") or artifact.suffix in (
                            ".log",
                            ".jsonl",
                            ".pyc",
                        ):
                            continue
                        key = hashlib.sha256(str(artifact.resolve()).encode("utf-8")).hexdigest()[
                            :20
                        ]
                        artifacts[key] = artifact.resolve()
                        preview = (
                            bounded_text(artifact, 16000)
                            if artifact.suffix.lower() in (".txt", ".md", ".csv", ".tsv", ".json")
                            else None
                        )
                        files.append(
                            {
                                "id": key,
                                "name": artifact.relative_to(chosen).as_posix(),
                                "size": artifact.stat().st_size,
                                "text": preview,
                            }
                        )
                        if len(files) >= 100:
                            break
                scored = next(
                    (
                        row
                        for row in rows
                        if isinstance(row, dict)
                        and row.get("task_id", row.get("task")) == summary["task_id"]
                        and row.get("mode") == mode
                    ),
                    {},
                )
                selections.append(
                    {
                        "mode": "parallel"
                        if mode == "agg"
                        else "sequential"
                        if mode == "seq"
                        else mode,
                        "label": selection.get("chosen_label", ""),
                        "why": selection.get("why", ""),
                        "score": scored.get("frac"),
                        "graded": bool(scored.get("graded")),
                        "files": files,
                        "diff": self.harness_diff(
                            path.parent, str(selection.get("chosen_label", ""))
                        ),
                    }
                )
            tasks.append(
                {
                    "id": summary["task_id"],
                    "domain": summary.get("domain", ""),
                    "status": (
                        "running"
                        if record["status"] in ACTIVE
                        and summary.get("status", "partial") == "partial"
                        and not summary.get("errors")
                        else summary.get("status", "partial")
                    ),
                    "selections": selections,
                }
            )
        return tasks, artifacts

    @staticmethod
    def artifact_paths(chosen: Path, params: dict) -> list[Path]:
        """Use each runner's actual deliverable boundary, excluding its runtime state."""
        benchmark = params["benchmark"]
        if benchmark == "lab":
            return sorted((chosen / "output").rglob("*"))
        if benchmark == "wb":
            from ..benchmarks.wb_render import ALLOWLIST

            paths = set()
            for pattern in ALLOWLIST.get(params.get("subset"), ()):
                if pattern.endswith("/**"):
                    paths.update((chosen / pattern[:-3]).rglob("*"))
                else:
                    paths.add(chosen / pattern)
            return sorted(paths)
        return sorted(chosen.rglob("*"))

    def harness_diff(self, parent: Path, label: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", label):
            return ""
        base, selected = parent / "harnesses/base", parent / "harnesses" / label
        if not base.is_dir() or not selected.is_dir():
            return ""
        names = {
            p.relative_to(directory).as_posix()
            for directory in (base, selected)
            for p in directory.rglob("*")
            if p.is_file()
        }
        result = []
        for name in sorted(names)[:200]:
            if Path(name).suffix not in (".md", ".py", ".yaml", ".json", ".toml", ".ts"):
                continue
            before, after = base / name, selected / name
            if not within(before, base) or not within(after, selected):
                continue
            left = bounded_text(before).splitlines(keepends=True) if before.is_file() else []
            right = bounded_text(after).splitlines(keepends=True) if after.is_file() else []
            result.extend(
                difflib.unified_diff(left, right, fromfile="base/" + name, tofile="adopted/" + name)
            )
        return "".join(result)[:64000]

    def download(self, identifier: str, artifact_id: str) -> Path:
        with self.lock:
            record = copy.deepcopy(self.jobs[identifier])
        _, artifacts = self.results(record)
        if artifact_id not in artifacts:
            raise KeyError(artifact_id)
        return artifacts[artifact_id]

    def close(self):
        with self.lock:
            active = self.active_id
        if active:
            self.cancel(active)
            self.threads[active].join(timeout=10)
