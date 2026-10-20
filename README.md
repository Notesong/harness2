<h3 align="center">
  <img src="assets/Harness2-wordmark.png" alt="Harness²" width="360"><br>
  Recursive Agent Harnessing for an Open World
</h3>

<p align="center">
  <a href="https://arxiv.org/pdf/2610.14224"><img src="https://img.shields.io/badge/Paper-arXiv-EA4335?logo=arxiv&logoColor=white" alt="Paper"></a>
  <a href="https://harness2.github.io"><img src="https://img.shields.io/badge/Website-harness2.github.io-4285F4?logo=googlechrome&logoColor=white" alt="Website"></a>
  <a href="docs/BENCHMARKS.md"><img src="https://img.shields.io/badge/Docs-Benchmark%20guides-FBBC04" alt="Benchmark guides"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/Core%20license-Apache%202.0-34A853" alt="Core license: Apache 2.0"></a>
</p>

## About this fork

This is [Notesong's independently maintained fork](https://github.com/Notesong/harness2)
of [Google Research's Harness²](https://github.com/google-research/harness2).
It adds Windows support, a local browser interface, a llama.cpp workflow,
independent model profiles, and an optional capability catalog. The original
research, authorship, citation, and license notices are retained.

Start with [Harness² Studio](docs/UI.md) for the interface or the
[Windows guide](docs/WINDOWS.md) for PowerShell setup. The demo needs no model.
Local JobBench/OpenCode experiments can load an existing GGUF model, write a
readable report and timeline, and unload their owned server when finished.
Profiles keep configurations and harness ancestry separate; the capability
catalog is **disabled by default**, with a separate opt-in for each profile.

The latest code checkpoint passed CI on Windows and Ubuntu with Python 3.12 and
3.13. One complete local JobBench experiment has also been exercised. That does
not establish general model quality, full LAB or WorkBuddy/Docker execution,
or successful transfer between profiles. See [the fork overview](docs/FORK.md)
and [validation record](docs/VALIDATION.md) for the evidence and limits.

The overview and illustrations below describe the upstream research framework.

<p align="center">
  <a href="docs/BENCHMARKS.md#jobbench">
    <img src="assets/harness2-setup.gif" alt="Install, set up, and run JobBench: uv pip install -e '.[render]', harness2-setup jb, then harness2 --bench jb --k 1 --mode parallel. Click for prerequisites and commands." width="640">
  </a>
</p>

A **harness** is the system around a model that enables it to act as an agent:
prompts, tools, memory, skills, orchestration, and more. **Harnessing** is the
process of optimizing that system.

Harness² is a plug-and-play framework for **recursive agent harnessing** in an
open world, where agents encounter tasks unseen during harness design. It
connects two levels of recursion while keeping model weights fixed:

* **Task-level recursion** refines the execution harness for the current task
  through a contrastive **Propose–Probe–Compose** step: propose edits, probe
  their effects on the same task, and compose a refined harness from behavioral
  contrasts between executions, without ground-truth feedback.
* **Harnessing-level recursion** updates a persistent *improvement harness*
  with adopted harnesses and evidence-supported lessons from each task to guide
  future refinement.

<p align="center">
  <a href="assets/harness2_pipeline.png">
    <img src="assets/harness2_pipeline.png" alt="Harness² pipeline. Task-level recursion: an agent executor runs the initial harness, a harness editor proposes invariant and domain edits, candidate probes execute them, and a harness composer combines them into the final harness. Harnessing-level recursion: an experience reflector reflects on the step and updates the improvement harness used for the next task." width="100%">
  </a>
</p>

In the upstream paper, across three professional benchmarks and four
model–harness configurations, a single Harness² step improves GLM-5 and Gemini
3.5 Flash over their base harnesses, with gains of up to 11.5 points on
WorkBuddy-Bench, 4.5 points on
JobBench, and 11.8 percentage points on LAB's partial pass rate. Increasing the
task-level recursion budget brings further gains.
These are the upstream authors' reported results, not measurements from this fork.

The release supports **parallel and sequential recursion**, **OpenCode and
Codex executors**, and **JobBench, LAB, and WorkBuddy-Bench**.

## Video overview

https://github.com/user-attachments/assets/76b10548-b14b-4c8f-9aa5-a93662df4d56

## Contents

- [About this fork](#about-this-fork)
- [Video overview](#video-overview)
- [Harness components](#harness-components)
- [Install](#install)
- [Run a benchmark](#run-a-benchmark)
- [Experiments](#experiments)
- [Web access](#web-access)
- [Results and resume](#results-and-resume)
- [Fork acknowledgments](#fork-acknowledgments)
- [Citation](#citation)
- [Contributing](#contributing)
- [License](#license)
- [Disclaimer](#disclaimer)

## Harness components

Harness² exposes eight editable components around the model: **system prompt,
guardrails, memory, tool guidance, subagents, skills, scripts, and plugins**.
They control context construction, the tool interface, delegation, and behavior
inside the tool loop.

<p align="center">
  <a href="assets/components.png">
    <img src="assets/components.png" alt="Eight harness components grouped by runtime role: system prompt, guardrails, memory, and skills construct context; tool guidance and scripts define the tool interface; subagents orchestrate delegated work; plugins control middleware." width="100%">
  </a>
</p>

## Install

For a local interface on Windows, double-click **`Launch Harness2.cmd`** after
installing Python 3.12. It opens **Harness² Studio** in your browser, with a free
demo, model setup, experiment controls, readable reports, run history, and result files. See the
[Studio guide](docs/UI.md) and [local model guide](docs/LOCAL_MODELS.md).

Studio's **Profiles** keep each model's configurations, harness ancestry,
experience, and run history independent. Candidates can be tested without
adoption, simplified, retired, or explicitly borrowed; see [model profiles](docs/PROFILES.md).
The optional [capability catalog](docs/CAPABILITIES.md) shares selected components
with provenance. It starts disabled; model reference access is a separate profile opt-in.

Use **Windows or Linux, Python 3.12, and [uv](https://docs.astral.sh/uv/getting-started/installation/)**.
On Windows, run these commands in PowerShell from the repository root:

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -e '.[render]'
.\.venv\Scripts\harness2-demo.exe --output runs/demo --k 2
```

See the [Windows guide](docs/WINDOWS.md) for benchmark setup, dependencies, and
the distinction between native execution and Docker tasks. On Linux:

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e '.[render]'
harness2-demo --output runs/demo --k 2
```

The local demo runs both recursion modes with deterministic editors and real
Python executions. It requires no model credentials.

For model-backed benchmarks, install **Node.js 20+** and the
[benchmark dependencies](docs/SETUP.md#requirements). LAB needs Pandoc and
Poppler; WorkBuddy-Bench needs Docker. For local JobBench runs, follow the
[llama.cpp guide](docs/LOCAL_MODELS.md). For Vertex AI, also install the Google
Cloud CLI, then configure your models:

```bash
cp .env.example .env
# Edit .env with your Vertex AI project and model IDs.
gcloud auth application-default login
```

See the [setup guide](docs/SETUP.md) for model settings, authentication
alternatives, and the [Codex executor](docs/SETUP.md#codex-executor).

## Run a benchmark

The following example installs LAB and runs one task, then evaluates and reports
its result. Cloud models can incur API charges; local models consume compute
and time. Start with a small task selection. For the complete local JobBench
workflow with automatic model cleanup, use [the local-model guide](docs/LOCAL_MODELS.md).

```bash
harness2-setup lab
harness2-check --bench lab
harness2 --bench lab --k 1 --mode parallel \
  --only-domains antitrust-competition --max-tasks 1
harness2-judge --bench lab --k 1 --mode parallel
harness2-report --bench lab --k 1 --mode parallel
```

Sequential recursion runs the same commands with `--mode sequential`. `--k` is
the task-level recursion budget; see [Experiments](#experiments). Judging and
reporting must use the same benchmark, executor, subset, `--k`, `--mode`, and
seed as the refinement run. Add `--dry-run` to `harness2` to preview task
selection without model calls.

| Benchmark | Tasks | Install | Guide |
|---|---|---|---|
| JobBench | Professional work | `harness2-setup jb` | [JobBench](docs/BENCHMARKS.md#jobbench) |
| LAB | Legal work | `harness2-setup lab` | [LAB](docs/BENCHMARKS.md#lab) |
| WorkBuddy-Bench | Office, web, and code | `harness2-setup wb` | [WorkBuddy](docs/BENCHMARKS.md#workbuddy-bench) |

Choose one benchmark per command. WorkBuddy also takes `--subset office`,
`--subset web`, or `--subset code` on every command after setup. The
[benchmark guides](docs/BENCHMARKS.md) cover each one's data, domains, and
grading.

## Experiments

Scale task-level recursion in two ways: **parallel recursion** explores multiple
candidate harnesses and composes refinements from their execution evidence;
**sequential recursion** carries the refined harness and accumulated evidence
through successive Propose–Probe–Compose steps.

<p align="center">
  <a href="assets/harness2_recursion_modes.png">
    <img src="assets/harness2_recursion_modes.png" alt="Two ways to scale task-level recursion. Parallel: probe k general and k domain-specific candidate harnesses, then produce k compositions from the shared evidence. Sequential: carry the refined harness and accumulated evidence through k Propose–Probe–Compose steps." width="100%">
  </a>
</p>
<p align="center"><em>Parallel recursion explores wider; sequential recursion refines deeper.</em></p>

| Option | Behavior |
|---|---|
| `--k 1` | One Propose–Probe–Compose step |
| `--k K --mode parallel` | Probe `K` general and `K` domain-specific candidates, then produce `K` compositions |
| `--k K --mode sequential` | Refine the harness through `K` successive steps |
| `--substrate cx` | Use Codex instead of the default OpenCode executor |

After each task, harnessing-level recursion retains the adopted harness and
evidence-supported lessons to guide future refinement. See the
[experiment guide](docs/EXPERIMENTS.md) for task selection, concurrency,
reproducibility, and baseline comparisons.

## Web access

Executor network access is unrestricted, and Codex runs with its sandbox
disabled. Public benchmark rubrics may be accessible online. Before evaluation,
configure [network restrictions](docs/EXPERIMENTS.md#web-access) to prevent
access to benchmark answers and grading criteria.

## Results and resume

Results are saved under `runs/`, organized by benchmark and subset where
applicable. They include task summaries, deliverables, adopted harnesses,
reflection notes, and evaluation results.

Repeat the same standalone benchmark command to resume. Studio has a **Resume**
control for supported experiments. Use a new `HARNESS2_RUNS_DIR` when changing
models or task selection. See [results and resume](docs/EXPERIMENTS.md#results-and-resume)
for details; each command also supports `--help`.

## Fork acknowledgments

Notesong directs and maintains this fork, with AI collaborators:

- **Arden (Codex)** assisted with the Windows port, Studio, local-model workflows,
  profiles, capability catalog, testing, and documentation.
- **Vesper (GPT-5.6 Sol)** contributed independent review, architecture feedback,
  and discussion of the local experiments through conversations with Notesong.

The original Harness² framework and research are the work of the upstream
authors, credited in the citation below.

## Citation

```bibtex
@article{xu2026harness2,
  title   = {Harness$^2$: Recursive Agent Harnessing for an Open World},
  author  = {Xu, Ruiyao and Chen, Yanfei and CuiZhu, Zhongying and Dalvi Mishra, Bhavana and Ming, Yifei and Yu, Han and Han, Rujun and Lee, Chen-Yu and Pfister, Tomas},
  journal = {arXiv preprint arXiv:2610.14224},
  year    = {2026},
  url     = {https://arxiv.org/abs/2610.14224}
}
```

## Contributing

For changes to this fork, see [CONTRIBUTING.md](CONTRIBUTING.md). Contributions
intended for the original project follow its upstream contribution process.

## License

Harness² is licensed under [Apache 2.0](LICENSE). Third-party code under
[`third_party/`](third_party/README.md), benchmarks, and datasets retain their
upstream licenses; see
[third-party notices](dependencies/README.md).
The bundled distribution records its combined license expression as
`Apache-2.0 AND MIT AND LicenseRef-WorkBuddy-Bench`; the individual license texts
and dataset terms still apply to their respective material.

## Disclaimer

This fork is maintained independently of Google. The upstream project is not an
officially supported Google product. It is not eligible
for the [Google Open Source Software Vulnerability Rewards Program](https://bughunters.google.com/open-source-security).
