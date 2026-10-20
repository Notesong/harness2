// SPDX-License-Identifier: Apache-2.0
"use strict";

// Production catalog views with a small DOM substitute. No browser or model call.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function walk(node) { return [node, ...node.children.flatMap(walk)]; }
const flush = async () => { await new Promise(setImmediate); };

function harness() {
  const nodes = new Map();
  const requests = [];
  const responses = new Map();
  class Node {
    constructor(tag) {
      this.tagName = tag;
      this.children = [];
      this.attributes = {};
      this.dataset = {};
      this.listeners = {};
      this.hidden = false;
      this.checked = false;
      this.value = "";
      this.className = "";
      this.classList = { add() {}, toggle() {} };
      this._text = "";
    }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map((node) => node.textContent).join(""); }
    set innerHTML(_) { throw new Error("Catalog content cannot enter an HTML sink"); }
    get firstElementChild() { return this.children[0] || null; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this._text = ""; this.children = children; }
    setAttribute(key, value) { this.attributes[key] = String(value); }
    removeAttribute(key) { delete this.attributes[key]; }
    addEventListener(event, action) { (this.listeners[event] ||= []).push(action); }
    async fire(event) { await Promise.all((this.listeners[event] || []).map((action) => action({ preventDefault() {} }))); }
    focus() {}
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
      requests.push({ url, body: JSON.parse(options.body || "{}"), method: options.method || "GET" });
      const result = responses.get(url);
      if (result === undefined) throw new Error(`Unexpected request: ${url}`);
      return { ok: true, status: 200, json: async () => result };
    },
  });
  const source = fs.readFileSync(path.join(__dirname, "../harness2/ui/assets/app.js"), "utf8");
  const boot = source.indexOf("  $$('[data-view]').forEach");
  assert.ok(boot > 0);
  vm.runInContext(source.slice(0, boot) + `globalThis.ui = {
    renderCapabilities, renderCapabilityList, renderCapabilityPublisher, renderCapabilityDetail, renderCapabilityFork, capabilityAction, renderRunProfileCapture,
    setState(value) { state = value; view = "capabilities"; },
    select(entry) { selectedCapabilityId = entry.id; capabilityCache.set(entry.id, entry); },
    cache(profile) { profileCache.set(profile.id, profile); },
  };\n})();`, context);
  return { ui: context.ui, node: document.querySelector, requests, respond: (url, result) => responses.set(url, result) };
}

function profile(id = "owen", name = "Owen") {
  return { id, name, active_configuration_id: "config", active_revision_id: "baseline",
    configurations: [{ id: "config", label: "Original configuration" }],
    revisions: [{ id: "baseline", label: "Original baseline", tree: { "system.md": "Own guidance.", "keep.md": "Keep this." } }],
    notes: [], events: [], runs: [] };
}
function entry() {
  return { id: "checkpoint", name: "Progress checkpointing", description: "Preserve completed work across compaction.",
    why: "Repeated reads after a context boundary.", kind: "skill", portability: "model-sensitive", evidence_status: "observed",
    origin: { profile_id: "owen", profile_name: "Owen", configuration_id: "config", revision_id: "candidate" },
    files: { "system.md": "<script>Never execute this</script>\nProgress before plans." },
    costs: { context_tokens: 120, time_seconds: 2 }, incompatibilities: ["Needs a writable progress file."],
    failures: ["Not evaluated under a second configuration."], derived_from: [], evidence: [{ run_id: "first", evidence_status: "observed" }] };
}
function setup(fixture, enabled = false, entries = []) {
  const state = { settings: {}, benchmarks: [], jobs: [], profiles: [profile(), profile("kestrel", "Kestrel")], capabilities: { enabled, profile_access: {}, entries } };
  fixture.ui.setState(state);
  for (const item of state.profiles) fixture.respond(`/api/profiles/${item.id}`, item);
  return state;
}
function field(form, name) { return walk(form).find((node) => node.name === name); }

test("dormant catalog makes no model or publishing requests and leaves access controls hidden", () => {
  const fixture = harness();
  setup(fixture);
  fixture.ui.renderCapabilities();
  assert.match(fixture.node("#capability-status").textContent, /The catalog is off/);
  assert.match(fixture.node("#capability-status").textContent, /New experiments receive no catalog/);
  assert.match(fixture.node("#capability-status").textContent, /Existing recorded run snapshots stay unchanged/);
  assert.match(fixture.node("#capability-status").textContent, /Enable catalog/);
  assert.equal(fixture.node("#capability-access-panel").hidden, true);
  assert.equal(fixture.node("#capability-publish-panel").hidden, true);
  assert.match(fixture.node("#capability-detail").textContent, /No example capabilities have been added/);
  assert.equal(fixture.requests.length, 0);
});

