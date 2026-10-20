// SPDX-License-Identifier: Apache-2.0
"use strict";

// Production views in a small DOM substitute; no browser or model is invoked.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const flush = () => new Promise(setImmediate);
function walk(node) { return [node, ...node.children.flatMap(walk)]; }

function harness() {
  const nodes = new Map();
  const requests = [];
  const responses = new Map();
  class Node {
    constructor(tag) {
      this.tagName = tag; this.children = []; this.attributes = {}; this.dataset = {}; this.listeners = {};
      this.hidden = false; this.checked = false; this.disabled = false; this.value = ""; this.className = ""; this._text = "";
      this.scrollTop = 0; this.scrollHeight = 100; this.clientHeight = 100;
      this.classList = { add() {}, toggle() {} };
    }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map((node) => node.textContent).join(""); }
    set innerHTML(_) { throw new Error("Visible chat must remain plain text"); }
    get firstElementChild() { return this.children[0] || null; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this._text = ""; this.children = children; }
    setAttribute(key, value) { this.attributes[key] = String(value); }
    removeAttribute(key) { delete this.attributes[key]; }
    addEventListener(event, action) { (this.listeners[event] ||= []).push(action); }
    async fire(event) { await Promise.all((this.listeners[event] || []).map((action) => action({ preventDefault() {} }))); }
    focus() { this.focused = true; }
    scrollIntoView() { this.scrolled = true; }
    reportValidity() { return true; }
  }
  const document = {
    createElement: (tag) => new Node(tag),
    createTextNode: (text) => { const node = new Node("text"); node.textContent = text; return node; },
    getElementById: (id) => [...nodes.values()].flatMap(walk).find((node) => node.id === id) || null,
    querySelector: (selector) => { if (!nodes.has(selector)) nodes.set(selector, new Node("div")); return nodes.get(selector); },
    querySelectorAll: () => [],
  };
  const context = vm.createContext({
    document, URLSearchParams, location: { hash: "", pathname: "/" },
    sessionStorage: { getItem: (key) => key === "harness2_token" ? "fixture-token" : null, setItem() {} },
    history: {}, window: { scrollTo() {} }, clearTimeout() {}, setTimeout: () => 1,
    fetch: async (url, options) => {
      requests.push({ url, method: options.method || "GET", body: JSON.parse(options.body || "{}") });
      const response = responses.get(url);
      if (response === undefined) throw new Error(`Unexpected request: ${url}`);
      return { ok: true, status: 200, json: async () => typeof response === "function" ? response() : response };
    },
  });
  const source = fs.readFileSync(path.join(__dirname, "../harness2/ui/assets/app.js"), "utf8");
  const boot = source.indexOf("  $$('[data-view]').forEach");
  vm.runInContext(source.slice(0, boot) + `globalThis.ui = {
    renderConversations, renderConversationDetail, renderConversationRecovery, createConversation, talkToProfile,
    conversationAction, sendConversationMessage, loadConversation, selectConversation, renderProfileDetail,
    formParams, renderConversationOption, conversationOptionAllowed, renderRunProfileCapture,
    setState(value) { state = value; view = "conversations"; },
    setView(value) { view = value; },
    select(value) { selectedConversationId = value.id; conversationCache.set(value.id, value); },
    cacheProfile(value) { profileCache.set(value.id, value); },
    cached(id) { return conversationCache.get(id); },
    draft(id,value) { conversationDrafts.set(id,value); },
  };\n})();`, context);
  return { ui: context.ui, node: document.querySelector, requests, respond: (url, value) => responses.set(url, value) };
}
function profile() {
  return { id: "owen", name: "Owen", active_configuration_id: "config", active_revision_id: "baseline",
    configurations: [{ id: "config", label: "64K", snapshot: { settings: { provider: "local", local_model: "qwen-local" } } }],
    revisions: [{ id: "baseline", label: "Own baseline", tree: { "system.md": "Keep the task." }, parents: [], evidence: [] }], notes: [], runs: [], events: [] };
}
function conversation(active = true) {
  return { id: "chat-one", profile_id: "owen", configuration_id: "config", revision_id: "baseline", title: "A conversation", status: "open", run_id: "",
    active_worker_id: active ? "worker-one" : null, workers: active ? [{ id: "worker-one", label: "Solver · baseline", session_id: "native-session", status: "active" }] : [],
    messages: [], decisions: [], recovery: {}, conversational_memory: [], runtime: { status: active ? "ready" : "offline", owned: true } };
}
function setup(fixture, chat = conversation()) {
  const item = profile();
  fixture.ui.cacheProfile(item);
  const state = { profiles: [item], jobs: [], conversations: [{ ...chat, message_count: chat.messages.length }], settings: {}, benchmarks: [{ id: "jb", installed: true, domains: [{ id: "technical_writers", count: 1 }] }] };
  fixture.ui.setState(state); fixture.ui.select(chat);
  fixture.respond(`/api/conversations/${chat.id}`, chat);
  return state;
}

