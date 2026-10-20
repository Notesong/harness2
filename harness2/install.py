# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Repeatable installation of benchmark tools and release-owned runner overlays."""

from __future__ import annotations

import hashlib
import json
import shlex
import shutil
import stat
import subprocess
import tarfile
import tempfile
import tomllib
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.request import urlopen

from . import config
from .local_models import validate_api_base_url

PATCHES = {
    "opencode": "opencode-compat.diff",
    "job-bench-eval": "job-bench-eval-judge.diff",
    "job-bench-eval-native-lock": "job-bench-eval-native-lock.diff",
    "harvey-labs": "harvey-labs-judge-vertex-anthropic.diff",
    "workbuddy-bench": "workbuddy-bench-runtime.diff",
}

RUNNERS = (
    "jb_runner/run_benchmark_codex_cli_adapt.sh",
    "lab_runner/opencode/run_opencode.py",
    "wb_runner/agents/oc_agent.py",
)


def missing_runners() -> str:
    """Explain which setup runners or patches are absent from third_party/, if any.

    A copy of the code without third-party material still runs the demo and tests, but
    setup needs these files to install a benchmark.
    """
    need = [config.THIRD_PARTY / "patches" / name for name in PATCHES.values()]
    need += [config.THIRD_PARTY / name for name in RUNNERS]
    absent = [path for path in need if not path.exists()]
    if not absent:
        return ""
    return (
        "benchmark runners and patches are not included in this copy "
        f"({len(absent)} of {len(need)} missing under third_party/); "
        "see third_party/README.md"
    )


def run(cmd, *, cwd=None, dry_run=False):
    cmd = config.command(cmd)
    rendered = subprocess.list2cmdline(cmd) if config.IS_WINDOWS else shlex.join(cmd)
    print((f"[{cwd}] " if cwd else "") + rendered, flush=True)
    if not dry_run:
        subprocess.run(cmd, cwd=cwd, check=True)


