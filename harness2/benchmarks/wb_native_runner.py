# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at https://www.apache.org/licenses/LICENSE-2.0

"""Run Harness² WorkBuddy jobs on a native host using the upstream Python APIs.

The benchmark still executes tasks in Linux Docker containers. This replaces
the host shell orchestration (run.sh, curl, fuser and nohup) for selected jobs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

from .. import config
from ..processes import remove_tree, run, start, terminate_tree


def _shell_vars(output: str) -> dict[str, str]:
    """Read upstream's shell-quoted metadata without evaluating shell code."""
    result = {}
    for line in output.splitlines():
        name, separator, value = line.partition("=")
        if separator and name.replace("_", "").isalnum():
            words = shlex.split(value)
            result[name] = words[0] if words else ""
    return result


def _free_port(preferred: int) -> int:
    for port in range(preferred, 65536):
        with socket.socket() as probe:
            try:
                probe.bind(("0.0.0.0", port))
            except OSError:
                continue
            return port
    raise RuntimeError("No free port for the job-private WorkBuddy proxy")


def _health(port: int) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
        return json.load(response)


def _wait_routes(port: int, routes: list[str], proc: subprocess.Popen | None) -> None:
    deadline = time.monotonic() + float(os.environ.get("PROXY_ROUTE_WAIT", "20"))
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError("WorkBuddy proxy exited; see this instance's proxy log")
        try:
            state = _health(port)
            if state.get("status") == "ok" and all(r in state.get("routes", {}) for r in routes):
                return
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    raise RuntimeError(f"WorkBuddy proxy routes {routes} were not ready on port {port}")


def _staged_root(
    repo: Path, manifest: dict, *, instance: str, original_dataset: str
) -> Path | None:
    """Recognize only this instance's short Windows staging directory."""
    if manifest.get("instance_id") != instance:
        return None
    dataset = manifest.get("dataset")
    decisions = manifest.get("decisions") or {}
    if not isinstance(dataset, str) or not isinstance(decisions, dict):
        return None
    if (
        decisions.get("dataset_staged_to") != dataset
        or decisions.get("dataset_staged_from") != original_dataset
    ):
        return None
    relative = Path(dataset)
    original = Path(original_dataset)
    if relative.anchor or len(relative.parts) != 3 or ".." in relative.parts:
        return None
    generated, dataset_name, tasks_name = relative.parts
    if (
        not re.fullmatch(r"h2[a-z0-9_]{8}", generated)
        or dataset_name != original.parent.name
        or tasks_name != original.name
    ):
        return None
    repo = repo.resolve()
    staged = (repo / generated).resolve()
    if staged.parent != repo or staged.name != generated:
        return None
    # A reparse point in the dataset path must not authorize removal elsewhere.
    if (repo / relative).resolve() != staged / dataset_name / tasks_name:
        return None
    return staged


