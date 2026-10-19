# Windows port review

The review base is upstream `google-research/harness2` commit
`1cfe254e8d25b15f37048c3730134686abe60335`, also the fork's original `main`.
Three independent review passes inspected architecture and runner parity,
processes and filesystem behavior, and packaging and test evidence.

## Corrections from review

- **Process containment:** Windows children now start suspended, enter a Job
  Object, and resume only after containment succeeds. Assignment failures stop
  the child and close its streams. This closes the race in which a quick
  launcher could create an unowned grandchild.
- **Runner parity:** restored optional JobBench task discovery and bounded
  per-model task concurrency, including interruption cleanup. WorkBuddy's direct
  connection now treats an empty endpoint/key variable like the original shell
  runner. LAB Codex configuration preserves non-BMP Unicode in TOML paths.
- **Filesystem invariants:** reject drive paths, alternate streams, device
  names, traversal, and case/separator collisions before changing a harness.
  Normalize internal names and fingerprint ordering independently of host path
  comparison rules. Materialize harness text as UTF-8 with LF endings.
- **Result integrity:** concurrent completion markers use distinct temporary
  files; malformed cached records are rejected. Summary writes retain their
  process lock. Distinct evidence filenames preserve separate source artifacts.
- **Packaging:** installed runners come from the installed distribution rather
  than an incidental working-directory folder. Package data is explicitly
  enumerated, source archives include the provenance manifest, and wheel
  metadata includes the licenses of the bundled third-party material.

The recurrence algorithms, recursion budgets, selection rules, and grading
formulas remain in their existing core modules. Platform handling is centralized
in process, lock, configuration, installation, and adapter boundaries. The core
edits above concern storage, validation, and evidence handling.

## Evidence and remaining boundaries

Hostile tests use actual Windows processes and Job Objects, process termination,
concurrent writers, an abruptly terminated lock holder, long Unicode paths,
and a clean installed-wheel environment. Some error conditions are injected;
runner protocol tests replace models and external services with fixtures.

The clean wheel test uses a new virtual environment without system site packages,
an outside-checkout working directory, isolated Python, and a minimal environment.
The two-mode `k=2` demo completed both tasks; a resumed run started no new model
executions or scripted editor calls. License texts match upstream after newline
normalization, and bundled runner bytes are checked against the asset manifest.

[VALIDATION.md](VALIDATION.md) distinguishes unit tests, native integrations,
pinned upstream checks, model-backed execution, and Docker-backed execution.
An unperformed tier is not implied by a passing lower tier. The port is kept on
the `windows-support` branch for review; it is not merged into `main`.