test("viewing a saved offline conversation does not start or load a model", async () => {
  const fixture = harness(); const chat = conversation(false); setup(fixture, chat);
  fixture.ui.renderConversations(); await fixture.ui.loadConversation();
  assert.match(fixture.node("#conversation-runtime").textContent, /Visiting this conversation does not load a model/);
  assert.match(fixture.node("#conversation-heading").textContent, /Start conversation · load model/);
  assert.equal(fixture.node("#conversation-draft").disabled, true);
  assert.ok(fixture.requests.every((request) => request.method === "GET"));
  fixture.ui.setView("profiles"); await fixture.ui.loadConversation();
  assert.equal(fixture.requests.length, 1, "Only poll conversation detail while its view is active");
});

test("visible messages preserve delivery states, reasons, worker attribution and literal content", () => {
  const fixture = harness(); const chat = conversation();
  chat.messages = ["queued", "delivered", "answered", "undelivered", "failed"].map((status, index) => ({ id: `message-${index}`, role: "user", text: `<script>literal ${index}</script>`, status, worker_id: "worker-one", reason: status === "failed" ? "Worker stopped before answering." : "" }));
  chat.messages.push({ id: "hello", role: "assistant", text: "Can I ask about the task?", status: "answered", worker_id: "worker-one", reasoning_content: "PRIVATE_NOT_VISIBLE" });
  setup(fixture, chat); fixture.ui.renderConversations();
  const messages = fixture.node("#conversation-messages").textContent;
  assert.match(messages, /Queued/); assert.match(messages, /Delivered to worker/); assert.match(messages, /Answered/); assert.match(messages, /Not delivered/); assert.match(messages, /Failed/);
  assert.ok(messages.includes(chat.messages[0].text)); assert.match(messages, /Worker stopped before answering/);
  assert.match(messages, /Solver · baseline/); assert.match(messages, /Can I ask about the task/);
  assert.match(messages, /Model message/);
  assert.doesNotMatch(messages, /PRIVATE_NOT_VISIBLE/);
  assert.match(fixture.node("#conversation-runtime").textContent, /Native OpenCode session: native-session/);
});

test("polling and context switches retain a draft and identify the new actual worker", () => {
  const fixture = harness(); const chat = conversation(); setup(fixture, chat);
  const draft = fixture.node("#conversation-draft"); draft.value = "Why did you choose this?";
  fixture.ui.renderConversationDetail(chat);
  const switched = { ...chat, active_worker_id: "worker-two", workers: [{ ...chat.workers[0], status: "ended" }, { id: "worker-two", label: "Proposer · domain", session_id: "second-native", status: "active" }] };
  fixture.ui.renderConversationDetail(switched);
  assert.equal(draft.value, "Why did you choose this?"); assert.equal(draft.disabled, false);
  assert.match(fixture.node("#conversation-runtime").textContent, /Working context: Proposer · domain/);
  assert.match(fixture.node("#conversation-runtime").textContent, /second-native/);
  fixture.ui.renderConversationDetail({ ...switched, workers: switched.workers.map((worker) => ({ ...worker, status: "ended" })) });
  assert.equal(draft.disabled, true, "A stale active_worker_id alone cannot enable delivery");
  assert.equal(draft.value, "Why did you choose this?");
});

test("creation is save-only; the deliberate profile Talk action separately starts the native session", async () => {
  const fixture = harness(); const chat = conversation(false); setup(fixture, chat);
  fixture.respond("/api/conversations", chat); fixture.respond("/api/conversations/chat-one/start", conversation());
  await fixture.ui.createConversation("owen"); await flush();
  assert.deepEqual(fixture.requests.filter((request) => request.method === "POST").map((request) => request.url), ["/api/conversations"]);
  fixture.requests.length = 0;
  await fixture.ui.talkToProfile(profile()); await flush();
  const mutations = fixture.requests.filter((request) => request.method === "POST");
  assert.deepEqual(mutations.map((request) => request.url), ["/api/conversations", "/api/conversations/chat-one/start"]);
  assert.equal(mutations[0].body.configuration_id, "config"); assert.equal(mutations[0].body.revision_id, "baseline");
});

