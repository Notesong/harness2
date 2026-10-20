// SPDX-License-Identifier: Apache-2.0
"use strict";

// Production view functions with a small DOM substitute. No model or browser call.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function harness() {
  const nodes = new Map();
  const requests = [];
  let responseProfile;
  class Node {
    constructor(tag) {
      this.tagName = tag;
      this.children = [];
      this.attributes = {};
      this.dataset = {};
      this.listeners = {};
      this.hidden = false;
      this.value = "";
      this.className = "";
      this.classList = { add() {}, toggle() {} };
      this._text = "";
    }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map((node) => node.textContent).join(""); }
    set innerHTML(_) { throw new Error("Profile content cannot enter an HTML sink"); }
    get firstElementChild() { return this.children[0] || null; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this._text = ""; this.children = children; }
    setAttribute(key, value) { this.attributes[key] = String(value); }
    removeAttribute(key) { delete this.attributes[key]; }
    addEventListener(event, action) { (this.listeners[event] ||= []).push(action); }
    click() { for (const action of this.listeners.click || []) action({ preventDefault() {} }); }
    focus() { this.focused = true; }
    scrollIntoView() { this.scrolled = true; }
    reportValidity() { return true; }
  }
  const document = {
    createElement: (tag) => new Node(tag),
    createTextNode: (text) => { const node = new Node("text"); node.textContent = text; return node; },
    getElementById: (id) => [...nodes.values()].flatMap(walk).find((node) => node.id === id) || null,
    querySelector: (selector) => {
      if (!nodes.has(selector)) nodes.set(selector, new Node("div"));
      return nodes.get(selector);
    },
    querySelectorAll: () => [],
  };
  const context = vm.createContext({
    document, URLSearchParams, location: { hash: "", pathname: "/" },
    sessionStorage: { getItem: (key) => key === "harness2_token" ? "fixture-token" : null, setItem() {} },
    history: {}, window: { scrollTo() {} }, clearTimeout() {}, setTimeout: () => 1,
    fetch: async (url, options) => {
      requests.push({ url, body: JSON.parse(options.body || "{}") });
      return { ok: true, status: 200, json: async () => responseProfile };
    },
  });
  const source = fs.readFileSync(path.join(__dirname, "../harness2/ui/assets/app.js"), "utf8");
  const boot = source.indexOf("  $$('[data-view]').forEach");
  assert.ok(boot > 0, "Find the app boot boundary");
  vm.runInContext(source.slice(0, boot) + `globalThis.ui = {
    renderProfileDetail, renderProfileRevisions, renderExperimentProfile, renderRunProfileCapture, renderProfileInventory,
    formParams, experimentSettings, profileAction, revisionFiles, runHarnessChoices, renderProfiles,
    setState(value) { state = value; },
    cache(value) { profileCache.set(value.id, value); },
  };\n})();`, context);
  return { ui: context.ui, node: document.querySelector, requests, respond: (profile) => { responseProfile = profile; } };
}

function walk(node) { return [node, ...node.children.flatMap(walk)]; }
function profile() {
  return {
    id: "owen", name: "Owen", description: "A continuing local profile.",
    active_configuration_id: "config-one", active_revision_id: "baseline",
    configurations: [
      { id: "config-one", label: "Q3 · 64K", snapshot: { settings: { provider: "local", local_model: "owen-local", local_context: "65536", local_output: "8192" }, identity: { runtime: "fixture" } } },
      { id: "config-two", label: "Different budget", snapshot: { settings: { provider: "local", local_model: "other-budget", local_context: "32768", local_output: "4096" } } },
    ],
    revisions: [
      { id: "baseline", label: "Original baseline", tree: { "system.md": "Keep the objective." }, parents: [], evidence: [], operation: "baseline" },
      { id: "smaller", label: "Smaller candidate", tree: { "system.md": "Write the files first." }, parents: ["baseline"], why: "Test whether less helps.", evidence: [{ evidence_status: "observed", detail: "One run; no baseline grade." }], operation: "simplify", experience: { cue: "After compaction", lesson: "Keep progress separate from plans." } },
      { id: "borrowed", label: "Borrowed candidate", tree: { "system.md": "A borrowed idea." }, parents: ["baseline"], borrowed_from: [{ profile_id: "second-model", revision_id: "foreign" }], operation: "import", retired: true },
    ],
    notes: [{ text: "<script>plain text, not executable</script>", evidence_status: "suspected" }],
    runs: [{ job_id: "first-run", title: "First chapter" }],
    events: [
      { type: "created", time: "2026-10-19T03:00:00Z", data: { name: "Owen" } },
      { type: "revision_added", time: "2026-10-19T04:00:00Z", data: { id: "smaller" } },
    ],
  };
}

