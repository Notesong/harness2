# Validation record

This record separates local portability checks from validation of an installed
benchmark or a model service. The Windows review ran on Windows 11 AMD64
(`10.0.26300`) with CPython 3.12.10. It did not run a paid model request.
The CI configuration also targets Ubuntu and Python 3.13; configuration alone
does not establish that those jobs passed.

## What the local checks establish

| Tier | Evidence | Boundary |
|---|---|---|
| Unit and fixture tests | `python -m unittest discover -s tests -v` exercises tree validation, evidence handling, selection failure, resume, library prefixes, configuration, runner commands, checksum failures, archive traversal, and staging cleanup. | Several runner and installer tests replace subprocess results, upstream modules, or HTTP responses. They establish the wrapper's behavior for those fixtures. |
| Native local integration | Tests launch real Python child processes, serialize independent processes with file locks, terminate process trees, perform filesystem writes, invoke Git on a temporary repository, and extract synthetic archives including a path beyond Windows `MAX_PATH`. | These use local fixtures. The fake Codex Python program is not the installed Codex CLI; WorkBuddy module stubs are not Harbor or Docker. |
| Deterministic workflow | The demo runs the real recursion, evidence, selection, library, summary, and resume code. `DemoProposer` edits and selects by script; the executor runs a Python ledger calculation. | This verifies local workflow mechanics, not model quality, benchmark scoring, or access to a provider. |
| Distribution installation | A newly created virtual environment installed the wheel offline with no dependencies or editable link. Both demo modes ran from a directory outside the checkout, using `python -I` and a minimal environment. | The build reused the development environment's installed setuptools and wheel. This is a fresh runtime installation, not a newly provisioned Windows machine or a fresh online build-isolation test. |
| Upstream and external tools | The additional upstream checks below used actual OpenCode source, WorkBuddy data, and scratch dependency-lock resolution. | A successful dry-run, stubbed command, or import does not establish end-to-end benchmark execution. |
| Model and Docker execution | No model request or Docker benchmark run forms part of this local validation. | Credentials, provider permissions, model behavior, Docker drive sharing, Linux mount builds, Harbor task execution, and benchmark scores remain unverified by these checks. |

The wheel demo completed two ledger tasks with `--k 2` in both modes: 28 local
executions, 32 scripted editor calls, and zero failed tasks. A second invocation
against the same output performed zero executions and zero editor calls.
The installed interpreter imported `harness2` and runner assets from its own
`site-packages`, with user site packages disabled and no source checkout on
`sys.path`. An unrelated `third_party/` in the invocation directory did not
override the installed patches. The base package needed only Python's standard
library for this run; the optional `render` and `bridge` extras were absent.

Additional checks recorded during the Windows port:

- The actual pinned OpenCode source installed its filtered CLI dependencies and
  returned a version through its Bun launcher on the host. This did not call a
  model. The restricted execution environment blocked a working-directory read;
  the successful startup check ran through the normal host environment.
- Actual WorkBuddy Office and Web archives passed checksum verification and
  extraction, yielding 50 Office and 70 Web tasks in scratch storage. Against
  the pinned upstream API, Web manifest resolution, staging, task preparation,
  configuration composition, and sharded task-hash fixtures were exercised with
  proxy, backend, and Docker operations mocked. These were not container runs.
- The JobBench and LAB upstream locks passed scratch
  `uv sync --frozen --dry-run --no-build --offline` resolution, including their
  default development groups (52 and 81 packages respectively). This did not
  install their benchmark environments or verify that every package has a
  usable native Windows build.

Only `dependencies/opencode` and its local tools were installed for the
upstream CLI check. The JobBench/LAB lock checks and downloaded WorkBuddy data
were separate scratch checks; they do not make the checkout ready for a full
benchmark run.

## Packaging and license checks

The wheel and source archive contained all 49 intended files under
`third_party/`, byte for byte. These consist of runner source, harness text and
configuration, Dockerfiles, compatibility patches, license copies, and
provenance documents. Downloaded dependencies, `node_modules`, virtual
environments, runs, credentials, logs, and Python bytecode were not included in
the wheel. The source archive includes `dependencies/ASSETS.json` and
`scripts/check_provenance.py` so its bundled assets can be verified.

The distribution metadata records
`Apache-2.0 AND MIT AND LicenseRef-WorkBuddy-Bench` and includes eight license
files. Harness² and JobBench retain Apache 2.0; LAB and OpenCode retain MIT;
WorkBuddy retains its custom Tencent license, including its provision concerning
use within the European Union. The metadata does not replace these texts.
All eight license texts were compared with `HEAD` after normalizing line
endings, and no text had changed. The root and third-party license files now
have LF bytes matching their Git index entries and `.gitattributes`.

`git status` can report third-party files as modified because the original
Windows checkout used CRLF and the working files were normalized to LF.
Inspect a textual diff and `git ls-files --eol` before treating these as content
changes. Asset checksums cover actual shipped bytes, so refresh the asset
manifest only after runner edits and line-ending normalization are complete.

## Repeatability limits

The Git commit pins and compatibility patches are explicit in
[`dependencies/PINNED.md`](../dependencies/PINNED.md). Python setup runs the
upstream `uv` locks with `--locked`; OpenCode uses Bun 1.3.11 and a frozen patched
lock; Codex uses the configured release version. Setup also installs
`python-docx` for JobBench and `google-auth[requests]` for LAB without fixed
versions. Those supplementary resolutions and their transitive packages are
not fully locked by Harness². Optional main-environment extras also have their
own version constraints rather than a complete platform lock.

JobBench resolves the current Hugging Face dataset revision once per download
and records it. WorkBuddy downloads from the selected repository's `main`
revision and verifies each archive against the checksum list obtained there;
that list is not a separately pinned release input. LAB task files belong to
its pinned Git checkout. The WorkBuddy Codex staging build uses the moving
`node:22-slim` base image. No claim of a hermetic, byte-reproducible benchmark
environment follows from the repository pins alone.

`harness2-check` checks presence, imports, Git revision and applied patches,
configuration, and selected tool readiness. Its Google credential probe may
refresh an access token over the network. It does not make a judge or executor
model request, and cannot establish access to the selected models.

## Repeating local checks

From the checkout, with its development virtual environment installed:

```powershell
$env:TEMP = "$PWD\.test-tmp"
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/check_provenance.py
.\.venv\Scripts\ruff.exe check harness2 tests scripts
.\.venv\Scripts\python.exe -m pip wheel --no-index --no-deps --no-build-isolation --wheel-dir dist .
.\.venv\Scripts\python.exe -m venv .test-tmp\wheel-check
.\.test-tmp\wheel-check\Scripts\python.exe -m pip install --no-index --no-deps dist\harness2-0.1.0-py3-none-any.whl
```

Run the installed interpreter with `-I -m harness2.cli.demo --output demo --k 2`
from a new writable directory outside the checkout. For an isolated runtime
check, remove development `PYTHONPATH` and model settings, omit any local
`.env`, restrict PATH to that environment and Windows system tools, and verify
the imported module path points into the new environment. Run it again with
the same output to check resume. Creating a separate venv does not by itself
remove shell variables or prevent the current directory from exposing source.
