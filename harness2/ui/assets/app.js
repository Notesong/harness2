"use strict";

(() => {
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const views = { overview: "Overview", new: "New experiment", runs: "Runs", setup: "Setup" };
  const statusLabels = { starting: "Starting", running: "Running", succeeded: "Complete", failed: "Failed", partial: "Needs attention", exception: "Failed", cancelled: "Stopped", interrupted: "Interrupted", pending: "Waiting", completed: "Complete", complete: "Complete", skipped: "Skipped" };
  const kindLabels = { demo: "Free demo", benchmark: "Experiment", check: "Environment check", setup: "Benchmark setup", preview: "Experiment plan", judge: "Evaluation", report: "Report", authenticate: "Authentication" };
  const settingsFields = { local_url: "local-url", local_model: "local-model", local_context: "local-context", local_output: "local-output", project: "project", region: "region", solver: "solver", improver: "improver", judge: "judge", codex_url: "codex-url", codex_model: "codex-model" };
  let token = "";
  let state = null;
  let view = "overview";
  let selectedId = null;
  let selectedJob = null;
  let stateLoading = false;
  let detailLoading = false;
  let launching = false;
  let closing = false;
  let appClosed = false;
  let pollTimer;
  let settingsLoaded = false;
  let previousProvider = null;
  let benchmarksSignature = "";
  let setupSignature = "";
  let jobsSignature = "";
  let recentSignature = "";
  let tasksSignature = "";
  let toastTimer;
  let lastLogId = null;
  let detailTabId = null;
  let followingLog = true;
  const openTasks = new Set();
  const taskSelections = new Map();

  function remember(key, value) { try { sessionStorage.setItem(key, value); } catch (_) { /* Session storage can be disabled. */ } }
  function recalled(key) { try { return sessionStorage.getItem(key); } catch (_) { return null; } }
  const fragment = new URLSearchParams(location.hash.slice(1));
  if (fragment.has("token")) {
    token = fragment.get("token") || "";
    remember("harness2_token", token);
    history.replaceState(null, "", location.pathname + location.search);
  } else {
    token = recalled("harness2_token") || "";
  }
  selectedId = recalled("harness2_selected_job");

  function element(tag, className, text) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== undefined && text !== null) result.textContent = String(text);
    return result;
  }
  function text(selector, value) { $(selector).textContent = value === undefined || value === null ? "" : String(value); }
  function human(value) { return String(value || "").replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()); }
  function count(value) { const n = Number(value); return Number.isFinite(n) ? n.toLocaleString() : "—"; }
  function when(value) {
    if (!value) return "";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(date);
  }
  function bytes(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "";
    if (n < 1024) return `${n} B`;
    return n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : `${(n / (1024 * 1024)).toFixed(1)} MB`;
  }
  function statusBadge(status) {
    if (status === "ok") status = "succeeded";
    const known = Object.hasOwn(statusLabels, status) ? status : "pending";
    return element("span", `status ${known}`, statusLabels[known]);
  }
  function empty(title, message, roomy = false) {
    const wrap = element("div", `empty-state${roomy ? " roomy" : ""}`);
    wrap.append(element("span", "empty-icon", "◫"), element("h3", "", title), element("p", "", message));
    return wrap;
  }
  function toast(message, error = false) {
    clearTimeout(toastTimer);
    const region = $("#toast");
    region.textContent = message;
    region.classList.toggle("error-toast", error);
    region.hidden = false;
    toastTimer = setTimeout(() => { region.hidden = true; }, 5500);
  }
  function message(selector, value, error = false) {
    text(selector, value);
    $(selector).classList.toggle("error-text", error);
  }

  async function api(path, options = {}) {
    if (!token) throw new Error("This session needs a connection. Reopen the dashboard from Harness² to continue.");
    const headers = { Authorization: `Bearer ${token}`, ...options.headers };
    let body = options.body;
    if (body !== undefined && typeof body !== "string") {
      body = JSON.stringify(body);
      headers["Content-Type"] = "application/json";
    }
    let response;
    try { response = await fetch(path, { ...options, headers, body, cache: "no-store" }); }
    catch (_) { throw new Error("The workspace connection is unavailable. Keep the dashboard launcher open and try again."); }
    if (response.status === 401 || response.status === 403) throw new Error("This session has expired. Reopen the dashboard from Harness² to reconnect.");
    const result = await response.json().catch(() => null);
    if (!response.ok) throw new Error(result?.error || `The request could not be completed (${response.status}).`);
    if (result === null) throw new Error("The workspace returned an unreadable response. Please try again.");
    return result;
  }

  function showView(next, focus = true) {
    if (appClosed) return;
    if (!Object.hasOwn(views, next)) return;
    const changed = view !== next;
    view = next;
    $$(".view").forEach((section) => { section.hidden = section.id !== `view-${next}`; });
    $$(".nav-item").forEach((button) => {
      const current = button.dataset.view === next;
      button.classList.toggle("selected", current);
      if (current) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
    });
    text("#breadcrumb", `Workspace / ${views[next]}`);
    if (changed && focus) {
      window.scrollTo({ top: 0, behavior: "instant" });
      $("#main").focus({ preventScroll: true });
    }
    if (next === "runs" && selectedId) loadDetail(true);
    if (next === "new") renderPlan();
  }

  function providerLabel(settings = state?.settings) {
    if (!settings) return "Not configured";
    if (settings.provider === "local") return settings.local_model ? `Local · ${settings.local_model}` : "Local · choose a model";
    if (settings.provider === "vertex") return settings.project ? `Vertex AI · ${settings.project}` : "Vertex AI · choose a project";
    return settings.codex_model ? `Codex · ${settings.codex_model}` : "Codex Responses endpoint";
  }
  function isActive(status) { return status === "starting" || status === "running"; }
  function activeJob() { return (state?.jobs || []).find((job) => job.id === state?.active_id || isActive(job.status)); }
  function updateBusy() {
    const busy = appClosed || closing || launching || Boolean(activeJob());
    $$("[data-job-start]").forEach((button) => {
      button.disabled = busy;
      button.title = busy ? "Finish or stop the active run before starting another." : "";
    });
    if (!state || !(state.benchmarks || []).some((benchmark) => benchmark.installed)) {
      $("#submit-experiment").disabled = true;
      $("#preview-experiment").disabled = true;
    }
    if (busy) message("#experiment-form-message", "One run is active. You can follow it in Runs while you plan the next experiment.");
    else if ($("#experiment-form-message").textContent.startsWith("One run")) message("#experiment-form-message", "");
  }
  function benchmarkParams(id) {
    return id.startsWith("wb-") ? { benchmark: "wb", subset: id.slice(3) } : { benchmark: id, subset: null };
  }
  function localProfileProblem(params) {
    if (state?.settings?.provider !== "local") return "";
    if (params.substrate !== "oc" || params.benchmark !== "jb") return "The complete local pipeline currently supports JobBench with OpenCode. LAB needs a separately configured judge; Codex and WorkBuddy need their own runner connection.";
    return "";
  }

  function renderBenchmarks() {
    const benchmarks = state.benchmarks || [];
    const signature = JSON.stringify(benchmarks);
    if (signature === benchmarksSignature) return;
    benchmarksSignature = signature;
    const select = $("#benchmark");
    const previous = select.value;
    const options = benchmarks.map((benchmark) => {
      const option = element("option", "", `${benchmark.name}${benchmark.installed ? "" : " · needs setup"}`);
      option.value = benchmark.id;
      option.disabled = !benchmark.installed;
      return option;
    });
    if (!benchmarks.some((benchmark) => benchmark.installed)) {
      const option = element("option", "", "Prepare a benchmark in Setup");
      option.value = "";
      options.unshift(option);
    }
    select.replaceChildren(...options);
    const next = benchmarks.find((benchmark) => benchmark.id === previous && benchmark.installed) || benchmarks.find((benchmark) => benchmark.installed);
    select.value = next?.id || "";
    renderDomains();
    const list = $("#benchmark-setup-list");
    list.replaceChildren(...benchmarks.map((benchmark) => {
      const item = element("div", "benchmark-setup-item");
      const body = element("div");
      body.append(element("h3", "", benchmark.name));
      const detail = benchmark.installed ? `${count(benchmark.task_count)} tasks · ${(benchmark.domains || []).length} domains` : benchmark.problem || benchmark.description || "Prepare this benchmark to begin.";
      body.append(element("p", "", detail));
      const button = element("button", "button secondary small", benchmark.installed ? "Prepare again" : "Prepare");
      button.type = "button";
      button.dataset.jobStart = "";
      button.setAttribute("aria-label", `Prepare ${benchmark.name}`);
      button.addEventListener("click", () => launch("setup", { ...benchmarkParams(benchmark.id), substrate: $("#prepare-runner").value }));
      item.append(body, button);
      return item;
    }));
    if (!benchmarks.length) list.append(empty("No benchmarks found", "Run an environment check to see what needs attention."));
  }
  function renderDomains() {
    const benchmark = (state?.benchmarks || []).find((item) => item.id === $("#benchmark").value);
    const domain = $("#domain");
    const previous = domain.value;
    const choices = benchmark?.domains || [];
    domain.replaceChildren(...choices.map((item) => {
      const option = element("option", "", `${human(item.id)} (${count(item.count)} tasks)`);
      option.value = item.id;
      return option;
    }));
    if (!choices.length) {
      const option = element("option", "", "No domains available");
      option.value = "";
      domain.append(option);
    }
    domain.value = choices.some((item) => item.id === previous) ? previous : choices[0]?.id || "";
    text("#benchmark-description", benchmark?.description || "Prepare a benchmark in Setup, then return here to choose your first task.");
    const problem = $("#benchmark-problem");
    problem.hidden = Boolean(benchmark?.installed && choices.length);
    if (!problem.hidden) {
      problem.replaceChildren(element("p", "", benchmark?.problem || "This benchmark needs preparation before you can run an experiment."));
      const button = element("button", "text-button", "Open Setup →");
      button.type = "button";
      button.addEventListener("click", () => showView("setup"));
      problem.append(button);
    }
    renderPlan();
  }
  function renderPlan() {
    const benchmark = (state?.benchmarks || []).find((item) => item.id === $("#benchmark").value);
    const domain = benchmark?.domains?.find((item) => item.id === $("#domain").value);
    const requested = Number($("#max-tasks").value) || 1;
    const available = Number(domain?.count);
    const taskCount = Number.isFinite(available) ? Math.min(requested, available) : requested;
    const branches = Number($("#branches").value) || 1;
    const workers = Number($("#workers").value) || 1;
    text("#plan-provider", providerLabel());
    text("#plan-description", benchmark && domain ? `${benchmark.name} · ${human(domain.id)}. Work through up to ${count(taskCount)} task${taskCount === 1 ? "" : "s"}, exploring ${branches} branch${branches === 1 ? "" : "es"} per step. Tasks stay in order within each domain; the limit is ${workers} parallel domain${workers === 1 ? "" : "s"}.` : "Prepare a benchmark, then choose one domain for a focused first experiment.");
    text("#domain-help", domain ? `${count(domain.count)} tasks available in this domain. Your experiment will use up to ${count(taskCount)}.` : "Select one domain to keep the first experiment focused.");
    const localNote = $("#local-profile-note");
    localNote.hidden = state?.settings?.provider !== "local";
    localNote.textContent = localProfileProblem({ benchmark: benchmark?.id?.startsWith("wb-") ? "wb" : benchmark?.id, substrate: $("#substrate").value }) || "The complete local pipeline supports JobBench with OpenCode. Other benchmarks and runners need additional configuration.";
  }
  function formParams() {
    if (!$("#experiment-form").reportValidity()) return null;
    const id = $("#benchmark").value;
    const benchmark = state?.benchmarks?.find((item) => item.id === id);
    if (!benchmark?.installed || !$("#domain").value) { toast("Prepare a benchmark in Setup before starting.", true); return null; }
    return { ...benchmarkParams(id), substrate: $("#substrate").value, k: Number($("#branches").value), mode: $("#mode").value, domains: [$("#domain").value], max_tasks: Number($("#max-tasks").value), workers: Number($("#workers").value), pipeline: true };
  }

  function renderRecent() {
    const jobs = (state.jobs || []).slice(0, 5);
    const signature = JSON.stringify(jobs);
    if (signature === recentSignature) return;
    recentSignature = signature;
    const wrap = $("#recent-jobs");
    if (!jobs.length) { wrap.replaceChildren(empty("Your workspace is ready for its first run.", "Try the free demo, then follow the work from start to finish.")); return; }
    wrap.replaceChildren(...jobs.map((job) => {
      const row = element("button", "recent-row");
      row.type = "button";
      const info = element("span", "recent-info");
      info.append(element("strong", "", job.title || kindLabels[job.kind] || "Run"), element("small", "", `${kindLabels[job.kind] || "Run"} · ${when(job.created_at)}`));
      row.append(info, statusBadge(job.status), element("span", "row-arrow", "↗"));
      row.addEventListener("click", () => selectJob(job.id));
      return row;
    }));
  }
  function renderJobs() {
    if (!state) return;
    const filter = $("#job-filter").value;
    const search = $("#job-search").value.trim().toLocaleLowerCase();
    const jobs = (state.jobs || []).filter((job) => {
      const match = filter === "all" || (filter === "attention" ? ["failed", "interrupted", "cancelled"].includes(job.status) : filter === "running" ? isActive(job.status) : job.status === filter);
      return match && `${job.title || ""} ${kindLabels[job.kind] || ""}`.toLocaleLowerCase().includes(search);
    });
    const signature = JSON.stringify([jobs, selectedId]);
    if (signature === jobsSignature) return;
    jobsSignature = signature;
    const list = $("#job-list");
    const focusedId = document.activeElement?.dataset.jobId;
    const scroll = list.scrollTop;
    const horizontal = list.scrollLeft;
    list.replaceChildren(...jobs.map((job) => {
      const button = element("button", `job-list-item${job.id === selectedId ? " selected" : ""}`);
      button.type = "button";
      button.dataset.jobId = job.id;
      if (job.id === selectedId) button.setAttribute("aria-current", "true");
      button.append(element("h3", "", job.title || kindLabels[job.kind] || "Run"));
      const meta = element("div", "job-list-meta");
      meta.append(statusBadge(job.status), element("small", "", when(job.created_at)));
      button.append(meta);
      button.addEventListener("click", () => selectJob(job.id));
      return button;
    }));
    if (!jobs.length) list.append(empty(search || filter !== "all" ? "No matching runs" : "A fresh start", search || filter !== "all" ? "Try another search or filter." : "Your first demo or experiment will appear here."));
    list.scrollTop = scroll;
    list.scrollLeft = horizontal;
    if (focusedId) $$("[data-job-id]").find((button) => button.dataset.jobId === focusedId)?.focus({ preventScroll: true });
  }
  function fillSettings(settings) {
    if (settingsLoaded) return;
    settingsLoaded = true;
    $("#provider").value = ["local", "vertex", "custom"].includes(settings.provider) ? settings.provider : "local";
    Object.entries(settingsFields).forEach(([key, id]) => { $(`#${id}`).value = settings[key] || ""; });
    providerChanged();
  }
  function providerChanged() {
    const provider = $("#provider").value;
    message("#settings-message", "");
    const localModel = $("#local-model").value.trim();
    if (previousProvider === "local" && provider === "vertex") {
      let cleared = false;
      ["solver", "improver", "judge"].forEach((id) => {
        const input = $(`#${id}`);
        if (input.value.startsWith("local/") || (localModel && input.value === localModel)) {
          input.value = "";
          cleared = true;
        }
      });
      if (cleared) message("#settings-message", "Choose your Vertex solver, improver, and judge models in Advanced model settings before saving.");
    }
    if (provider === "local" && previousProvider && previousProvider !== "local") fillLocalModels();
    previousProvider = provider;
    ["local", "vertex", "custom"].forEach((id) => { $(`#provider-${id}`).hidden = id !== provider; });
    $("#local-token-limits").hidden = provider !== "local";
    ["local-context", "local-output"].forEach((id) => { $(`#${id}`).disabled = provider !== "local"; });
    ["solver", "improver", "judge"].forEach((id) => { $(`#${id}`).readOnly = provider === "local"; });
    text("#test-connection", provider === "vertex" ? "Check environment" : "Test connection");
    if (provider !== "local") $("#advanced-models").open = true;
    message("#connection-test-message", "");
  }
  function fillLocalModels() {
    const model = $("#local-model").value.trim();
    if ($("#provider").value !== "local" || !model) return;
    $("#solver").value = `local/${model}`;
    $("#improver").value = `local/${model}`;
    $("#judge").value = model;
  }
  function renderTools() {
    const tools = state.tools || [];
    const signature = JSON.stringify(tools);
    if (signature === setupSignature) return;
    setupSignature = signature;
    text("#tools-summary", `${tools.filter((tool) => tool.available).length} / ${tools.length} ready`);
    $("#tools-list").replaceChildren(...tools.map((tool) => {
      const row = element("li");
      const marker = element("span", `tool-check${tool.available ? "" : " missing"}`, tool.available ? "✓" : "!");
      marker.setAttribute("aria-label", tool.available ? "Available" : "Needs attention");
      const body = element("div");
      body.append(element("strong", "", tool.name), element("p", "", tool.detail || (tool.available ? "Ready" : "Not available")));
      row.append(marker, body);
      return row;
    }));
  }
  function renderState() {
    const benchmarks = state.benchmarks || [];
    const jobs = state.jobs || [];
    const running = jobs.filter((job) => isActive(job.status));
    const installed = benchmarks.filter((benchmark) => benchmark.installed);
    text("#stat-benchmarks", count(installed.length));
    text("#stat-benchmarks-note", `${benchmarks.length - installed.length} available to prepare`);
    text("#stat-experiments", count(jobs.filter((job) => ["demo", "benchmark"].includes(job.kind)).length));
    text("#stat-running", count(running.length));
    text("#stat-running-note", running.length ? running[0].title || "Work is in progress" : "A quiet moment in your workspace");
    text("#nav-running", running.length);
    $("#nav-running").hidden = !running.length;
    text("#workspace-name", state.workspace || "Local workspace");
    $("#workspace-name").title = state.workspace || "";
    text("#onboarding-provider", providerLabel(state.settings));
    text("#key-status", state.settings?.key_configured ? "A key is saved locally. Leave the field empty to keep it." : "No key is saved. Local models usually do not need one.");
    fillSettings(state.settings || {});
    renderBenchmarks();
    renderTools();
    renderRecent();
    renderJobs();
    renderPlan();
    updateBusy();
  }

  async function pollState() {
    if (appClosed || closing || stateLoading) return;
    stateLoading = true;
    try {
      state = await api("/api/state");
      if (appClosed || closing) return;
      $("#connection-error").hidden = true;
      $("#connection-status").classList.remove("offline");
      $("#connection-status").lastElementChild.textContent = "Workspace connected";
      if (selectedId && !(state.jobs || []).some((job) => job.id === selectedId)) {
        selectedId = null;
        selectedJob = null;
      }
      if (!selectedId && (state.jobs || []).length) {
        selectedId = state.active_id || state.jobs[0].id;
        remember("harness2_selected_job", selectedId);
      }
      renderState();
      const summary = state.jobs?.find((job) => job.id === selectedId);
      if (selectedId && (!selectedJob || isActive(summary?.status) || summary?.status !== selectedJob.status)) loadDetail();
    } catch (error) {
      if (appClosed || closing) return;
      text("#connection-error", error.message);
      $("#connection-error").hidden = false;
      $("#connection-status").classList.add("offline");
      $("#connection-status").lastElementChild.textContent = "Connection needed";
    } finally { stateLoading = false; }
  }
  function selectJob(id) {
    if (id !== selectedId) {
      selectedJob = null;
      tasksSignature = "";
      openTasks.clear();
      taskSelections.clear();
      followingLog = true;
      text("#job-log", "Loading run output…");
    }
    selectedId = id;
    remember("harness2_selected_job", id);
    renderJobs();
    showView("runs");
  }
  async function loadDetail(force = false) {
    if (appClosed || closing || !selectedId || (detailLoading && !force)) return;
    const id = selectedId;
    detailLoading = true;
    try {
      const result = await api(`/api/jobs/${encodeURIComponent(id)}`);
      if (appClosed || closing || id !== selectedId) return;
      selectedJob = result;
      renderDetail(result);
    } catch (error) { if (!appClosed && !closing && id === selectedId && view === "runs") toast(error.message, true); }
    finally { detailLoading = false; }
  }
  function renderDetail(job) {
    if (detailTabId !== job.id) {
      detailTabId = job.id;
      selectTab(["demo", "benchmark", "judge"].includes(job.kind) ? "results" : "log");
    }
    $("#job-empty").hidden = true;
    $("#job-detail-content").hidden = false;
    text("#detail-title", job.title || kindLabels[job.kind] || "Run");
    text("#detail-kind", (kindLabels[job.kind] || "Run").toUpperCase());
    const badge = statusBadge(job.status);
    badge.id = "detail-status";
    $("#detail-status").replaceWith(badge);
    text("#detail-time", `${isActive(job.status) ? "Started" : "Created"} ${when(job.started_at || job.created_at)}`);
    $("#job-cancel").hidden = !isActive(job.status);
    $("#job-resume").hidden = !["failed", "interrupted", "cancelled"].includes(job.status) || !["demo", "benchmark"].includes(job.kind);
    $("#job-resume").disabled = launching || Boolean(activeJob());
    $("#job-open-folder").disabled = !job.output_dir;
    $("#job-error").hidden = !job.error;
    text("#job-error", job.error || "");
    const phases = Array.isArray(job.phases) ? job.phases : [];
    $("#job-phases").replaceChildren(...phases.map((phase, index) => {
      const status = String(phase.status || "pending");
      const known = Object.hasOwn(statusLabels, status) || status === "active" ? status : "pending";
      const item = element("li", known);
      item.append(element("span", "", ["complete", "completed", "succeeded"].includes(status) ? "✓" : index + 1), document.createTextNode(phase.label || human(phase.id)));
      item.setAttribute("aria-label", `${phase.label || phase.id}: ${statusLabels[known] || "Active"}`);
      return item;
    }));
    text("#job-phase-label", job.phase ? human(job.phase) : isActive(job.status) ? "The run is starting. Progress appears as the work completes." : "");
    const progress = job.progress || {};
    const total = Number(progress.total || job.total_tasks || 0);
    const done = Number(progress.done || 0);
    const failed = Number(progress.failed || 0);
    $("#task-progress").hidden = !(total > 0 && ["demo", "benchmark", "judge"].includes(job.kind));
    if (total > 0) {
      text("#task-progress-label", `${count(done)} of ${count(total)} tasks complete`);
      text("#task-failed-label", failed ? `${count(failed)} need attention` : "");
      $("#task-progress-bar").max = total;
      $("#task-progress-bar").value = Math.min(done, total);
    }
    renderTasks(job);
    const log = $("#job-log");
    const value = job.log || (isActive(job.status) ? "Waiting for output…" : "No output was recorded for this job.");
    if (log.textContent !== value) {
      const follow = lastLogId !== job.id || (log.clientHeight ? log.scrollHeight - log.scrollTop - log.clientHeight < 65 : followingLog);
      const scroll = log.scrollTop;
      log.textContent = value;
      if (follow) log.scrollTop = log.scrollHeight;
      else log.scrollTop = scroll;
      followingLog = follow;
      lastLogId = job.id;
    }
  }
  function renderTasks(job) {
    const tasks = Array.isArray(job.tasks) ? job.tasks : [];
    const signature = JSON.stringify([job.id, tasks, job.status]);
    if (signature === tasksSignature) return;
    tasksSignature = signature;
    const wrap = $("#job-tasks");
    const focusedTask = document.activeElement?.dataset.taskSelection;
    wrap.replaceChildren(...tasks.map((task, index) => {
      const id = String(task.id || index);
      const card = element("details", "task-card");
      card.open = openTasks.has(id) || (tasks.length === 1 && !openTasks.has(`closed:${id}`));
      card.addEventListener("toggle", () => {
        if (card.open) { openTasks.add(id); openTasks.delete(`closed:${id}`); }
        else { openTasks.delete(id); openTasks.add(`closed:${id}`); }
      });
      const summary = element("summary");
      const title = element("span", "task-name", task.id || `Task ${index + 1}`);
      title.append(element("span", "task-domain", human(task.domain)));
      summary.append(title, statusBadge(task.status || "pending"));
      const body = element("div", "task-body");
      const selections = Array.isArray(task.selections) ? task.selections : [];
      if (!selections.length) body.append(element("p", "field-help", task.status === "running" ? "This task is in progress. Its selections and files will appear here." : "No selection or output files are available for this task yet."));
      else {
        const label = element("label", "selection-label", "Chosen harness");
        const select = element("select", "selection-select");
        select.id = `selection-${index}`;
        select.dataset.taskSelection = id;
        label.htmlFor = select.id;
        selections.forEach((selection, i) => {
          const option = element("option", "", [human(selection.mode), selection.label].filter(Boolean).join(" · ") || `Selection ${i + 1}`);
          option.value = String(i);
          select.append(option);
        });
        const selected = Math.min(taskSelections.get(id) || 0, selections.length - 1);
        select.value = String(selected);
        const result = element("div");
        renderSelection(result, selections[selected], job.id);
        select.addEventListener("change", () => {
          taskSelections.set(id, Number(select.value));
          renderSelection(result, selections[Number(select.value)], job.id);
        });
        body.append(label, select, result);
      }
      card.append(summary, body);
      return card;
    }));
    if (!tasks.length) wrap.append(empty(isActive(job.status) ? "The work is getting started." : "No task results for this job.", isActive(job.status) ? "Task results and selected output files will appear as they become available. You can follow the live log in the meantime." : "Check the live log for this job’s details, or open its output folder."));
    if (focusedTask) $$('[data-task-selection]').find((select) => select.dataset.taskSelection === focusedTask)?.focus({ preventScroll: true });
  }
  function renderSelection(container, selection, jobId) {
    container.replaceChildren();
    const score = element("div", "selection-score");
    if (selection.graded && typeof selection.score === "number" && Number.isFinite(selection.score)) {
      const formatted = selection.score >= 0 && selection.score <= 1
        ? selection.score.toLocaleString(undefined, { style: "percent", maximumFractionDigits: 1 })
        : selection.score.toLocaleString(undefined, { maximumFractionDigits: 4 });
      score.append(element("strong", "", formatted), element("span", "", "Graded score"));
    }
    else score.append(element("span", "", selection.graded ? "Grading complete" : "Score not available yet"));
    container.append(score);
    if (selection.why) container.append(element("p", "selection-why", selection.why));
    const files = Array.isArray(selection.files) ? selection.files : [];
    container.append(element("h3", "files-heading", `Output files (${files.length})`));
    if (!files.length) container.append(element("p", "field-help", "No output files are available for this selection."));
    files.forEach((file) => {
      const row = element("div", "file-row");
      row.append(element("span", "file-name", file.name), element("span", "file-size", bytes(file.size)));
      const download = element("button", "text-button", "Download ↓");
      download.type = "button";
      download.setAttribute("aria-label", `Download ${file.name}`);
      download.addEventListener("click", () => downloadArtifact(jobId, file, download));
      row.append(download);
      container.append(row);
      if (typeof file.text === "string") {
        const preview = element("details", "output-preview");
        preview.append(element("summary", "", `Read ${file.name}`), element("pre", "", file.text));
        container.append(preview);
      }
    });
    if (selection.diff) {
      const diff = element("details", "diff-preview");
      diff.append(element("summary", "", "Inspect harness changes"), element("pre", "", selection.diff));
      container.append(diff);
    }
  }
  async function downloadArtifact(jobId, file, button) {
    button.disabled = true;
    try {
      const response = await fetch(`/api/artifacts/${encodeURIComponent(jobId)}/${encodeURIComponent(file.id)}`, { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" });
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.error || "The file could not be downloaded.");
      }
      const url = URL.createObjectURL(await response.blob());
      const link = element("a");
      link.href = url;
      link.download = file.name || "output";
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { toast(error.message, true); }
    finally { button.disabled = false; }
  }

  function confirmCompute(params, resume = false) {
    const dialog = $("#confirm-dialog");
    const previousFocus = document.activeElement;
    text("#confirm-title", resume ? "Pick up this experiment?" : "Give this experiment a go?");
    text("#confirm-description", resume ? "The run will continue from the results already available. Remaining work may use your configured models." : "This experiment will run real tasks, propose harness changes, and evaluate the results using your configured models.");
    const benchmarkId = params.benchmark === "wb" ? `wb-${params.subset}` : params.benchmark;
    const benchmark = state?.benchmarks?.find((item) => item.id === benchmarkId);
    const executions = ((!params.mode || params.mode === "both") ? 7 : 4) * (params.k || 1);
    $("#confirm-facts").replaceChildren(element("p", "", `${benchmark?.name || "Experiment"} · ${(params.domains || []).map(human).join(", ") || "Selected domains"}`), element("p", "", `Up to ${params.max_tasks || 1} task(s) per domain · ${params.k || 1} branch(es) · ${params.workers || 1} parallel domain(s)`), element("p", "", `${executions} planned full executions per task, plus harness editing, reflection, and judging. Saved work may reduce this; retries can add work.`), element("p", "", providerLabel()));
    text("#confirm-compute", state?.settings?.provider === "local" ? "Your local model will use this machine’s computing resources. You can stop the run from Runs." : "This uses your connected provider and may incur charges. You can stop the run from Runs.");
    text("#confirm-accept", resume ? "Resume run" : "Start experiment");
    dialog.returnValue = "cancel";
    return new Promise((resolve) => {
      dialog.addEventListener("close", () => {
        resolve(dialog.returnValue === "confirm");
        if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
      }, { once: true });
      dialog.showModal();
      dialog.querySelector(".dialog-actions .secondary").focus();
    });
  }
  async function launch(kind, params = {}, requiresConfirm = false) {
    if (launching || activeJob()) { toast("A run is already active. Follow or stop it in Runs."); return; }
    const problem = kind === "benchmark" ? localProfileProblem(params) : "";
    if (problem) { message("#experiment-form-message", problem, true); toast(problem, true); return; }
    if (requiresConfirm && !await confirmCompute(params)) return;
    launching = true;
    updateBusy();
    try {
      const job = await api("/api/jobs", { method: "POST", body: { kind, params, ...(requiresConfirm ? { confirm: true } : {}) } });
      if (state) {
        state.jobs = [job, ...(state.jobs || []).filter((item) => item.id !== job.id)];
        state.active_id = isActive(job.status) ? job.id : null;
        renderState();
      }
      selectJob(job.id);
      toast(`${kindLabels[kind] || "Run"} started.`);
      pollState();
    } catch (error) { toast(error.message, true); message("#experiment-form-message", error.message, true); }
    finally { launching = false; updateBusy(); }
  }
  async function resumeJob() {
    if (!selectedJob || launching || activeJob()) return;
    const job = selectedJob;
    const problem = job.kind === "benchmark" ? localProfileProblem(job.params || {}) : "";
    if (problem) { toast(problem, true); return; }
    if (job.kind === "benchmark" && !await confirmCompute(job.params || {}, true)) return;
    launching = true;
    updateBusy();
    try {
      const result = await api(`/api/jobs/${encodeURIComponent(job.id)}/resume`, { method: "POST", body: { confirm: true } });
      if (state) { state.jobs = [result, ...(state.jobs || []).filter((item) => item.id !== result.id)]; state.active_id = isActive(result.status) ? result.id : null; }
      selectJob(result.id);
      toast("Run resumed.");
      pollState();
    } catch (error) { toast(error.message, true); }
    finally { launching = false; updateBusy(); }
  }
  async function cancelJob() {
    if (!selectedJob) return;
    const button = $("#job-cancel");
    button.disabled = true;
    try {
      const result = await api(`/api/jobs/${encodeURIComponent(selectedJob.id)}/cancel`, { method: "POST", body: {} });
      selectedJob = { ...selectedJob, ...result };
      renderDetail(selectedJob);
      toast("Stopping the run. Completed work stays in its output folder.");
      pollState();
    } catch (error) { toast(error.message, true); }
    finally { button.disabled = false; }
  }
  async function openFolder(jobId) {
    try { await api("/api/open-folder", { method: "POST", body: jobId ? { job_id: jobId } : {} }); }
    catch (error) { toast(error.message, true); }
  }
  function settingsValues() {
    const result = { provider: $("#provider").value, key_configured: Boolean(state?.settings?.key_configured) };
    Object.entries(settingsFields).forEach(([key, id]) => {
      const input = $(`#${id}`);
      if (!input.disabled) result[key] = input.value.trim();
    });
    const key = $("#api-key").value.trim();
    if (key) result.api_key = key;
    return result;
  }
  async function saveSettings(event) {
    event.preventDefault();
    const button = $("#save-settings");
    button.disabled = true;
    message("#settings-message", "Saving connection…");
    try {
      const settings = await api("/api/settings", { method: "POST", body: settingsValues() });
      if (state) state.settings = settings;
      $("#api-key").value = "";
      message("#settings-message", "Connection saved. You’re ready to plan an experiment.");
      renderPlan();
      pollState();
    } catch (error) { message("#settings-message", error.message, true); }
    finally { button.disabled = false; }
  }
  async function testConnection() {
    if ($("#provider").value === "vertex") { launch("check"); return; }
    const settings = settingsValues();
    const local = settings.provider === "local";
    const url = local ? settings.local_url : settings.codex_url;
    const model = local ? settings.local_model : settings.codex_model;
    if (!url) { message("#connection-test-message", "Enter the server address first.", true); return; }
    const button = $("#test-connection");
    button.disabled = true;
    const previousLabel = button.textContent;
    button.textContent = "Testing…";
    message("#connection-test-message", "Looking for models at your endpoint…");
    try {
      const result = await api("/api/connection", { method: "POST", body: { url, provider: settings.provider, ...(settings.api_key ? { api_key: settings.api_key } : {}) } });
      if (!result.ok) throw new Error(result.error || "The endpoint did not respond successfully.");
      const models = Array.isArray(result.models) ? result.models : [];
      if (local) {
        $("#local-models").replaceChildren(...models.map((name) => { const option = element("option"); option.value = String(name); return option; }));
      }
      if (models.length && !models.includes(model)) $(local ? "#local-model" : "#codex-model").value = models[0];
      if (local) fillLocalModels();
      message("#connection-test-message", `Connected. ${models.length} model${models.length === 1 ? "" : "s"} available. Save the connection when you’re ready.`);
    } catch (error) { message("#connection-test-message", error.message, true); }
    finally { button.disabled = false; button.textContent = previousLabel; }
  }

  function confirmShutdown() {
    const dialog = $("#shutdown-dialog");
    const previousFocus = document.activeElement;
    const job = activeJob();
    text("#shutdown-description", `${job?.title || "The current operation"} is still active. Closing the app stops this run. Completed results and files stay in your workspace.`);
    dialog.returnValue = "cancel";
    return new Promise((resolve) => {
      dialog.addEventListener("close", () => {
        resolve(dialog.returnValue === "confirm");
        if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
      }, { once: true });
      dialog.showModal();
      dialog.querySelector(".dialog-actions .secondary").focus();
    });
  }
  async function closeApp() {
    if (closing || appClosed) return;
    const confirmed = Boolean(activeJob()) || launching;
    if (confirmed && !await confirmShutdown()) return;
    closing = true;
    $("#close-app").disabled = true;
    updateBusy();
    try {
      await api("/api/shutdown", { method: "POST", body: { confirm: confirmed } });
      appClosed = true;
      clearInterval(pollTimer);
      clearTimeout(toastTimer);
      $("#toast").hidden = true;
      $("#connection-error").hidden = true;
      $$(".view").forEach((section) => { section.hidden = true; });
      $("#closed-screen").hidden = false;
      $("#connection-status").classList.add("offline");
      $("#connection-status").lastElementChild.textContent = "App closed";
      text("#breadcrumb", "Workspace / Closed");
      $$("button, input, select").forEach((control) => { control.disabled = true; });
      document.title = "Harness² Studio · Closed";
      $("#main").focus({ preventScroll: true });
      window.scrollTo({ top: 0, behavior: "instant" });
    } catch (error) {
      toast(error.message, true);
    } finally {
      closing = false;
      $("#close-app").disabled = appClosed;
      updateBusy();
    }
  }

  function selectTab(name, focus = false) {
    ["results", "log"].forEach((id) => {
      const tab = $(`#tab-${id}`);
      const selected = name === id;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      $(`#panel-${id}`).hidden = !selected;
    });
    if (name === "log" && followingLog) {
      const log = $("#job-log");
      log.scrollTop = log.scrollHeight;
    }
    if (focus) $(`#tab-${name}`).focus();
  }
  $$('[data-view]').forEach((button) => button.addEventListener("click", (event) => { event.preventDefault(); showView(button.dataset.view); }));
  ["start-demo", "onboarding-demo", "submit-experiment", "preview-experiment", "check-environment", "authenticate"].forEach((id) => { $(`#${id}`).dataset.jobStart = ""; });
  $("#start-demo").addEventListener("click", () => launch("demo", { k: 2, mode: "both" }));
  $("#onboarding-demo").addEventListener("click", () => launch("demo", { k: 2, mode: "both" }));
  $("#experiment-form").addEventListener("submit", (event) => { event.preventDefault(); const params = formParams(); if (params) launch("benchmark", params, true); });
  $("#preview-experiment").addEventListener("click", () => { const params = formParams(); if (params) launch("preview", params); });
  $("#benchmark").addEventListener("change", renderDomains);
  ["domain", "substrate", "max-tasks", "workers", "branches", "mode"].forEach((id) => { $(`#${id}`).addEventListener("input", renderPlan); });
  $("#job-search").addEventListener("input", renderJobs);
  $("#job-filter").addEventListener("change", renderJobs);
  $("#provider").addEventListener("change", providerChanged);
  $("#local-model").addEventListener("input", fillLocalModels);
  $("#settings-form").addEventListener("submit", saveSettings);
  $("#test-connection").addEventListener("click", testConnection);
  $("#check-environment").addEventListener("click", () => launch("check"));
  $("#authenticate").addEventListener("click", () => launch("authenticate", { provider: "vertex" }));
  $("#open-workspace").addEventListener("click", () => openFolder());
  $("#close-app").addEventListener("click", closeApp);
  $("#job-open-folder").addEventListener("click", () => selectedJob && openFolder(selectedJob.id));
  $("#job-cancel").addEventListener("click", cancelJob);
  $("#job-resume").addEventListener("click", resumeJob);
  $("#follow-log").addEventListener("click", () => { const log = $("#job-log"); followingLog = true; log.scrollTop = log.scrollHeight; log.focus({ preventScroll: true }); });
  $("#job-log").addEventListener("scroll", () => { const log = $("#job-log"); if (log.clientHeight) followingLog = log.scrollHeight - log.scrollTop - log.clientHeight < 65; });
  ["results", "log"].forEach((id) => {
    $(`#tab-${id}`).addEventListener("click", () => selectTab(id));
    $(`#tab-${id}`).addEventListener("keydown", (event) => {
      if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        selectTab(event.key === "Home" ? "results" : event.key === "End" ? "log" : id === "results" ? "log" : "results", true);
      }
    });
  });
  showView("overview", false);
  pollState();
  pollTimer = setInterval(pollState, 2000);
})();
