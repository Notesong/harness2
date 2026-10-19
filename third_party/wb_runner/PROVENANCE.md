# workbuddy-bench runner assets

These runners derive from the workbuddy-bench integration and retain its
[upstream license](LICENSE). See the [pinned revision and patches](../../../dependencies/PINNED.md).

The integration adds editable harness installation and configurable models,
paths, and endpoints. Local model-provider names, environment variables, and
log paths use the `harness2` namespace. Setup installs these agents and harness configurations into the WorkBuddy checkout.

The bundled runtime patch also supports a native Windows host: shared proxy
configuration uses Windows byte-range locks, backend credential commands use
Windows command quoting, dataset staging copies symlink targets into a short
temporary directory to accommodate long Web rubric paths, and resumed
trials can be copied when Windows does not permit creating symlinks. Modified
upstream files carry a Harness2 change notice. Execution inside the benchmark
containers still uses Linux tools and paths; Docker Desktop must use Linux
containers.

[ASSETS.json](../../../dependencies/ASSETS.json) records the shipped file checksums.
Verify them with `python scripts/check_provenance.py`.