function setup(fixture) {
  const item = profile();
  fixture.ui.cache(item);
  fixture.ui.setState({ settings: { provider: "custom", codex_model: "setup-only" },
    profiles: [{ id: item.id, name: item.name }], jobs: [{ id: "first-run", kind: "benchmark", status: "succeeded", title: "First chapter" }],
    benchmarks: [{ id: "jb", installed: true, name: "JobBench", domains: [{ id: "technical_writers", count: 3 }] }] });
  return item;
}

test("profile ancestry keeps literal text, parents, borrowing, experience and independent evidence labels", () => {
  const fixture = harness();
  const item = setup(fixture);
  fixture.ui.renderProfileDetail(item);
  const view = fixture.node("#profile-detail");
  assert.match(view.textContent, /A CONTINUING IDENTITY/);
  assert.match(view.textContent, /Descends from Original baseline/);
  assert.match(view.textContent, /Borrowed as a hypothesis: second-model \/ foreign/);
  assert.match(view.textContent, /Keep progress separate from plans/);
  assert.ok(view.textContent.includes(item.notes[0].text));
  const badges = walk(view).filter((node) => node.className.includes("evidence-badge"));
  assert.ok(badges.some((node) => node.textContent === "Observed"));
  assert.ok(badges.some((node) => node.textContent === "Suspected"));
  assert.ok(badges.some((node) => node.textContent === "Unclassified"), "Do not infer evidence status for an unclassified baseline");
  assert.match(view.textContent, /Restore for future runs/);
  assert.match(view.textContent, /Test whether less helps/);
});

test("candidate tests pin a chosen configuration and harness without adopting them", () => {
  const fixture = harness();
  const item = setup(fixture);
  fixture.node("#experiment-profile").value = item.id;
  fixture.ui.renderExperimentProfile();
  fixture.node("#experiment-configuration").value = "config-two";
  fixture.node("#experiment-revision").value = "smaller";
  fixture.ui.renderExperimentProfile();
  fixture.node("#benchmark").value = "jb";
  fixture.node("#domain").value = "technical_writers";
  fixture.node("#substrate").value = "oc";
  fixture.node("#mode").value = "parallel";
  fixture.node("#max-tasks").value = "1";
  fixture.node("#workers").value = "1";
  fixture.node("#branches").value = "1";
  const params = fixture.ui.formParams();
  assert.equal(params.profile_id, "owen");
  assert.equal(params.profile_configuration_id, "config-two");
  assert.equal(params.profile_revision_id, "smaller");
  assert.equal(fixture.ui.experimentSettings(params).local_model, "other-budget");
  assert.equal(item.active_revision_id, "baseline");
  assert.equal(item.active_configuration_id, "config-one");
  assert.match(fixture.node("#experiment-profile-help").textContent, /without changing the profile’s active harness/);
  assert.equal(fixture.requests.length, 0);
});

test("new profiles offer a clean bundled baseline and explicit hypothesis imports", () => {
  const fixture = harness();
  const item = setup(fixture);
  fixture.ui.setState({ profiles: [{ id: "owen", name: "Owen" }, { id: "second", name: "Second model" }], jobs: [] });
  const view = fixture.ui.renderProfileRevisions({ ...item, revisions: [] });
  assert.match(view.textContent, /copies no learned changes from another profile/);
  assert.match(view.textContent, /does not activate it or change the source/);
  assert.match(view.textContent, /Import as a hypothesis/);
  assert.equal(fixture.requests.length, 0);
});

