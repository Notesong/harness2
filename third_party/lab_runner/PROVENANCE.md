# harvey-labs runner assets

These runners derive from the harvey-labs integration and retain its
[upstream license](LICENSE). See the [pinned revision and patches](../../../dependencies/PINNED.md).

The integration adds editable harness installation and configurable models,
paths, and endpoints. Local model-provider names, environment variables, and
log paths use the `harness2` namespace. Setup installs these runners into the LAB checkout as overlays.

The Windows integration adds portable process timeouts, native document-parser
selection, Windows path escaping in Codex TOML, and removal of read-only scratch
files. These local modifications retain the MIT license above.

[ASSETS.json](../../../dependencies/ASSETS.json) records the shipped file checksums.
Verify them with `python scripts/check_provenance.py`.
