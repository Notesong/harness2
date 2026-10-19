# SPDX-License-Identifier: Apache-2.0
"""Check, refine, judge, and report one local JobBench experiment.

The model runtime stays available through grading, reporting, and a final wrap-up. A managed runtime is
closed on completion, failure, or interruption; external servers remain running.
Each invocation uses a new output directory to keep experiment identities apart.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .. import config, processes
from ..local_models import profile, profile_defaults
from ..local_server import ManagedLocalServer
from ..locking import file_lock
from ..run_story import append_event, build_story, deterministic_story, write_story
from . import _common as C


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, formatter_class=C.HelpFormatter)
    result.add_argument("--bench", choices=("jb",), default="jb")
    result.add_argument("--substrate", choices=("oc",), default="oc")
    C.add_run_identity_args(result.add_argument_group("experiment identity"))
    result.add_argument("--split", choices=("all", "train", "test"), default="all")
    result.add_argument("--split-file", type=Path)
    result.add_argument(
        "--only-domains", default="", help="comma list; omit to include every domain"
    )
    result.add_argument("--max-tasks", type=int, default=1, help="task limit per selected domain")
    result.add_argument("--workers", type=int, default=1, help="domains to run concurrently")
    result.add_argument("--output", type=Path, help="new or empty experiment directory")
    result.add_argument(
        "--dry-run", action="store_true", help="preview tasks without starting a model"
    )
    return result


def commands(args: argparse.Namespace) -> list[tuple[str, list[str]]]:
    """Keep benchmark, model-independent run identity, and selection flags aligned."""
    cell = ["--bench", args.bench, "--substrate", args.substrate]
    identity = [*cell, "--k", str(args.k), "--mode", args.modes]
    if args.seed is not None:
        identity += ["--seed", str(args.seed)]
    sweep = [
        *identity,
        "--split",
        args.split,
        "--max-tasks",
        str(args.max_tasks),
        "--workers",
        str(args.workers),
    ]
    if args.only_domains:
        sweep += ["--only-domains", args.only_domains]
    if args.split_file:
        sweep += ["--split-file", str(args.split_file.resolve())]
    prefix = [sys.executable, "-u", "-m"]
    return [
        ("Check benchmark setup", [*prefix, "harness2.cli.check", *cell]),
        ("Run and refine harnesses", [*prefix, "harness2.cli.run_sweep", *sweep]),
        ("Judge adopted results", [*prefix, "harness2.cli.judge", *identity]),
        ("Build score report", [*prefix, "harness2.cli.report", *identity]),
    ]


@contextmanager
def _stop_signals(cancelled: threading.Event):
    previous = {}

    def stop(_signal, _frame):
        cancelled.set()
        raise KeyboardInterrupt

    if threading.current_thread() is threading.main_thread():
        for name in ("SIGTERM", "SIGHUP"):
            if signum := getattr(signal, name, None):
                previous[signum] = signal.getsignal(signum)
                signal.signal(signum, stop)
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _local_environment() -> dict[str, str]:
    environment = profile_defaults(os.environ)
    local = profile(environment)
    if local is None:
        raise ValueError("Configure HARNESS2_LOCAL_BASE_URL and HARNESS2_LOCAL_MODEL first.")
    if (
        any(
            environment[name] != local.opencode_model
            for name in ("HARNESS2_SOLVER_MODEL", "HARNESS2_IMPROVER_MODEL")
        )
        or environment["HARNESS2_JUDGE_MODEL"] != local.model
        or environment["HARNESS2_JUDGE_BASE_URL"].rstrip("/") != local.base_url
    ):
        raise ValueError(
            "This command requires the configured local model for solving, improving, "
            "and judging. Remove conflicting model or judge endpoint overrides."
        )
    return {**environment, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}


def _run(args: argparse.Namespace) -> int:
    if min(args.k, args.workers, args.max_tasks) < 1:
        raise ValueError("--k, --workers, and --max-tasks must be positive integers.")
    C.parse_modes(args.modes)
    phases = commands(args)
    if args.dry_run:
        return processes.run(
            [*phases[1][1], "--dry-run"], cwd=config.ROOT, stdin=subprocess.DEVNULL
        ).returncode
    environment = _local_environment()
    if args.output:
        output = args.output.expanduser().resolve()
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        output = config.RUNS / "local" / f"{stamp}-{uuid.uuid4().hex[:8]}"
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError(f"Choose a new or empty --output directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    environment["HARNESS2_RUNS_DIR"] = str(output)
    phases.append(
        (
            "Write readable wrap-up",
            [sys.executable, "-u", "-m", "harness2.cli.summarize", "--output", str(output)],
        )
    )
    cancelled = threading.Event()
    print(f"Experiment: {output}", flush=True)
    with file_lock(output / ".local-run.lock", blocking=False), _stop_signals(cancelled):
        runtime = ManagedLocalServer(
            environment,
            root=config.ROOT,
            log_path=output / "local-server.log",
            cancelled=cancelled.is_set,
        )
        try:
            append_event(
                output,
                "Preparing model runtime",
                "Starting the configured managed runtime, or retaining the external connection.",
                "model",
            )
            runtime.start()
            append_event(
                output,
                "Model runtime prepared",
                "The managed model is ready."
                if environment.get("HARNESS2_LOCAL_RUNTIME") == "managed"
                else "Using the configured external endpoint; its lifecycle remains external.",
                "model",
            )
            for index, (label, command) in enumerate(phases, 1):
                print(f"\n[{index}/{len(phases)}] {label}", flush=True)
                append_event(output, label + " started", "", "phase")
                is_report = "harness2.cli.report" in command
                is_summary = "harness2.cli.summarize" in command
                try:
                    result = processes.run(
                        command,
                        cwd=config.ROOT,
                        env=runtime.environment,
                        stdin=subprocess.DEVNULL,
                        capture_output=is_report,
                        **({"timeout": 300} if is_summary else {}),
                        **(
                            {"text": True, "encoding": "utf-8", "errors": "replace"}
                            if is_report
                            else {}
                        ),
                    )
                except (OSError, subprocess.SubprocessError):
                    if not is_summary:
                        raise
                    _wrapup_fallback(output)
                    append_event(
                        output,
                        label + " unavailable",
                        "The benchmark result is preserved; a factual wrap-up was used.",
                        "warning",
                    )
                    continue
                if is_report:
                    (output / "report.txt").write_text(result.stdout or "", encoding="utf-8")
                    print(result.stdout or "", end="", flush=True)
                    if result.stderr:
                        print(result.stderr, end="", file=sys.stderr, flush=True)
                if result.returncode:
                    if is_summary:
                        _wrapup_fallback(output)
                        append_event(
                            output,
                            label + " unavailable",
                            "The benchmark result is preserved; a factual wrap-up was used.",
                            "warning",
                        )
                        continue
                    append_event(
                        output,
                        label + " failed",
                        f"Recorded exit code: {result.returncode}.",
                        "error",
                    )
                    print(
                        f"Stopped after {label.lower()} (exit {result.returncode}). "
                        "Completed results are preserved.",
                        file=sys.stderr,
                        flush=True,
                    )
                    return result.returncode
                append_event(output, label + " finished", "Recorded exit code: 0.", "phase")
        finally:
            runtime.close()
            append_event(
                output,
                "Model runtime released",
                "Owned model processes were closed. External servers remain under their owner's control.",
                "model",
            )
            if (output / "run-report.json").is_file():
                try:
                    write_story(output, build_story(output))
                except OSError:
                    print(
                        "The final timeline could not be saved; existing results remain available.",
                        file=sys.stderr,
                    )
    print(f"\nComplete. Results and report: {output}", flush=True)
    return 0


def _wrapup_fallback(output: Path) -> None:
    try:
        story = deterministic_story(output)
        story["summary_status"] = "unavailable"
        story["warnings"].append(
            "Model wrap-up: the summary step did not finish. The factual report preserves the recorded benchmark result."
        )
        write_story(output, story)
    except OSError:
        print(
            "The optional wrap-up could not be saved; benchmark results remain available.",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return _run(args)
    except KeyboardInterrupt:
        print(
            "\nStopped. Completed results are preserved; owned processes are closed.",
            file=sys.stderr,
        )
        return 130
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