test("simplification is a candidate operation with explicit file choices", () => {
  const fixture = harness();
  const item = setup(fixture);
  const view = fixture.ui.renderProfileRevisions(item);
  const checks = walk(view).filter((node) => node.type === "checkbox");
  assert.equal(checks.length, 3);
  assert.ok(checks.every((node) => !node.checked && node.value === "system.md"));
  assert.match(view.textContent, /The source revision stays intact/);
  assert.match(view.textContent, /Save smaller candidate/);
});

test("run detail displays the pinned starting point and offers candidate capture", () => {
  const fixture = harness();
  setup(fixture);
  fixture.ui.renderRunProfileCapture({ id: "first-run", kind: "benchmark", status: "succeeded", params: { profile_id: "owen" }, profile: {
    profile_id: "owen", profile_name: "Owen", configuration_id: "config-one", revision_id: "baseline",
  } });
  const view = fixture.node("#run-profile-capture");
  assert.match(view.textContent, /Owen · recorded starting point/);
  assert.match(view.textContent, /Configuration config-one · harness baseline/);
  assert.match(view.textContent, /completion alone does not prove improvement/);
  assert.match(view.textContent, /Save as a candidate/);
});

test("saving a candidate makes one archive request and never silently activates it", async () => {
  const fixture = harness();
  const item = setup(fixture);
  fixture.respond(item);
  await fixture.ui.profileAction(item.id, "from-run", { job_id: "first-run", label: "Candidate", why: "One observed run." }, "Saved.");
  assert.equal(fixture.requests.length, 1);
  assert.equal(fixture.requests[0].url, "/api/profiles/owen/from-run");
  assert.equal(fixture.requests[0].body.label, "Candidate");
  assert.ok(!Object.hasOwn(fixture.requests[0].body, "parents"), "Do not invent ancestry from today's active harness");
  assert.equal(item.active_revision_id, "baseline");
});

test("multi-task candidate capture carries the exact task and upstream mode", () => {
  const fixture = harness();
  const choices = fixture.ui.runHarnessChoices({ tasks: [
    { id: "technical_writers__task1", selections: [{ mode: "parallel", label: "agg1" }, { mode: "sequential", label: "seq2" }] },
    { id: "technical_writers__task2", selections: [{ mode: "parallel", label: "agg3" }, { mode: "sequential", label: "" }] },
  ] });
  assert.equal(choices.length, 3);
  assert.equal(choices[0].mode, "agg");
  assert.equal(choices[1].mode, "seq");
  assert.equal(choices[2].task_id, "technical_writers__task2");
});

test("profile counts use singular labels for one revision and one run", () => {
  const fixture = harness();
  fixture.ui.setState({ profiles: [{ id: "owen", name: "Owen", revision_count: 1, run_count: 1 }], jobs: [] });
  fixture.ui.renderProfiles();
  assert.match(fixture.node("#profile-list").textContent, /1 revision · 1 run/);
  assert.doesNotMatch(fixture.node("#profile-list").textContent, /1 revisions|1 runs/);
});

test("preview starting points stay planned and show exact configuration and harness IDs", () => {
  const fixture = harness();
  setup(fixture);
  fixture.ui.renderRunProfileCapture({ id: "preview-one", kind: "preview", status: "succeeded", profile_plan: {
    profile_id: "owen", profile_name: "Owen", configuration_id: "planned-config", revision_id: "planned-revision",
  }, params: { profile_id: "owen" } });
  const view = fixture.node("#run-profile-capture");
  assert.match(view.textContent, /Owen · planned starting point/);
  assert.match(view.textContent, /Configuration planned-config · harness planned-revision/);
  assert.match(view.textContent, /Starting an experiment records its own immutable snapshot/);
  assert.doesNotMatch(view.textContent, /recorded starting point|Later profile changes do not alter this run/);
  fixture.ui.renderRunProfileCapture({ id: "preview-older", kind: "preview", status: "succeeded", params: {
    profile_id: "owen", profile_configuration_id: "older-config", profile_revision_id: "older-revision",
  } });
  assert.match(view.textContent, /planned starting point/);
  assert.match(view.textContent, /Configuration older-config · harness older-revision/);
  assert.doesNotMatch(view.textContent, /recorded with run/);
});

