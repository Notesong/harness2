# Validation record

This record separates local portability checks from validation of an installed
benchmark or a model service. The original Windows port snapshot passed 50
tests on Windows 11 AMD64 (`10.0.26300`) with CPython 3.12.10 and made no model
requests. Later UI and local-provider checks are recorded separately below.
The original CI definition also targeted Ubuntu and Python 3.13. Actual
hosted failures and the subsequent passing matrix are recorded below.

## Current fork checkpoint

Commit [`3455d45`](https://github.com/Notesong/harness2/commit/3455d45194ba8768b3b9120f04fc0b72757d7d1f)
passed [all four CI jobs](https://github.com/Notesong/harness2/actions/runs/39298522902)
on Windows and Ubuntu with Python 3.12 and 3.13. Each job passed its platform's
279-test Python suite (with explicit platform-dependent skips), all 27 Node UI
tests, the provenance checks, Ruff, and the deterministic demo. The local native
Windows run passed 279 tests in 83.495 seconds with two explicit skips. This
checkpoint includes the regression and fix for an asynchronous Windows shutdown
race. A fresh installed-wheel proof and the dormant catalog check are detailed
[below](#dormant-capability-catalog-2026-10-19).

One real local JobBench task and a later managed-model lifecycle check provide
the model-backed evidence described later in this document. They do not
establish a graded baseline improvement, a new full profile-backed experiment,
successful capability transfer, full LAB execution, or WorkBuddy/Docker execution.
The following sections preserve earlier checkpoints as historical evidence;
their smaller test counts are not the current suite size.

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

## Subsequent hosted CI

As of 2026-10-19, [GitHub Actions run 39194138279](https://github.com/Notesong/harness2/actions/runs/39194138279)
passed all four jobs for commit `da17aeb4753e5a7976349a3ee16d0e15bdc75934`:
Windows and Ubuntu, each on Python 3.12 and 3.13. Each job passed the 83-test
suite, provenance checks, Ruff, and the deterministic `k=2` demo. These hosted
checks did not run an upstream benchmark model or Docker workload.

The preceding `aabd61ec5db5eda51ac62b8f3fd4aee608c56f3b` run failed on test
fixture assumptions: three Windows tests compared temporary-path aliases
without resolving them, and a Linux LAB fixture placed results under `/tmp`,
which the runner deliberately rejects. The correction canonicalized fixture
roots and used an allowed LAB results directory; production code was unchanged.
Matrix fail-fast was disabled so every job could report its result. The passing
job and step summaries were independently fetched through the GitHub API and
retained locally in `.tmp/ci-39194138279-jobs.json`.

The separate review-fix worktree at
`0cc8d8209b19727617cd1b902bffa395deea35d9` subsequently passed **129 tests in
74.221 seconds** on the normal Windows host, with Ruff, JavaScript syntax,
and all 48 asset checks passing. [Its hosted CI run](https://github.com/Notesong/harness2/actions/runs/39196937582)
also passed all four Windows/Ubuntu and Python 3.12/3.13 jobs. The additional
checks cover endpoint credential validation, attempt-status logging, native
JobBench judge locking, atomic writes, and UI result counts. Four independent
processes exercised the actual patched upstream judge's lock; fresh setup,
API-only upgrade, full-patch reuse, and conflicting-source rejection were also
checked without model calls.

A fresh offline source-archive to wheel installation passed the installed
UI/demo proof for that review snapshot, as detailed below. These fixes and
checks were isolated from the `da17aeb` checkout executing the live model test.
They do not retroactively change which implementation that test exercised.

## Subsequent UI validation

After adding UI context/output controls, the normal-host suite passed
**83 tests in 40.366 seconds**, with Ruff and all 47 provenance checks passing.
This snapshot includes the original 50 portability tests, local-provider
configuration, UI API behavior, and startup checks. Later review fixes have the
separate 129-test checkpoint above. Socket tests require the normal host
environment here: the restricted execution environment blocks loopback access
with Windows error 10013, so its HTTP test failures do not establish an
application failure.

Browser checks at 1440-pixel desktop width and 390-pixel narrow width found no
horizontal overflow. The UI read local `/models` metadata, ran the real
deterministic `k=2` demo in both modes (two tasks, zero failures), displayed the
`Total: 85.15` artifact, displayed a diff, and exposed the artifact download.
The browser console showed no warnings or errors during these checks. The
actual JobBench preview, cancellation of model confirmation, and UI shutdown
were also exercised. These UI checks establish those local interactions; they
do not establish a successful model-driven benchmark or external-provider
access.

## Later report, history and owned-model lifecycle checks

The subsequent feature snapshot passed **187 Windows tests in 60.263 seconds**.
It adds readable reports and chronological timelines, discovery of existing
CLI runs, navigation through run history, and automatic cleanup of a local
server owned by an operation. Native tests use real loopback Python children
and process-tree containment; fixture tests exercise serialization, completed
and interrupted history, factual summary fallback, and cleanup failures.
These regressions do not rerun the model task or establish model quality.
The final focused checks passed 13 report tests and 10 local-run tests,
including the added summary-provenance regression. These focused checks are
recorded separately from the earlier full-suite count.

A real managed-server check on **2026-10-19 15:53:26–15:54:18 UTC** loaded the
same 27B GGUF in 28.516 seconds, made one bounded retrospective summary
request, and automatically stopped only its owned server process. That request
generated 283 tokens in 18.17 seconds (15.52 tokens/second); total elapsed time
was 51.813 seconds. It summarized saved public run facts, not a continuation of
the original agent session. No task execution or grading was rerun, and the
four selected documents and grade hashes were unchanged. A fresh-process check
confirmed `summary_status=model`, creation time
`2026-10-19T15:54:17.911705+00:00`, and the local Qwen retrospective attribution.
The initial proof driver had retained an older formatter import and reported
its cached fallback status; the preserved proof and later metadata distinguish
that observer result from the model request and saved report.

After owned cleanup, a host memory check found no llama server process, about
12.1 GiB RAM used with 19.66 GiB free, and 948 MiB VRAM used. Those measurements
establish cleanup for this local proof, not a universal memory-use guarantee.
The retrospective narrative and its provenance are saved alongside
`run-summary.md`, `run-report.json`, and `run-timeline.json`. The original
experiment and score retain their earlier timestamps and limitations.

## Subsequent LAB native readiness

A later model-free check installed the pinned LAB checkout's 81 locked
packages in its own Windows virtual environment, without changing its lock or
project metadata. Document, judge-SDK, and plotting imports passed. The current
adapter discovered 1,143 tasks across 25 practice areas and rendered its base
harness; this is the adapter's two-level discovery scope, not the full recursive
inventory of the upstream checkout. Synthetic PDF, PPTX, and XLSX documents
were read through both the host parser and judge reader. An existing bundled
Poppler binary was used without changing the machine PATH.

DOCX parsing failed because Pandoc was absent. The unchanged upstream LAB
judge rejected the configured local Qwen alias during provider detection,
before any network request. There were zero model calls, and the saved local
profile was unchanged. These checks establish installed dependency and selected
reader readiness, not a completed LAB benchmark or local LAB judging. Local
records are in `.tmp/lab-readiness/prepare.json` and `readiness.json`.

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

## Separate 64K engineering smoke test: completed with recovery

This is an engineering and capacity smoke test, not a pristine controlled
repeat. The first attempt's task-specific forensic report was committed and
pushed in `252dd802cc2e80f1fa9f0e82ab551fbd3070bcbe` before this web-enabled
same-task run began. There is no evidence that the model accessed that report,
but the possible public-history channel cannot be ruled out. Unchanged task
and rubric hashes establish input identity, not freedom from contamination.
Context size, completion allowance, and thinking policy were changed together;
the outcome cannot isolate the causal effect of any one setting.

The same GGUF weights were loaded under the new alias `qwen3.8-local-64k`,
with context 65,536, output allowance 8192, one slot, `q8_0` K/V caches, and
server-wide `--reasoning off`. In the pinned llama.cpp implementation, that
flag sets both reasoning control and the template's `enable_thinking=false`
default ([source](https://github.com/ggml-org/llama.cpp/blob/8216c84623cf5b22b29319344ca5e685003892f5/common/arg.cpp#L3588)).
Zero reported reasoning counters do not guarantee that ordinary response text
contains no reasoning-like prefix. Exact HTTP request bodies were not captured.
Text and structured-tool checks passed without request-level thinking overrides. An actual 40,045-token prompt returned
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
hashes, per-stage logs, and timestamps under `evidence/`.

The baseline then completed naturally at **03:35:11 UTC**, approximately
32 minutes 46 seconds after its executor started. All four required Markdown
files were preserved, totaling 52,729 bytes. Its last assistant finish was
`stop`, with no error; the session recorded zero compaction messages, zero
`length` finishes, and zero reasoning parts. Two adaptation sessions began
next. This establishes a completed baseline rollout; the baseline was not
graded, so it provides no scored comparison with the selected result.

One adaptation session then crossed OpenCode's proactive compaction threshold:
its preceding turn used 58,021 tokens against a 57,344-token usable budget.
The summary completed naturally at **04:05:32 UTC**, with 52,771 input tokens,
3,318 output tokens, a `stop` finish, zero reported reasoning tokens, and no
server truncation. OpenCode accepted the 13,615-character exposed summary and
created a continuation. Read-only boundary and continuation snapshots were
preserved locally. Later adaptation compactions and their continuations were
also captured without changing the model, source, profile, or timeouts.
Read-only SQLite observations had short bounded handles; brief interference
with Windows temporary-directory cleanup cannot be ruled out merely because
the handles were read-only. The capture record notes this limitation.

The domain proposal was accepted on its second transport attempt and changed
`LongTermMEMORY.md` and `guardrails.md`. The independent proposal exhausted its
two 3600-second attempts and explicitly fell back to the inherited harness.
Both probe executions collected one of the four required Markdown documents at
their 2400-second boundary. Neither probe stream contains a natural final stop;
one ended its last completed generation with `length` at 8192 output tokens.
The frozen runner preserved nonempty partial output before checking whether it
should log a timeout, so these partial probes were delivered to composition.
Their exact per-attempt exit codes were not persisted. The timing and stream
state support timeout with partial-output preservation; wrapper exit zero does
not establish completed probe work.

Composition was accepted, and the final executor completed naturally at
**07:14:33 UTC**, after approximately 33 minutes. Its persisted stream ends with
`stop`, and all four required Markdown files were preserved, totaling **42,582
bytes**. Reflection returned an accepted result. With `k=1`, the final output
was selected directly; there was no blind model-selector call. The sweep ended
at **07:45:50 UTC** with exit zero, one task delivered, and the proposal fallback
still recorded. This is a completed workflow with a failed adaptation branch
and partial probes, rather than evidence that every stage succeeded.

The original output reader fell back to raw Markdown because the actual
upstream extractor and judge imported POSIX `fcntl` unconditionally. The
original automatic judge failed before a model request with
`ModuleNotFoundError: No module named 'fcntl'`, leaving the selection explicitly
ungraded; its report still ran and exited zero. The earlier readiness check
had not imported that judge module.

Only after the original sweep and driver ended, a previously reviewed guard
preserved the original failure and applied the validated native-only upstream
file-lock repair. It verified the original task/rubric, implementation,
selected output and effective model-profile fingerprints, as well as the owned
server identity. The recovered judge and report both exited zero, finishing at
**08:02:42 UTC**. Output and profile fingerprints were unchanged by recovery.
The wrapper implementation used during model execution remained the frozen
`da17aeb` snapshot; later repository fixes were integrated separately.

The actual upstream judge processed the unchanged ten original rubrics using
**the same local Qwen model**. The selected result scored **38/66 weighted
points (57.58%)**, with **37/40 criteria passed** and `all_pass=false`. All ten
rubric result entries were present without a nonempty structured error field.
There is no baseline grade and therefore no measured improvement over the
baseline. This single-task self-judged result is not an independent assessment
or a benchmark-wide score. Logs, original failure, guarded recovery, grade,
selected-file hashes and stage metadata remain under the ignored
`runs/local-model-64k/evidence/` directory; actual benchmark results are under
its `jb/` directory.

## Packaging and license checks

The review snapshot's wheel and source archive contained all **50** intended
files under `third_party/`, byte for byte, including the separately shipped
native JobBench judge patch. These consist of runner source, harness text and
configuration, Dockerfiles, compatibility patches, license copies, and
provenance documents. All 48 asset digests passed. Downloaded dependencies,
`node_modules`, virtual environments, runs, credentials, logs, and Python
bytecode were not included in the wheel. The source archive includes
`dependencies/ASSETS.json` and `scripts/check_provenance.py` so its bundled
assets can be verified.

A fresh venv installed the review wheel offline and an isolated interpreter ran
from a separate workspace outside the checkout. All 48 Python files, three UI
assets, and eight licenses matched the source snapshot. The installed UI
completed the two-task, both-mode `k=2` deterministic demo, previewed
`Total: 85.15`, resumed with zero executions and zero editor calls, and shut
down with exit code zero. No optional extras, build tools, development
`PYTHONPATH`, model variables, or preexisting workspace `.env` were available
to that installed runtime. The active experiment's `.env`, upstream judge, and
Harness Python source bytes and modification times were unchanged by the
proof. It made zero model calls. The local report is
`.tmp/installed-ui-proof-caa2e7ea6449/report.json` in the review worktree; its
source archive predates this completed-outcome documentation.

After the report/history/owned-server additions, another frozen source snapshot
was built into a source archive and wheel entirely offline, then installed
without dependencies in a newly created venv. Its **53 Python files, three UI
assets, 50 third-party files and eight license texts** matched the captured
source bytes, and all 48 asset checks passed. An isolated interpreter imported
the new local-server, report, history and summary modules from installed
`site-packages`; the new `harness2-local-run` console entry point returned help
successfully. Only Harness² and pip were installed in that runtime.

From a separate workspace with no development `PYTHONPATH`, model variables or
local `.env`, the installed both-mode `k=2` demo completed two tasks with **28
executions, 31 scripted editor calls and zero failed tasks**; resume performed
zero executions and zero editor calls. The proof's initial assertion had
assumed the older run's 32 editor calls. That assertion failure is preserved;
the reported counts are the new run's actual counters. There were zero model
calls and no benchmark rerun. The source snapshot includes recorded hashes for
uncommitted feature files on top of `0cc8d82`; it is not represented as the old
commit alone. The proof is `.tmp/final-wheel-proof-20261019/report.json`.
A final documentation-only source-archive refresh follows the frozen runtime
proof; it does not change the tested installed code.

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

### Independent profile foundation (2026-10-19)

The profile checkpoint passed the native Windows suite: **240 tests in 75.990s**,
with two explicit skips for account symlink privileges and a case-sensitive
filename fixture. The **13 Node rendering/behavior tests**, Ruff, JavaScript
syntax, whitespace checks, and all 48 third-party asset digests also passed.

Profile tests use real archive files, cross-process locks, native filesystem
operations, a real Studio child process, and the real JobBench harness
materializer. Their model lease and task executor are substituted. They verify
immutable configuration/revision ancestry, candidate tests without adoption,
atomic pin publication, changed settings/file/tool-policy detection, credential
rotation, domain-scoped answer-free experience, retirement/restore, explicit
borrowing, and durable finished outcomes that survive working-run rewrites.
Raw notes, session/error bodies, and grader values are excluded from that outcome
record. Evidence classifications do not become stronger automatically.

The actual saved 64K experiment was explicitly imported as Owen. Its baseline,
domain proposal, composition, reflection, observations, and evaluation caveats
are preserved. SHA256 comparisons confirmed **69 original source files remained
unchanged**. Repeating the import after consistency hardening left all **17
archive JSON records unchanged**, including the active selection. The historical
external-server configuration and a separately captured future managed
configuration have matching model/runtime byte hashes.

The real Studio screens and CLI dry-run were exercised with Owen and an
explicit baseline candidate. The UI preview completed without starting a model;
the active harness stayed unchanged. Browser error/warning logs were empty.
No new model-backed profile experiment was run in this checkpoint. The completed
64K run predates the profile machinery; importing it does not establish that a
new profile-backed full model pipeline has been exercised. The existing actual
model startup/summary/unload proof remains the earlier lifecycle evidence.

A new offline wheel (441,311 bytes; SHA256
`16718f95af0c384eeb6e720766fbeee67acc4ab58d149549437bfed56807a189`)
was installed into a fresh virtual environment and exercised with `-I` outside
the checkout. All 57 Python files and three UI assets matched the final source;
50 bundled third-party files and eight license files were intentional and
unchanged. Profile creation/configuration/revision/activation and every public
CLI help command worked from the installed package. Its deterministic two-task,
both-mode demo performed 28 executions and 32 scripted editor calls with no
failures; resume performed zero additional executions or editor calls. These are
scripted fixtures, not model-backed profile results. The initial proof script's
UTF-8 decoder assumption for Windows CP1252 help output was corrected and retained
in the local verification report; the CLI itself had returned successfully.

### Dormant capability catalog (2026-10-19)

The final catalog checkpoint passed the native Windows suite: **279 tests in 83.495s**,
with the same two explicit platform/privilege skips. All **27 Node UI tests**,
Ruff, JavaScript syntax, whitespace checks, and 48 third-party asset checks
passed. Catalog tests exercise real temporary archives, immutable publication,
explicit exposure gates, pinned context, foreign candidate forks, declared
ancestry, and authenticated HTTP routes. Model behavior and transfer benefits
are not established by these fixtures.

Hosted Windows checks of the preceding profile commit exposed path-alias
assumptions and disagreement in fresh PID lookups after shutdown. Path assertions
now compare resolved identities. Lifecycle tests retain original native process
handles: the owned root must already be signaled when shutdown returns, and a
known live grandchild must terminate within a bounded wait. A fresh numeric PID
lookup is diagnostic only. Both hosted Windows versions then exposed an actual
shutdown race: a cached Python exit code could skip waiting while the original
native process handle was still unsignaled. Cleanup now waits on that handle
unconditionally and refreshes the final exit code. This follows the native
[asynchronous termination contract](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess).
The strict process-identity assertions remain in place.
An added real-process regression recreates a cached exit code while the retained
handle is still unsignaled. Cleanup must wait and report the actual final code;
a negative control using the old wait shortcut fails this test.

The live Studio catalog remains **disabled and empty**, with no profile opted
in and no catalog events. Real Owen snapshot/context construction received no
catalog key or directory. Hash comparisons confirmed all **17 saved profile
JSON records unchanged**. The disabled screen was inspected in the actual
browser with no error/warning logs. No model server was started.

A fresh offline wheel (461,329 bytes; SHA256
`dcf0e75fdc15f149c1d98d1490b919502a18af6dd9ab8dfbe71507d09b84cb52`)
was installed into a new virtual environment and exercised with `-I` outside
the checkout. All 60 Python files and three UI assets matched the frozen
production source. Fifty bundled third-party files and eight license files
were unchanged. An installed synthetic catalog fixture exercised publication,
profile opt-in, pinned references, a modified inactive fork, and disabling again.
The deterministic two-task demo completed with 28 executions, 32 scripted editor
calls, and no failures; resume added zero executions or editor calls. These
checks made no model calls and did not modify Owen. The initial proof wrapper's
CP1252 output decoding assumption and corrected rerun are retained locally.

### Conversational sidecar validation boundaries

The initial conversational checkpoint's native Windows suite passed **346 tests** (two
platform-specific skips), and **39 Node view tests** passed. A policy
check restricts conversation-enabled benchmark runs to Windows: POSIX phase
cancellation does not contain the bridge's separate native process groups.
This is an explicit support limit, not a claim established by mocked owners.

A fresh offline wheel install verified 64 Python modules, three UI assets,
50 third-party files, 48 asset checksums, and eight license files against the
source. Installed conversation creation/listing and separate archives worked
without loading a model. The deterministic two-task demo completed with no
failures; resume added no executions or editor calls. The final wheel SHA256
was `e0677d47a55cb8a6aae8eeabe1a41fc1aef898d958075fdcb7603aa8cf3c3793`;
the local source-manifest and reports are in `.tmp/conversations-wheel-proof/`.

The conversation UI fixtures exercise save-only creation, deliberate start,
active-worker delivery, visible message states and reasons, native-context
switches, source links, draft preservation, optional run gating, and protection
against an older poll replacing a newer message result. They execute production
view functions with a small DOM substitute and substituted HTTP responses. They
do not start OpenCode or a model, prove delivery to a real native session, or
establish successful recovery after compaction.

Conversation history, working continuity, reserved conversational memory, and
technical profiles are separate stores. Recording human contact or a sourced
decision is not evidence of improved task performance. Earlier completed
JobBench runs and model lifecycle proofs predate this sidecar; they do not
validate live conversational behavior. Actual native-session and model-backed
checks must be reported separately from these UI fixtures.

The **11 native Windows transport tests** use real Python HTTP child processes
in place of OpenCode. They exercise authenticated loopback requests, confirmed
native message IDs, bounded public projections, exact-session resume, model and
session override rejection, startup cancellation, and owned cleanup, including
private runtime files beyond Windows's traditional path limit. They do not run
the OpenCode plugin or model inference.

A separate local proof ran the actual pinned Bun/OpenCode server, attached CLI,
and conversation plugin against a **scripted local model API**. Its six API calls
exercised the real `SessionBridge` monitor and archive acknowledgements for a
public message, a decision citing the delivered human message's ledger ID, and a
checkpoint referencing that decision. A human message arrived while the original
working session was busy. The proof also forced native compaction, observed the
recovery hook and archived event, then closed and reopened the exact retained
session. This is transport/plugin integration evidence; the responses were
scripted. The local record is
`.tmp/native-session-proof-20261019-222233/report.json`, which includes the tested
transport source hash.

A bounded **real Qwen 27B conversation check** then exercised the Studio
`Conversations` service with a disposable profile, an empty harness, and a copy
of Owen's managed runtime recipe: 65,536 context tokens and a 512-token output
ceiling. Qwen acknowledged `copper lantern 47`; after Stop unloaded both owned
processes, Continue reopened the same native session and Qwen recalled that
phrase. Both load/response/unload cycles completed in **119.9 seconds**. Original
native process handles confirmed all four owned model/session processes had
terminated, no llama.cpp process remained, and Owen's archived JSON hashes were
unchanged. The local report and visible conversation are retained under
`.tmp/cp-222310/`. This two-message check did not exercise a conversational
JobBench workflow, model-written decision/checkpoint records, model-backed
compaction, or any improvement in task performance.

The notification/inventory follow-up adds archive regressions for tool-message
retries across worker restarts, canonical decision/checkpoint request replay,
changed-payload rejection, and preserving a later checkpoint when an older
acknowledgement is retried. Runtime fixtures classify explicit posts using native
assistant-parent identities and verify that a tool reply does not prematurely
complete a human turn. Lost acknowledgements are replayed with a recovery record
above 30 KB without duplicating records or bypassing changed-payload validation.
These checks use real temporary archives with a substituted native client.

A new integration proof used the actual pinned Bun/OpenCode server, attached
CLI, and plugin with six **scripted provider calls**. An explicit post under the
native formal-task turn was archived as model-initiated; notification attention
had been enabled before that post. Its claim succeeded once and left the message
unread. The native reply to a delivered human message was classified as a reply
and was ineligible for an alert. Reopening attention state preserved the claim
and unread records. The same proof exercised tool acknowledgements, forced
compaction, and exact-session continuation, then closed every owned process.
Relevant production source hashes matched before and after the proof. Evidence
is retained at `.tmp/native-attention-proof-20261019-231236/report.json`.
This validates native integration and notification bookkeeping with scripted
output; it did not display a browser or operating-system notification.

The profile inventory is a projection of saved files and the pinned OpenCode
source catalog. It does not execute profile components or probe effective tools,
remote services, shell confinement, or model expertise. Notification attempts
and unread bookkeeping likewise do not establish OS display or human receipt.
The earlier real Qwen continuity check predates these additions; it does not
validate model-initiated notification behavior or the inventory's runtime tool
conditions. No additional model inference was used for this follow-up.

The follow-up passed **377 native Windows tests** (two platform skips) and
**52 Node UI tests**. A fresh offline wheel install verified 66 Python modules,
three UI assets, 50 third-party files, 48 provenance digests, and eight license
files. Installed inventory/attention fixtures and the deterministic demo/resume
passed without model calls. The wheel SHA256 was
`aad0996f967b614e72000ab9ba5573fce74c9fa50519f885bcb5b30ac2d45eef`;
reports and the source manifest are in `.tmp/notifications-wheel-proof/`.

The duplicate-reply follow-up passed 390 native Windows Python tests (two skips)
and 56 frontend tests. Store/runtime tests cover exact native ancestry, late
tool acknowledgements, different turns and workers, immutable-history linkage,
and grouped unread state. The synthetic HTTP UI fixture explicitly uses an
external runtime and guards against inheriting a real managed-model startup.

A retained Owen conversation provided real OpenCode evidence of one exact
tool-post/final-answer repetition. A read-only SQLite inspection plus saved
request/acknowledgement records supported six append-only ancestry links. All
19 original history events and all 10 messages remained unchanged; only the one
proven duplicate pair was folded in Studio. Browser inspection confirmed that
the original tool record remains accessible in its disclosure. This repair
required no new model inference. The revised instruction to avoid posting a
human reply through both channels has not yet been tested in a new model turn.

From the checkout, with its development virtual environment installed:

```powershell
$env:TEMP = "$PWD\.test-tmp"
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/check_provenance.py
.\.venv\Scripts\ruff.exe check harness2 tests scripts
node --test tests/ui_story.test.cjs tests/profile_ui.test.cjs tests/capability_ui.test.cjs tests/conversation_ui.test.cjs
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
