# First local compaction experiment

Status: OpenCode accepted a truncated summary and resumed local model execution.
The first nine completed resumed tool actions are preserved. They show repeated
reading and a specific earlier discovery being reused. This does not establish
completion of the JobBench workflow or preservation of the full earlier history.

This is the first real local-model JobBench run on the Windows port: one
`technical_writers/task1` task, OpenCode substrate, `k=1`, parallel mode, one
worker. The model is Unsloth's `Qwen3.8-27B-UD-Q3_K_XL.gguf`, served as
`qwen3.8-local` by llama.cpp build `b11406-8216c8462` at a loopback endpoint.
The server has a 32,768-token context and a 2,048-token reasoning budget; the
OpenCode model configuration requests a maximum output of 4,096 tokens.
The pinned OpenCode commit is `23fb5e0516c99ac04a1aa46c193efda2e1b9bb24`.
There is no configured cloud model fallback. Web-fetch tools still access the
public reference sites required by this task.

## Evidence preserved

Local raw evidence is under `runs/local-model-smoke/compaction-001/` and is
ignored by Git. Each capture directory has a `SHA256.json` manifest. The first
capture preserves the original request, pre-boundary messages and tool
results, the compaction trigger and assistant metadata, model routing, token
usage, task provenance, server arguments, and the relevant pinned OpenCode
source. The second capture also inventories the task workspace and preserves
a contemporaneous server-log prefix.

`scripts/capture_compaction.py` opens the live database in SQLite read-only
mode and uses SQLite's backup API to obtain an internally consistent snapshot
including committed WAL data. The backup copies the dedicated experiment
database into memory; subsequent queries and exports are restricted to the
specified session and its messages, parts, and todos. Account tables and other
sessions are not exported. The source database is never edited.

The default `session.json` export omits separate model reasoning text and text
enclosed in thinking tags. The final capture additionally preserves the complete
recorded messages and parts for this session, including local Qwen reasoning, as an opaque
`session.raw.json`. Explicitly copied provenance files and the original CLI
event-stream prefix also remain verbatim. These files stay in ignored local
artifacts; no reasoning prose is reproduced in this report. The analysis uses
visible messages, tool results, the visible summary, and one literal marker
presence check described below. Each export's scope is labeled in
`provenance.json`. This preserves recorded database parts, not model activations
or a claim to preserve every internal state.

The files include `session.json`, `pre-boundary.json`, `compaction.json`,
`compacted-context.txt`, `resumed-actions.json`, `timeline.json`, and
`provenance.json`. The context file is provisional until the compaction
assistant has completed. An empty file while generation is in progress is
not evidence of an empty completed summary.

The tracked capture utility can be rerun against this dedicated experiment
database, using a new output directory each time:

```powershell
python scripts/capture_compaction.py --db .tmp/local-model/opencode-data/opencode/opencode-local.db --session ses_eae03cb2cffeZdi61DrU4Zj6BE --boundary msg_15207fdff001K4FXjWsZjzRODJ --output runs/local-model-smoke/compaction-001/next-capture
```

Optional `--workspace` inventories that experiment's files and copies its
`output/` contents. Repeated `--source` options preserve explicitly selected
non-secret provenance files. Do not pass credential files or environment dumps.
`--preserve-raw-session` explicitly adds the unfiltered session export for an
authorized local experiment.
Filesystem observations happen immediately after the database snapshot; they
are not an atomic snapshot across both processes.

## Before the boundary

The user task requires four Markdown deliverables: a migration guide from API
v2.0 to v2.2, an API reference, a troubleshooting guide, and release notes. The
agent must resolve three escalated support scenarios, preserve the distinction
between deprecated and removed authentication, document incremental sync,
respect the actual webhook event list, and account for endpoint rate-limit
categories. Only final deliverables belong in `output/`, and filesystem access
must remain inside the task workspace.

The exposed trajectory shows:

1. Qwen read `TASK_INSTRUCTIONS.txt` and all four supplied reference files.
2. It guessed a URL for the requested Swagger-style Markdown gist; that URL
   returned 404. It then tried GitHub and Google searches, opened the author's
   gist listing, identified the correct gist, and successfully fetched its raw
   contents. This sequence is a useful historical fingerprint; merely knowing
   that a Swagger-style reference exists would not establish preservation of
   this earlier work.
3. Qwen said it had the required material and would verify the output location
   and write the four deliverables. Its next tool invocation was recorded as
   `invalid`, with a JSON parsing error. A statement of intended writing is not
   evidence that the files were created.
4. A filesystem inventory at `2026-10-19T02:48:41Z`, while compaction was still
   generating, confirms that `output/` existed and was empty. The five task
   instruction/reference files retained their supplied hashes. There were no
   custom `AGENTS.md` or harness prose files in this baseline workspace. The
   baseline fingerprint is `90529ca7bde38926`; its source components are
   comment-only and render to an empty harness. This is the baseline's existing
   behavior, not material lost during compaction.

