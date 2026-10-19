# Windows

Harness² can run from PowerShell with Python 3.12 or 3.13. The local demo needs
only Python. Benchmark setup targets **Windows x64 with Python 3.12**; the pinned
JobBench and LAB dependencies do not all provide native Windows ARM64 wheels.
Model-backed benchmarks also need the tools and credentials below.

## Install and run the local demo

For the graphical interface, double-click `Launch Harness2.cmd` in the checkout.
It creates the main Python environment if needed and opens Harness² Studio in
your browser. The demo works without a model. After manual installation, run
`.\.venv\Scripts\harness2-ui.exe` instead. See [Studio](UI.md) for its controls
and [local models](LOCAL_MODELS.md) for llama.cpp setup.

Install Git, Python 3.12, and uv, then open PowerShell:

```powershell
git clone --branch windows-support https://github.com/Notesong/harness2.git
cd harness2
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -e '.[render]'
.\.venv\Scripts\harness2-demo.exe --output runs/demo --k 2
```

The demo executes both recursion modes without credentials. Results are written
to `runs/demo/demo.json`. These commands invoke the environment directly and
work without changing PowerShell's script execution policy.

For subsequent commands, put this environment on the current shell's PATH:

```powershell
$env:PATH = "$PWD\.venv\Scripts;$env:PATH"
```

Keep the checkout path reasonably short, such as `C:\src\harness2`; upstream
task archives and generated run directories can contain deeply nested paths.

Use a short source path when building a wheel from an extracted source archive,
too. On this Windows host, setuptools hit `MAX_PATH` while staging a vendored
file beneath a long build directory. The same offline build succeeded from a
short temporary directory. Harness²'s own long-path handling does not change
the packaging tool's behavior.

## Benchmark requirements

| Component | Windows behavior |
|---|---|
| Harness² demo, recursion, result saving and reporting | Native Python |
| OpenCode | Pinned Bun executable and upstream source run on the Windows host |
| Codex executor for JobBench and LAB | Native `codex.exe`; Python runners |
| JobBench and LAB judges | Separate native Python environments |
| WorkBuddy | Windows host orchestration; tasks and executors run in Linux Docker containers |

Install Node.js 20+, and ensure `git`, `npm`, and `uv` are on PATH. Vertex AI
also requires Google Cloud CLI and `gcloud` on PATH. The installer resolves
Windows `.cmd` launchers and creates each benchmark environment using
`.venv/Scripts/python.exe` automatically. Setup installs its own pinned Bun
runtime; a global Bun install is unnecessary.

LAB also needs Pandoc and Poppler (`pdftoppm` on PATH). Install Windows builds
of these tools before running its setup check. The pinned OpenCode supports
PowerShell on Windows, and can also use Git Bash. Harness² setup and benchmark
launchers do not require Bash.

For local JobBench with OpenCode, follow the [llama.cpp guide](LOCAL_MODELS.md);
Google credentials are unnecessary. For Vertex AI, copy the configuration
template and fill in model IDs available to your project:

```powershell
Copy-Item .env.example .env
notepad .env
gcloud auth application-default login
harness2-setup lab --dry-run
harness2-setup lab
harness2-check --bench lab
harness2 --bench lab --k 1 --mode parallel --only-domains antitrust-competition --max-tasks 1
harness2-judge --bench lab --k 1 --mode parallel
harness2-report --bench lab --k 1 --mode parallel
```

Use `jb` instead of `lab` for JobBench. See the [benchmark guides](BENCHMARKS.md)
for task selection and grading. PowerShell uses `$env:NAME = 'value'` for shell
environment variables; `.env` accepts the same `NAME=value` syntax on both
platforms. Forward slashes in `.env` paths avoid escaping issues.

## WorkBuddy and Docker Desktop

Install Docker Desktop and select its **Linux containers** engine. `docker info`
must succeed and report `OSType: linux`. Enable access to the drive containing
the checkout if Docker requests it. WorkBuddy's Linux task images retain their
Linux tools and behavior.

```powershell
harness2-setup wb --subset office
harness2-check --bench wb --subset office
harness2 --bench wb --subset office --k 1 --mode parallel --max-tasks 1
```

Setup downloads and verifies dataset archives in Python, and uses Docker to
build the Linux OpenCode mount. For Codex, it stages separate Windows and Linux
distributions so `codex.exe` is never mounted as a Linux executable. Container
images and benchmark datasets can require substantial disk space.

WorkBuddy retains its [custom Tencent license](../third_party/licenses/workbuddy-bench.LICENSE),
including the territorial provision concerning use within the European Union.
Windows changes do not change the license or dataset terms. JobBench retains
Apache 2.0; LAB and OpenCode retain MIT. Original notices remain bundled with
the runners and patches.

## Optional Codex executor

```powershell
uv pip install --python .venv/Scripts/python.exe -e '.[render,bridge]'
harness2-bridge
```

Copy the printed `HARNESS2_CODEX_*` values into `.env`, then run setup and the
benchmark with `--substrate cx`. For WorkBuddy, start the bridge with
`--host 0.0.0.0` and use its printed container endpoint. See
[Codex setup](SETUP.md#codex-executor) for details.

## Verification

```powershell
python -m unittest discover -s tests -v
python scripts/check_provenance.py
harness2-demo --output runs/demo --k 2
```

The tests cover local recursion and resume, concurrent file locking, executable
and environment paths, runner commands, and timeout cleanup without model calls.
The CI workflow is configured for Windows and Ubuntu with Python 3.12 and 3.13.
A complete model-backed benchmark also requires a working model connection,
datasets, and the relevant external tools; the local suite does not validate
those services. See [the validation record](VALIDATION.md) for actual local-model
execution and its limits.
