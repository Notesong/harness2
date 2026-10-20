"use strict";

// Run with: node --test tests/ui_story.test.cjs
// Exercise the production rendering functions without a browser or model request.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function harness() {
  const nodes = new Map();
  class Node {
    constructor(tag) {
      this.tagName = tag;
      this.children = [];
      this.attributes = {};
      this.dataset = {};
      this.hidden = false;
      this.value = "";
      this.className = "";
      this.classList = {
        add: (...items) => { this.className += " " + items.join(" "); },
        toggle: () => {},
      };
      this._text = "";
    }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map((node) => node.textContent).join(""); }
    set innerHTML(_) { throw new Error("Report content must not enter an HTML sink"); }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this._text = ""; this.children = nodes; }
    replaceWith(node) { if (node.id) nodes.set("#" + node.id, node); }
    setAttribute(key, value) { this.attributes[key] = String(value); }
    removeAttribute(key) { delete this.attributes[key]; }
    addEventListener() {}
    focus() { this.focused = true; }
  }
  const document = {
    createElement: (tag) => new Node(tag),
    createTextNode: (text) => { const node = new Node("text"); node.textContent = text; return node; },
    querySelector: (selector) => {
      if (!nodes.has(selector)) nodes.set(selector, new Node("div"));
      return nodes.get(selector);
    },
    querySelectorAll: () => [],
  };
  const context = vm.createContext({
    document, URLSearchParams, location: { hash: "", pathname: "/" },
    sessionStorage: { getItem: () => null, setItem() {} }, history: {},
  });
  const source = fs.readFileSync(path.join(__dirname, "../harness2/ui/assets/app.js"), "utf8");
  const boot = source.indexOf("  $$('[data-view]').forEach");
  assert.ok(boot > 0, "Locate the app boot boundary without changing production code");
  vm.runInContext(source.slice(0, boot) + "globalThis.uiTest = { renderStory, renderDetail, selectTab };\n})();", context);
  return { ui: context.uiTest, node: document.querySelector };
}

test("recorded results, narrative, model interpretation, and cautions stay readable plain text", () => {
  const { ui, node } = harness();
  const modelText = '<script>alert("not HTML")</script>\nI changed the writing approach.';
  ui.renderStory({ status: "partial", tasks: [{ selections: [{ graded: true, score: 38 / 66, files: [{ id: "one", name: "guide.md" }] }] }], story: {
    overview: ["The final run produced four documents."],
    warnings: ["The general proposal timed out and used the baseline harness."],
    model_summary: modelText, summary_status: "model", events: [],
  } });
  const report = node("#job-overview");
  assert.match(report.textContent, /57[.,]58%/);
  assert.match(report.textContent, /Recorded score/);
  assert.match(report.textContent, /four documents/);
  assert.match(report.textContent, /general proposal timed out/);
  assert.match(report.textContent, /IN THE MODEL’S OWN WORDS/);
  assert.ok(report.textContent.includes(modelText));
});

test("a deterministic report does not claim to be the model's own wrap-up", () => {
  const { ui, node } = harness();
  ui.renderStory({ status: "succeeded", tasks: [], story: { overview: ["The recorded overview."], events: [], warnings: [], model_summary: "A duplicate fallback containing the same benchmark facts.", summary_status: "deterministic" } });
  assert.match(node("#job-overview").textContent, /The recorded overview/);
  assert.match(node("#job-overview").textContent, /No model-written wrap-up is saved/);
  assert.doesNotMatch(node("#job-overview").textContent, /duplicate fallback/);
  assert.doesNotMatch(node("#job-overview").textContent, /MODEL’S OWN WORDS/);
});

test("timeline keeps recorded order, timestamps, and fallback detail", () => {
  const { ui, node } = harness();
  ui.renderStory({ status: "partial", tasks: [], story: { events: [
    { time: "2026-10-19T03:35:11Z", title: "Baseline finished", detail: "Four output files.", kind: "complete" },
    { time: "2026-10-19T05:35:24Z", title: "Probe runs began", detail: "General proposal fell back to the baseline.", kind: "fallback" },
  ] } });
  const events = node("#job-timeline").children[0].children;
  assert.equal(events.length, 2);
  assert.match(events[0].textContent, /Baseline finished/);
  assert.match(events[1].textContent, /fell back/);
  assert.match(events[1].className, /timeline-caution/);
  assert.equal(events[0].children[0].children[1].dateTime, "2026-10-19T03:35:11Z");
});

test("imported run detail defaults to its overview and hides execution controls", () => {
  const { ui, node } = harness();
  ui.renderDetail({ id: "imported-history", imported: true, kind: "benchmark", status: "failed", title: "Earlier local experiment", phases: [], tasks: [], progress: {}, story: {} });
  assert.equal(node("#job-resume").hidden, true);
  assert.equal(node("#job-cancel").hidden, true);
  assert.equal(node("#imported-run-note").hidden, false);
  assert.equal(node("#panel-overview").hidden, false);
  assert.equal(node("#panel-results").hidden, true);
  assert.match(node("#detail-kind").textContent, /IMPORTED HISTORY/);
  ui.selectTab("timeline");
  assert.equal(node("#panel-timeline").hidden, false);
  assert.equal(node("#panel-overview").hidden, true);
});
