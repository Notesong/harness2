# Upstream pins and patches

`harness2/config.py` is the authoritative pin table. The setup script fetches
these commits directly from the public remotes and refuses to overwrite a
checkout at another revision.

| Repository | Commit |
|---|---|
| job-bench-eval | `fa03a17f9ff662f136798a274e10011c3aa00fd0` |
| workbuddy-bench | `b516950be5b56eb3be406c2f76ee1c5111dcb57f` |
| harvey-labs | `b58a28ae9fde5ee4b810e53b4fd0a1915325a35d` |
| opencode | `23fb5e0516c99ac04a1aa46c193efda2e1b9bb24` |

## Included patches

- `opencode-compat.diff`: fixes a moving Git dependency to the commit already in
  the upstream lock and adds Opus 4.8 adaptive-thinking support to the pinned
  provider adapter.
- `job-bench-eval-judge.diff`: passes reasoning effort, raises the judge output
  cap, and rejects empty completions. This original API patch remains unchanged.
- `job-bench-eval-native-lock.diff`: applies after the API patch and adds
  self-contained stdlib Windows byte locking alongside POSIX `flock`. It preserves
  grading, rubric, parsing, and extraction behavior. Setup applies and verifies both
  patches independently, so fresh, API-only, and fully patched checkouts upgrade
  idempotently without resetting files.
- `harvey-labs-judge-vertex-anthropic.diff`: adds Claude and Gemini Vertex judges
  using ADC and improves malformed/ambiguous response handling.
- `workbuddy-bench-runtime.diff`: registers the release's executor adapters,
  preserves declared harness mounts, refreshes short-lived credentials, and
  preserves Gemini thought signatures across tool calls. It also fixes an
  undefined constant in the upstream Anthropic route support. Windows host
  support adds native file locking, credential-command quoting, dataset staging
  without symlink privileges, and a copy fallback when resuming trials.

Patches are checked before applying and skipped if already applied. Derived runner
files belong in `third_party/{jb,lab,wb}_runner/` in a source checkout and are
packaged under `harness2/third_party/` in wheels. Setup copies the LAB and
WorkBuddy overlays into the upstream checkouts. JobBench uses native Python
launchers; its original Codex shell launcher remains a reference asset.

The upstream dependency locks are used for Python installations. Setup also adds
`python-docx` for JobBench and `google-auth[requests]` for LAB without fixed
versions; those supplementary resolutions are not pinned by the upstream locks.
OpenCode uses
Bun 1.3.11 and the patched lock. Codex uses the version in `config.CODEX_VERSION`.
JobBench's Hugging Face revision is resolved once for a download and recorded in
`dataset/main/.harness2-download.json`. WorkBuddy's dataset fetcher, as patched
by setup, verifies each archive against the dataset repository's `SHA256SUMS` and
refuses one it cannot verify; see
[the WorkBuddy guide](../docs/BENCHMARKS.md#workbuddy-bench). LAB task files are part of its pinned Git checkout.

These inputs support the public execution workflow. Historical paper runs may
have used different tasks, model deployments, and additional local tooling;
see [experiment notes](../README.md#experiments).