def execute(job_slug: str, *, dry_run: bool = False) -> int:
    import yaml

    repo = Path.cwd()
    config.load_dotenv(repo / ".env")
    env = {**os.environ, "PYTHONUTF8": "1"}
    env["PYTHONPATH"] = os.pathsep.join(
        (str(repo / "src"), str(config.PKG.parent), env.get("PYTHONPATH", ""))
    )

    def module(name: str, *args, capture: bool = False) -> str:
        result = run(
            [sys.executable, "-m", f"workbuddy_bench.{name}", *map(str, args)],
            env=env,
            cwd=repo,
            check=True,
            capture_output=capture,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return result.stdout or ""

    job_path = repo / "configs" / "jobs" / f"{job_slug}.yaml"
    job = yaml.safe_load(job_path.read_text(encoding="utf-8")) or {}
    model_path = repo / "configs" / "models" / f"{job['model']}.yaml"
    model = yaml.safe_load(model_path.read_text(encoding="utf-8"))["model"]
    from workbuddy_bench.runner.config_loaders import load_harness_config

    harness = load_harness_config(str(job["harness"]), repo / "configs").harness
    agent = harness.get("name", "")
    metadata = _shell_vars(module("runner.harness_adapters", agent, capture=True))
    connection = job.get("model_connection") or "local_proxy"
    if connection not in ("direct", "local_proxy"):
        raise RuntimeError(f"Unsupported model_connection: {connection!r}")
    protocols = model.get("protocols") or [model.get("protocol", "openai")]
    if isinstance(protocols, str):
        protocols = [protocols]
    protocols = [model.get("protocol", "openai"), *protocols]
    extra_body = (model.get("params") or {}).get("extra_body") or (
        job.get("model_params_override") or {}
    ).get("extra_body")
    if connection == "direct" and (metadata.get("HARNESS_PROTOCOL") not in protocols or extra_body):
        raise RuntimeError(
            "This job requires model_connection: local_proxy for its protocol/extra_body"
        )
    shards = int(env.get("SHARDS", "1"))
    if shards < 1 or (env.get("SHARD_CONCURRENCY") and int(env["SHARD_CONCURRENCY"]) < 1):
        raise RuntimeError("SHARDS and SHARD_CONCURRENCY must be positive integers")

    instance = f"{job_slug}-{uuid.uuid4().hex[:12]}"
    state_dir = repo / "scripts" / "logs" / "instances" / instance
    state_dir.mkdir(parents=True)
    manifest_path = state_dir / "manifest.json"
    proxy = None
    proxy_log = None
    manifest = {}
    try:
        stage = ["--no-stage"] if dry_run else []
        module(
            "runner.resolve_manifest",
            "--job-config",
            job_path,
            "--model-config",
            model_path,
            "--instance-id",
            instance,
            "--instance-dir",
            state_dir,
            "--harness-backend",
            "local",
            *stage,
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if dry_run:
            print(json.dumps(manifest, indent=2, ensure_ascii=False))
            return 0
        mount = manifest.get("harness_mount") or {}
        if mount.get("required"):
            image = mount.get("selected_image")
            if (
                not image
                or run(
                    ["docker", "image", "inspect", image],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode
            ):
                raise RuntimeError(
                    f"Required harness mount image is missing: {image}. "
                    "Run harness2-setup wb --substrate oc first."
                )
        module("runner.validate_model", "--manifest", manifest_path)
        if connection == "local_proxy":
            shared = env.get("SHARED_PROXY") == "1"
            port = int(env.get("PROXY_PORT", "3456"))
            if not shared:
                port = _free_port(port)
            proxy_url = f"http://{env.get('PROXY_HOST', 'host.docker.internal')}:{port}"
            manifest.setdefault("connection", {}).update(
                proxy_url=proxy_url,
                harness_base_url=proxy_url,
                proxy_location="host",
                uses_proxy=True,
            )
            manifest["proxy_url"] = proxy_url
            manifest_path.write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            logs = Path(env.get("PROXY_LOG_DIR", str(repo / "scripts" / "logs" / "proxy")))
            logs.mkdir(parents=True, exist_ok=True)
            proxy_config = state_dir / "proxy.yaml"
            common = [
                "--manifest",
                manifest_path,
                "--model-config",
                model_path,
                "--port",
                port,
                "--log-dir",
                logs,
                "--max-concurrent",
                env.get("PROXY_MAX_CONCURRENT", "64"),
                "--default-experiment",
                job_slug,
                "--default-harness",
                agent,
            ]
            routes = [manifest["model_route"]] if manifest.get("model_route") else []
            if shared:
                shared_config = env.get("SHARED_CONFIG", str(logs / "shared-proxy.yaml"))
                output = module(
                    "runner.proxy_config", *common, "--shared", shared_config, capture=True
                )
                contributed = output.rsplit(" routes=", 1)
                if len(contributed) != 2:
                    raise RuntimeError(f"proxy_config did not report shared routes: {output}")
                routes = contributed[1].strip().split(",")
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/admin/reload", method="POST"
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    if json.load(response).get("status") != "ok":
                        raise RuntimeError("Shared WorkBuddy proxy reload failed")
            else:
                module("runner.proxy_config", *common, "--output", proxy_config)
                proxy_log = (logs / f"proxy-{instance}.log").open("w", encoding="utf-8")
                proxy = start(
                    [
                        sys.executable,
                        "-m",
                        "workbuddy_bench.proxy",
                        "--config",
                        str(proxy_config),
                        "--port",
                        str(port),
                        "--log-dir",
                        str(logs),
                    ],
                    cwd=repo,
                    env=env,
                    stdout=proxy_log,
                    stderr=subprocess.STDOUT,
                )
            if metadata.get("HARNESS_USES_ANTHROPIC_ENV") == "1":
                env.setdefault("ANTHROPIC_API_KEY", "dummy-for-proxy")
                env["ANTHROPIC_BASE_URL"] = proxy_url
            elif metadata.get("HARNESS_PROXY_URL_ENV"):
                env[metadata["HARNESS_PROXY_URL_ENV"]] = proxy_url
            if env.get("SKIP_PROXY_ROUTE_CHECK") != "1":
                _wait_routes(port, routes, proxy)
        elif (
            metadata.get("HARNESS_BACKEND_BASE_ENV")
            and metadata.get("HARNESS_USES_ANTHROPIC_ENV") != "1"
        ):
            for target, source in (
                ("HARNESS_BACKEND_BASE_ENV", "backend_url_env"),
                ("HARNESS_BACKEND_KEY_ENV", "backend_key_env"),
            ):
                if metadata.get(target) and not env.get(metadata[target]):
                    env[metadata[target]] = env.get(model.get(source, ""), "")

        users = _shell_vars(
            module("runner.bench_config", job_path, "--emit-user-vars", capture=True)
        )
        prepare = []
        for key, option in (("AGENT_USER", "--agent-user"), ("VERIFIER_USER", "--verifier-user")):
            if users.get(key):
                prepare.extend((option, users[key]))
        module("runner.prepare_tasks", manifest["dataset"], *prepare)
        args = ["--config", job_path, "--shards", shards, "--manifest", manifest_path]
        if env.get("SHARD_CONCURRENCY"):
            args.extend(("--per-shard-concurrency", env["SHARD_CONCURRENCY"]))
        for key, option in (
            ("NO_FORCE_BUILD", "--no-force-build"),
            ("DISABLE_VERIFICATION", "--disable-verification"),
        ):
            if env.get(key) == "1":
                args.append(option)
        # This path also runs the host-side LLM judge while the proxy is alive.
        module("runner.sharded_eval", *args)
        if connection == "local_proxy":
            try:
                module("runner.split_proxy_log", "--manifest", manifest_path)
            except subprocess.CalledProcessError:
                print(
                    "WorkBuddy proxy-log split failed; verifier results are available.",
                    file=sys.stderr,
                )
        return 0
    finally:
        if proxy is not None:
            terminate_tree(proxy, grace=5)
        if proxy_log is not None:
            proxy_log.close()
        staged = _staged_root(
            repo, manifest, instance=instance, original_dataset=str(job.get("dataset", ""))
        )
        if staged is not None:
            remove_tree(staged, ignore_errors=True)
        # The POSIX layout has a known per-instance path; retain cleanup for it
        # and for existing checkouts that have not adopted short Windows paths.
        legacy = repo / ".workspace" / "tmp" / "staged" / instance
        if legacy.resolve() == legacy:
            remove_tree(legacy, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return execute(args.job, dry_run=args.dry_run or os.environ.get("DRY_RUN") == "1")


if __name__ == "__main__":
    raise SystemExit(main())
