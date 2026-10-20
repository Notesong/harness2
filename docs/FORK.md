# About the Notesong fork

This repository is an independently maintained fork of
[google-research/harness2](https://github.com/google-research/harness2). Its
starting point is upstream commit
[`1cfe254e`](https://github.com/google-research/harness2/commit/1cfe254e8d25b15f37048c3730134686abe60335).
The upstream authors developed Harness²'s research framework and reported the
paper's results. The Windows port and additional local workflows are changes in
this fork. Neither the fork nor its testing is an upstream endorsement.

## What this fork adds

| Area | Changes and guide |
|---|---|
| Windows support | Native launchers, process-tree containment, locking, paths, installation, and portable judge patches. See [Windows](WINDOWS.md) and the [port review](REVIEW.md). |
| Harness² Studio | Browser setup, experiment previews, stop/resume controls, past runs, files, readable reports, and progress timelines. See [Studio](UI.md). |
| Local models | JobBench/OpenCode through llama.cpp, with optional managed startup, a bounded final wrap-up, and automatic unloading of the owned server. See [local models](LOCAL_MODELS.md). |
| Model profiles | Immutable configuration snapshots, independent harness ancestry and experience, inactive candidates, explicit adoption, and retirement. See [profiles](PROFILES.md). |
| Capability catalog | Explicit publication and forks of selected components, provenance, and opt-in references. Disabled by default; no automatic sharing or adoption. See [capabilities](CAPABILITIES.md). |
| Experiment evidence | An account of the local run, context-compaction observations, test boundaries, installation checks, and retained third-party provenance. See [validation](VALIDATION.md) and the [compaction study](experiments/LOCAL_COMPACTION.md). |

The framework still supports the upstream parallel/sequential recursion and
benchmark interfaces. Platform handling lives primarily in shared process,
filesystem, configuration, installation, and benchmark-adapter boundaries.
The [review notes](REVIEW.md) describe the portability changes to the core.

## What has been exercised

The reviewed code checkpoint is
[`3455d45`](https://github.com/Notesong/harness2/commit/3455d45194ba8768b3b9120f04fc0b72757d7d1f).
Its [four CI jobs](https://github.com/Notesong/harness2/actions/runs/39298522902)
passed on Windows and Ubuntu, each with Python 3.12 and 3.13. The native Windows
suite contains 279 tests; 27 additional Node tests cover UI behavior. Fresh
offline wheel installation and the deterministic demo/resume were exercised
outside the development checkout.

One real local JobBench task completed refinement, adopted-result judging, and
reporting with a 27B Qwen model through llama.cpp. The baseline was not graded,
and the task had prior exposure; its selected score is not evidence of an
improvement or a contamination-free benchmark result. A later bounded real-model
check exercised managed startup, wrap-up generation, and unloading.

Profile storage, context construction, catalog gates, forks, and ancestry have
fixture and integration coverage. A full model-backed profile workflow and
successful capability transfer have not been demonstrated. Full LAB execution
and WorkBuddy/Docker execution have not been demonstrated either. Adapter
tests, setup checks, and downloaded datasets do not establish those results.
See [VALIDATION.md](VALIDATION.md) for exact checkpoints and qualifications.

## Attribution and contributions

Fork documentation describes this checkout's behavior and can evolve with its
implementation. Upstream research claims remain attributed to the upstream
authors, and the [original citation](../README.md#citation) is retained. Git
history records the fork's changes separately from the starting upstream code.

Use this fork's issues and pull requests for its additions. Work intended for
the original project should follow that project's contribution instructions;
see [CONTRIBUTING.md](../CONTRIBUTING.md).

The root [Apache 2.0 license](../LICENSE), original copyright notices, and
third-party license texts remain intact. JobBench retains Apache 2.0; LAB and
OpenCode retain MIT; WorkBuddy retains its custom Tencent terms. The root
license does not replace those terms or the separate dataset licenses.
See [third-party notices](../dependencies/README.md),
[pins and patches](../dependencies/PINNED.md), and the bundled
[`PROVENANCE.md` files](../third_party/README.md) before modifying those assets.
