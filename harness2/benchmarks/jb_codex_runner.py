# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at https://www.apache.org/licenses/LICENSE-2.0

"""Native JobBench Codex launcher; no Bash, GNU timeout, or Unix scratch path.

Keeps the adapter's task staging, output, trajectory and attempt-index contract.
The original upstream-derived shell launcher remains in third_party/jb_runner.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from ..processes import run as run_process
from ..processes import start, terminate_tree
from .jb_opencode_runner import PROMPT, _log, _nonempty, _stamp


def _session_id(path: Path) -> str:
    def walk(value):
        if isinstance(value, dict):
            for key in ("session_id", "sessionID", "thread_id", "threadID"):
                if isinstance(value.get(key), str) and value[key]:
                    return value[key]
            for item in value.values():
                if found := walk(item):
                    return found
        elif isinstance(value, list):
            for item in value:
                if found := walk(item):
                    return found
        return ""

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            if found := walk(json.loads(line)):
                return found
        except json.JSONDecodeError:
            continue
    return ""


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_one(
    task_id: str,
    model: str,
    *,
    tasks_base: Path,
    render_dir: Path | None,
    label: str,
    executable: str,
    timeout: float,
) -> bool:
    task_parent = tasks_base / task_id
    task_dir = task_parent / "task_folder"
    task_name = task_id.replace("/", "_")
    output = task_parent / "model_output" / label
    if _nonempty(output):
        print(f"SKIP (done): {task_id} ({label})", flush=True)
        return True
    logs = tasks_base / ".harness2-logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / f"jb_{task_name}_{label}_{_stamp()}.log"
    if not (task_dir / "TASK_INSTRUCTIONS.txt").is_file():
        _log(log, f"[ERROR] TASK_INSTRUCTIONS.txt missing in {task_dir}")
        return False
    traj_dir = task_parent / os.environ.get("TRAJ_DIR_NAME", "model_traj") / label
    traj_dir.mkdir(parents=True, exist_ok=True)
    trajectory = traj_dir / f"{task_name}_{label}_attempt1_{_stamp()}.jsonl"

    with tempfile.TemporaryDirectory(prefix="harness2-jb-codex-") as temporary:
        workspace = Path(temporary)
        shutil.copytree(task_dir, workspace / "task_folder")
        (workspace / "output").mkdir()
        if render_dir is not None:
            for entry in ("AGENTS.md", ".agents", "scripts", ".codex"):
                source = render_dir / entry
                if source.is_dir():
                    shutil.copytree(source, workspace / entry)
                elif source.is_file():
                    shutil.copy2(source, workspace / entry)
        prompt = PROMPT.format(
            task_folder=workspace / "task_folder",
            output_dir=workspace / "output",
            workspace=workspace,
        )
        cmd = [
            executable,
            "exec",
            prompt,
            "--model",
            model,
            "-c",
            'model_reasoning_effort="high"',
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "--dangerously-bypass-hook-trust",
            "--ephemeral",
            "--json",
            "-C",
            str(workspace),
            "--add-dir",
            str(workspace),
        ]
        started = _utc()
        _log(log, f"Running {task_id} ({label}) -> {trajectory}")
        with trajectory.open("w", encoding="utf-8") as out, log.open("a", encoding="utf-8") as err:
            try:
                code = run_process(
                    cmd,
                    cwd=workspace,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    timeout=timeout,
                ).returncode
            except subprocess.TimeoutExpired:
                code = 124
        delivered = _nonempty(workspace / "output")
        if code == 0 and not delivered:
            code = 1
        index = traj_dir / "attempt_index.tsv"
        new_index = not index.is_file()
        with index.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
            if new_index:
                writer.writerow(
                    (
                        "task_name",
                        "model_id",
                        "model_name",
                        "attempt",
                        "status",
                        "exit_code",
                        "timeout_seconds",
                        "session_id",
                        "started_at",
                        "finished_at",
                        "traj_file",
                    )
                )
            writer.writerow(
                (
                    task_name,
                    model,
                    label,
                    1,
                    "success" if code == 0 else "timeout" if code == 124 else "failed",
                    code,
                    timeout,
                    _session_id(trajectory),
                    started,
                    _utc(),
                    trajectory,
                )
            )
        if delivered:
            output.mkdir(parents=True, exist_ok=True)
            for item in (workspace / "output").iterdir():
                shutil.move(str(item), str(output / item.name))
        _log(log, f"Finished {task_id}: exit={code}, output={output if delivered else 'NONE'}")
        return code == 0 and delivered


def _parallel_tasks(task_ids: list[str], model: str, label: str, concurrency: int) -> bool:
    """Bound native task processes so interruption can stop every active rollout."""
    pending = iter(task_ids)
    active: list[subprocess.Popen] = []
    failed = False
    try:
        while True:
            while len(active) < concurrency:
                task_id = next(pending, None)
                if task_id is None:
                    break
                active.append(
                    start(
                        [sys.executable, "-m", "harness2.benchmarks.jb_codex_runner"],
                        env={
                            **os.environ,
                            "BENCHMARK_MODELS": model,
                            "LABEL": label,
                            "TASK_IDS": task_id,
                            "MAX_CONCURRENT_PER_MODEL": "1",
                        },
                        stdin=subprocess.DEVNULL,
                    )
                )
            if not active:
                return failed
            completed = [process for process in active if process.poll() is not None]
            if not completed:
                time.sleep(0.05)
            for process in completed:
                failed = process.wait() != 0 or failed
                terminate_tree(process)
                active.remove(process)
    finally:
        for process in active:
            terminate_tree(process)


def main() -> int:
    env = os.environ
    missing = [
        name
        for name in ("BENCHMARK_MODELS", "HARNESS2_CODEX_BIN", "TASKS_BASE_DIR")
        if not env.get(name)
    ]
    if missing:
        raise SystemExit(f"[ERROR] unset: {', '.join(missing)}")
    tasks_base = Path(env["TASKS_BASE_DIR"])
    task_ids = env.get("TASK_IDS", "").split() or [
        path.parent.relative_to(tasks_base).as_posix()
        for path in sorted(tasks_base.glob("*/task[0-9]*/task_folder"))
        if path.is_dir()
    ]
    if not task_ids:
        raise SystemExit(f"[ERROR] no task_folder directories found in {tasks_base}")
    max_concurrent = int(env.get("MAX_CONCURRENT_PER_MODEL", "1"))
    if max_concurrent < 1:
        raise SystemExit("[ERROR] MAX_CONCURRENT_PER_MODEL must be a positive integer")
    render_dir = Path(env["HARNESS_RENDER_DIR"]) if env.get("HARNESS_RENDER_DIR") else None
    failed = False
    for model in env["BENCHMARK_MODELS"].split():
        label = env.get("LABEL") or model.replace("/", "_").replace(".", "-") + "-codexcli"
        if not env.get("LABEL") and env.get("RUN_LABEL"):
            label += "-" + env["RUN_LABEL"].replace("/", "_").replace(" ", "_").replace(".", "-")
        # Models remain serial; task fan-out follows the original shell contract.
        if max_concurrent > 1:
            failed = _parallel_tasks(task_ids, model, label, max_concurrent) or failed
        else:
            for task_id in task_ids:
                ok = run_one(
                    task_id,
                    model,
                    tasks_base=tasks_base,
                    render_dir=render_dir,
                    label=label,
                    executable=env["HARNESS2_CODEX_BIN"],
                    timeout=float(env.get("TIMEOUT_PER_TASK", "3600")),
                )
                failed = failed or not ok
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