The last build assistant is `msg_15202f6de001LMOdeDuGPJcUwv`. Its reported usage
is input 3,296, cached input 27,376, output 2,096, total 32,768, with finish
reason `length`. The corresponding llama.cpp request, task 3334, ended with
`n_tokens = 32767, truncated = 1`. These independent records support actual
context truncation rather than an assumed hit on the configured 4,096-token
completion limit. They do not by themselves establish why the malformed tool
JSON occurred.

OpenCode then created automatic compaction trigger
`msg_15207fdfa0011DWAfuHDfeFffc` (`auto=true`, `overflow=false`) and compaction
assistant `msg_15207fdff001K4FXjWsZjzRODJ`. The latter's recorded provider/model
is also `local/qwen3.8-local`.

## The summary and first resumed actions

The compaction assistant finished at `2026-10-19T02:50:59.883Z`, after about
10 minutes 42 seconds, with 7,315 characters of visible summary. OpenCode
reported input 22,975, output 4,096, cached input 0, total 27,071, finish
`length`, and no error. llama.cpp task 5439 ended with `n_tokens = 27070,
truncated = 0`. Thus this second limit was the requested completion budget,
not another exhausted 32K context. The provider's reported reasoning token
field is zero even though reasoning-type parts exist; that accounting field
does not establish an absence of model thinking.

The summary preserves the four exact deliverable names, output location,
filesystem and publication constraints, endpoint details, JWT lifetimes,
webhook behavior, release facts, and the three escalated support cases. It
ends partway through the support-ticket discoveries. It never reaches the
requested Accomplished or Relevant files sections.

Material omissions from the visible summary are clear: it contains neither the correct gist
ID/URL found through the earlier search, nor the unsuccessful initial URL,
nor the malformed last tool call, nor an explicit record that deliverables
have not been written. It does not falsely assert that those deliverables are
complete; it omits the progress distinction. Omission from visible summary text
does not establish that a detail is absent from all retained context.

OpenCode accepted the `length` summary, issued the synthetic continuation,
and began build assistant `msg_15211c9b1001BwaeCnHw8NOfHs` six milliseconds
after the summary completion. That assistant used the same local model and
completed a genuine PowerShell directory-listing tool call. Its visible text
said it had the reference data and would write the four deliverables after
checking the output directory. This agrees with the pre-boundary plan, while
the completed tool result independently shows the output directory is empty.
The first nine completed tool actions after compaction were:

1. List the output and task directories. The output directory remained empty.
2. Reread `TASK_INSTRUCTIONS.txt`.
3. Reread `api_endpoints_data.csv`.
4. Reread `developer_notes_raw.txt`.
5. Reread `release_notes_template.md`.
6. Reread `support_tickets_escalated.json`.
7. Fetch the previously discovered gist ID using an incorrect
   `raw.githubusercontent.com` repository-style URL; this failed.
8. Fetch that same ID's correct `gist.github.com` page; this succeeded.
9. Follow that page's raw-file URL and fetch the Markdown format reference again.

All five local files had already been read, and the summary itself said there
was no need to reread the reference data. The repeated work is observable. It
may be deliberate checking or context reconstruction; these records alone do
not tell us which. Those reads restore the original facts independently of the
summary, so later correctness on those facts cannot be credited solely to
compression memory.

The gist ID is more discriminating. The exact value
`a4b16faf0febd12efbc6c3d7370383a6` was discovered before compaction, is absent
from the five local references and the visible summary, and was reused before
any new external search or successful post-boundary fetch could reveal it.
Its first reused host/path was wrong, so continuity of that identifier did not
preserve the successful earlier access method.

## What crosses the boundary

The pinned implementation matters when interpreting continuity:

- `session/message-v2.ts:923` implements `filterCompacted`. For a finished,
  error-free summary, it walks backwards to that summary's compaction trigger
  and excludes older dialogue. In this case the older original task request
  and earlier tool results will therefore leave the dialogue input; no recent
  pre-boundary tool tail is deliberately retained by this filter.
- `session/compaction.ts` selects the compaction agent's model if explicitly
  configured, otherwise the triggering user's model. It generates a summary
  with no tools and a continuation template asking for goals, instructions,
  discoveries, completed work, outstanding work, and relevant paths. The actual
  model routing remains observable in the session metadata.
- On automatic continuation, `compaction.ts` adds a synthetic user message
  asking the agent to continue with its next steps. The compaction trigger is
  converted to the text “What did we do so far?” by `message-v2.ts`.
- Retained compaction context includes more than visible summary text.
  `message-v2.ts:788` forwards reasoning-type parts. The installed
  `@ai-sdk/openai-compatible` version `2.0.41` serializes them as
  `reasoning_content` (`dist/index.js:230` and `:260`). The saved model Jinja
  template defaults `preserve_thinking` to true, including across user turns.
  The SDK source/version and exact server-exposed template are preserved and
  hashed. This is a candidate carry channel that a text-only summary review
  would miss; final serialization was not captured on the wire.
