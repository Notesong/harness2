# Model profiles

A profile is a continuing identity such as **Owen**. Changing a context window,
quantization, runtime, or harness creates another recorded configuration or
revision under that identity. It does not erase the earlier work or create a new
identity automatically.

The shared harness engine still owns process containment, filesystem rules,
benchmark boundaries, model startup and shutdown, and reporting. A profile owns
its selected model configuration, harness ancestry, experience, observations,
and associated runs.

## Start in Studio

Open **Profiles** and create a profile. Configure the connection in **Setup** and
save it, then use **Capture saved Setup** in the profile. Capturing a local
configuration reads and hashes the specified GGUF and runtime executable; it
does not load a model or make a network request. Reading a large model can take
time depending on storage speed.

Create a clean baseline candidate, or explicitly save a selected harness from a
finished JobBench/OpenCode run. Review the configuration and harness, then choose
**Use for future runs**. Capturing something does not activate it automatically.
Each profile has its own active selections.

In **New experiment**, select the profile and its configuration and harness.
The initial implementation supports local JobBench with OpenCode. The run records
that precise starting point before execution. Subsequent changes to the profile's
active selections cannot change the run's pinned starting point. Resume uses the
same snapshot. Existing experiments without a profile retain their original
behavior.

Profile runs automatically archive their selected harnesses as candidates, along
with public outcome and fallback/timeout facts. This does not activate those
candidates. You can also explicitly save a harness from an older completed run.
Adoption is an explicit selection for future work. A completed run, a selected
candidate, and demonstrated improvement are separate facts.

## Configurations describe what was recorded

A configuration contains public connection settings and an identity snapshot:

* Solver, improver, and judge model IDs are recorded separately.
* The local context and output budgets are recorded.
* Local weights and the runtime executable receive streaming SHA256 hashes, sizes,
  and paths when they can be read consistently. File mutation or replacement
  during hashing leaves the fingerprint unknown rather than certifying it.
* A quantization label extracted from a filename is explicitly an annotation,
  not verified GGUF metadata. The runtime's version is unknown unless supplied
  by saved historical evidence. Capture never runs the executable to ask.
* Managed llama.cpp startup has reasoning disabled in its current fixed recipe.
  An external server's actual reasoning mode remains unknown unless independently
  recorded. Capturing a connection does not inspect its runtime.
* Declared `OPENCODE_CONFIG_CONTENT` and `OPENCODE_CONFIG` JSON receive a canonical
  fingerprint after authentication fields and credential-bearing URL parts are
  redacted. Public boolean tool gates and allow/ask/deny permission modes are
  recorded. Changing those declarations requires another configuration.

These snapshots are deliberately explicit about their limits. A runtime
executable hash does not capture every linked library. Declared OpenCode settings
do not capture every global configuration file, installed plugin, dependency,
or effective tool on an external server. A cloud connection has unknown local
weights and runtime. Unknown facts remain unknown.

API keys stay in the workspace's private settings. They are neither copied into
profiles nor represented as model experience. Provider configuration text is not
stored in profile history. Credential rotation alone does not change the public
tool-policy fingerprint.

## Revisions have ancestry

A revision stores a complete harness tree, parent revision IDs, explicit borrowing
references, its domain, the reason for the change, supporting evidence references,
and any selected experience entry. A revision can descend from more than one
parent. Returning to an earlier revision and making a different change creates
another branch without rewriting the previous branch.

**Try a smaller harness** creates a candidate by removing selected files. The
source revision remains intact. Compare the candidate with the current harness
before deciding which to carry. Retirement records a reason and removes a
revision from active consideration while preserving its files and ancestry. A
retired revision can be selected again later.

**Borrow from another profile** is an explicit import. It copies an independent
candidate with a reference to its source and starts as a hypothesis. It neither
changes the source profile nor activates the borrowed harness. No successful
component is distributed to every profile automatically. This operation copies
the entire selected source harness. Use a capability fork to merge published
component files into a recipient profile's own starting harness instead.

The separate [capability catalog](CAPABILITIES.md) can publish selected component
files with provenance and support explicit file-level forks. Model exposure is
disabled by default. Enabling workspace sharing alone does not expose references
to a profile: that profile must opt in and a new experiment must pin its packet.
Publishing and forking never change either profile's active harness automatically.