test("human inspection remains available while off, with literal files and no fork controls", () => {
  const fixture = harness();
  const capability = entry();
  setup(fixture, false, [capability]);
  fixture.ui.select(capability);
  fixture.ui.renderCapabilities();
  const detail = fixture.node("#capability-detail");
  assert.match(detail.textContent, /Owen/);
  assert.match(detail.textContent, /Configuration config · harness candidate/);
  assert.ok(detail.textContent.includes(capability.files["system.md"]));
  assert.match(detail.textContent, /Declared: Observed/);
  assert.match(detail.textContent, /publisher’s account/);
  assert.match(detail.textContent, /Needs a writable progress file/);
  assert.match(detail.textContent, /Context Tokens 120/);
  assert.doesNotMatch(detail.textContent, /Save fork as a candidate/);
  assert.equal(fixture.requests.length, 0);
});

test("enabling the catalog is one explicit action and does not opt profiles in", async () => {
  const fixture = harness();
  const state = setup(fixture);
  fixture.respond("/api/capabilities/settings", { ...state.capabilities, enabled: true });
  fixture.ui.renderCapabilities();
  const enable = walk(fixture.node("#capability-status")).find((node) => node.tagName === "button");
  await enable.fire("click");
  await flush();
  const mutations = fixture.requests.filter((request) => request.method === "POST");
  assert.equal(mutations.length, 1);
  assert.equal(mutations[0].url, "/api/capabilities/settings");
  assert.deepEqual(mutations[0].body, { enabled: true });
  assert.match(fixture.node("#capability-profile-access").textContent, /Access off · independent development/);
  assert.doesNotMatch(fixture.requests.map((request) => request.url).join(" "), /\/access/);
});

test("profile reference access is an explicit separate request", async () => {
  const fixture = harness();
  const state = setup(fixture, true);
  fixture.respond("/api/capabilities/access", { ...state.capabilities, profile_access: { owen: true } });
  fixture.ui.renderCapabilities();
  const allow = walk(fixture.node("#capability-profile-access")).find((node) => node.tagName === "button");
  await allow.fire("click");
  const mutations = fixture.requests.filter((request) => request.method === "POST");
  assert.equal(mutations.length, 1);
  assert.deepEqual(mutations[0].body, { profile_id: "owen", enabled: true });
  assert.equal(mutations[0].url, "/api/capabilities/access");
});

test("family links distinguish recorded ancestors from later variants", () => {
  const fixture = harness();
  const capability = entry();
  const parent = { ...capability, id: "origin", name: "An earlier idea" };
  const child = { ...capability, id: "variant", name: "Kestrel’s version", derived_from: [capability.id] };
  capability.derived_from = [parent.id];
  setup(fixture, false, [parent, capability, child]);
  fixture.ui.select(capability);
  fixture.ui.renderCapabilities();
  assert.match(fixture.node("#capability-detail").textContent, /Derived fromAn earlier idea/);
  assert.match(fixture.node("#capability-detail").textContent, /Later variantKestrel’s version/);
});

