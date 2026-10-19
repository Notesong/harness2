# Local models with llama.cpp

Harness² can use a running llama.cpp server for JobBench's OpenCode executor,
improver, selector, and judge. The server must support OpenAI-compatible chat
completions and structured tool calls. The local provider also reaches LAB's
host OpenCode sessions, but that does not replace LAB's provider-specific judge
integration. WorkBuddy's generated Vertex routes are a separate configuration.

Start your server before running an experiment. The following Windows recipe
uses the 64K, thinking-off configuration tested with llama.cpp build `b11406`
(`8216c8462`). It passed text, structured-tool, and actual 40,045-token prompt
checks with the tested 27B Q3 model on a 16 GB RTX 5060 Ti. That establishes
transport and context capacity; the full benchmark outcome is recorded separately
in [the validation record](VALIDATION.md). Adapt the paths for your installation:

```powershell
$server = 'C:\tools\llama.cpp\llama-server.exe'
$model = 'E:\models\model.gguf'
$logs = Join-Path $PWD 'runs\local-server'
New-Item -ItemType Directory -Force $logs | Out-Null
$serverArgs = @(
  '--model', ('"{0}"' -f $model), '--host', '127.0.0.1', '--port', '8087',
  '--alias', 'local-model-64k', '--ctx-size', '65536', '--parallel', '1',
  '--cache-type-k', 'q8_0', '--cache-type-v', 'q8_0',
  '--flash-attn', 'on', '--fit', 'on', '--fit-target', '1536',
  '--batch-size', '512', '--ubatch-size', '128',
  '--jinja', '--no-webui', '--offline', '--reasoning', 'off'
)
$process = Start-Process -FilePath $server -ArgumentList $serverArgs `
  -WindowStyle Hidden -PassThru `
  -RedirectStandardOutput "$logs\stdout.log" `
  -RedirectStandardError "$logs\stderr.log"
$process.Id
```

These flags were available in the tested llama.cpp build. Check your binary's
`--help`; context size, cache types, GPU backend, and memory margin must fit
your hardware. Paths containing spaces must remain quoted inside the argument
string passed to `Start-Process`. The server reads the GGUF in place; Harness²
does not move or modify it. Stop only the server process you started when you
finish. A single server slot serializes requests even when the workflow submits
independent stages concurrently.

Thinking is controlled by the server's chat template. OpenCode's model
`reasoning: false` capability does not disable template thinking. The recipe uses
`--reasoning off`; its probes did not add a request-level thinking override.
For reasoning runs, choose an explicit `--reasoning-budget`. Keep that choice
fixed during an experiment and record it with the server command. The earlier
32K/4096-output reasoning run reached two compaction boundaries while its
output directory remained empty and was manually stopped for capacity
adjustment; see [the preserved experiment](experiments/LOCAL_COMPACTION.md).

Put these settings in the checkout's ignored `.env`, or export them in the shell:

```dotenv
HARNESS2_LOCAL_BASE_URL=http://127.0.0.1:8087/v1
HARNESS2_LOCAL_MODEL=local-model-64k
HARNESS2_LOCAL_CONTEXT=65536
HARNESS2_LOCAL_OUTPUT=8192
```

The model setting must match the server's alias. The API base URL includes
`/v1`, without `/chat/completions`. The two limits describe the server's actual
context and the maximum completion requested by OpenCode; output must be
smaller than context. If omitted, the generic helper defaults remain 32768 and
4096; those defaults do not configure or discover the server's actual limits.
Set both explicitly to match your server. With no explicit solver, improver, or judge settings,
this profile supplies:

```dotenv
HARNESS2_SOLVER_MODEL=local/local-model-64k
HARNESS2_IMPROVER_MODEL=local/local-model-64k
HARNESS2_JUDGE_MODEL=local-model-64k
HARNESS2_JUDGE_BASE_URL=http://127.0.0.1:8087/v1
HARNESS2_JUDGE_API_KEY=local
```

Use a different model alias when changing GGUF weights. Resume and judging
check the recorded alias, endpoint, and configured limits; they cannot identify
different weights served under an unchanged alias.

Explicit model and judge settings take precedence over those defaults. The
dummy `local` key satisfies clients that require a nonempty API key; it is not
a secret or server authentication. If your server requires authentication,
set `HARNESS2_LOCAL_API_KEY`; the provider and default judge configuration both
use that key. Keep it in the ignored `.env` or shell environment.
For a matching local JobBench judge, Harness² defaults to one rubric request
at a time and the local completion limit. Per-rubric and overall judge timeouts
are 600 and 7200 seconds. Override these with `HARNESS2_JUDGE_WORKERS`,
`HARNESS2_JUDGE_OUTPUT`, `HARNESS2_JUDGE_RUBRIC_TIMEOUT`, and
`HARNESS2_JUDGE_TIMEOUT` if your server has different capacity. The original
rubrics and scoring procedure are unchanged.

Harness² merges `provider.local` into `OPENCODE_CONFIG_CONTENT`, retaining
unrelated providers, agent settings, tools, and plugins. The explicit profile
selects its local model for normal and small-model requests and disables
sharing, model-catalog fetching, auto-update checks, and LSP downloads. Existing
environment configuration must be a JSON object; malformed content is rejected
instead of discarded. Harness plugins remain enabled. Plugins, MCP servers,
configured instructions, and agent web tools can still use the network, so
these settings do not guarantee an entirely offline agent session.

For a small installed JobBench task set:

```powershell
.\.venv\Scripts\python.exe -m harness2.cli.check --bench jb --substrate oc
.\.venv\Scripts\python.exe -m harness2.cli.run_sweep --bench jb --substrate oc `
  --k 1 --mode parallel --workers 1 --max-tasks 1 --only-domains technical_writers
.\.venv\Scripts\python.exe -m harness2.cli.judge --bench jb --substrate oc --k 1 --mode parallel
.\.venv\Scripts\python.exe -m harness2.cli.report --bench jb --substrate oc --k 1 --mode parallel
```

`--max-tasks 1` caps each selected profession, so use `--only-domains` to keep
this to one task. A partially downloaded dataset must be recorded as partial;
the standard setup command downloads the complete JobBench dataset. Move the
partial directory aside before running a complete download: setup refuses to
overwrite existing task files that lack its full-download record. Keep the
original task and rubric unchanged. The judge uses the upstream rubric and
the same configured local endpoint; successful model transport does not imply
that its work satisfies the rubric. See [the validation record](VALIDATION.md)
for the tested stages and remaining limits.
