# Contributing

This is [Notesong's fork](https://github.com/Notesong/harness2) of
[Google Research's Harness²](https://github.com/google-research/harness2).
See [the fork overview](docs/FORK.md) for the changes and their validation scope.

## Changes to this fork

Open an issue or pull request in this repository. Describe the problem, the
resulting behavior, and how it was checked. Distinguish scripted fixtures and
mocked services from actual model or Docker execution. Keep unrelated changes
separate, and update the relevant guide when behavior changes.

Use a development environment with Python 3.12 or 3.13 and Node.js 22 for the
UI checks. From the repository root, run the checks appropriate to your change:

```text
python -m unittest discover -s tests -v
node --test tests/ui_story.test.cjs tests/profile_ui.test.cjs tests/capability_ui.test.cjs
python scripts/check_provenance.py
ruff check harness2 tests scripts
harness2-demo --output runs/demo --k 2
```

Install the package and Ruff into that environment first with
`python -m pip install -e . ruff`. See
[setup](docs/SETUP.md), [Windows](docs/WINDOWS.md), and
[the validation instructions](docs/VALIDATION.md#repeating-local-checks).
Platform-specific tests may explicitly skip where a required native facility
or privilege is unavailable. Do not replace a failing integration with a mock
without documenting the resulting evidence limit.

Preserve existing copyright and license notices. Bundled third-party changes
need accurate provenance and refreshed asset hashes; see
[third-party assets](third_party/README.md). Do not commit credentials, model
weights, downloaded datasets, local profile/catalog archives, or run artifacts.

## Contributions to the original project

Submit upstream changes to [google-research/harness2](https://github.com/google-research/harness2)
and follow its [contribution guide](https://github.com/google-research/harness2/blob/main/CONTRIBUTING.md),
including its Google Contributor License Agreement and community requirements.
Those instructions describe the upstream project's process; this fork is
maintained independently.