test("forks require explicit collision replacement and preserve modified text in one candidate request", async () => {
  const fixture = harness();
  const capability = entry();
  const state = setup(fixture, true, [capability]);
  const target = profile("kestrel", "Kestrel");
  fixture.respond("/api/capabilities/fork", { profile: { ...target, revisions: [...target.revisions, { id: "forked", label: "Borrowed hypothesis", tree: { "system.md": "Adapted" } }] }, revision_id: "forked", catalog: state.capabilities });
  const section = fixture.ui.renderCapabilityFork(capability);
  await flush();
  const form = walk(section).find((node) => node.tagName === "form");
  const collision = walk(section).find((node) => node.type === "checkbox" && node.value === "system.md");
  assert.ok(collision.required);
  assert.equal(collision.checked, false);
  field(form, "fork-label").value = "Borrowed hypothesis";
  field(form, "fork-why").value = "Test our own compaction failure.";
  await form.fire("submit");
  assert.equal(fixture.requests.filter((request) => request.method === "POST").length, 0);
  collision.checked = true;
  const edit = walk(section).find((node) => node.type === "checkbox" && node !== collision);
  edit.checked = true;
  const editor = walk(section).find((node) => node.tagName === "textarea" && node.className === "capability-file-editor");
  editor.value = "Keep our own progress format.\n<script>literal</script>";
  await form.fire("submit");
  const mutations = fixture.requests.filter((request) => request.method === "POST");
  assert.equal(mutations.length, 1);
  assert.equal(mutations[0].url, "/api/capabilities/fork");
  assert.deepEqual(mutations[0].body.replace_paths, ["system.md"]);
  assert.equal(mutations[0].body.replace_files["system.md"], editor.value);
  assert.equal(mutations[0].body.parent_revision_id, "baseline");
  assert.equal(mutations[0].body.target_profile_id, "kestrel");
  assert.equal(target.active_revision_id, "baseline");
  assert.ok(!Object.hasOwn(mutations[0].body, "activate"));
});

test("publishing selects source files and carries declared limits without executing anything", async () => {
  const fixture = harness();
  const state = setup(fixture, true);
  fixture.respond("/api/capabilities/publish", state.capabilities);
  fixture.ui.renderCapabilityPublisher(state.profiles);
  await flush();
  const form = walk(fixture.node("#capability-publish-form")).find((node) => node.tagName === "form");
  field(form, "publish-name").value = "A small checkpoint";
  field(form, "publish-description").value = "Preserve progress.";
  field(form, "publish-why").value = "A repeated context failure.";
  field(form, "publish-kind").value = "guardrail";
  field(form, "publish-portability").value = "model-sensitive";
  field(form, "publish-evidence").value = "suspected";
  field(form, "publish-incompatibilities").value = "Needs progress.md\nNo read-only tasks";
  const checks = walk(form).filter((node) => node.type === "checkbox");
  assert.ok(checks.length >= 2);
  assert.ok(checks.every((node) => !node.checked));
  checks.find((node) => node.value === "system.md").checked = true;
  await form.fire("submit");
  const mutations = fixture.requests.filter((request) => request.method === "POST");
  assert.equal(mutations.length, 1);
  assert.equal(mutations[0].url, "/api/capabilities/publish");
  assert.deepEqual(mutations[0].body.paths, ["system.md"]);
  assert.deepEqual(mutations[0].body.incompatibilities, ["Needs progress.md", "No read-only tasks"]);
  assert.deepEqual(mutations[0].body.derived_from, []);
  assert.equal(mutations[0].body.configuration_id, "config");
  assert.equal(mutations[0].body.revision_id, "baseline");
  assert.equal(mutations[0].body.evidence_status, "suspected");
  assert.ok(!Object.hasOwn(mutations[0].body, "activate"));
});

test("turning off after opening a form blocks a stale publish submission", async () => {
  const fixture = harness();
  const state = setup(fixture, true);
  fixture.ui.renderCapabilityPublisher(state.profiles);
  await flush();
  const form = walk(fixture.node("#capability-publish-form")).find((node) => node.tagName === "form");
  state.capabilities.enabled = false;
  await form.fire("submit");
  assert.equal(fixture.requests.filter((request) => request.method === "POST").length, 0);
});

test("stronger declared evidence requires a reference and is sent as a claim", async () => {
  const fixture = harness();
  const state = setup(fixture, true);
  fixture.respond("/api/capabilities/publish", state.capabilities);
  fixture.ui.renderCapabilityPublisher(state.profiles);
  await flush();
  const form = walk(fixture.node("#capability-publish-form")).find((node) => node.tagName === "form");
  for (const [name, value] of [["publish-name", "Checkpoint"], ["publish-description", "Preserve work"], ["publish-why", "Context boundary"], ["publish-kind", "skill"], ["publish-portability", "unknown"], ["publish-evidence", "observed"]]) field(form, name).value = value;
  walk(form).find((node) => node.type === "checkbox" && node.value === "system.md").checked = true;
  await form.fire("submit");
  assert.equal(fixture.requests.filter((request) => request.method === "POST").length, 0);
  field(form, "publish-references").value = "runs/example-run/summary.json";
  await form.fire("submit");
  const mutation = fixture.requests.find((request) => request.method === "POST");
  assert.deepEqual(mutation.body.evidence, [{ reference: "runs/example-run/summary.json" }]);
  assert.equal(mutation.body.evidence_status, "observed");
});