test("a send clears only its submitted draft and preserves text typed while saving", async () => {
  const fixture = harness(); const chat = conversation(); setup(fixture, chat);
  let release; fixture.respond("/api/conversations/chat-one/messages", new Promise((resolve) => { release = resolve; }));
  const draft = fixture.node("#conversation-draft"); draft.value = "First thought";
  const pending = fixture.ui.sendConversationMessage();
  draft.value = "A second thought"; fixture.ui.draft(chat.id, draft.value);
  release({ ...chat, messages: [{ id: "queued", role: "user", text: "First thought", status: "queued", worker_id: "worker-one" }] });
  await pending;
  assert.equal(draft.value, "A second thought");
  assert.deepEqual(fixture.requests[0].body, { text: "First thought" });
  fixture.ui.select({ ...chat, status: "closed" });
  await fixture.ui.sendConversationMessage();
  assert.equal(fixture.requests.length, 1, "Closed conversations cannot queue a new message");
});

test("working decisions keep message ancestry and reported completion separate from plans and memory", async () => {
  const fixture = harness(); const chat = conversation();
  chat.messages = [{ id: "source-id", role: "user", text: "Write files earlier?", status: "answered", worker_id: "worker-one" }];
  chat.recovery = { id: "checkpoint", objective: "Produce four documents.", completed: [{ text: "Saved one document.", references: [{ path: "output/first.md" }] }], planned: ["Write the remaining three."], open_questions: ["Which source is authoritative?"], constraints: ["Keep outputs in the task folder."], decision_ids: ["decision-one"] };
  chat.decisions = [{ id: "decision-one", text: "Externalize work earlier.", why: "A question changed the sequence.", status: "proposed", source_message_ids: ["source-id"] }];
  chat.conversational_memory = [{ text: "Do not inject this as a harness instruction." }];
  setup(fixture, chat); fixture.ui.renderConversations();
  const recovery = fixture.node("#conversation-recovery").textContent;
  assert.match(recovery, /Reported completed · not independently verified/); assert.match(recovery, /Saved one document/);
  assert.match(recovery, /Planned workWrite the remaining three/); assert.match(recovery, /Open questionsWhich source/);
  assert.match(fixture.node("#conversation-decisions").textContent, /does not establish a benefit or adopt a technical harness/);
  const sourceButton = walk(fixture.node("#conversation-decisions")).find((node) => node.tagName === "button");
  await sourceButton.fire("click");
  assert.equal(walk(fixture.node("#conversation-messages")).find((node) => node.id === "conversation-message-source-id").scrolled, true);
  assert.doesNotMatch(fixture.node("#conversation-messages").textContent + recovery, /Do not inject this/);
  const html = fs.readFileSync(path.join(__dirname, "../harness2/ui/assets/index.html"), "utf8");
  assert.match(html, /Conversational memory · separate space/); assert.match(html, /This version does not populate it/);
});

test("conversation during work is opt-in and gated by profile, local model, JobBench, OpenCode and one worker", () => {
  const fixture = harness(); const state = setup(fixture);
  for (const [id, value] of [["experiment-profile", "owen"], ["experiment-configuration", "config"], ["experiment-revision", "baseline"], ["benchmark", "jb"], ["domain", "technical_writers"], ["substrate", "oc"], ["workers", "1"], ["max-tasks", "1"], ["branches", "1"], ["mode", "parallel"]]) fixture.node(`#${id}`).value = value;
  assert.equal(fixture.ui.conversationOptionAllowed(), true);
  state.features = { conversation_runs: false };
  fixture.ui.renderConversationOption();
  assert.equal(fixture.ui.conversationOptionAllowed(), false);
  assert.match(fixture.node("#experiment-conversation-help").textContent, /Windows process containment/);
  state.features.conversation_runs = true;
  assert.ok(!Object.hasOwn(fixture.ui.formParams(), "conversation_enabled"));
  fixture.node("#experiment-conversation").checked = true;
  assert.equal(fixture.ui.formParams().conversation_enabled, true);
  fixture.node("#workers").value = "2";
  assert.equal(fixture.ui.formParams(), null);
  fixture.ui.renderConversationOption(); assert.equal(fixture.node("#experiment-conversation").checked, false);
  for (const [id, value] of [["workers", "1"], ["substrate", "cx"], ["benchmark", "lab"], ["experiment-profile", ""]]) { fixture.node(`#${id}`).value = value; if (id !== "workers") assert.equal(fixture.ui.conversationOptionAllowed(), false); }
});

