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

"""Install a benchmark's pinned tools, runner overlays, dependencies, and data."""

from __future__ import annotations

import argparse
import shutil
import subprocess

from harness2 import config, install


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("benchmark", choices=("jb", "lab", "wb"))
    p.add_argument(
        "--subset",
        choices=("office", "web", "code"),
        default=None,
        help="wb only: one subset to install; omitted installs office, web, and code",
    )
    p.add_argument("--substrate", choices=("oc", "cx"), default="oc")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    if args.subset and args.benchmark != "wb":
        p.error("--subset only applies to wb")
    dry = args.dry_run
    missing = install.missing_runners()
    if missing and not dry:
        p.exit(1, f"setup failed: {missing}\n")
    if missing:
        print(f"Note: {missing}")
    name = {"jb": "job-bench-eval", "lab": "harvey-labs", "wb": "workbuddy-bench"}[args.benchmark]
    try:
        if not dry:
            for binary in ("git", "uv", "npm") + (("docker",) if args.benchmark == "wb" else ()):
                if not shutil.which(binary):
                    raise RuntimeError(f"Missing {binary}; see README.md")
            if args.benchmark == "wb":
                install.require_linux_docker()
        install.clone("opencode", dry_run=dry)
        install.patch("opencode", dry_run=dry)
        install.run(
            ["npm", "install", "--prefix", config.DEPENDENCIES / "tools", "bun@1.3.11"], dry_run=dry
        )
        bun_args = [config.bun_binary(), "install", "--frozen-lockfile", "--ignore-scripts"]
        if config.IS_WINDOWS:
            # The CLI does not need desktop/web dependencies. Copying a flat
            # layout avoids Bun's Windows isolated-linker/hardlink failures.
            bun_args += [
                "--filter",
                "./packages/opencode",
                "--network-concurrency",
                "1",
                "--backend",
                "copyfile",
                "--linker",
                "hoisted",
            ]
        install.run(
            bun_args,
            cwd=config.OPENCODE_DIR,
            dry_run=dry,
        )
        install.clone(name, dry_run=dry)
        install.patch(name, dry_run=dry)
        install.overlays(name, dry_run=dry)
        repo = config.DEPENDENCIES / name
        install.run(["uv", "sync", "--locked", "--python", "3.12"], cwd=repo, dry_run=dry)
        if args.benchmark == "jb":
            install.run(
                ["uv", "pip", "install", "--python", config.venv_python(repo), "python-docx"],
                dry_run=dry,
            )
            install.download_jobbench(dry_run=dry)
        elif args.benchmark == "lab":
            install.run(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    config.venv_python(repo),
                    "google-auth[requests]",
                ],
                dry_run=dry,
            )
        else:
            install.workbuddy_models(dry_run=dry)
            subsets = [args.subset] if args.subset else ["office", "web", "code"]
            install.download_workbuddy(subsets, dry_run=dry)
            if args.substrate == "oc":
                install.build_workbuddy_mount(dry_run=dry)
        if args.substrate == "cx":
            install.codex(dry_run=dry, containers=args.benchmark == "wb")
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        p.exit(1, f"setup failed: {exc}\n")
    print(
        "Setup plan complete."
        if dry
        else "Installation complete. Run harness2-check before an experiment."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
