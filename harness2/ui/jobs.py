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
from ..local_server import ManagedLocalServer
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
    "local_context",
    "local_output",
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
        self.conversations = None
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
        from .history import discover_history

        known_outputs = {record.get("output_dir") for record in self.jobs.values()}
        for record in discover_history(workspace):
            if record["output_dir"] not in known_outputs:
                self.jobs.setdefault(record["id"], record)

    def _write(self, record: dict):
        target = self.directory / f"{record['id']}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        os.replace(temporary, target)

    def _event(self, record: dict, title: str, detail: str = "", *, kind="observed"):
        if not record.get("output_dir"):
            return
        path = Path(record["output_dir"]) / "run-timeline.jsonl"
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps({
                "time": now(), "title": title, "detail": detail, "kind": kind,
            }, ensure_ascii=False) + "\n")

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
        result = {
            "benchmark": benchmark,
            "subset": subset,
            "substrate": substrate,
            "mode": mode,
            "domains": list(dict.fromkeys(domains)),
            "pipeline": bool(data.get("pipeline", True)),
            **numbers,
        }
        profile_id = data.get("profile_id")
        if profile_id:
            if not isinstance(profile_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", profile_id):
                raise ValueError("Choose a valid model profile.")
            result["profile_id"] = profile_id
            for key in ("profile_configuration_id", "profile_revision_id"):
                if data.get(key):
                    if not isinstance(data[key], str) or not re.fullmatch(r"[a-f0-9]{64}", data[key]):
                        raise ValueError("Choose a valid saved profile configuration or harness.")
                    result[key] = data[key]
        if "conversation_enabled" in data and type(data["conversation_enabled"]) is not bool:
            raise ValueError("Conversation participation must be enabled explicitly.")
        if data.get("conversation_enabled"):
            if not profile_id or benchmark != "jb" or substrate != "oc" or numbers["workers"] != 1:
                raise ValueError("Conversation runs require a profile, JobBench, OpenCode, and one domain worker.")
            result["conversation_enabled"] = True
        return result

    def _cell(self, params: dict) -> list[str]:
        args = ["--bench", params["benchmark"], "--substrate", params["substrate"]]
        if params["subset"]:
            args += ["--subset", params["subset"]]
        return args

    def _plan(self, kind: str, params: dict, output: Path | None, *, provider=None) -> list[dict]:
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
        if params["pipeline"]:
            phases = [
                phase("check", "Check benchmark setup", "harness2.cli.check", cell),
                run,
                phase("judge", "Judge adopted results", "harness2.cli.judge", identity),
                phase("report", "Build score report", "harness2.cli.report", identity),
            ]
            if (provider or self.workspace.settings()["provider"]) == "local":
                summary = phase(
                    "summary", "Write a readable wrap-up", "harness2.cli.summarize",
                    ["--output", output],
                )
                summary["timeout"] = 300
                phases.append(summary)
            return phases
        return [run]

    def start(
        self, kind: str, data: dict, *, confirmed: bool = False, resume_id: str | None = None
    ) -> dict:
        if kind not in KINDS:
            raise ValueError("Unknown operation.")
        if kind in ("benchmark", "judge") and not confirmed:
            raise ValueError("Confirm that you want to use the configured model before starting.")
        params = self.parameters(data)
        source_id = data.get("source_id")
        pinned = None
        profile_plan = None
        with self.lock:
            if self.active_id:
                raise BusyError("Another operation is running. Let it finish or stop it first.")
            if self.conversations is not None and self.conversations.busy:
                raise BusyError("Stop the idle conversation before starting another operation.")
            effective_settings = self.workspace.settings()
            if resume_id:
                prior = self.jobs.get(resume_id)
                if prior and prior.get("imported"):
                    raise ValueError("Imported runs are available for viewing; start a new experiment to run again.")
                if (
                    not prior
                    or prior["kind"] not in ("demo", "benchmark")
                    or prior["status"] in ACTIVE
                ):
                    raise ValueError("This operation cannot be resumed.")
                current = self.workspace.settings()
                if prior["kind"] == "benchmark" and not prior.get("profile") and any(
                    current.get(key) != prior.get("models", {}).get(key) for key in MODEL_FIELDS
                ):
                    raise ValueError(
                        "The model connection changed. Restore the original settings to resume, or start a new experiment."
                    )
                kind, params = prior["kind"], prior["params"]
                output = Path(prior["output_dir"])
                if prior.get("profile"):
                    from ..profile_runtime import load_snapshot

                    pinned = load_snapshot(output / "profile-start.json")
            else:
                output = None
            if kind in ("judge", "report"):
                source = self.jobs.get(source_id)
                if source and source.get("imported"):
                    raise ValueError("Imported runs are read-only. Their saved report is available in the run overview.")
                if not source or source["kind"] != "benchmark" or not source.get("output_dir"):
                    raise ValueError("Choose the experiment to judge or report.")
                params, output = source["params"], Path(source["output_dir"])
                if source.get("profile"):
                    from ..profile_runtime import load_snapshot

                    pinned = load_snapshot(output / "profile-start.json")
            if params.get("conversation_enabled") and kind not in ("benchmark", "preview", "judge", "report"):
                raise ValueError("Conversation participation is available for profile experiments only.")
            if params.get("conversation_enabled") and kind in ("benchmark", "preview"):
                from ..conversation_runtime import require_worker_containment

                require_worker_containment()
            if (
                kind in ("benchmark", "judge")
                and (params.get("profile_id") or self.workspace.settings()["provider"] == "local")
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
            if params.get("profile_id") and pinned is None:
                if kind not in ("benchmark", "preview"):
                    raise ValueError("Choose a profile for an experiment or plan.")
                if kind == "preview":
                    # A plan needs no output, immutable records, or model startup.
                    from ..profiles import ProfileStore

                    profile = ProfileStore(self.workspace.root).get(params["profile_id"])
                    selected_configuration = params.get("profile_configuration_id") or profile.get("active_configuration_id")
                    selected_revision = params.get("profile_revision_id") or profile.get("active_revision_id")
                    if (not any(row["id"] == selected_configuration for row in profile["configurations"])
                            or not any(row["id"] == selected_revision for row in profile["revisions"])):
                        raise ValueError("Choose an active configuration and harness in this profile first.")
                    effective_settings = next(row["snapshot"]["settings"] for row in profile["configurations"]
                                              if row["id"] == selected_configuration)
                    profile_plan = {
                        "profile_id": profile["id"], "profile_name": profile["name"],
                        "configuration_id": selected_configuration, "revision_id": selected_revision,
                    }
                else:
                    from ..profile_runtime import pin_profile
                    from ..profiles import ProfileStore

                    pinned = pin_profile(
                        ProfileStore(self.workspace.root), params["profile_id"], output,
                        configuration_id=params.get("profile_configuration_id"),
                        revision_id=params.get("profile_revision_id"),
                    )
            if pinned:
                effective_settings = pinned["configuration"]["settings"]
            if params.get("conversation_enabled") and effective_settings.get("provider", "local") != "local":
                raise ValueError("Conversational experiments require a saved local model configuration.")
            phases = (self._plan(kind, params, output, provider=effective_settings.get("provider", "local"))
                      if params.get("profile_id") else self._plan(kind, params, output))
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
                "models": {key: effective_settings.get(key) for key in MODEL_FIELDS},
                "resumed_from": resume_id,
            }
            if pinned:
                record["profile"] = {key: pinned[key] for key in (
                    "profile_id", "profile_name", "configuration_id", "revision_id", "snapshot_hash",
                )}
                exposed = (pinned.get("capabilities") or {}).get("entries", [])
                record["capability_catalog"] = {
                    "exposed": bool(exposed), "entry_count": len(exposed),
                    "entry_ids": [entry["id"] for entry in exposed],
                }
                from ..profile_runtime import capture_run_reference
                from ..profiles import ProfileStore

                capture_run_reference(
                    ProfileStore(self.workspace.root), pinned, output,
                    job_id=identifier, title=record["title"], status="starting", created_at=record["created_at"],
                )
            elif profile_plan:
                record["profile_plan"] = profile_plan
            if params.get("conversation_enabled") and kind in ("benchmark", "preview"):
                if kind == "benchmark":
                    if self.conversations is None or pinned is None:
                        raise ValueError("The conversation service and a pinned profile are required.")
                    if resume_id and prior.get("conversation_id"):
                        record["conversation_id"] = prior["conversation_id"]
                    self.conversations.begin_run(record, pinned)
                else:
                    record["conversation_plan"] = {"enabled": True, "serialized_workers": True}
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
        secret_values = []

        def redact(text):
            for secret in secret_values:
                text = text.replace(secret, "[redacted]")
            return text

        exit_code = 0
        model_server = None
        log_path = self.directory / f"{identifier}.log"
        try:
            env = self.workspace.environment()
            # Participation belongs to this recorded operation, never to an
            # ambient variable inherited by the Studio process.
            env.pop("HARNESS2_CONVERSATION_ID", None)
            env.pop("HARNESS2_CONVERSATION_WORKSPACE", None)
            if record["output_dir"]:
                env["HARNESS2_RUNS_DIR"] = record["output_dir"]
            if record.get("profile"):
                from ..profile_runtime import load_snapshot, run_environment, verify_configuration

                pinned = load_snapshot(Path(record["output_dir"]) / "profile-start.json")
                if any(record["profile"].get(key) != pinned[key] for key in record["profile"]):
                    raise ValueError("The experiment's pinned profile changed.")
                env = run_environment(env, Path(record["output_dir"]))
                verify_configuration(pinned["configuration"], env)
                from ..cli.local_run import _local_environment

                env = _local_environment(env)
            if record.get("conversation_id"):
                env = self.conversations.run_environment(record, env)
            secret_values = self.workspace.secrets()
            record.update(status="running", started_at=now())
            with log_path.open("a", encoding="utf-8", newline="\n") as log:
                if (
                    record.get("kind") in ("benchmark", "judge", "check")
                    and env.get("HARNESS2_UI_PROVIDER", "local") == "local"
                    and env.get("HARNESS2_LOCAL_RUNTIME", "external") == "managed"
                    and identifier not in self.cancelled
                ):
                    with self.lock:
                        record["phase"] = "Loading local model"
                        record["local_server"] = {"status": "loading"}
                        self._write(record)
                    self._event(record, "Loading the local model")
                    log.write("\n── Loading local model ──\n")
                    log.flush()
                    model_server = ManagedLocalServer(
                        env,
                        root=self.workspace.root,
                        log_path=self.directory / f"{identifier}-model.log",
                        cancelled=lambda: identifier in self.cancelled,
                    )
                    model_server.start()
                    env = model_server.environment
                    runtime_key = env.get("HARNESS2_LOCAL_API_KEY")
                    if runtime_key:
                        secret_values.append(runtime_key)
                    with self.lock:
                        record["local_server"] = {
                            "status": "running", "pid": model_server.pid
                        }
                        self._write(record)
                    log.write("Local model ready; it will unload after this operation.\n")
                    log.flush()
                    self._event(record, "Local model ready")
                for index, phase in enumerate(record["phases"]):
                    with self.lock:
                        if identifier in self.cancelled:
                            break
                        record.update(phase=phase["label"], phase_index=index)
                        phase["status"] = "running"
                        phase["started_at"] = now()
                        self._write(record)
                    self._event(record, phase["label"], "Starting this stage.")
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
                    deadline_timer = None
                    phase_timed_out = threading.Event()
                    try:
                        with self.lock:
                            self.procs[identifier] = proc
                            stopping = identifier in self.cancelled
                        if stopping:
                            processes.terminate_tree(proc)
                        if phase.get("timeout"):
                            def timed_out(target=proc, timeout_event=phase_timed_out):
                                timeout_event.set()
                                processes.terminate_tree(target)

                            deadline_timer = threading.Timer(phase["timeout"], timed_out)
                            deadline_timer.daemon = True
                            deadline_timer.start()
                        for line in proc.stdout:
                            log.write(redact(line))
                            log.flush()
                            for match in re.finditer(
                                r"=== harness eval(?P<done> done)?: (?:LABEL=)?(?P<label>[\w/.-]+)",
                                line,
                            ):
                                suffix = match["label"].rsplit("__", 1)[-1]
                                if re.fullmatch(r"(?:base|dom|inv|agg)\d+", suffix):
                                    name = {"base": "Baseline", "dom": "Domain-specific probe",
                                            "inv": "General-method probe", "agg": "Final combined run"}[
                                                re.sub(r"\d+$", "", suffix)]
                                    self._event(
                                        record,
                                        name + (" returned" if match["done"] else " started"),
                                        "This records an executor stage, which may retain partial output."
                                        if match["done"] else "Solving the task under this harness.",
                                    )
                        exit_code = proc.wait()
                    finally:
                        try:
                            if deadline_timer is not None:
                                deadline_timer.cancel()
                                if deadline_timer.ident is not None:
                                    deadline_timer.join(timeout=5)
                        finally:
                            try:
                                processes.terminate_tree(proc)
                            finally:
                                proc.stdout.close()
                                with self.lock:
                                    self.procs.pop(identifier, None)
                    if phase_timed_out.is_set():
                        exit_code = exit_code or 124
                    self._event(
                        record, phase["label"] + (" finished" if exit_code == 0 else " stopped"),
                        f"Exit code: {exit_code}.", kind="result",
                    )
                    with self.lock:
                        phase["status"] = (
                            "cancelled"
                            if identifier in self.cancelled
                            else "succeeded"
                            if exit_code == 0
                            else "failed"
                        )
                        phase["finished_at"] = now()
                        self._write(record)
                    if exit_code:
                        break
        except Exception as exc:
            exit_code = 1
            record["error"] = redact(str(exc))
            with log_path.open("a", encoding="utf-8") as log:
                log.write("\n" + record["error"] + "\n")
        finally:
            if model_server is not None:
                with self.lock:
                    record["phase"] = "Unloading local model"
                    record["local_server"]["status"] = "unloading"
                try:
                    model_server.close()
                except Exception as exc:
                    exit_code = exit_code or 1
                    record["error"] = redact(f"Could not unload the local model: {exc}")
                    record["local_server"]["status"] = "unload_failed"
                else:
                    record["local_server"]["status"] = "unloaded"
                    try:
                        self._event(record, "Local model unloaded", "Its owned server has stopped.", kind="result")
                    except OSError:
                        pass
                try:
                    with log_path.open("a", encoding="utf-8") as log:
                        log.write(
                            "\nLocal model unloaded.\n"
                            if record["local_server"]["status"] == "unloaded"
                            else "\n" + record["error"] + "\n"
                        )
                except OSError:
                    exit_code = exit_code or 1
                    record["error"] = record["error"] or "Could not save the operation log."
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
                if record.get("conversation_id"):
                    try:
                        self.conversations.end_run(record)
                    except (OSError, ValueError) as exc:
                        record["conversation_error"] = redact(str(exc))
                try:
                    tasks, _ = self.results(record)
                    record["progress"] = {
                        "done": sum(task["status"] == "ok" for task in tasks),
                        "failed": sum(task["status"] not in ("ok", "running") for task in tasks),
                        "total": record["total_tasks"],
                    }
                except OSError:
                    pass
                try:
                    self._write(record)
                finally:
                    self.active_id = None
                if record.get("profile"):
                    try:
                        from ..profile_runtime import profile_run_outcome
                        from ..profiles import ProfileStore

                        profile_run_outcome(
                            ProfileStore(self.workspace.root), record["profile"], Path(record["output_dir"]),
                            record,
                        )
                    except (OSError, ValueError) as exc:
                        record["profile_archive_error"] = redact(str(exc))
                        self._write(record)

    def get(self, identifier: str) -> dict:
        with self.lock:
            if identifier not in self.jobs:
                raise KeyError(identifier)
            record = copy.deepcopy(self.jobs[identifier])
        if record.get("conversation_id") and self.conversations is not None and record.get("status") in ACTIVE:
            from ..conversation_runtime import provenance

            record["conversation"] = provenance(self.conversations.store, record["conversation_id"])
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
        if record.get("output_dir") and record.get("kind") == "benchmark":
            from ..run_story import build_story

            try:
                record["story"] = build_story(Path(record["output_dir"]), record)
            except (OSError, ValueError):
                record["story"] = {
                    "overview": [], "events": [], "model_summary": "",
                    "summary_status": "unavailable",
                    "warnings": ["The readable report could not be loaded. Saved outputs remain available."],
                }
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