test("a run-linked conversation points to whole-run stop instead of an independent model stop", () => {
  const fixture = harness(); const chat = { ...conversation(), run_id: "run-one" }; const state = setup(fixture, chat);
  state.jobs = [{ id: "run-one", kind: "benchmark", status: "running" }];
  fixture.ui.renderConversations();
  assert.match(fixture.node("#conversation-heading").textContent, /Follow run \/ stop work/);
  assert.doesNotMatch(fixture.node("#conversation-heading").textContent, /Stop chat/);
  fixture.ui.renderRunProfileCapture({ ...state.jobs[0], profile: { profile_id: "owen", configuration_id: "config", revision_id: "baseline" }, conversation_id: chat.id });
  assert.match(fixture.node("#run-profile-capture").textContent, /Open this run’s conversation/);
  assert.match(fixture.node("#run-profile-capture").textContent, /Human contact is recorded as context, not proof of benefit/);
});

test("an older poll cannot overwrite a later send result", async () => {
  const fixture = harness(); const chat = conversation(); setup(fixture, chat);
  let release; fixture.respond("/api/conversations/chat-one", new Promise((resolve) => { release = resolve; }));
  const polling = fixture.ui.loadConversation();
  fixture.respond("/api/conversations/chat-one/messages", { ...chat, messages: [{ id: "new", role: "user", text: "Hello", status: "queued", worker_id: "worker-one" }] });
  await fixture.ui.conversationAction(chat.id, "messages", { text: "Hello" });
  release(chat); await polling;
  assert.equal(fixture.ui.cached(chat.id).messages.length, 1);
});

test("service availability gates delivery even when a stale journal worker still appears active", async () => {
  const fixture = harness(); const chat = conversation();
  chat.runtime = { status: "offline", can_message: false, can_start: false, can_stop: false };
  setup(fixture, chat); fixture.ui.renderConversations();
  fixture.node("#conversation-draft").value = "Retain this draft.";
  await fixture.ui.sendConversationMessage();
  assert.equal(fixture.node("#conversation-send").disabled, true);
  assert.equal(fixture.requests.length, 0);
  assert.doesNotMatch(fixture.node("#conversation-heading").textContent, /Stop chat/);
});

test("a resumed run's runtime identity controls stop routing, not its original conversation run ID", () => {
  const fixture = harness(); const chat = { ...conversation(), run_id: "original-run" };
  chat.runtime = { status: "loading", mode: "run", job_id: "resumed-run", can_stop: false, can_start: false };
  chat.active_worker_id = null;
  const state = setup(fixture, chat);
  state.jobs = [{ id: "original-run", status: "succeeded" }, { id: "resumed-run", status: "running" }];
  fixture.ui.renderConversations();
  assert.match(fixture.node("#conversation-heading").textContent, /Follow run \/ stop work/);
  assert.doesNotMatch(fixture.node("#conversation-heading").textContent, /Stop chat|Continue chat/);
  assert.match(fixture.node("#conversation-runtime").textContent, /Waiting for the run’s next working context/);
  assert.doesNotMatch(fixture.node("#conversation-runtime").textContent, /Loading the conversation’s model/);
});

test("ambiguous delivery is visible as possible human contact rather than a solitary control", () => {
  const fixture = harness(); setup(fixture);
  fixture.ui.renderRunProfileCapture({ id: "uncertain-run", kind: "benchmark", status: "running",
    profile: { profile_id: "owen", configuration_id: "config", revision_id: "baseline" },
    conversation_id: "chat-one", conversation: { human_messages_delivered: 0, human_delivery_unconfirmed: 1, human_contact_possible: true } });
  const detail = fixture.node("#run-profile-capture").textContent;
  assert.match(detail, /0 human messages confirmed delivered/);
  assert.match(detail, /1 message delivery unconfirmed/);
  assert.match(detail, /Human contact may have occurred/);
  assert.match(detail, /cannot be treated as having no human contact/);
});
