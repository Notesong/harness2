# Validation record

This record separates local portability checks from validation of an installed
benchmark or a model service. The original Windows port snapshot passed 50
tests on Windows 11 AMD64 (`10.0.26300`) with CPython 3.12.10 and made no model
requests. Later UI and local-provider checks are recorded separately below.
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
| Model and Docker execution | The original port snapshot contained no model requests or Docker benchmark runs. | Credentials, provider permissions, model behavior, Docker drive sharing, Linux mount builds, Harbor task execution, and benchmark scores are not established by the portability checks. |

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

## Subsequent UI validation

A normal-host test-suite snapshot passed 75 tests in 37.823 seconds, including
the browser UI backend, local-provider configuration, and original portability
regressions. Ruff and all 47 release asset checks passed. Socket tests require
the normal host environment here: the restricted execution environment blocks
loopback access with Windows error 10013, so its HTTP test failures do not
establish an application failure.

A pre-commit normal-host suite passed **80 tests in 39.181 seconds**:
50 portability tests, 11 local-provider tests, 17 UI API tests, and two startup
tests. Ruff, all 47 release asset checks, and `git diff --check` also passed.
After adding UI context/output controls, the suite passed **83 tests in
40.366 seconds**, with Ruff and all 47 provenance checks passing again.

Browser checks at 1440-pixel desktop width and 390-pixel narrow width found no
horizontal overflow. The UI read local `/models` metadata, ran the real
deterministic `k=2` demo in both modes (two tasks, zero failures), displayed the
`Total: 85.15` artifact, displayed a diff, and exposed the artifact download.
The browser console showed no warnings or errors during these checks. The
actual JobBench preview, cancellation of model confirmation, and UI shutdown
were also exercised. These UI checks establish those local interactions; they
do not establish a successful model-driven benchmark or external-provider
access.

## First local model experiment: manually stopped after preserved compaction

The first actual JobBench baseline was manually stopped at **2026-10-19
02:57:25.666707 UTC** (October 18, 21:57 CDT), about 30 minutes after it
started. Its output directory was still empty at the second automatic
compaction boundary. The agreed stop preserved the experiment before a
separate capacity adjustment; it was not a framework crash or autonomous
timeout. No baseline, adaptation, judge, report, or benchmark score completed.

The owned llama.cpp server used build `b11406`, commit `8216c8462`, with the
user's existing Unsloth `Qwen3.8-27B-UD-Q3_K_XL.gguf` file (13,146,393,504 bytes)
read in place. It loaded through Vulkan on an RTX 5060 Ti with 16 GB VRAM,
leaving about 2.3 GB free after loading. The loopback endpoint was
`http://127.0.0.1:8087/v1`, alias `qwen3.8-local`, with one slot, a 32,768-token
context, `q8_0` K/V caches, flash attention, and a 1536 MiB fit margin. The
server's reasoning budget was 2048 tokens. No unrelated model process was
terminated.

Completed live checks:

- A text request returned `READY`. A structured tool request returned
  `add_numbers` with arguments `{"a":2,"b":3}`. Those two probes explicitly
  disabled template thinking. The warm tool probe generated 36 tokens at
  23.27 tokens/second; this small probe is not representative of the long task.
- JobBench's pinned checkout and judge patch were installed. Its locked Python
  environment installed 52 packages, followed by the setup workflow's
  supplementary `python-docx` 1.2.0. The real readiness check passed.
- Exactly one original task, `technical_writers/task1`, was downloaded from
  JobBench revision `d43a66575635da0d3f6cb9a20976dc1a60f3ffc9`: eight files,
  43,796 bytes, including its unchanged rubric and supplied external-reference
  file. All files were size/hash checked. The dataset is explicitly marked
  partial; the full dataset was not downloaded.
- The actual OpenCode baseline read the original inputs and fetched the
  required public formatting reference. Its first 10,867-token prompt ran at
  491.7 prompt tokens/second; generation slowed to roughly 7–12 tokens/second as
  the conversation grew. This demonstrated real local-provider transport and
  tool execution, rather than a mocked runner.