def clone(name: str, *, dry_run=False):
    repo = config.DEPENDENCIES / name
    pin = config.DEPENDENCY_PINS[name]
    if (repo / ".git").exists():
        revision = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if revision.returncode == 0:
            head = revision.stdout.strip()
            if head == pin:
                return
            raise RuntimeError(
                f"{repo} is at {head}, expected {pin}; move this checkout aside before setup"
            )
        origin = subprocess.run(
            ["git", "-C", str(repo), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if any(path.name != ".git" for path in repo.iterdir()) or (
            origin.stdout.strip() != config.DEPENDENCY_ORIGINS[name]
        ):
            raise RuntimeError(f"Incomplete checkout at {repo}; move it aside before setup")
    else:
        if repo.exists() and any(repo.iterdir()):
            raise RuntimeError(f"Refusing to overwrite nonempty {repo}")
        run(["git", "init", "-q", repo], dry_run=dry_run)
        run(
            ["git", "-C", repo, "remote", "add", "origin", config.DEPENDENCY_ORIGINS[name]],
            dry_run=dry_run,
        )
    # Benchmarks contain shell scripts and strict patches; do not let Git for
    # Windows silently rewrite the downloaded revision to CRLF.
    run(["git", "-C", repo, "config", "core.autocrlf", "false"], dry_run=dry_run)
    if config.IS_WINDOWS:
        run(["git", "-C", repo, "config", "core.longpaths", "true"], dry_run=dry_run)
    run(["git", "-C", repo, "fetch", "--depth", "1", "origin", pin], dry_run=dry_run)
    run(["git", "-C", repo, "checkout", "--detach", "FETCH_HEAD"], dry_run=dry_run)


def patch_keys(name: str) -> tuple[str, ...]:
    """Ordered independent overlays, preserving upgrades from an older patch set."""
    if name == "job-bench-eval":
        return (name, "job-bench-eval-native-lock")
    return (name,)


@contextmanager
def patch_source(name: str):
    """Present an LF patch even when this source tree was checked out with CRLF."""
    source = config.THIRD_PARTY / "patches" / PATCHES[name]
    body = source.read_bytes()
    if b"\r\n" not in body:
        yield source
        return
    with tempfile.TemporaryDirectory(prefix="harness2-patch-") as tmp:
        normalized = Path(tmp) / source.name
        normalized.write_bytes(body.replace(b"\r\n", b"\n"))
        yield normalized


def patch(name: str, *, dry_run=False):
    repo = config.DEPENDENCIES / name
    for key in patch_keys(name):
        if dry_run:
            source = config.THIRD_PARTY / "patches" / PATCHES[key]
            run(["git", "-C", repo, "apply", "--check", source], dry_run=True)
            run(["git", "-C", repo, "apply", source], dry_run=True)
            continue
        with patch_source(key) as source:
            base = ["git", "-C", repo, "apply", "--ignore-space-change"]
            if (
                subprocess.run(
                    config.command([*base, "--reverse", "--check", source]),
                    capture_output=True,
                ).returncode
                == 0
            ):
                continue
            run([*base, "--check", source])
            run([*base, source])


def overlays(name: str, *, dry_run=False):
    assets = config.THIRD_PARTY
    repo = config.DEPENDENCIES / name
    pairs = []
    if name == "harvey-labs":
        pairs = [
            (assets / "lab_runner" / "opencode", repo / "opencode"),
            (assets / "lab_runner" / "tts_lab", repo / "external" / "tts_lab"),
            (assets / "lab_runner" / "adapt_runs_cx", repo / "external" / "adapt_runs_cx"),
        ]
    if name == "workbuddy-bench":
        pairs = [
            (assets / "wb_runner" / "agents", repo / "src" / "workbuddy_bench" / "agents"),
            (assets / "wb_runner" / "configs" / "harnesses", repo / "configs" / "harnesses"),
        ]
    for source, dest in pairs:
        print(f"Copy runner assets: {source} -> {dest}", flush=True)
        if not dry_run:
            shutil.copytree(
                source,
                dest,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "PROVENANCE.md"),
            )


def workbuddy_models(*, dry_run=False):
    """Generate the two Vertex routes from the user's model IDs, never credentials."""
    solver = config.SOLVER_MODEL_OC.removeprefix("google-vertex/")
    judge = config.JUDGE_MODEL
    problem = ""
    if not config.SOLVER_MODEL_OC.startswith("google-vertex/gemini") or not judge.startswith(
        "gemini"
    ):
        problem = (
            "WorkBuddy setup requires a google-vertex/gemini... executor "
            "and a gemini... judge; choose IDs available in your project"
        )
    elif not config.VERTEX_PROJECT:
        problem = "Set GOOGLE_CLOUD_PROJECT for WorkBuddy's Vertex routes"
    if problem:
        if not dry_run:
            raise config.ConfigError(problem)
        print(f"Configure WorkBuddy solver and judge routes -- {problem}", flush=True)
        return
    for role, model in (("solver", solver), ("judge", judge)):
        payload = {
            "model": {
                "name": "google/" + model,
                "protocols": ["openai"],
                "backend_url_env": "HARNESS2_WB_VERTEX_URL",
                "backend_key_command": "gcloud auth application-default print-access-token",
                "backend_key_ttl": 1800,
                "thought_signature_passback": True,
                "params": {
                    "max_output_tokens": 64000 if role == "solver" else 16384,
                    "extra_body": {"reasoning_effort": "high" if role == "solver" else "low"},
                },
            }
        }
        path = config.wb_repo() / "configs" / "models" / "harness2" / f"{role}.yaml"
        print(f"Configure WorkBuddy {role}: {model}", flush=True)
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)

            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def codex(*, dry_run=False, containers=False):
    root = config.DEPENDENCIES / "codex"
    run(
        ["npm", "install", "--prefix", root, f"@openai/codex@{config.CODEX_VERSION}"],
        dry_run=dry_run,
    )
    if not dry_run:
        filename = config.executable_name("codex")
        candidates = list((root / "node_modules" / "@openai").glob(f"**/bin/{filename}"))
        if len(candidates) != 1:
            raise RuntimeError(
                f"Expected one native Codex binary in {root}; found {len(candidates)}"
            )
        dest = Path(config.CODEX_DIST)
        platform = candidates[0].parent.parent
        shutil.copytree(platform, dest, dirs_exist_ok=True)
        _share_with_containers(dest)
        binary = dest / filename
        if binary.is_symlink():
            binary.unlink()
        # A regular file also works in Docker bind mounts and requires no Windows
        # Developer Mode or elevated permission to create symlinks.
        shutil.copy2(dest / "bin" / filename, binary)
    if containers and config.IS_WINDOWS:
        _codex_linux(dry_run=dry_run)
    configure_codex(dry_run=dry_run)


