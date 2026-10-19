# Harness² Studio

Studio is a local browser interface for the existing Harness² commands. It
provides model setup, benchmark preparation, small experiments, live activity,
stop and resume controls, selected output files, and harness diffs. It uses
Python's standard library and bundled HTML, CSS, and JavaScript.

## Open Studio

On Windows, double-click `Launch Harness2.cmd` in the repository. The first
launch requires Python 3.12 and internet access to create `.venv` and install
the main package with its optional document rendering dependencies. Later
launches use that environment. Benchmark dependencies are prepared separately
from Setup.

After installing the package yourself, run `harness2-ui` from the environment
where it is installed, or:

```powershell
.\.venv\Scripts\python.exe -m harness2.ui.server
```

An installed wheel uses the current directory as its workspace; an editable
checkout uses the repository root. Opening Studio again reconnects to the
existing server for that workspace. Use **Close app** to shut it down. Closing
the browser tab alone leaves the server and any active operation running.
`--no-browser` prints the URL, and `--port` selects a port instead of requesting
an unused one.

## Start with the demo

Choose **Try the free demo**. It runs two deterministic ledger tasks with two
candidates in both recursion modes, using the actual recursion and selection
code. Its scripted editor and local Python executor require no model service
or provider credits. In **Runs**, select the demo to inspect its output files,
harness changes, and log.

## Connect a model and choose work

In **Setup**, choose the local connection and its model lifetime. **Start and
unload automatically** starts the existing **llama-server executable** with your
**GGUF model file** when an experiment begins. Enter absolute paths to both
files, the loopback API address including `/v1`, and the model alias. The server
stays available for setup checks, refinement, judging, and the final report;
Studio then stops it and releases its model resources. A failed or stopped run
also closes its owned model server. No model weights are downloaded.

Managed startup refuses an occupied endpoint. Use **External server** when you
want to use a server already running; Studio never stops an external server.
In external mode, **Test connection** reads the model list without generating a
completion. In managed mode, testing and saving settings do not load the model;
startup and readiness checks happen when a real experiment starts. Choose the
exact model ID for an external server and save.

Keys are write-only in the interface and are stored in the workspace's ignored
`.env`. A managed server's temporary access key stays within its running job.
The local profile configures OpenCode and the judge together; see
[LOCAL_MODELS.md](LOCAL_MODELS.md) for the full-workflow CLI, context limits,
and server setup.

**Advanced model settings** shows the local context window and maximum output
in tokens, defaulting to 32,768 and 4,096. Match the context window configured
on your external server, and keep output smaller than context. These fields tell
Harness² the budget available to OpenCode. In managed mode, the context setting
also configures the server at its next startup. Saving settings does not start,
restart, or resize a running server. Saved budget changes apply to new operations
and are part of an experiment's resume identity.

Studio's complete local pipeline currently supports JobBench with OpenCode.
LAB's host executor can use a local model through the CLI, but its judge needs
separate provider configuration. Studio blocks that incomplete local pipeline.
WorkBuddy requires additional container networking and runner configuration.
A Codex Responses endpoint is a separate advanced connection; it does not
configure OpenCode's improver or a judge automatically. Vertex requires your
project, credentials, and explicit solver, improver, and judge model choices.

Prepare a benchmark from Setup. Preparation downloads upstream dependencies
and task data and can take time. **Check environment** records detailed readiness
information in Runs. A presence check alone does not prove model access.

In **New experiment**, choose one domain and begin with one task and one branch.
The initial search approach is **Parallel**. With one branch, it schedules four
full executions per task; **Compare both** schedules seven, sharing the
baseline. Harness editing, reflection, and judging add model calls. The start
confirmation shows the planned execution count; saved work can reduce it and
retries can add work.
**Check the plan** previews task selection without a model completion. **Start
experiment** shows the connection and compute use before it starts. The normal
pipeline checks setup, runs refinement, judges the adopted output, and builds
the score report, then adds a readable wrap-up before unloading an owned model.
Concurrent domains controls domain workers; the current form
selects one domain.

## Follow, stop, and resume

Studio runs one operation at a time. Its subprocesses use the same process-tree
containment as the CLI. **Stop run** terminates owned processes and preserves
completed files and stops any model server owned by that operation. A server
crash also terminates its contained Windows jobs and managed model server;
the next launch marks the operation interrupted. Resume is available for demos
and experiments and reuses their output directory. Restore the original model
connection before resuming an experiment; otherwise start a new experiment.

Results appear as each task finishes. Scores only appear after judging and
are not inferred from a completed execution. Output files can be downloaded,
text files previewed, and changes compared with the baseline. **Open folder**
opens the actual output directory in the system file manager.

The run's readable report explains what was recorded, which files were produced,
and how the selected result was graded. It separates partial probes and proposal
fallbacks from the final execution. A chronological timeline preserves phase
starts, returns, recovery attempts, and model cleanup; events without a recorded
time are labelled accordingly. Grading without a baseline score is not presented
as proof of an improvement.

After a graded local experiment, one bounded local-model request can write a
short wrap-up from these public facts. The interface labels a model-written
wrap-up separately from the factual fallback. It does not display private
thinking or raw agent transcripts. A summary failure leaves the grade and output
files intact. The same readable report is saved as `run-summary.md`, with public
JSON and timeline files alongside it.

The file list follows each benchmark's deliverable boundary. LAB exposes its
`output/` folder; WorkBuddy exposes the authored artifacts allowed for its
selected subset. Hidden folders, runtime files outside those boundaries, and
WorkBuddy's verifier scores and grading text are excluded from downloads.

## Local state

Experiment outputs live under `runs/ui/` by default. `HARNESS2_RUNS_DIR` changes
the runs root. `.harness2-ui/` stores the job history, redacted activity logs,
and the current server session. These paths and `.env` are ignored by Git.
Existing CLI runs remain accessible on disk; this initial interface lists
operations it launches.

The server binds only to `127.0.0.1`, checks Host and Origin, and requires a
random session token for every API request and artifact download. The browser
receives that token in its initial URL fragment and retains it in session
storage. Keep that launch link private. Assets have no third-party scripts or
fonts. Model and benchmark commands may still use the network for tools,
packages, reference material, and the configured model endpoint.

Saved settings apply to Studio-launched commands. Direct CLI commands retain
their usual shell-over-`.env` precedence. The interface never displays saved
keys and redacts configured secrets from new activity logs; generated model
artifacts are ordinary workspace files and are not rewritten by that filter.