- Each build step reconstructs its system environment, skills, applicable
  instruction files, and available tool definitions (`session/prompt.ts:1431`
  and `:1473`; `session/llm.ts:99`). Static instructions or tool usage therefore
  need not have survived through the summary. Plugins may also transform
  system/messages; the snapshot records the relevant source and workspace.
- The task's reference files and any successfully written artifacts persist
  on disk. Reopening a reference can restore information independently of the
  summary. This is external memory, and needs separate accounting.

This is a reconstruction from the pinned source and exposed database, not a
captured byte-for-byte HTTP request. Provider templates and plugin transforms
can affect the final input. Successful continuation alone would not prove that
historical details passed through the compressed summary.

## Continuity observations

| Question | Before compaction | Retained context / first resumed actions |
| --- | --- | --- |
| Objective and output constraints | Four named deliverables; final files only; workspace access fence | Preserved in visible summary; subsequently refreshed by rereading instructions |
| Completed versus planned work | References and correct gist fetched; writing announced; last call invalid; output empty | Visible summary omits explicit progress status; resumed agent still treats writing as future work and checks output. No false completion claim in this observation window |
| Specific earlier discovery | Correct gist ID found after 404 and searches | Identifier occurs in a retained compaction reasoning part and is reused in a later fetch before new external evidence. Successful earlier raw URL is not reused |
| Decision rationale / unresolved uncertainty | Task requires resolving API-key migration and rate-category ambiguity; no completed deliverable yet | Many facts survive; no strong exposed pre/post pair establishes preservation of an earlier decision's rationale |
| Repeated research or file reads | All five local files and external gist already read | All five files reread; gist fetched again |
| Strategy changes | Transition from gathering sources to intended writing | Same writing objective, with a renewed source-checking pass. Motivation for that pass is not established |
| Tool constraints | Last invocation was invalid JSON | First resumed calls are valid; available tool definitions are freshly supplied, so validity alone is not evidence of remembering the earlier error |

A literal marker-only check found the gist ID in compaction reasoning part
`prt_15209fe3b0016uOpSLf4Ed0Dyr`, and not in its visible text part. The check
records only booleans, part IDs, timestamps, and hashes in local
`channel-marker.json`; it does not display or interpret reasoning prose.
Together with the earlier successful discovery and later fetch, this provides
a concrete historically connected chain, stronger than merely observing that
execution continued. Source inspection supplies a plausible retained channel.
It is not causal proof: the final HTTP request was not captured, and this was
not a controlled experiment excluding prior model knowledge or other possible
representations. The original run, server, and configuration were left unchanged.

The practical weakness observed here is that the compaction completion budget
was exhausted before the visible summary reached progress and next-step
sections, while the resumed agent repeated substantial source reading. A
separate experiment could prioritize state and unresolved work earlier in the
summary and compare the resulting behavior. This observation does not establish
that such a change would improve task quality or total token use.

## Second boundary and experiment cutoff

A second automatic compaction began at `2026-10-19T02:55:52.075Z`, less than
five minutes after the first summary finished, with no deliverables written.
It is assistant `msg_152163f0b001NLVVdUdWxPLnTK`, triggered by user marker
`msg_152163f070011xLmoTbKZzfjbX`. Its preceding build assistant,
`msg_152145604001Ix00RpsjkjR1py`, reported input 6,589, cached input 22,457,
output 648, total 29,694, and finish `tool-calls`. The corresponding server
request was not context-truncated. With context 32,768 and output reserve
4,096, the pinned overflow check's usable threshold is 28,672 tokens; 29,694
is above it. This is an automatic proactive compaction trigger, distinct from
the first actual context truncation.

`runs/local-model-smoke/compaction-002/01-second-boundary/` preserves a
consistent scoped database export, opaque full recorded session, raw stream
and server-log prefixes, source/config identity, and an empty output inventory
at `2026-10-19T02:56:43Z`. The second summary had not completed at capture,
and no second-boundary continuation is claimed.

After the evidence was secured, the run owner manually stopped the verified
owned sweep and server at `2026-10-19T02:57:25.666707Z` for the agreed capacity
cutoff. The termination exit value `-1` is an operator stop, not a framework
failure or timeout. No baseline output, adaptation, or judging completed.
`manual-stop.json`, the sanitized first profile, the final original server logs,
and a post-stop database snapshot are preserved in
`compaction-002/02-after-manual-stop/`. That snapshot still has an unfinished
second summary, not an error produced by an autonomous model run. A separately
configured rerun is a different experiment and must not be presented as this
32K attempt completing the full solver-to-report workflow.

See the [validation record](../VALIDATION.md) for the separate follow-up test's
status and the [local-model guide](../LOCAL_MODELS.md) for its configuration
options. This report's evidence and conclusions remain scoped to the first
32K attempt.