test("current capability inventory names selected components and keeps runtime availability qualified", () => {
  const fixture = harness(); const item = setup(fixture);
  item.capabilities = {
    configuration: { id: "config-one", label: "Q3 · 64K", model: "qwen-local", context: 65536, output: 8192, tool_policy_sha256: "policy-hash", association: "Saved model association." },
    revision: { id: "baseline", label: "Own selected harness", content_sha256: "content-hash" },
    components: { always_on: [{ path: "guardrails.md", label: "Guardrails", present: true, bytes: 120 }], skills: [], scripts: [{ path: "scripts/check.py", name: "check.py", present: true, description: "<script>Literal script description</script>" }], subagents: [], plugins: [] },
    builtin_tools: [{ id: "bash", label: "Run shell commands", status: "runtime-dependent", qualification: "Runs as the current operating-system user." }, { id: "websearch", label: "Web search", status: "conditional", qualification: "Needs the configured remote service." }],
    application_tools: [{ id: "harness2_conversation", label: "Conversation sidecar", status: "conditional", qualification: "Only in explicitly conversational sessions." }],
    permissions: { os_sandbox: false, shell_confined: false, summary: "Declared application policy is not an operating-system sandbox; shell commands are not confined." },
    runtime: { source: "Pinned source inventory; not live tool observation.", substrate: "OpenCode", version: "1.14.18" },
    limits: ["A loaded model cannot wake itself while inference is idle.", "Catalog access requires a separate opt-in."] };
  const view = fixture.ui.renderProfileInventory(item);
  assert.match(view.textContent, /Current capabilities/); assert.match(view.textContent, /Own selected harness/);
  assert.match(view.textContent, /Harness baseline/); assert.match(view.textContent, /Configuration config-one/);
  assert.match(view.textContent, /Custom skills · 0/); assert.match(view.textContent, /Helper scripts · 1/);
  assert.ok(view.textContent.includes("<script>Literal script description</script>"));
  assert.match(view.textContent, /Run shell commandsRuntime Dependent/); assert.match(view.textContent, /Web searchConditional/);
  assert.match(view.textContent, /Only in explicitly conversational sessions/);
  assert.match(view.textContent, /not an operating-system sandbox/); assert.match(view.textContent, /shell commands are not confined/);
  assert.match(view.textContent, /not live tool observation/); assert.match(view.textContent, /cannot wake itself/);
  const starting = view.children.find((node) => node.className === "inventory-starting-point");
  const provenance = starting.children.find((node) => node.tagName === "details");
  assert.match(starting.children.filter((node) => node.tagName !== "details").map((node) => node.textContent).join(" "), /qwen-local.*65,536 context.*8,192 output/);
  assert.equal(provenance.children[0].textContent, "Saved configuration & provenance");
  assert.equal(Boolean(provenance.open), false); assert.match(provenance.textContent, /config-one.*content-hash.*policy-hash.*Saved model association/);
  const scope = view.children.find((node) => node.className === "inventory-boundaries");
  assert.equal(scope.tagName, "details"); assert.equal(Boolean(scope.open), false);
  assert.equal(scope.children[0].textContent, "Inventory scope and permissions");
  assert.equal((scope.textContent.match(/operating-system sandbox/g) || []).length, 1, "The backend permission summary is not repeated by a fallback");
  assert.equal(fixture.requests.length, 0, "An inventory never loads a model or tests a tool");
});

test("an unavailable tool catalog preserves its limits without inventing enabled tools", () => {
  const fixture = harness(); const item = setup(fixture);
  item.capabilities = { configuration: {}, revision: {}, components: {}, builtin_tools: [], permissions: {}, limits: ["The pinned runtime changed; its tool catalog needs a fresh review."] };
  const view = fixture.ui.renderProfileInventory(item);
  assert.match(view.textContent, /No harness selected/); assert.match(view.textContent, /No configuration selected/);
  assert.match(view.textContent, /pinned runtime changed/); assert.doesNotMatch(view.textContent, /Enabled|tool success confirmed/);
  assert.equal(fixture.requests.length, 0);
});