test("fork ancestry shows who tried it and keeps local validation separate", () => {
  const fixture = harness();
  const capability = { ...entry(), forks: [{ profile_id: "kestrel", profile_name: "Kestrel", revision_id: "hypothesis", revision_label: "Try checkpointing", active: false, local_validation: "unvalidated" }] };
  setup(fixture, false, [capability]);
  fixture.ui.select(capability);
  fixture.ui.renderCapabilities();
  assert.match(fixture.node("#capability-detail").textContent, /Forks in profiles/);
  assert.match(fixture.node("#capability-detail").textContent, /Kestrel · Try checkpointing/);
  assert.match(fixture.node("#capability-detail").textContent, /Saved revision · local validation: Unvalidated/);
  assert.doesNotMatch(fixture.node("#capability-detail").textContent, /Active harness/);
});

test("case-insensitive receiving paths require visible replacement consent", async () => {
  const fixture = harness();
  const capability = entry();
  setup(fixture, true, [capability]);
  const target = profile("kestrel", "Kestrel");
  target.revisions[0].tree = { "SYSTEM.md": "Own guidance" };
  fixture.respond("/api/profiles/kestrel", target);
  const section = fixture.ui.renderCapabilityFork(capability);
  await flush();
  const collision = walk(section).find((node) => node.type === "checkbox" && node.value === "system.md");
  assert.ok(collision, "Windows file case must not hide a replacement decision");
  assert.equal(collision.checked, false);
});

test("changing a source profile does not let a delayed earlier response replace its file choices", async () => {
  const fixture = harness();
  const state = setup(fixture, true);
  let release;
  fixture.respond("/api/profiles/owen", new Promise((resolve) => { release = resolve; }));
  const kestrel = profile("kestrel", "Kestrel");
  kestrel.revisions[0].tree = { "kestrel.md": "Own source" };
  fixture.respond("/api/profiles/kestrel", kestrel);
  fixture.ui.renderCapabilityPublisher(state.profiles);
  const form = walk(fixture.node("#capability-publish-form")).find((node) => node.tagName === "form");
  field(form, "publish-profile").value = "kestrel";
  await field(form, "publish-profile").fire("change");
  release(profile());
  await flush();
  await field(form, "publish-revision").fire("change");
  const paths = walk(form).filter((node) => node.type === "checkbox").map((node) => node.value);
  assert.deepEqual(paths, ["kestrel.md"]);
});

test("run disclosure preserves recorded catalog availability after the catalog is turned off", () => {
  const fixture = harness();
  const capability = entry();
  setup(fixture, false, [capability]);
  fixture.ui.renderRunProfileCapture({ id: "past-run", kind: "benchmark", status: "running", profile: {
    profile_id: "owen", configuration_id: "config", revision_id: "baseline",
  }, capability_catalog: { exposed: true, entry_count: 1, entry_ids: [capability.id] } });
  const detail = fixture.node("#run-profile-capture");
  assert.match(detail.textContent, /Catalog references: 1 \(availability recorded; use unconfirmed\)/);
  assert.match(detail.textContent, /Inspect the recorded references/);
  assert.match(detail.textContent, /Progress checkpointing/);
  assert.doesNotMatch(detail.textContent, /Catalog not exposed/);
  fixture.ui.renderRunProfileCapture({ id: "new-run", kind: "benchmark", status: "running", profile: {
    profile_id: "owen", configuration_id: "config", revision_id: "baseline",
  }, capability_catalog: { exposed: false, entry_count: 0, entry_ids: [] } });
  assert.match(detail.textContent, /Catalog not exposed for this run/);
  assert.doesNotMatch(detail.textContent, /Inspect the recorded references/);
});

test("responsive navigation includes six labelled views and no inline scripts", () => {
  const html = fs.readFileSync(path.join(__dirname, "../harness2/ui/assets/index.html"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "../harness2/ui/assets/app.css"), "utf8");
  assert.equal((html.match(/class="nav-item(?: selected)?"/g) || []).length, 6);
  assert.match(html, /data-view="capabilities"/);
  assert.match(css, /\.navigation \.nav-item\{width:16\.666%/);
  assert.doesNotMatch(html, /onclick=|<script>\s*\S/);
});