## Evidence and adoption are independent

Notes distinguish these evidence classifications:

| Classification | Meaning |
| --- | --- |
| Observed | Something happened in a recorded run. |
| Suspected | An interpretation or hypothesis needs testing. |
| Replicated | It happened again under comparable conditions, supported by references. |
| Transferred | Supporting evidence shows usefulness on another task. |

The person recording a note chooses its classification; Studio does not infer
replication or transfer from a grade. Active, candidate, and retired describe
current use, not evidence strength. A revision may be active with only one
observation behind it.

Observations and hypotheses can include an optional conversation reference.
This preserves why an idea arose without copying a conversation into every
model context. Casual conversation is not archived automatically.

## Experiments cannot rewrite the archive

Profile state lives under `.harness2/profiles/` in the workspace. Immutable
SHA256-addressed configuration and revision objects are published through an
append-only, hash-linked event history. Active selections, retirement, notes,
and run references are derived from those events. A workspace lock serializes
access; file hashes and event links detect corruption. This is integrity
checking, not protection against an owner deliberately rewriting the entire
archive.

Every profile experiment gets `profile-start.json`, containing its exact
configuration, harness, selected experience, and any separately opted-in catalog
packet. The harness becomes that run's
baseline. Domain-matching experience can seed the proposer's disposable working
library. The working library can still prune or reorganize during optimization;
it does not become the profile's canonical past.

At completion or interruption, public task/stage outcomes and selected candidates
are copied into the profile archive. Resuming or pruning a working run cannot
rewrite those copies. Raw session prose, error bodies, and judge results are not
copied into this answer-free outcome record. Very large runs archive at most 128
task summaries per completion checkpoint and record that limit explicitly.

The same profile selection is available from the command line:

```powershell
.\.venv\Scripts\python.exe -m harness2.cli.local_run --profile owen --only-domains technical_writers --max-tasks 1
```

Use `--revision FULL_REVISION_ID` to test a candidate without adopting it, or
`--configuration FULL_CONFIGURATION_ID` to select another saved recipe. Add
`--dry-run` to preview the task stream without loading a model or creating a run.

Permanent notes, grades, and full profile histories are not copied into solver
or proposer context. The model receives the explicitly selected harness and
applicable experience. An opted-in catalog adds a frozen reference packet to the
proposer's disposable context; it does not add foreign instructions to the
solver's active harness. Existing answer-key boundaries and recognized
grader-marker checks apply. Those checks are tripwires; the primary boundary is
choosing answer-free sources rather than reading evaluation reports into a harness.

Back up the complete `.harness2/profiles/` directory together. Retain the run
folders if you also need the original evidence files referenced by the archive.
Profiles contain harness text and history, not copies of model weights. Do not
edit object or event JSON in place; create another revision or note instead.

## Owen's first chapter

Importing Owen is a deliberate migration, not an automatic startup side effect:

```powershell
.\.venv\Scripts\python.exe -m harness2.profile_import --owen
```

The command requires the saved `runs/local-model-64k` experiment in that workspace.
It reads the old server manifest, model/runtime hash evidence, public run
metadata, and saved harness trees. It does not use today's `.env` to reconstruct
the old configuration and does not restart inference. Original files are read-only.

The import preserves the original baseline, accepted domain proposal, selected
composition, experience reflection, invariant fallback, partial-probe
qualifications, and evaluation caveats. The historical configuration describes
the original external server; using automatic managed lifetime for future work
is a separate configuration. Repeating the import reuses the same objects and
keeps any later active selection.

The selected result produced four documents and received **57.58%** from the same
local model acting as judge. The baseline was not graded. One proposal timed out,
both probes retained partial output, and the judge needed a Windows portability
repair before it could complete. The record therefore says **adopted within one
run; benefit over baseline unknown; benefit across tasks unproven**. The grade
belongs to historical annotation, not to the harness or experience that a future
task receives.

This first version provides the storage and experiment boundary for continued
development. It does not claim that Owen has demonstrated transfer, automate
evidence promotion, schedule maintenance reviews, or start additional model runs.