def _codex_linux(*, dry_run=False):
    """Stage Linux Codex for WorkBuddy while preserving the native Windows CLI.

    Let the Linux Docker engine select the matching npm package and architecture.
    Copying the Windows executable into a Linux task container cannot work.
    """
    dockerfile = f"""FROM node:22-slim
RUN npm install --prefix /codex @openai/codex@{config.CODEX_VERSION}
RUN node -e 'const fs=require("fs"), p=require("path"); \
 const root="/codex/node_modules/@openai"; \
 const files=[]; function walk(d) {{ for (const e of fs.readdirSync(d, {{withFileTypes:true}})) {{ \
 const f=p.join(d,e.name); if(e.isDirectory()) walk(f); \
 else if(e.name==="codex" && p.basename(d)==="bin") files.push(f); }} }} \
 walk(root); if(files.length!==1) throw Error("Expected one Linux Codex binary"); \
 const dist=p.dirname(p.dirname(files[0])); fs.cpSync(dist,"/out",{{recursive:true}}); \
 fs.copyFileSync(files[0],"/out/codex"); fs.chmodSync("/out/codex",0o755);'
"""
    image = "harness2-codex-stage:" + config.CODEX_VERSION
    container = "harness2-codex-stage-" + uuid.uuid4().hex
    dest = Path(config.CODEX_CONTAINER_DIST)
    if dry_run:
        print(f"Stage Linux Codex {config.CODEX_VERSION} with Docker into {dest}")
        return
    with tempfile.TemporaryDirectory(prefix="harness2-codex-") as tmp:
        Path(tmp, "Dockerfile").write_text(dockerfile, encoding="utf-8", newline="\n")
        run(["docker", "build", "-t", image, tmp])
        run(["docker", "create", "--name", container, image])
        try:
            dest.mkdir(parents=True, exist_ok=True)
            run(["docker", "cp", f"{container}:/out/.", dest])
        finally:
            run(["docker", "rm", container])


def _share_with_containers(root: Path) -> None:
    """Make the staged dist readable by the container user it is bind-mounted for.

    The copy inherits the installing shell's umask, and under a private one (077) the
    WorkBuddy agent, which runs as a different user, cannot open /opt/codex at all.
    """
    if config.IS_WINDOWS:
        return
    for path in (root, *root.rglob("*")):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            continue
        extra = stat.S_IRGRP | stat.S_IROTH
        if stat.S_ISDIR(mode) or mode & stat.S_IXUSR:
            extra |= stat.S_IXGRP | stat.S_IXOTH
        path.chmod(mode | extra)


CODEX_CONFIG_REMEDY = "rerun harness2-setup <bench> --substrate cx"
CODEX_PROVIDER = "harness2"


def configure_codex(*, dry_run=False):
    if dry_run and not config.env("HARNESS2_CODEX_BASE_URL"):
        print("Configure Codex -- set HARNESS2_CODEX_BASE_URL before installation", flush=True)
        return
    config.require("HARNESS2_CODEX_BASE_URL", needed_by="Codex setup")
    validate_api_base_url(config.CODEX_BASE_URL)
    body = (
        f"model = {json.dumps(config.SOLVER_MODEL_CX)}\n"
        f"model_provider = {json.dumps(CODEX_PROVIDER)}\n"
        f'[model_providers.{CODEX_PROVIDER}]\nname = "Configured Responses endpoint"\n'
        f"base_url = {json.dumps(config.CODEX_BASE_URL)}\n"
        'wire_api = "responses"\nenv_key = "VERTEX_PROXY_KEY"\n'
    )
    if not dry_run:
        config.CODEX_HOME_DIR.mkdir(parents=True, exist_ok=True)
        (config.CODEX_HOME_DIR / "config.toml").write_text(body, encoding="utf-8")


