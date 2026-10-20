# Capability catalog

The catalog is a workspace-local collection of components that profiles can
offer as references. It is **disabled for model exposure by default**. Opening
Studio and creating a profile do not enable sharing or change an active harness.
While sharing is disabled, existing catalog entries can be inspected, but
publication and forks are blocked. Owen's earlier experiments are not published
automatically.

The catalog is separate from the shared harness engine. Process containment,
filesystem boundaries, model lifetime, and benchmark rules remain shared
infrastructure. A capability is a recorded technique or component, not an
exception to those boundaries.

## Find it in Studio

Open **Capabilities** to inspect the catalog's status and any saved entries.
Leave it off until you want to exchange material. **Enable catalog** permits
publication and forks; **Choose which profiles may see the catalog** controls
reference access separately for each profile.

With the catalog enabled, expand **Publish from a profile**, choose the source
profile, configuration, revision, and files, then describe the capability and its
limits. Open an entry to read its files as plain text, follow its family links,
or create a candidate in another profile. Reading a file never executes it.

## Publishing preserves its source

Publish explicitly selected files from a saved profile revision. A catalog entry
records its name, origin profile, configuration and revision, motivation, form,
portability, costs, known limitations, evidence references, and ancestry. Its
file contents are copied into the catalog. Later edits to the source profile do
not silently update the published version.

Publication does not run scripts, install plugins, activate a revision, or add
files to another model's toolbox. A deterministic helper, an on-demand skill,
and a behavioral instruction can have different portability. The publisher's
description and evidence labels are claims to inspect, rather than automatic
certification of correctness or transfer.

Publishing with an observed, replicated, or transferred label requires a supporting
reference. The catalog records that declaration without verifying the referenced
run or establishing that the capability caused its outcome.

The selected origin configuration is a declared association. Selecting it does
not establish that the configuration caused or authored the component. Likewise,
independent invention is a declared origin, not a finding the catalog certifies.

Supporting runs remain references. Their reports, grades, raw sessions, and
private configuration are not read into model context. Existing recognized
grader-marker checks also apply to the public packet and component files.
These checks are tripwires; publishing answer-free sources is the primary
boundary. Only the packet's allowed public reference fields are exposed to a
model; freeform administrative reference text is not included.

## Browsing has three explicit gates

Model exposure requires all of the following:

1. Workspace catalog sharing is enabled.
2. The chosen profile has opted in to catalog references.
3. A new experiment pins the available reference packet into its starting
   snapshot.

Enabling workspace sharing alone does not expose the catalog to every profile.
When sharing is off, or a profile has not opted in, a new experiment receives
neither catalog metadata nor component files. The catalog is not mentioned in
its model context. Runs without a selected profile retain their existing
behavior.

For an opted-in experiment, the pinned packet is reference material in the
proposer's disposable context. It is not appended to the solver's active
harness. The proposer can inspect it while developing a candidate; it is not
required to adopt it. Reading a reference does not modify the permanent profile
archive. Exposure proves that a reference was available, not that the model used
it or that it helped.

The proposer is asked to record `CAPABILITY_SOURCE: <full capability ID>` in its
final decision notes when borrowing. Archiving a selected candidate recognizes
declarations in that candidate's stage notes or final selection reason, and only
for IDs actually exposed in its pinned packet. It preserves the origin profile,
revision, and capability version, with **model-declared / unvalidated locally**
evidence. An explicit human fork also records its ancestry. Without either, the
archive records availability without asserting transmission. A declaration is
not proof that the component was used correctly or provided a benefit.

An experiment's exposure is frozen at its start. Disabling sharing, changing
profile opt-in, or publishing another version affects future experiments; it
does not rewrite a running or resumed experiment's pinned packet. Stop an
exposed experiment to prevent its further execution. Information a model has
already read cannot be withdrawn from its history.

This distinction keeps clean starting conditions possible: a new profile can
work without catalog exposure, then begin a separately recorded experiment
with references available.

The exposure packet is bounded to 32 entries and 1 MiB. The current implementation
offers the whole catalog to an opted-in profile; it has no per-entry selection
control. If the packet exceeds either limit, a new opted-in run fails preflight
rather than silently receiving a truncated catalog.

## Forking creates a candidate

An explicit fork copies the published entry's component files into a new revision
of the recipient profile. It records the exact source capability version and local
parent revision. It starts as **foreign / unvalidated locally**, regardless of
what happened in the originating profile.

Forking does not activate that candidate, change either profile's active
selection, execute code, install dependencies, or grant tools. Existing local
content is retained unless replacement is explicitly chosen for a collision.
A skill fork may add a discovery hint to the candidate's guardrails so the skill
can be found. Those integration changes are recorded with the fork; the source
profile's guardrails are not copied implicitly. Unrelated local files are retained.
In the fork form, **Adapt the borrowed files before saving** permits optional text
changes to the incoming files. The source entry stays intact. The resulting
candidate can be tested, simplified, retired, or explicitly selected for future
work through the normal profile workflow.

A later publication from that candidate preserves its recorded capability
ancestry. Independent invention and transmission are different origins:
an independently developed component does not receive a borrowed-from link just
because similar material exists elsewhere in the catalog.

Evidence remains separate from adoption. A foreign component can be tried
without a claim of local success. A good result does not automatically promote
its evidence status, publish it again, or distribute it to other profiles.

The archive lives in `.harness2/capabilities/`. Back up that complete directory
alongside `.harness2/profiles/` and the relevant run folders. Published objects
and availability/consent events use the archive's integrity checks; do not edit
them in place. This is a local archive, not protection against its owner
deliberately rewriting its history.

## Present scope

The first implementation provides local publication, browsing, ancestry,
explicit file-level forks, and pinned reference exposure for local
JobBench/OpenCode profile experiments. Existing entries remain available for
human inspection while sharing is disabled. It does not create a network registry,
automatic sharing, an autonomous package installer, or a scheduler for future
experiments.

Validation uses temporary archives and deterministic context construction.
Model-backed capability transfer has not been demonstrated. Leaving the catalog
disabled is a supported steady state, not an incomplete migration.