The baseline started at 21:27:21 CDT. It reached the 32K context limit,
returned a `length` finish, and recorded an invalid-tool repair. Its first
compaction summary completed with a 4096-token `length` finish and OpenCode
accepted it. The resumed agent repeated local reads and external-reference
research, then reached a second proactive compaction threshold. Read-only
snapshots retained the boundary, exposed summary, resumed actions, and final
operator cutoff. All phases used `providerID=local` and
`modelID=qwen3.8-local`; the small model was also local. The preserved record
carefully separates observable continuity from details absent from an exposed
summary; it does not expose private reasoning or infer loss from omission alone.
See [the full compaction experiment](experiments/LOCAL_COMPACTION.md).

The actual run retained template thinking; OpenCode's `reasoning: false`
capability does not disable it. The server settings were not changed mid-run.
The selected task requires four Markdown documents and an external reference,
so being the smallest download does not make it a short generation workload.
The configured `k=1`, parallel, one-task run entails four executor rollouts,
three adaptation stages, reflection, and the upstream ten-rubric judge. Its
single final uses the existing trivial-selection path; it does not exercise a
blind model-selector session. Logs, probe responses, task provenance, and phase
metadata are retained under the ignored `runs/local-model-smoke/` directory;
actual run state is under `runs/jb/`. `manual-stop.json` records the operator
cutoff and `first-profile-sanitized.json` records its configuration without keys.

## Separate 64K local model experiment: in progress

The same GGUF weights were loaded under the new alias `qwen3.8-local-64k`,
with context 65,536, output allowance 8192, one slot, `q8_0` K/V caches, and
server-wide `--reasoning off`. Text and structured-tool checks passed without
request-level thinking overrides. An actual 40,045-token prompt returned
`READY` in 109.17 seconds without truncation; prompt throughput was 367.77
tokens/second. The short tool response generated 36 tokens at 16.17
tokens/second. GPU memory free after these checks was approximately 1497 MiB
of 16,311 MiB; this is measured fit for this configuration, not a guarantee
for arbitrary contexts or concurrent workloads.

The fresh readiness check passed and the separate one-task `k=1`, parallel
sweep started at **2026-10-19 03:02:22.875 UTC**. All eight original task files,
including the rubric, were hash checked again. There were no instruction or
rubric changes, weight changes, cloud fallbacks, or executor timeout extensions:
the original 2400-second limit per executor attempt and two-attempt policy
remain (the outer rollout deadline is twice that limit plus 600 seconds). The new run root
is `runs/local-model-64k/`, with server identity, probe responses, original task
hashes, per-stage logs, and timestamps under `evidence/`. This snapshot still
has no completed baseline, adaptation, judge, report, or benchmark score.

## Packaging and license checks

The wheel and source archive contained all 49 intended files under
`third_party/`, byte for byte. These consist of runner source, harness text and
configuration, Dockerfiles, compatibility patches, license copies, and
provenance documents. Downloaded dependencies, `node_modules`, virtual
environments, runs, credentials, logs, and Python bytecode were not included in
the wheel. The source archive includes `dependencies/ASSETS.json` and
`scripts/check_provenance.py` so its bundled assets can be verified.

A later clean wheel/UI runtime proof used wheel SHA-256
`67a4d7bd20847b94049a83c1380e379557271644e54b5e9ef2b80234c2948eb7`
(373,917 bytes). A new venv installed it offline and an isolated interpreter
ran from a separate workspace outside the checkout. The installed UI completed
the two-task, both-mode `k=2` deterministic demo, previewed `Total: 85.15`,
resumed with zero executions and zero editor calls, and shut down with exit
code zero. Installed Python and UI assets matched the source snapshot; the
source `.env` and process model settings were unchanged by that proof. Its
source archive was provisional pending the later forensics documentation
freeze; a documentation-only change does not retroactively alter that runtime
snapshot.

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