def codex_config_error() -> str | None:
    """Why `CODEX_HOME/config.toml` no longer matches the live Codex configuration.

    `configure_codex` bakes the endpoint and the model into a file that the Codex CLI
    reads at rollout time, and setup is the only thing that writes it. Nothing else
    re-reads `HARNESS2_CODEX_BASE_URL` / `HARNESS2_CODEX_MODEL` afterwards, so callers
    verify the derived file here before a rollout is paid for -- otherwise a changed
    endpoint is silently ignored and the run contacts the stale one. Returns None when
    the file agrees with the environment, else a message naming the remedy.
    """
    if config.CODEX_BASE_URL:
        try:
            validate_api_base_url(config.CODEX_BASE_URL)
        except ValueError as exc:
            return f"{exc} {CODEX_CONFIG_REMEDY}"
    path = config.CODEX_HOME_DIR / "config.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return (
            f"CODEX_HOME has no readable config.toml: {path} -- the configured provider "
            f"block there routes Codex to your Responses endpoint; {CODEX_CONFIG_REMEDY}"
        )
    except tomllib.TOMLDecodeError as exc:
        return f"{path} is not valid TOML ({exc}); {CODEX_CONFIG_REMEDY}"
    provider = data.get("model_provider")
    block = (data.get("model_providers") or {}).get(provider) or {}
    stale = [
        "base_url differs from configuration" if key == "base_url"
        else f"{key} is {found!r}, configuration says {want!r}"
        for key, want, found in (
            ("base_url", config.CODEX_BASE_URL, block.get("base_url")),
            ("model", config.SOLVER_MODEL_CX, data.get("model")),
        )
        if found != want
    ]
    if stale:
        return f"{path} is stale ({'; '.join(stale)}); {CODEX_CONFIG_REMEDY}"
    return None


def download_jobbench(*, dry_run=False):
    """Install the JobBench task tree from its public Hugging Face dataset repo.

    The dataset is public, so an executor with web access may retrieve it;
    see `docs/BENCHMARKS.md`.
    """
    repo = config.jb_repo()
    dest = config.long_path(repo / "dataset" / "main")
    if (dest / ".harness2-download.json").is_file():
        return

    code = """from huggingface_hub import HfApi, snapshot_download
from pathlib import Path
import json, sys
revision = HfApi().dataset_info("JobBench/job-bench").sha
snapshot_download("JobBench/job-bench", repo_type="dataset", revision=revision,
                  allow_patterns=["dataset/**"], local_dir=sys.argv[1])
(Path(sys.argv[1]) / "revision.json").write_text(json.dumps({"revision": revision}), encoding="utf-8")
"""
    if dry_run:
        print("Download JobBench/job-bench dataset/** from Hugging Face; record its revision")
        return
    if dest.exists() and any(dest.iterdir()):
        raise RuntimeError(
            f"{dest} already contains data without a download record; move it aside first"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="download-", dir=dest.parent) as tmp:
        run([config.venv_python(repo), "-c", code, tmp])
        staged = Path(tmp) / "dataset"
        if not list(staged.glob("*/*/task_folder/TASK_INSTRUCTIONS.txt")):
            raise RuntimeError("JobBench download did not contain task instructions")
        if dest.exists():
            dest.rmdir()
        staged.rename(dest)
        shutil.copy2(Path(tmp) / "revision.json", dest / ".harness2-download.json")


