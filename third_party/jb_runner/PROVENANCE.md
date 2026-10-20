# job-bench-eval runner assets

The files in this directory derive from the job-bench-eval integration and retain its
[upstream license](LICENSE). See the [pinned revision and patches](../../../dependencies/PINNED.md).

What is still vendored here:

| Path | Origin |
|---|---|
| `run_benchmark_codex_cli_adapt.sh` | Upstream `eval/run_benchmark_codex_cli.sh`, kept close to the original |
| `codex_base_harness/AGENTS.md` | Upstream Codex baseline harness |
| `harness_baseline_oc/` | Upstream OpenCode baseline harness tree |

The integration adds editable harness installation and configurable models,
paths, and endpoints. Local model-provider names, environment variables, and
log paths use the `harness2` namespace. The shell Codex launcher is retained as
a reference; the adapter now invokes the native Python
`harness2/benchmarks/jb_codex_runner.py` on Windows and POSIX. This preserves the
upstream task staging, attempt index, trajectory and output conventions.

The OpenCode launcher that used to sit beside it,
`run_benchmark_opencode_harness_adapt.sh`, was a fork that shared almost nothing
with upstream. It has been replaced by the first-party Python module
[`harness2/benchmarks/jb_opencode_runner.py`](../../benchmarks/jb_opencode_runner.py),
which the adapter launches as a subprocess in its place. That module is release
code, not a vendored asset, so it is not listed in `ASSETS.json`.

[ASSETS.json](../../../dependencies/ASSETS.json) records the shipped file checksums.
Verify them with `python scripts/check_provenance.py`.

The unchanged `job-bench-eval-judge.diff` API patch and the separate
`job-bench-eval-native-lock.diff` apply in that order to the pinned upstream
`eval/judge.py`. Setup and preflight check each independently, preserving upgrades
from an existing API-only installation. The native patch replaces the unconditional POSIX `fcntl`
import with conditional stdlib `msvcrt`/`fcntl` locking. Windows waits on byte zero
until ownership is available; exception exit and process death release ownership.
The lock file stays in place so sibling processes share the same lock identity.
This locking addition leaves rubric, grading, parsing, and extraction functions
unchanged. The patched upstream file carries a prominent modification notice;
its Apache 2.0 license remains unchanged. Native integration tests use the actual
patched judge, and skip explicitly when its dependency checkout is unavailable.
