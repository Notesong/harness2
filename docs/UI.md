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

Managed startup refuses an occupied endpoint. Choose **Connect to an existing
server** when you want to use a server already running; Studio never stops an
external server.
In external mode, **Test connection** reads the model list without generating a
completion. In managed mode, testing and saving settings do not load the model;
startup and readiness checks happen when a real experiment starts. Choose the
exact model ID for an external server and save.

Keys are write-only in the interface and are stored in the workspace's ignored
`.env`. A managed server's temporary access key stays within its running job.
The local connection configures OpenCode and the judge together; see
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
**Parallel domains** controls domain workers; the current form
selects one domain.

## Keep a model's profile

**Profiles** gives a model a continuing identity with separate configuration
snapshots and harness ancestry. Create a profile, save its connection in Setup,
then use **Capture saved Setup**. This records public settings and hashes local
model/runtime files without loading the model. Choose a clean baseline or save
a finished run's harness as a candidate, then explicitly select the configuration
and harness to use for future runs.

New experiments can select a profile and a particular configuration and harness.
Their recorded starting snapshot stays fixed even when the profile later changes.
The profile's notes, evaluation results, and full history are kept outside model
context. The selected harness and applicable experience seed the run; optional
catalog references require the separate opt-in described below.
Existing runs can still be opened from **Runs** or the profile's associated runs.

Profiles also support observations and unresolved hypotheses, optional conversation
references, smaller harness candidates, retirement, and explicit borrowing from
another profile. Borrowing creates an independent hypothesis and does not activate
it. Active and retired describe use; observed, suspected, replicated, and
transferred describe supporting evidence. See [PROFILES.md](PROFILES.md) for
the storage boundaries, identity limits, and Owen's historical import.

**Current capabilities** explains the profile's active configuration and harness:
its always-on guidance, technical memory file, custom skills, subagents, scripts,
and plugins. **Runtime tools and conditions** lists the pinned OpenCode tools
separately, including tools that depend on configuration or remote services.
An empty custom group does not mean OpenCode has no built-in tools. The inventory
reads saved metadata; it does not probe a running model, execute files, demonstrate
expertise, or change the active selection. **Permissions and practical limits**
also explains that application permission checks do not sandbox host shell commands.

## Keep capabilities available for later

**Capabilities** opens the workspace's shared component catalog. It starts off,
with no example entries and no profile access enabled. You can inspect saved
entries while it is off; publication and forks remain disabled.

When you choose **Enable catalog**, each profile still needs its own **Allow
reference access** choice before new experiments can receive catalog material.
Use **Publish from a profile** to snapshot selected files from a saved revision.
An entry shows its source, declared evidence and costs, limitations, files, and
related variants. Forking into another profile creates an unvalidated candidate;
it does not activate the candidate or run its files.

New experiment plans show current reference availability. A profile run's
recorded starting point states whether catalog references were exposed, with
links to inspect the recorded entries. Availability does not establish use or
benefit. Disabling the catalog affects new experiments; existing runs keep their
pinned starting snapshots. See [CAPABILITIES.md](CAPABILITIES.md) for the exposure
gates, ancestry, and current limits.

## Talk with the working session

**Conversations** keeps visible chat with a profile's actual native OpenCode
session. Creating or viewing a conversation does not start a model; **Start
conversation** does. A profile's **Talk to [name]** deliberately creates and
starts one using its saved configuration and harness.

For a run-linked conversation on Windows, choose a local profile, JobBench/OpenCode, and one
domain worker, then check **Conversation during work**. This option starts off.
The run links to the conversation, which identifies its active worker and native
context. Delivery states distinguish queued, delivered, answered, undelivered,
and failed messages. **Working continuity** shows sourced decisions and recovery
state; conversational memory remains a separate reserved space.

Use the run's stop control during work. An idle chat has its own stop control
and disconnects after ten minutes without activity, unloading its owned model.
**Continue chat** explicitly reconnects its retained session. See
[CONVERSATIONS.md](CONVERSATIONS.md) for lifecycle, storage, and evidence limits.

Unread counts appear beside **Conversations** and in its conversation list.
They survive Studio restarts. Messages are marked read when their entries are
visible in the focused conversation; fetching or opening background history does
not clear everything. The notification panel offers **Enable desktop notifications**
and **Turn notifications off**. Permission is requested only after your click.
Supported browsers can alert you to new model-initiated posts outside the focused
conversation; replies and working-record updates do not trigger alerts. Existing
backlog stays unread without generating a burst of notifications. Alerts include
a brief excerpt of the visible message.

Keep Studio open in the browser for these alerts. Browser and operating-system
permissions still apply, and an alert attempt does not establish that it was
displayed or read. If an embedded browser does not support notifications, use
**Open Studio in my browser**; unread counts remain usable either way. A changed
local address may require **Allow in this browser** again. Clicking a notification
opens that conversation's saved history and does not load a model.

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

Open a run's **Overview** for its readable report, **Timeline** for short recorded
progress entries, **Outputs** for deliverables, or **Activity** for command output.
The run's **Home** and **All runs** links remain available after completion.

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
Recognized existing CLI runs are discovered as read-only history. Profiles and
their append-only histories live in the separate `.harness2/profiles/` archive.
The optional catalog has its own `.harness2/capabilities/` archive. Back up both
archives and the run folders when preserving profiles and their supporting work.
Conversations and their private retained native sessions use separate directories
under `.harness2/`; see [conversation storage](CONVERSATIONS.md#retained-session-data).

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