def download_workbuddy(subsets, *, dry_run=False):
    """Fetch the upstream archives with required checksums, without Bash or tar.

    Keep the upstream dataset names and endpoint overrides. Stage extraction on
    the destination filesystem so an interrupted setup never creates a partial
    dataset that a later run mistakes for a complete download.
    """
    datasets = config.long_path(config.wb_repo() / "datasets")
    hf_repo = config.env("WB_BENCH_HF_REPO", "tencent/workbuddy-bench")
    endpoint = config.env("WB_BENCH_HF_ENDPOINT", "https://huggingface.co").rstrip("/")
    base = f"{endpoint}/datasets/{hf_repo}/resolve/main"
    for subset in dict.fromkeys(subsets):
        if subset not in ("office", "web", "code", "sec"):
            raise ValueError(f"Unknown WorkBuddy subset: {subset}")
        name = f"wb-bench-{subset}-v1.0"
        target = datasets / name
        if target.is_dir():
            print(f"Skip existing WorkBuddy dataset: {target}", flush=True)
            continue
        print(f"Download and verify WorkBuddy {name}.tar.gz", flush=True)
        if dry_run:
            continue
        datasets.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f".stage-{subset}-", dir=datasets) as tmp:
            stage = Path(tmp)
            filename = name + ".tar.gz"
            with urlopen(base + "/SHA256SUMS", timeout=120) as response:
                checksums = response.read().decode("utf-8")
            expected = next(
                (
                    fields[0].lower()
                    for line in checksums.splitlines()
                    if len(fields := line.split()) == 2 and fields[1].lstrip("*") == filename
                ),
                None,
            )
            if expected is None or len(expected) != 64:
                raise RuntimeError(f"SHA256SUMS has no SHA-256 checksum for {filename}")
            archive = stage / filename
            digest = hashlib.sha256()
            with urlopen(base + "/" + filename, timeout=120) as response, archive.open("wb") as out:
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
                    out.write(chunk)
            if digest.hexdigest() != expected:
                raise RuntimeError(f"Checksum failed for WorkBuddy {filename}")
            extracted = stage / "extracted"
            with tarfile.open(archive, "r:gz") as bundle:
                bundle.extractall(extracted, filter="data")
            source = extracted / name
            if not source.is_dir():
                raise RuntimeError(f"WorkBuddy {filename} did not contain {name}/")
            source.rename(target)


# WorkBuddy config-loader integration; the upstream license is retained at
# third_party/licenses/workbuddy-bench.LICENSE.
_WORKBUDDY_BUILD = r"""
import subprocess
import sys
from pathlib import Path
from workbuddy_bench.runner.config_loaders import load_harness_config, select_harness_mount_image

root = Path.cwd()
resolved = load_harness_config(sys.argv[1], root / "configs")
harness = resolved.harness
mount = harness.get("mount") or {}
build = mount.get("build") or {}
image = select_harness_mount_image(mount, "local")
if not image:
    raise RuntimeError("WorkBuddy harness declares no local mount image")
family = mount.get("family") or mount.get("name") or ""
dockerfile = Path(mount.get("dockerfile") or build.get("dockerfile") or
                  root / "configs" / "harnesses" / family / "docker" / "Dockerfile")
context = Path(mount.get("context") or build.get("context") or dockerfile.parent)
args = build.get("args") or {}
cmd = ["docker", "build", "-f", str(dockerfile)]
for key, value in args.items():
    cmd += ["--build-arg", f"{key}={value}"]
cmd += ["-t", str(image), str(context)]
subprocess.run(cmd, check=True)
"""


def build_workbuddy_mount(*, dry_run=False):
    """Build the declared Linux OpenCode image using the upstream config loader."""
    if dry_run:
        print(f"Build WorkBuddy's Linux OpenCode {config.WB_OC_HARNESS_VER} mount with Docker")
        return
    repo = config.wb_repo()
    run(
        [config.venv_python(repo), "-c", _WORKBUDDY_BUILD, f"opencode/{config.WB_OC_HARNESS_VER}"],
        cwd=repo,
        dry_run=dry_run,
    )


def require_linux_docker() -> None:
    """WorkBuddy task and executor images require Docker's Linux engine."""
    result = subprocess.run(
        config.command(["docker", "info", "--format", "{{.OSType}}"]),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
        check=True,
    )
    if result.stdout.strip() != "linux":
        raise RuntimeError("WorkBuddy requires Docker Desktop running Linux containers")
