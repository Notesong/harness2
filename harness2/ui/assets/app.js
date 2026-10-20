"use strict";

(() => {
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const views = { overview: "Home", new: "New experiment", profiles: "Profiles", conversations: "Conversations", capabilities: "Capabilities", runs: "Runs", setup: "Setup" };
  const statusLabels = { starting: "Starting", running: "Running", succeeded: "Complete", failed: "Failed", partial: "Needs attention", exception: "Failed", cancelled: "Stopped", interrupted: "Interrupted", pending: "Waiting", completed: "Complete", complete: "Complete", skipped: "Skipped" };
  const kindLabels = { demo: "Free demo", benchmark: "Experiment", check: "Environment check", setup: "Benchmark setup", preview: "Experiment plan", judge: "Evaluation", report: "Report", authenticate: "Authentication" };
  const settingsFields = { local_url: "local-url", local_model: "local-model", local_context: "local-context", local_output: "local-output", local_runtime: "local-runtime", local_executable: "local-executable", local_weights: "local-weights", project: "project", region: "region", solver: "solver", improver: "improver", judge: "judge", codex_url: "codex-url", codex_model: "codex-model" };
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
  const profileCache = new Map();
  let selectedProfileId = null;
  let profileListSignature = "";
  let profileDetailSignature = "";
  let profileLoadingId = null;
  let profileBusy = false;
  let runProfileSignature = "";
  let experimentChoiceProfileId = null;
  const capabilityCache = new Map();
  let selectedCapabilityId = null;
  let capabilitySignature = "";
  let capabilityDetailSignature = "";
  let capabilityLoadingId = null;
  let capabilityBusy = false;
  const conversationCache = new Map();
  const conversationDrafts = new Map();
  let selectedConversationId = null;
  let conversationListSignature = "";
  let conversationDetailSignature = "";
  let conversationLoading = false;
  let conversationBusy = false;
  const conversationVersions = new Map();
  const attentionSeenIds = new Set();
  const readInFlight = new Set();
  const readConfirmedIds = new Map();
  let notificationPrimed = false;
  let notificationBusy = false;
  let notificationError = "";

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
  selectedProfileId = recalled("harness2_selected_profile");
  selectedConversationId = recalled("harness2_selected_conversation");

  function element(tag, className, text) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== undefined && text !== null) result.textContent = String(text);
    return result;
  }
  function text(selector, value) { $(selector).textContent = value === undefined || value === null ? "" : String(value); }
  function human(value) { return String(value || "").replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()); }
  function count(value) { const n = Number(value); return Number.isFinite(n) ? n.toLocaleString() : "—"; }
  function counted(value, singular, plural = `${singular}s`) { return `${count(value)} ${Number(value) === 1 ? singular : plural}`; }
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
    if (next === "new") renderExperimentProfile();
    if (next === "profiles") {
      renderProfiles();
      if (changed && selectedProfileId && profileLoadingId !== selectedProfileId) loadProfile(selectedProfileId, { force: true });
    }
    if (next === "capabilities") renderCapabilities();
    if (next === "conversations") { renderConversations(); loadConversation(); }
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
  function experimentProfile() { return profileCache.get($("#experiment-profile").value) || null; }
  function experimentSettings(params = {}) {
    const profile = params.profile_id ? profileCache.get(params.profile_id) : experimentProfile();
    const configurationId = params.profile_configuration_id || ($("#experiment-profile").value === profile?.id ? $("#experiment-configuration").value : "") || profile?.active_configuration_id;
    const configuration = profile?.configurations?.find((item) => item.id === configurationId);
    return configuration?.snapshot?.settings || state?.settings;
  }
  function localProfileProblem(params) {
    if (experimentSettings(params)?.provider !== "local") return "";
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
    const profile = experimentProfile();
    const revision = profile?.revisions?.find((item) => item.id === $("#experiment-revision").value);
    text("#plan-provider", providerLabel(experimentSettings()));
    text("#plan-starting-point", profile ? `${profile.name} · ${revision?.label || "choose an active harness"}` : "Current baseline");
    text("#plan-description", benchmark && domain ? `${benchmark.name} · ${human(domain.id)}. Work through up to ${count(taskCount)} task${taskCount === 1 ? "" : "s"}, exploring ${branches} branch${branches === 1 ? "" : "es"} per step. Tasks stay in order within each domain; the limit is ${workers} parallel domain${workers === 1 ? "" : "s"}.` : "Prepare a benchmark, then choose one domain for a focused first experiment.");
    text("#domain-help", domain ? `${count(domain.count)} tasks available in this domain. Your experiment will use up to ${count(taskCount)}.` : "Select one domain to keep the first experiment focused.");
    const localNote = $("#local-profile-note");
    localNote.hidden = experimentSettings()?.provider !== "local";
    localNote.textContent = localProfileProblem({ benchmark: benchmark?.id?.startsWith("wb-") ? "wb" : benchmark?.id, substrate: $("#substrate").value }) || "The complete local pipeline supports JobBench with OpenCode. Other benchmarks and runners need additional configuration.";
    renderConversationOption();
  }
  function formParams() {
    if (!$("#experiment-form").reportValidity()) return null;
    const id = $("#benchmark").value;
    const benchmark = state?.benchmarks?.find((item) => item.id === id);
    if (!benchmark?.installed || !$("#domain").value) { toast("Prepare a benchmark in Setup before starting.", true); return null; }
    const profileId = $("#experiment-profile").value;
    const profile = experimentProfile();
    const configurationId = $("#experiment-configuration").value;
    const revisionId = $("#experiment-revision").value;
    if (profileId && (!profile?.configurations?.some((configuration) => configuration.id === configurationId) || !profile?.revisions?.some((revision) => revision.id === revisionId))) {
      toast("Choose this run’s configuration and harness from the profile before starting.", true);
      return null;
    }
    if ($("#experiment-conversation").checked && !conversationOptionAllowed()) { toast("Conversation during work needs a local profile, JobBench with OpenCode, and one domain worker.", true); return null; }
    return { ...benchmarkParams(id), substrate: $("#substrate").value, k: Number($("#branches").value), mode: $("#mode").value, domains: [$("#domain").value], max_tasks: Number($("#max-tasks").value), workers: Number($("#workers").value), pipeline: true, ...(profileId ? { profile_id: profileId, profile_configuration_id: configurationId, profile_revision_id: revisionId } : {}), ...($("#experiment-conversation").checked ? { conversation_enabled: true } : {}) };
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
    Object.entries(settingsFields).forEach(([key, id]) => { $(`#${id}`).value = settings[key] || (key === "local_runtime" ? "external" : ""); });
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
    localRuntimeChanged();
    text("#test-connection", provider === "vertex" ? "Check environment" : "Test connection");
    if (provider !== "local") $("#advanced-models").open = true;
    message("#connection-test-message", "");
  }
  function localRuntimeChanged() {
    const local = $("#provider").value === "local";
    const managed = local && $("#local-runtime").value === "managed";
    $("#local-runtime").disabled = !local;
    $("#local-managed-paths").hidden = !managed;
    ["local-executable", "local-weights"].forEach((id) => {
      $(`#${id}`).disabled = !managed;
      $(`#${id}`).required = managed;
    });
    text("#local-server-help", managed ? "The server starts automatically when you begin a run. Use an unused local port; Harness² will keep this server private to the run." : "Start your local server, then test the connection to discover its models.");
    text("#local-token-help", managed ? "These limits configure the model server for each run. Context covers the prompt, tool output, and reply; maximum output must be smaller. Larger contexts need more memory." : "Match the context window configured on the running model server. Context covers the prompt, tool output, and reply; maximum output must be smaller. These settings tell Harness² the available budget. They do not restart or resize the server.");
    message("#connection-test-message", "");
  }
  function fillLocalModels() {
    const model = $("#local-model").value.trim();
    if ($("#provider").value !== "local" || !model) return;
    $("#solver").value = `local/${model}`;
    $("#improver").value = `local/${model}`;
    $("#judge").value = model;
  }

  function profileButton(label, action, className = "button secondary small") {
    const button = element("button", className, label);
    button.type = "button";
    button.disabled = profileBusy;
    button.addEventListener("click", action);
    return button;
  }
  function profileInput(form, label, name, options = {}) {
    const id = `profile-field-${name}`;
    const caption = element("label", "", label);
    caption.htmlFor = id;
    const input = element(options.multiline ? "textarea" : "input");
    input.id = id;
    input.name = name;
    input.required = Boolean(options.required);
    input.maxLength = options.multiline ? 4000 : 200;
    if (options.multiline) input.rows = 3;
    if (options.placeholder) input.placeholder = options.placeholder;
    form.append(caption, input);
    return input;
  }
  function profileSection(title, description) {
    const section = element("section", "profile-section");
    section.append(element("h3", "", title));
    if (description) section.append(element("p", "field-help", description));
    return section;
  }
  function evidenceBadge(value) {
    const known = ["observed", "suspected", "replicated", "transferred"];
    const status = known.includes(value) ? value : "unclassified";
    return element("span", `evidence-badge evidence-${status}`, human(status));
  }
  function revisionFiles(revision) {
    const tree = revision?.tree;
    const files = revision?.files || tree?.files || tree || {};
    if (Array.isArray(files)) return files.map((file) => typeof file === "string" ? file : file?.path || file?.name).filter((file) => typeof file === "string");
    return files && typeof files === "object" ? Object.keys(files).filter((file) => typeof files[file] === "string" || files[file]?.content !== undefined) : [];
  }
  function revisionLabel(profile, id) {
    return profile.revisions?.find((revision) => revision.id === id)?.label || id || "Unrecorded";
  }
  function profileSummary(profile) {
    return { id: profile.id, name: profile.name, description: profile.description,
      active_configuration_id: profile.active_configuration_id, active_revision_id: profile.active_revision_id,
      configuration_count: profile.configurations?.length || 0, revision_count: profile.revisions?.length || 0,
      run_count: profile.runs?.length || 0, notes_count: profile.notes?.length || 0 };
  }
  function cacheProfile(result) {
    const profile = result.profile || result;
    if (!profile?.id) throw new Error("The profile could not be read. Refresh the workspace and try again.");
    profileCache.set(profile.id, profile);
    if (state) state.profiles = [...(state.profiles || []).filter((item) => item.id !== profile.id), profileSummary(profile)];
    return profile;
  }
  async function loadProfile(id, { render = true, force = false } = {}) {
    if (!id) return null;
    if (!force && profileCache.has(id)) {
      if (render && id === selectedProfileId) renderProfileDetail(profileCache.get(id));
      return profileCache.get(id);
    }
    if (render) profileLoadingId = id;
    try {
      const profile = cacheProfile(await api(`/api/profiles/${encodeURIComponent(id)}`));
      if (render && id === selectedProfileId) renderProfileDetail(profile);
      if (id === $("#experiment-profile").value) renderExperimentProfile();
      return profile;
    } catch (error) {
      toast(error.message, true);
      if (render && id === selectedProfileId) $("#profile-detail").replaceChildren(empty("This profile is unavailable.", error.message, true));
      return null;
    } finally { if (profileLoadingId === id) profileLoadingId = null; }
  }
  async function profileAction(id, operation, body, success) {
    if (profileBusy) return null;
    profileBusy = true;
    $$("#view-profiles button, #run-profile-capture button").forEach((button) => { button.disabled = true; });
    try {
      const profile = cacheProfile(await api(`/api/profiles/${encodeURIComponent(id)}/${operation}`, { method: "POST", body }));
      profileListSignature = "";
      profileDetailSignature = "";
      profileBusy = false;
      renderProfiles();
      if (profile.id === selectedProfileId) renderProfileDetail(profile);
      renderExperimentProfile();
      if (selectedJob) renderRunProfileCapture(selectedJob, true);
      toast(success);
      return profile;
    } catch (error) { toast(error.message, true); return null; }
    finally {
      profileBusy = false;
      $$("#view-profiles button, #run-profile-capture button").forEach((button) => { button.disabled = false; });
    }
  }
  function selectProfile(id) {
    selectedProfileId = id;
    remember("harness2_selected_profile", id);
    profileListSignature = "";
    profileDetailSignature = "";
    renderProfiles();
    showView("profiles");
    loadProfile(id, { force: true });
  }
  function renderProfiles() {
    if (!state) return;
    const profiles = [...(state.profiles || [])].sort((a, b) => a.name.localeCompare(b.name));
    if (!profiles.some((profile) => profile.id === selectedProfileId)) selectedProfileId = profiles[0]?.id || null;
    const signature = JSON.stringify([profiles, selectedProfileId]);
    if (profileListSignature !== signature) {
      profileListSignature = signature;
      $("#profile-list").replaceChildren(...profiles.map((profile) => {
        const button = profileButton(profile.name, () => selectProfile(profile.id), `profile-list-item${profile.id === selectedProfileId ? " selected" : ""}`);
        button.replaceChildren(element("strong", "", profile.name), element("small", "", `${counted(profile.revision_count, "revision")} · ${counted(profile.run_count, "run")}`));
        if (profile.id === selectedProfileId) button.setAttribute("aria-current", "true");
        return button;
      }));
      if (!profiles.length) $("#profile-list").append(element("p", "field-help", "Create a profile to begin its own history."));
      if (!profiles.length) $(".profile-create-panel").open = true;
      const select = $("#experiment-profile");
      const previous = select.value;
      select.replaceChildren(element("option", "", "Use Setup settings · no continuing profile"), ...profiles.map((profile) => {
        const option = element("option", "", profile.name);
        option.value = profile.id;
        return option;
      }));
      select.firstElementChild.value = "";
      select.value = profiles.some((profile) => profile.id === previous) ? previous : "";
      renderExperimentProfile();
    }
    if (view === "profiles" && selectedProfileId) {
      const profile = profileCache.get(selectedProfileId);
      const summary = profiles.find((item) => item.id === selectedProfileId);
      const fresh = profile && summary && ["active_configuration_id", "active_revision_id", "configuration_count", "revision_count", "run_count", "notes_count"].every((key) => summary[key] === undefined || profileSummary(profile)[key] === summary[key]);
      if (profile && fresh) renderProfileDetail(profile);
      else if (profileLoadingId !== selectedProfileId) loadProfile(selectedProfileId, { force: true });
    }
  }
  function renderExperimentProfile() {
    const id = $("#experiment-profile").value;
    const profile = profileCache.get(id);
    let help = "Choose a profile to carry its own configuration and harness into this run.";
    $("#experiment-profile-choices").hidden = !id;
    if (id && !profile) {
      help = "Loading this profile’s starting point…";
      loadProfile(id, { render: false });
    } else if (profile) {
      const sameProfile = experimentChoiceProfileId === id;
      const configurations = profile.configurations || [];
      const revisions = profile.revisions || [];
      const previousConfiguration = sameProfile ? $("#experiment-configuration").value : "";
      const previousRevision = sameProfile ? $("#experiment-revision").value : "";
      const configurationSelect = $("#experiment-configuration");
      const revisionSelect = $("#experiment-revision");
      configurationSelect.replaceChildren(...configurations.map((item) => {
        const option = element("option", "", `${item.label || item.id}${item.id === profile.active_configuration_id ? " · active" : ""}`);
        option.value = item.id;
        return option;
      }));
      revisionSelect.replaceChildren(...revisions.map((item) => {
        const lifecycle = item.retired ? "retired" : item.id === profile.active_revision_id ? "active" : "candidate";
        const option = element("option", "", `${item.label || item.id} · ${lifecycle}`);
        option.value = item.id;
        return option;
      }));
      configurationSelect.value = configurations.some((item) => item.id === previousConfiguration) ? previousConfiguration : profile.active_configuration_id || configurations[0]?.id || "";
      revisionSelect.value = revisions.some((item) => item.id === previousRevision) ? previousRevision : profile.active_revision_id || revisions[0]?.id || "";
      configurationSelect.disabled = !configurations.length;
      revisionSelect.disabled = !revisions.length;
      const configuration = configurations.find((item) => item.id === configurationSelect.value);
      const revision = revisions.find((item) => item.id === revisionSelect.value);
      help = configuration && revision ? `${configuration.label} · ${revision.label}. This choice tests the selected harness without changing the profile’s active harness. The run saves its exact starting point.` : "Capture a configuration and create a harness revision in Profiles before starting this experiment.";
      if (state?.capabilities) {
        const available = capabilityState().enabled && capabilityState().profile_access?.[id] === true;
        help += available ? capabilityEntries().length ? ` Plan: ${counted(capabilityEntries().length, "catalog reference")} available. Launch records the final snapshot; availability does not confirm use.` : " Catalog access is on, but no entries are published yet." : " Catalog reference access is off for new experiments.";
      }
    }
    experimentChoiceProfileId = id || null;
    text("#experiment-profile-help", help);
    renderPlan();
  }
  function profileForm(section, label, operation, buildBody, success) {
    const form = element("form", "profile-form");
    const submit = element("button", "button secondary small profile-submit", label);
    submit.type = "submit";
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      const body = buildBody();
      if (body) profileAction(selectedProfileId, operation, body, success);
    });
    section.append(form);
    return { form, submit };
  }
  function renderProfileDetail(profile) {
    const signature = JSON.stringify([profile, (state?.jobs || []).filter((job) => job.kind === "benchmark" && ["succeeded", "partial"].includes(job.status)).map((job) => [job.id, job.status])]);
    if (profileDetailSignature === signature) return;
    profileDetailSignature = signature;
    const wrap = $("#profile-detail");
    const heading = element("header", "profile-heading");
    const title = element("div");
    title.append(element("span", "eyebrow", "A CONTINUING IDENTITY"), element("h2", "", profile.name));
    if (profile.description) title.append(element("p", "muted", profile.description));
    const start = profileButton("New experiment ↗", () => {
      $("#experiment-profile").value = profile.id;
      renderExperimentProfile();
      showView("new");
    }, "button primary small");
    const actions = element("div", "profile-heading-actions");
    const talk = profileButton(`Talk to ${profile.name}`, () => talkToProfile(profile), "button secondary small");
    talk.disabled = !profile.active_configuration_id || !profile.active_revision_id || conversationBusy;
    talk.title = "Creates a conversation and loads its selected local model. Stop the conversation to release its owned model.";
    actions.append(start, talk, element("small", "field-help", "Talk loads the saved local model and opens its working session."));
    heading.append(title, actions);
    const note = element("p", "profile-continuity-note", "Experiments take a recorded snapshot. Changing the active configuration or harness affects future runs; the history stays intact.");
    const inventory = renderProfileInventory(profile);
    const configurations = renderProfileConfigurations(profile);
    const revisions = renderProfileRevisions(profile);
    const ideas = renderProfileNotes(profile);
    const ancestry = renderProfileHistory(profile);
    wrap.replaceChildren(heading, note, inventory, configurations, revisions, ideas, ancestry);
  }
  function renderProfileInventory(profile) {
    const section = profileSection("Current capabilities", "Selected harness components and declared runtime tools. Availability is conditional; this is not a live capability test.");
    const inventory = profile.capabilities;
    if (!inventory) {
      section.append(element("p", "field-help", "An inventory has not been returned for this profile. Its saved configurations and harness files are available below."));
      return section;
    }
    const configuration = inventory.configuration || {};
    const revision = inventory.revision || {};
    const starting = element("div", "inventory-starting-point");
    starting.append(element("h4", "", revision.label || "No harness selected"), element("p", "field-help", `${configuration.label || "No configuration selected"}${configuration.model ? ` · ${configuration.model}` : ""}${configuration.context ? ` · ${count(configuration.context)} context` : ""}${configuration.output ? ` · ${count(configuration.output)} output` : ""}`));
    const provenance = element("details", "inventory-provenance");
    provenance.append(element("summary", "", "Saved configuration & provenance"));
    if (revision.id) provenance.append(element("small", "", `Harness ${revision.id}`));
    if (configuration.id) provenance.append(element("small", "", `Configuration ${configuration.id}`));
    if (revision.content_sha256) provenance.append(element("small", "", `Harness content SHA-256 ${revision.content_sha256}`));
    if (configuration.tool_policy_sha256) provenance.append(element("small", "", `Tool policy SHA-256 ${configuration.tool_policy_sha256}`));
    if (configuration.association) provenance.append(element("p", "field-help", configuration.association));
    starting.append(provenance);
    section.append(starting);
    const groups = { always_on: "Always-on guidance", skills: "Custom skills", subagents: "Custom subagents", scripts: "Helper scripts", plugins: "Profile plugins" };
    const components = element("div", "inventory-components");
    for (const [key, label] of Object.entries(groups)) {
      const items = inventory.components?.[key] || [];
      const group = element("details", "inventory-component-group");
      group.append(element("summary", "", `${label} · ${count(items.length)}`));
      if (!items.length) group.append(element("p", "field-help", `No ${label.toLowerCase()} in this selected harness. Built-in runtime tools are listed separately.`));
      for (const item of items) {
        const file = element("article", "inventory-component");
        file.append(element("strong", "", item.label || item.name || item.path), element("small", "", item.path || ""));
        if (item.description) file.append(element("p", "", item.description));
        if (item.present === false) file.append(element("p", "field-help", "Declared, but its file is missing."));
        else if (item.bytes !== undefined) file.append(element("small", "", bytes(item.bytes)));
        group.append(file);
      }
      components.append(group);
    }
    section.append(components);
    const tools = element("details", "inventory-tools");
    tools.append(element("summary", "", "Runtime tools and conditions"));
    for (const tool of [...(inventory.builtin_tools || []), ...(inventory.application_tools || [])]) {
      const row = element("article", "inventory-tool");
      row.append(element("strong", "", tool.label || tool.id), element("span", "evidence-badge", human(tool.status || "unknown")));
      if (tool.description) row.append(element("p", "", tool.description));
      if (tool.qualification) row.append(element("p", "field-help", tool.qualification));
      tools.append(row);
    }
    if (inventory.runtime?.source) tools.append(element("p", "field-help", inventory.runtime.source));
    if (inventory.runtime?.version) tools.append(element("p", "field-help", `${inventory.runtime.substrate || "Runtime"} ${inventory.runtime.version}${inventory.runtime.commit ? ` · ${inventory.runtime.commit}` : ""}`));
    section.append(tools);
    const permissions = inventory.permissions || {};
    const limits = element("details", "inventory-boundaries");
    limits.append(element("summary", "", "Inventory scope and permissions"));
    if (permissions.summary) limits.append(element("p", "", permissions.summary));
    else if (permissions.os_sandbox === false || permissions.shell_confined === false) limits.append(element("p", "inventory-permission-fact", "OpenCode permission rules are application policy. They are not an operating-system sandbox; shell commands are not confined to the working folder."));
    if (permissions.qualification) limits.append(element("p", "field-help", permissions.qualification));
    if (inventory.limits?.length) {
      const list = element("ul");
      list.append(...inventory.limits.map((limit) => element("li", "", limit)));
      limits.append(list);
    }
    section.append(limits);
    return section;
  }
  function renderProfileConfigurations(profile) {
    const section = profileSection("Configurations", "Immutable snapshots of the model, runtime, budgets, and roles. Save any Setup changes before capturing one.");
    for (const configuration of profile.configurations || []) {
      const card = element("details", "profile-card");
      const summary = element("summary");
      summary.append(element("strong", "", configuration.label || configuration.id));
      if (configuration.id === profile.active_configuration_id) summary.append(element("span", "lifecycle-badge active", "Active"));
      const settings = configuration.snapshot?.settings || {};
      const body = element("div", "profile-card-body");
      body.append(element("p", "field-help", [providerLabel(settings), settings.local_context ? `${count(settings.local_context)} context · ${count(settings.local_output)} output` : ""].filter(Boolean).join(" · ")));
      body.append(element("pre", "profile-snapshot", JSON.stringify(configuration.snapshot || {}, null, 2)));
      if (configuration.id !== profile.active_configuration_id) body.append(profileButton("Use for future runs", () => profileAction(profile.id, "activate", { configuration_id: configuration.id, why: `Selected configuration: ${configuration.label || configuration.id}` }, "Active configuration changed for future runs.")));
      card.append(summary, body);
      section.append(card);
    }
    if (!(profile.configurations || []).length) section.append(element("p", "field-help", "No configuration captured yet. Connect the model in Setup, then save its identity here."));
    const { form, submit } = profileForm(section, "Capture saved Setup", "configurations", () => ({ label: label.value.trim() }), "Configuration captured. Choose it when you’re ready to use it.");
    const label = profileInput(form, "Configuration name", "configuration-label", { required: true, placeholder: "e.g. Q3 · 64K · reasoning off" });
    form.append(submit, element("p", "field-help", "Capturing file identities may take a few seconds. This does not load the model."));
    return section;
  }
  function renderProfileRevisions(profile) {
    const section = profileSection("Harness ancestry", "Each candidate keeps its parents, borrowed material, and reason for changing. Active means selected for future work; evidence describes what has actually been demonstrated.");
    const revisions = profile.revisions || [];
    for (const revision of revisions) {
      const card = element("details", "profile-card revision-card");
      card.id = `profile-revision-${revision.id}`;
      card.open = revision.id === profile.active_revision_id;
      const summary = element("summary");
      summary.append(element("strong", "", revision.label || revision.id));
      const lifecycle = revision.retired ? "Retired" : revision.id === profile.active_revision_id ? "Active" : "Candidate";
      summary.append(element("span", `lifecycle-badge ${lifecycle.toLowerCase()}`, lifecycle));
      const body = element("div", "profile-card-body");
      const evidence = revision.evidence_status || revision.evidence?.status || (Array.isArray(revision.evidence) ? revision.evidence.find((item) => item?.evidence_status)?.evidence_status : null) || (typeof revision.evidence === "string" ? revision.evidence : revision.operation === "import" ? "suspected" : null);
      body.append(evidenceBadge(evidence));
      if (revision.domain) body.append(element("span", "soft-tag", human(revision.domain)));
      if (revision.why) body.append(element("p", "profile-reason", revision.why));
      const parents = revision.parents || [];
      const lineage = element("div", "revision-lineage");
      if (!parents.length) lineage.append(element("p", "field-help", "Starting point · no parent revision"));
      else {
        lineage.append(element("span", "field-help", "Descends from "));
        for (const parent of parents) {
          const id = typeof parent === "string" ? parent : parent.revision_id || parent.id;
          lineage.append(profileButton(revisionLabel(profile, id), () => {
            const node = document.getElementById(`profile-revision-${id}`);
            if (node) { node.open = true; node.scrollIntoView({ behavior: "smooth", block: "nearest" }); }
          }, "text-button"));
        }
      }
      for (const borrowed of revision.borrowed_from || []) {
        const ref = typeof borrowed === "string" ? borrowed : [borrowed.profile_name || borrowed.profile_id, borrowed.revision_id || borrowed.id, borrowed.component || borrowed.path].filter(Boolean).join(" / ");
        lineage.append(element("p", "field-help", `Borrowed as a hypothesis: ${ref}`));
      }
      body.append(lineage);
      if (revision.motivated_by?.length) body.append(element("p", "field-help", `Motivated by: ${revision.motivated_by.map((item) => typeof item === "string" ? item : JSON.stringify(item)).join(" · ")}`));
      if (revision.evidence && typeof revision.evidence === "object") {
        const details = element("details", "profile-evidence");
        details.append(element("summary", "", "Read the supporting evidence"), element("pre", "profile-snapshot", JSON.stringify(revision.evidence, null, 2)));
        body.append(details);
      }
      if (revision.experience && Object.keys(revision.experience).length) {
        const experience = element("details", "profile-evidence");
        experience.append(element("summary", "", "Experience carried by this revision"));
        for (const [key, value] of Object.entries(revision.experience)) experience.append(element("h4", "", human(key)), element("p", "profile-reason", value));
        body.append(experience);
      }
      const paths = revisionFiles(revision);
      body.append(element("p", "field-help", `${counted(paths.length, "harness file")} · ${human(revision.operation || "revision")}`));
      if (paths.length) {
        const files = element("details", "profile-files");
        files.append(element("summary", "", "Harness files"));
        for (const file of paths) {
          const contents = revision.tree?.[file];
          if (typeof contents === "string") {
            const preview = element("details", "output-preview");
            preview.append(element("summary", "", file), element("pre", "", contents));
            files.append(preview);
          } else files.append(element("p", "field-help", file));
        }
        body.append(files);
      }
      const actions = element("div", "profile-actions");
      actions.append(profileButton("Try this revision ↗", () => {
        $("#experiment-profile").value = profile.id;
        renderExperimentProfile();
        $("#experiment-revision").value = revision.id;
        renderExperimentProfile();
        showView("new");
      }));
      if (revision.id !== profile.active_revision_id) actions.append(profileButton(revision.retired ? "Restore for future runs" : "Use for future runs", () => profileAction(profile.id, "activate", { revision_id: revision.id, why: `${revision.retired ? "Restored" : "Selected"} harness: ${revision.label || revision.id}` }, "Active harness changed. Existing runs keep their starting point.")));
      body.append(actions);
      if (!revision.retired) {
        const retirement = element("details", "profile-toolbox");
        retirement.append(element("summary", "", "Retire this revision"), element("p", "field-help", revision.id === profile.active_revision_id ? "Retiring the active harness leaves this profile without an active harness until you select another. Existing runs stay pinned." : "Keep the history while taking this candidate out of current consideration. You can restore it later."));
        const label = element("label", "", "Reason for retirement");
        label.htmlFor = `retire-${revision.id}`;
        const retireReason = element("textarea");
        retireReason.id = label.htmlFor;
        retireReason.rows = 2;
        retireReason.maxLength = 4000;
        retirement.append(label, retireReason, profileButton("Retire revision", () => {
          const why = retireReason.value.trim();
          if (!why) { retireReason.focus(); toast("Add a reason before retiring this revision.", true); return; }
          profileAction(profile.id, "retire", { revision_id: revision.id, why }, "Revision retired. Its files and ancestry remain in history.");
        }, "button secondary small profile-submit"));
        body.append(retirement);
      }
      if (paths.length) body.append(renderSimplification(profile, revision, paths));
      card.append(summary, body);
      section.append(card);
    }
    const fresh = element("details", "profile-toolbox");
    fresh.open = !revisions.length;
    fresh.append(element("summary", "", "Start from a clean baseline"));
    fresh.append(element("p", "field-help", "The bundled JobBench / OpenCode baseline starts this profile independently. It copies no learned changes from another profile."));
    const { form, submit } = profileForm(fresh, "Create baseline candidate", "baseline", () => ({ label: baseline.value.trim() }), "Clean baseline saved as a candidate. It has not been activated.");
    const baseline = profileInput(form, "Baseline name", "baseline-label", { required: true, placeholder: "Original baseline" });
    form.append(submit);
    section.append(fresh, renderProfileBorrowing(profile), renderProfileRunImport(profile));
    return section;
  }
  function renderSimplification(profile, revision, paths) {
    const details = element("details", "profile-toolbox");
    details.append(element("summary", "", "Try a smaller harness"), element("p", "field-help", "Choose files to remove in a new candidate. The source revision stays intact; compare the candidate before adopting it."));
    const chosen = [];
    const { form, submit } = profileForm(details, "Save smaller candidate", "revisions", () => {
      const remove = chosen.filter((input) => input.checked).map((input) => input.value);
      if (!remove.length) { toast("Choose at least one file to remove.", true); return null; }
      return { parent_id: revision.id, label: label.value.trim(), why: why.value.trim(), remove_paths: remove, ...(origin.value.trim() ? { motivated_by: [origin.value.trim()] } : {}) };
    }, "Smaller candidate saved for comparison. The active harness is unchanged.");
    const label = profileInput(form, "Candidate name", `smaller-label-${revision.id}`, { required: true });
    const why = profileInput(form, "What might be unnecessary?", `smaller-why-${revision.id}`, { required: true, multiline: true });
    const origin = profileInput(form, "Origin or conversation reference (optional)", `smaller-origin-${revision.id}`, { placeholder: "e.g. Progress-state discussion · 20 October" });
    const choices = element("fieldset", "profile-file-choices");
    choices.append(element("legend", "", "Remove from the candidate"));
    for (const path of paths) {
      const line = element("label", "profile-checkbox");
      const check = element("input");
      check.type = "checkbox";
      check.value = path;
      chosen.push(check);
      line.append(check, element("span", "", path));
      choices.append(line);
    }
    form.append(choices, submit);
    return details;
  }
  function renderProfileBorrowing(profile) {
    const details = element("details", "profile-toolbox");
    details.append(element("summary", "", "Borrow from another profile"), element("p", "field-help", "An explicit import creates an independent candidate marked as a hypothesis. It does not activate it or change the source profile."));
    const sources = (state?.profiles || []).filter((item) => item.id !== profile.id);
    if (!sources.length) { details.append(element("p", "field-help", "Other profiles will appear here when you create them.")); return details; }
    const { form, submit } = profileForm(details, "Import as a hypothesis", "import", () => {
      if (!source.value || !revision.value) { toast("Choose a source profile and revision first.", true); return null; }
      return { source_profile_id: source.value, revision_id: revision.value, why: why.value.trim() };
    }, "Borrowed revision saved as a hypothesis. Test it before adoption.");
    const sourceLabel = element("label", "", "Source profile");
    sourceLabel.htmlFor = "profile-borrow-source";
    const source = element("select");
    source.id = sourceLabel.htmlFor;
    const blank = element("option", "", "Choose another profile");
    blank.value = "";
    source.append(blank, ...sources.map((item) => { const option = element("option", "", item.name); option.value = item.id; return option; }));
    source.required = true;
    const revisionLabelNode = element("label", "", "Reference revision");
    revisionLabelNode.htmlFor = "profile-borrow-revision";
    const revision = element("select");
    revision.id = revisionLabelNode.htmlFor;
    revision.required = true;
    revision.disabled = true;
    form.append(sourceLabel, source, revisionLabelNode, revision);
    source.addEventListener("change", async () => {
      revision.replaceChildren();
      revision.disabled = true;
      const id = source.value;
      const other = await loadProfile(id, { render: false, force: true });
      if (source.value !== id || !other) return;
      revision.replaceChildren(...(other.revisions || []).map((item) => {
        const option = element("option", "", `${item.label || item.id}${item.retired ? " · retired" : ""}`);
        option.value = item.id;
        return option;
      }));
      revision.disabled = !(other.revisions || []).length;
    });
    const why = profileInput(form, "Why might this help this profile?", "borrow-why", { required: true, multiline: true });
    form.append(submit);
    return details;
  }
  function renderProfileRunImport(profile) {
    const details = element("details", "profile-toolbox");
    details.append(element("summary", "", "Keep a candidate from a completed run"), element("p", "field-help", "Import the selected harness and its recorded evidence. A run’s selection does not establish an improvement over baseline."));
    const jobs = (state?.jobs || []).filter((job) => job.kind === "benchmark" && ["succeeded", "partial"].includes(job.status));
    if (!jobs.length) { details.append(element("p", "field-help", "Completed benchmark runs will appear here.")); return details; }
    let choices = [];
    const { form, submit } = profileForm(details, "Save run candidate", "from-run", () => {
      const choice = choices[Number(selection.value)];
      if (!choice) { toast("Choose a completed task selection first.", true); return null; }
      return { job_id: run.value, label: label.value.trim(), why: why.value.trim(), task_id: choice.task_id, mode: choice.mode };
    }, "Run candidate saved with its evidence. The active harness is unchanged.");
    const caption = element("label", "", "Completed run");
    caption.htmlFor = "profile-import-run";
    const run = element("select");
    run.id = caption.htmlFor;
    run.required = true;
    run.append(...jobs.map((job) => { const option = element("option", "", job.title || job.id); option.value = job.id; return option; }));
    form.append(caption, run);
    const selectionLabel = element("label", "", "Selected harness from the run");
    selectionLabel.htmlFor = "profile-import-selection";
    const selection = element("select");
    selection.id = selectionLabel.htmlFor;
    selection.required = true;
    selection.disabled = true;
    form.append(selectionLabel, selection);
    const loadSelections = async () => {
      const jobId = run.value;
      selection.replaceChildren();
      selection.disabled = true;
      submit.disabled = true;
      choices = [];
      try {
        const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
        if (run.value !== jobId) return;
        choices = runHarnessChoices(job);
        fillRunHarnessChoices(selection, choices);
        submit.disabled = !choices.length;
      } catch (error) { toast(error.message, true); }
    };
    run.addEventListener("change", loadSelections);
    const label = profileInput(form, "Candidate name", "run-candidate-label", { required: true });
    const why = profileInput(form, "Why keep this candidate?", "run-candidate-why", { required: true, multiline: true });
    form.append(submit);
    if (run.value) loadSelections();
    return details;
  }
  function runHarnessChoices(job) {
    return (job.tasks || []).flatMap((task) => (task.selections || []).filter((selection) => selection.label).map((selection) => ({
      task_id: task.id, mode: selection.mode === "parallel" ? "agg" : selection.mode === "sequential" ? "seq" : selection.mode,
      label: `${task.id} · ${human(selection.mode)} · ${selection.label}`,
    })));
  }
  function fillRunHarnessChoices(select, choices) {
    select.replaceChildren(...choices.map((choice, index) => {
      const option = element("option", "", choice.label);
      option.value = String(index);
      return option;
    }));
    if (!choices.length) {
      const option = element("option", "", "No completed harness selections available");
      option.value = "";
      select.append(option);
    }
    select.value = choices.length ? "0" : "";
    select.disabled = !choices.length;
  }
  function renderProfileNotes(profile) {
    const section = profileSection("Observations & hypotheses", "Record what happened separately from what you think it means. Evidence labels do not activate a harness.");
    for (const note of profile.notes || []) {
      const article = element("article", "profile-note");
      article.append(evidenceBadge(note.evidence_status), element("p", "", note.text), element("small", "muted", when(note.created_at || note.time)));
      if (note.motivated_by?.length) article.append(element("p", "field-help", `Motivated by: ${note.motivated_by.map((item) => typeof item === "string" ? item : JSON.stringify(item)).join(" · ")}`));
      section.append(article);
    }
    const { form, submit } = profileForm(section, "Keep this note", "notes", () => ({ text: entry.value.trim(), evidence_status: status.value, ...(origin.value.trim() ? { motivated_by: [origin.value.trim()] } : {}) }), "Note added to this profile’s history.");
    const entry = profileInput(form, "A useful observation or unresolved question", "note-text", { required: true, multiline: true });
    const origin = profileInput(form, "Origin or conversation reference (optional)", "note-origin", { placeholder: "Enough to remember why the idea arose" });
    const label = element("label", "", "Evidence status");
    label.htmlFor = "profile-note-status";
    const status = element("select");
    status.id = label.htmlFor;
    for (const [value, title] of [["suspected", "Suspected · a hypothesis to test"], ["observed", "Observed · happened in one run"], ["replicated", "Replicated · repeated under comparable conditions"], ["transferred", "Transferred · helped on a different task"]]) {
      const option = element("option", "", title);
      option.value = value;
      status.append(option);
    }
    form.append(label, status, submit);
    return section;
  }
  function renderProfileHistory(profile) {
    const section = profileSection("History & runs", "An append-only account of changes. Working experiment libraries can evolve without rewriting this archive.");
    const runs = element("div", "profile-associated-runs");
    for (const run of profile.runs || []) {
      const known = (state?.jobs || []).find((job) => job.id === run.job_id);
      if (known) runs.append(profileButton(run.title || known.title || run.job_id, () => selectJob(run.job_id), "text-button"));
      else runs.append(element("p", "field-help", run.title || run.output_dir || run.job_id));
    }
    if (!(profile.runs || []).length) runs.append(element("p", "field-help", "No runs are attached yet. Start an experiment from this profile to record its ancestry."));
    section.append(runs);
    const history = element("ol", "run-timeline profile-history");
    for (const event of [...(profile.events || [])].reverse()) {
      const item = element("li");
      const heading = element("div", "timeline-heading");
      const date = event.created_at || event.time || event.timestamp;
      const time = element("time", "", when(date) || "Time not recorded");
      if (date) time.dateTime = date;
      const action = event.action || event.kind || event.type || event.operation || "Profile change";
      heading.append(element("h4", "", event.title || human(action)), time);
      item.append(heading);
      const detail = event.detail || event.why || event.description;
      if (detail) item.append(element("p", "", detail));
      else if (event.data) {
        const data = event.data;
        let description = data.why || data.text || "";
        if (action === "created") description = `${data.name || profile.name} began this history.`;
        if (action === "configuration_added") description = `Captured ${profile.configurations?.find((configuration) => configuration.id === data.id)?.label || "a configuration"}.`;
        if (action === "revision_added") {
          const revision = profile.revisions?.find((row) => row.id === data.id);
          description = [revision?.label || "Saved harness candidate", revision?.why].filter(Boolean).join(" · ");
        }
        if (action === "activated") description = [description, data.configuration_id ? `Configuration: ${profile.configurations?.find((row) => row.id === data.configuration_id)?.label || data.configuration_id}` : "", data.revision_id ? `Harness: ${revisionLabel(profile, data.revision_id)}` : ""].filter(Boolean).join(" · ");
        if (action === "retired") description = `${revisionLabel(profile, data.revision_id)} retired. ${description}`;
        if (action === "run_recorded") description = data.title || data.output_dir || "Run ancestry recorded.";
        if (description) item.append(element("p", "", description));
      }
      history.append(item);
    }
    section.append(history);
    return section;
  }
  async function createProfile(event) {
    event.preventDefault();
    if (profileBusy || !$("#profile-create-form").reportValidity()) return;
    profileBusy = true;
    $("#profile-create").disabled = true;
    try {
      const profile = cacheProfile(await api("/api/profiles", { method: "POST", body: { name: $("#profile-name").value.trim(), description: $("#profile-description").value.trim() } }));
      $("#profile-create-form").reset();
      profileBusy = false;
      selectProfile(profile.id);
      toast(`${profile.name} has an independent starting point.`);
    } catch (error) { toast(error.message, true); }
    finally { profileBusy = false; $("#profile-create").disabled = false; }
  }
  function renderRunProfileCapture(job, force = false) {
    let wrap = $("#run-profile-capture");
    if (!wrap) {
      wrap = element("div", "run-profile-capture");
      wrap.id = "run-profile-capture";
      $("#job-detail-content").append(wrap);
    }
    const profiles = state?.profiles || [];
    const signature = JSON.stringify([job.id, job.status, profiles, job.profile || job.profile_plan || job.profile_binding || job.params, job.capability_catalog, job.conversation_id, job.conversation]);
    if (!force && signature === runProfileSignature) return;
    runProfileSignature = signature;
    wrap.replaceChildren();
    const planned = job.kind === "preview";
    const binding = planned ? job.profile_plan : job.profile || job.profile_binding || job.profile_snapshot;
    if (binding || job.params?.profile_id) {
      const info = element("div", "profile-run-binding");
      const profileId = binding?.profile_id || job.params?.profile_id;
      const name = profiles.find((profile) => profile.id === profileId)?.name || binding?.profile_name || "Profile";
      const configurationId = binding?.configuration_id || job.params?.profile_configuration_id || "not recorded";
      const revisionId = binding?.revision_id || job.params?.profile_revision_id || "not recorded";
      const explanation = planned ? "The plan uses these choices. Starting an experiment records its own immutable snapshot." : binding ? "Later profile changes do not alter this run." : "These profile identifiers were saved in the run’s parameters.";
      info.append(element("h3", "", `${name} · ${planned ? "planned" : "recorded"} starting point`), element("p", "field-help", `Configuration ${configurationId} · harness ${revisionId}. ${explanation}`));
      if (!planned && job.capability_catalog) {
        const catalog = job.capability_catalog;
        info.append(element("p", "field-help", catalog.exposed ? `Catalog references: ${count(catalog.entry_count)} (availability recorded; use unconfirmed).` : "Catalog not exposed for this run."));
        if (catalog.exposed && catalog.entry_ids?.length) {
          const references = element("details", "profile-evidence");
          references.append(element("summary", "", "Inspect the recorded references"));
          for (const id of catalog.entry_ids) {
            const entry = capabilityEntries().find((item) => item.id === id);
            references.append(capabilityButton(entry?.name || id, () => { showView("capabilities"); selectCapability(id); }, "text-button capability-run-reference"));
          }
          info.append(references);
        }
      }
      if (profileId) info.append(profileButton("Open profile ancestry →", () => selectProfile(profileId), "text-button"));
      if (job.conversation_id) info.append(conversationButton("Open this run’s conversation →", () => selectConversation(job.conversation_id), "text-button"), element("p", "field-help", "Human contact is recorded as context, not proof of benefit."));
      if (job.conversation) {
        const confirmed = Number(job.conversation.human_messages_delivered) || 0;
        info.append(element("p", "field-help", `${counted(confirmed, "human message")} confirmed delivered to the native session.`));
        const unconfirmed = Number(job.conversation.human_delivery_unconfirmed) || 0;
        if (unconfirmed || (job.conversation.human_contact_possible && !confirmed)) info.append(element("p", "notice", `${unconfirmed ? counted(unconfirmed, "message delivery", "message deliveries") : "Message delivery"} unconfirmed. Human contact may have occurred; this run cannot be treated as having no human contact.`));
      }
      wrap.append(info);
    }
    if (job.kind !== "benchmark" || !["succeeded", "partial"].includes(job.status) || !profiles.length) return;
    const details = element("details", "profile-toolbox");
    details.append(element("summary", "", "Save this run’s harness to a profile"), element("p", "field-help", "Creates a candidate with recorded evidence. Review and activate it separately; completion alone does not prove improvement."));
    const form = element("form", "profile-form");
    const caption = element("label", "", "Destination profile");
    caption.htmlFor = "run-profile-destination";
    const destination = element("select");
    destination.id = caption.htmlFor;
    destination.append(...profiles.map((profile) => { const option = element("option", "", profile.name); option.value = profile.id; return option; }));
    destination.value = profiles.some((profile) => profile.id === job.params?.profile_id) ? job.params.profile_id : profiles[0].id;
    form.append(caption, destination);
    const choices = runHarnessChoices(job);
    const selectionLabel = element("label", "", "Selected harness to keep");
    selectionLabel.htmlFor = "run-profile-selection";
    const selection = element("select");
    selection.id = selectionLabel.htmlFor;
    selection.required = true;
    fillRunHarnessChoices(selection, choices);
    form.append(selectionLabel, selection);
    const label = profileInput(form, "Candidate name", "result-label", { required: true, placeholder: "What changed in this run?" });
    const why = profileInput(form, "Why keep it?", "result-why", { required: true, multiline: true });
    const submit = element("button", "button secondary small profile-submit", "Save as a candidate");
    submit.type = "submit";
    submit.disabled = !choices.length;
    form.append(submit);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const choice = choices[Number(selection.value)];
      if (form.reportValidity() && choice) profileAction(destination.value, "from-run", { job_id: job.id, label: label.value.trim(), why: why.value.trim(), task_id: choice.task_id, mode: choice.mode }, "Harness candidate saved. Open Profiles to review its ancestry.");
    });
    details.append(form);
    wrap.append(details);
  }
  function conversationFocused(id) {
    return view === "conversations" && selectedConversationId === id && document.visibilityState !== "hidden" && typeof document.hasFocus === "function" && document.hasFocus();
  }
  function browserNotifications() { return typeof window.Notification === "function" ? window.Notification : null; }
  function renderConversationAttention() {
    const notifications = state?.notifications || { enabled: false, unread_count: 0 };
    const unread = Math.max(0, Number(notifications.unread_count) || 0);
    text("#nav-conversation-unread", count(unread));
    $("#nav-conversation-unread").hidden = unread === 0;
    $("#nav-conversation-unread").setAttribute("aria-label", `${counted(unread, "unread model message")}`);
    const apiType = browserNotifications();
    const permission = apiType?.permission || "unavailable";
    const wrap = $("#conversation-notifications");
    const copy = element("div");
    copy.append(element("strong", "", notifications.enabled ? "Desktop notifications are enabled." : "Desktop notifications are off."));
    copy.append(element("p", "field-help", !apiType ? "This browser does not offer desktop notifications. Unread messages still appear in the sidebar and conversation list." : permission === "denied" ? "This browser blocks notifications for this address. Change its site permissions to allow them; unread counts remain available." : "When enabled, new model-initiated posts can notify you while this conversation is not focused. Replies, your messages, delivery changes, and working records do not trigger alerts."));
    const details = element("details", "conversation-notification-details");
    details.id = "conversation-notification-details";
    details.open = document.getElementById(details.id)?.open || false;
    details.append(element("summary", "", "Notification details"), element("p", "field-help", "Browser permission and your operating system’s notification settings both apply. A notification attempt does not confirm that it was displayed or read."), element("p", "field-help", "An alert may show a brief excerpt of the visible model message outside Studio."));
    copy.append(details);
    if (notifications.enabled && permission === "default") copy.append(element("p", "field-help", "This browser address still needs permission. Studio may use a different local address after a restart; permission is specific to the browser and address."));
    if (notificationError) copy.append(element("p", "error-text", notificationError));
    const actions = element("div", "conversation-notification-actions");
    if (notifications.enabled) {
      const off = conversationButton("Turn notifications off", () => setConversationNotifications(false));
      off.disabled = notificationBusy;
      actions.append(off);
      if (apiType && permission === "default") {
        const allow = conversationButton("Allow in this browser", () => setConversationNotifications(true));
        allow.disabled = notificationBusy;
        actions.append(allow);
      }
    } else {
      const on = conversationButton("Enable desktop notifications", () => setConversationNotifications(true));
      on.disabled = notificationBusy || !apiType || permission === "denied";
      actions.append(on);
    }
    if (!apiType || permission === "denied") actions.append(conversationButton("Open Studio in my browser", openStudioBrowser));
    wrap.replaceChildren(copy, actions);
    processConversationNotifications();
  }
  async function openStudioBrowser() {
    try {
      const result = await api("/api/open-browser", { method: "POST", body: {} });
      toast(result.opened ? "Studio opened in your default browser. Notification permission is still your choice." : "The browser could not be opened. Reopen the Studio launch link in your browser.", !result.opened);
    } catch (error) { toast(error.message, true); }
  }
  async function setConversationNotifications(enabled) {
    if (notificationBusy) return;
    notificationBusy = true;
    notificationError = "";
    try {
      if (enabled) {
        const apiType = browserNotifications();
        if (!apiType) throw new Error("This browser does not offer desktop notifications. Unread counts remain available.");
        const permission = apiType.permission === "granted" ? "granted" : await apiType.requestPermission();
        if (permission !== "granted") throw new Error("Desktop notification permission was not granted. Unread messages remain available in Studio.");
      }
      const result = await api("/api/notifications/settings", { method: "POST", body: { enabled } });
      if (state) state.notifications = { ...state.notifications, ...(result.notifications || result) };
    } catch (error) { notificationError = error.message; }
    finally { notificationBusy = false; renderConversationAttention(); }
  }
  function processConversationNotifications() {
    if (!state?.notifications) return;
    const firstPoll = !notificationPrimed;
    notificationPrimed = true;
    for (const conversation of state.conversations || []) {
      for (const item of conversation.attention_messages || []) {
        const key = `${conversation.id}/${item.id}`;
        if (attentionSeenIds.has(key)) continue;
        attentionSeenIds.add(key);
        if (firstPoll || !state.notifications.enabled || item.role !== "assistant" || item.origin !== "model_initiated" || item.duplicate_of || conversationFocused(conversation.id)) continue;
        showConversationNotification(conversation, item);
      }
    }
  }
  async function showConversationNotification(conversation, item) {
    const apiType = browserNotifications();
    if (!apiType || apiType.permission !== "granted") return;
    try {
      const claim = await api("/api/notifications/claim", { method: "POST", body: { conversation_id: conversation.id, message_id: item.id } });
      if (!claim.claimed || !state?.notifications?.enabled || apiType.permission !== "granted" || conversationFocused(conversation.id)) return;
      const name = state.profiles?.find((profile) => profile.id === conversation.profile_id)?.name || conversation.profile_id;
      const notification = new apiType(`${name} — ${conversation.title || "Conversation"}`, { body: item.text || "A new model message is available.", tag: `harness2-${conversation.id}-${item.id}` });
      notification.onclick = () => {
        try { window.focus(); } catch (_) { /* Embedded browsers may control foreground focus. */ }
        selectConversation(conversation.id);
        notification.close();
      };
    } catch (_) {
      notificationError = "A desktop notification could not be shown. The unread message remains available in Studio.";
    }
  }
  function visibleAssistantIds(conversation) {
    if (!conversationFocused(conversation?.id)) return [];
    const unread = new Set(conversation.unread_message_ids || []);
    if (!unread.size) return [];
    const pane = $("#conversation-messages");
    if (typeof pane.getBoundingClientRect !== "function") return [];
    const area = pane.getBoundingClientRect();
    const height = window.innerHeight || document.documentElement?.clientHeight || 0;
    const width = window.innerWidth || document.documentElement?.clientWidth || 0;
    const confirmed = readConfirmedIds.get(conversation.id) || new Set();
    const groups = conversationMessageGroups(conversation);
    return (conversation.messages || []).filter((item) => item.role === "assistant" && unread.has(item.id) && !confirmed.has(item.id)).filter((item) => {
      const node = document.getElementById(`conversation-message-${groups.canonical.get(item.id) || item.id}`);
      if (typeof node?.getBoundingClientRect !== "function") return false;
      const box = node.getBoundingClientRect();
      return box.width > 0 && box.height > 0 && box.bottom > Math.max(0, area.top) && box.top < Math.min(height, area.bottom) && box.right > Math.max(0, area.left) && box.left < Math.min(width, area.right);
    }).map((item) => item.id);
  }
  async function markVisibleConversationRead() {
    const conversation = conversationCache.get(selectedConversationId);
    if (!conversation || readInFlight.has(conversation.id)) return;
    const ids = visibleAssistantIds(conversation).slice(0, 128);
    if (!ids.length) return;
    readInFlight.add(conversation.id);
    const version = (conversationVersions.get(conversation.id) || 0) + 1;
    conversationVersions.set(conversation.id, version);
    try {
      const result = await api(`/api/conversations/${encodeURIComponent(conversation.id)}/read`, { method: "POST", body: { message_ids: ids } });
      const confirmed = readConfirmedIds.get(conversation.id) || new Set();
      ids.forEach((id) => confirmed.add(id));
      readConfirmedIds.set(conversation.id, confirmed);
      if ((conversationVersions.get(conversation.id) || 0) === version) {
        conversationVersions.set(conversation.id, version + 1);
        cacheConversation(result);
      } else {
        const current = conversationCache.get(conversation.id);
        if (current && Array.isArray(current.unread_message_ids)) {
          const read = new Set(ids);
          const remaining = current.unread_message_ids.filter((id) => !read.has(id));
          cacheConversation({ ...current, unread_message_ids: remaining, unread_count: remaining.length,
            attention_messages: (current.attention_messages || []).filter((item) => !read.has(item.id)) });
        }
      }
      conversationListSignature = "";
      renderConversationAttention();
      renderConversations();
    } catch (_) { /* Preserve unread state after an unconfirmed read request. */ }
    finally { readInFlight.delete(conversation.id); }
  }

  function conversationOptionAllowed() {
    if (state.features?.conversation_runs === false) return false;
    return Boolean($("#experiment-profile").value && experimentProfile() && experimentSettings()?.provider === "local" && $("#benchmark").value === "jb" && $("#substrate").value === "oc" && Number($("#workers").value) === 1);
  }
  function renderConversationOption() {
    let checkbox = $("#experiment-conversation");
    if (!checkbox) {
      const control = element("div", "experiment-conversation-control");
      const label = element("label", "profile-checkbox");
      checkbox = element("input");
      checkbox.id = "experiment-conversation";
      checkbox.type = "checkbox";
      label.append(checkbox, element("span", "", "Conversation during work"));
      const help = element("p", "field-help");
      help.id = "experiment-conversation-help";
      control.append(label, help);
      $("#experiment-profile-control").append(control);
    }
    checkbox.disabled = !conversationOptionAllowed();
    if (checkbox.disabled) checkbox.checked = false;
    text("#experiment-conversation-help", state.features?.conversation_runs === false ? "Conversation during work currently requires Windows process containment. Saved conversations and standalone profile chat remain available." : checkbox.disabled ? "Choose a local profile, JobBench with OpenCode, and one domain worker to enable a conversation." : "Optional and off by default. Messages go to the active native worker during this run. Human contact is recorded, without assuming that it helps.");
  }
  function conversationButton(label, action, className = "button secondary small") {
    const button = element("button", className, label);
    button.type = "button";
    button.disabled = conversationBusy;
    button.addEventListener("click", action);
    return button;
  }
  function cacheConversation(result) {
    const conversation = result.conversation || result;
    if (!conversation?.id) throw new Error("This conversation could not be read.");
    conversationCache.set(conversation.id, conversation);
    if (state) {
      const previous = state.conversations?.find((item) => item.id === conversation.id) || {};
      const unread = conversation.unread_count ?? previous.unread_count ?? 0;
      const remaining = Array.isArray(conversation.unread_message_ids) ? new Set(conversation.unread_message_ids) : null;
      const attention = conversation.attention_messages || (previous.attention_messages || []).filter((item) => !remaining || remaining.has(item.id));
      state.conversations = [{ ...previous, id: conversation.id, profile_id: conversation.profile_id, title: conversation.title, run_id: conversation.run_id, status: conversation.status, active_worker_id: conversation.active_worker_id, message_count: conversation.messages?.length || 0, pending_count: conversation.pending_count ?? (conversation.messages || []).filter((item) => ["queued", "delivered"].includes(item.status)).length, unread_count: unread, attention_messages: attention, runtime: conversation.runtime }, ...(state.conversations || []).filter((item) => item.id !== conversation.id)];
      if (state.notifications) state.notifications.unread_count = Math.max(0, (Number(state.notifications.unread_count) || 0) - (Number(previous.unread_count) || 0) + Number(unread));
    }
    return conversation;
  }
  function selectConversation(id) {
    if (selectedConversationId) conversationDrafts.set(selectedConversationId, $("#conversation-draft").value);
    selectedConversationId = id;
    remember("harness2_selected_conversation", id);
    $("#conversation-draft").value = conversationDrafts.get(id) || "";
    conversationDetailSignature = "";
    showView("conversations");
  }
  async function loadConversation() {
    const id = selectedConversationId;
    if (view !== "conversations" || !id || conversationLoading === id || appClosed) return;
    conversationLoading = id;
    const version = conversationVersions.get(id) || 0;
    try {
      const result = await api(`/api/conversations/${encodeURIComponent(id)}`);
      if ((conversationVersions.get(id) || 0) !== version) return;
      conversationVersions.set(id, version + 1);
      const conversation = cacheConversation(result);
      if (selectedConversationId === id && view === "conversations") renderConversationDetail(conversation);
    } catch (error) {
      if (selectedConversationId === id && view === "conversations") text("#conversation-runtime", error.message);
    } finally { if (conversationLoading === id) conversationLoading = false; }
  }
  async function conversationAction(id, operation, body = {}) {
    if (conversationBusy) return null;
    conversationVersions.set(id, (conversationVersions.get(id) || 0) + 1);
    conversationBusy = true;
    updateConversationComposer(conversationCache.get(selectedConversationId));
    try {
      const result = await api(`/api/conversations/${encodeURIComponent(id)}/${operation}`, { method: "POST", body });
      conversationVersions.set(id, (conversationVersions.get(id) || 0) + 1);
      const conversation = cacheConversation(result);
      conversationDetailSignature = "";
      renderConversations();
      return conversation;
    } catch (error) { toast(error.message, true); return null; }
    finally {
      conversationBusy = false;
      conversationDetailSignature = "";
      renderConversations();
    }
  }
  async function createConversation(profileId, { start = false, configurationId, revisionId, title } = {}) {
    if (conversationBusy || !profileId) return null;
    conversationBusy = true;
    $("#conversation-create").disabled = true;
    let conversation;
    try {
      conversation = cacheConversation(await api("/api/conversations", { method: "POST", body: { profile_id: profileId, ...(configurationId ? { configuration_id: configurationId } : {}), ...(revisionId ? { revision_id: revisionId } : {}), ...(title ? { title } : {}) } }));
      selectConversation(conversation.id);
    } catch (error) { toast(error.message, true); }
    finally { conversationBusy = false; $("#conversation-create").disabled = false; }
    if (conversation && start) await conversationAction(conversation.id, "start");
    else renderConversations();
    return conversation || null;
  }
  function talkToProfile(profile) {
    return createConversation(profile.id, { start: true, configurationId: profile.active_configuration_id, revisionId: profile.active_revision_id });
  }
  function activeConversationWorker(conversation) {
    return conversation?.status === "open" ? conversation.workers?.find((worker) => worker.id === conversation.active_worker_id && worker.status === "active") : null;
  }
  function updateConversationComposer(conversation) {
    const allowed = Boolean(activeConversationWorker(conversation)) && conversation?.runtime?.can_message !== false && !conversationBusy && !appClosed;
    $("#conversation-draft").disabled = !allowed;
    $("#conversation-send").disabled = !allowed;
    text("#conversation-compose-help", !conversation ? "Choose a conversation to begin." : allowed ? "Delivered to the active worker in its native context. Ctrl+Enter sends; Enter starts a new line." : conversationBusy ? "Saving this request… Your draft stays here." : "No active worker is available. Start or continue the conversation explicitly; a saved draft stays here.");
  }
  async function sendConversationMessage(event) {
    event?.preventDefault();
    const id = selectedConversationId;
    const conversation = conversationCache.get(id);
    const draft = $("#conversation-draft").value;
    if (!activeConversationWorker(conversation) || conversation?.runtime?.can_message === false || conversationBusy || !draft.trim()) return;
    conversationDrafts.set(id, draft);
    const result = await conversationAction(id, "messages", { text: draft });
    if (result && conversationDrafts.get(id) === draft) {
      conversationDrafts.set(id, "");
      if (selectedConversationId === id && $("#conversation-draft").value === draft) $("#conversation-draft").value = "";
    }
  }
  function renderConversations() {
    if (!state || view !== "conversations") return;
    const conversations = state.conversations || [];
    const profiles = state.profiles || [];
    const signature = JSON.stringify([conversations, profiles, selectedConversationId]);
    if (signature !== conversationListSignature) {
      conversationListSignature = signature;
      const list = $("#conversation-list");
      list.replaceChildren(...conversations.map((conversation) => {
        const button = conversationButton(conversation.title, () => selectConversation(conversation.id), `conversation-list-item${selectedConversationId === conversation.id ? " selected" : ""}`);
        const name = profiles.find((profile) => profile.id === conversation.profile_id)?.name || conversation.profile_id;
        button.replaceChildren(element("strong", "", conversation.title || "Conversation"), element("small", "", `${name} · ${counted(conversation.message_count || 0, "message")}`), element("small", "", conversation.pending_count ? `${count(conversation.pending_count)} queued · ${human(conversation.runtime?.status || conversation.status)}` : human(conversation.runtime?.status || conversation.status)));
        if (conversation.unread_count) button.append(element("span", "conversation-unread-pill", `${count(conversation.unread_count)} unread`));
        if (selectedConversationId === conversation.id) button.setAttribute("aria-current", "true");
        return button;
      }));
      if (!conversations.length) list.append(element("p", "field-help", "Conversations will stay here after the model is unloaded."));
      const select = $("#conversation-profile");
      const previous = select.value;
      select.replaceChildren(...profiles.map((profile) => { const option = element("option", "", profile.name); option.value = profile.id; return option; }));
      select.value = profiles.some((profile) => profile.id === previous) ? previous : profiles[0]?.id || "";
      $("#conversation-create").disabled = !profiles.length || conversationBusy;
    }
    const conversation = conversationCache.get(selectedConversationId);
    if (conversation) renderConversationDetail(conversation);
    else {
      $("#conversation-heading").replaceChildren(empty("A little room to talk.", "Choose a conversation or create one. Opening this screen does not load a model.", true));
      $("#conversation-messages").replaceChildren();
      $("#conversation-runtime").replaceChildren();
      $("#conversation-recovery").replaceChildren(element("p", "field-help", "No working context selected."));
      $("#conversation-decisions").replaceChildren();
      updateConversationComposer(null);
    }
  }
  function conversationMessageGroups(conversation) {
    const messages = conversation.messages || [];
    const byId = new Map(messages.map((item) => [item.id, item]));
    const canonical = new Map();
    const duplicates = new Map();
    for (const item of messages) {
      const target = byId.get(item.duplicate_of);
      // Only the server's verified pairing folds records. Missing or invalid
      // targets remain visible rather than hiding an unpaired message.
      if (item.role !== "assistant" || target?.role !== "assistant" || target.id === item.id || target.duplicate_of) continue;
      canonical.set(item.id, target.id);
      if (!duplicates.has(target.id)) duplicates.set(target.id, []);
      duplicates.get(target.id).push(item);
    }
    return { messages: messages.filter((item) => !canonical.has(item.id)), canonical, duplicates };
  }
  function conversationMessageLabel(item) {
    const labels = { queued: "Queued", delivered: "Delivered to worker", answered: "Answered", undelivered: "Not delivered", failed: "Failed" };
    if (item.role !== "assistant" || item.status !== "answered") return labels[item.status] || human(item.status || "recorded");
    if (item.reply_to?.length) return "Reply";
    if (item.origin === "reply") return "Reply update";
    if (item.origin === "model_initiated") return "Model-initiated message";
    return "Session message";
  }
  function focusConversationMessage(conversation, id) {
    const canonical = conversationMessageGroups(conversation).canonical.get(id);
    if (canonical) {
      const disclosure = document.getElementById(`conversation-duplicates-${canonical}`);
      if (disclosure) disclosure.open = true;
    }
    const node = document.getElementById(`conversation-message-${id}`);
    node?.scrollIntoView({ behavior: "smooth", block: "center" });
    node?.focus({ preventScroll: true });
  }
  function renderConversationDetail(conversation) {
    if (conversation.id !== selectedConversationId) return;
    updateConversationComposer(conversation);
    const runId = conversation.runtime?.job_id || conversation.run_id;
    const run = (state?.jobs || []).find((job) => job.id === runId);
    const signature = JSON.stringify([conversation, run?.status, conversationBusy]);
    if (signature === conversationDetailSignature) return;
    conversationDetailSignature = signature;
    const worker = activeConversationWorker(conversation);
    const runtime = conversation.runtime || { status: "offline" };
    const heading = element("header", "conversation-heading");
    const name = (state?.profiles || []).find((profile) => profile.id === conversation.profile_id)?.name || conversation.profile_id;
    const title = element("div");
    title.append(element("span", "eyebrow", `${name} / VISIBLE CONVERSATION`), element("h2", "", conversation.title || "Conversation"), element("p", "field-help", `Configuration ${conversation.configuration_id || "unrecorded"} · harness ${conversation.revision_id || "unrecorded"}`));
    const actions = element("div", "conversation-actions");
    const runActive = runtime.mode === "run" || Boolean(run && isActive(run.status));
    if (runId) actions.append(conversationButton(runActive ? "Follow run / stop work ↗" : "Open associated run ↗", () => selectJob(runId)));
    if (!worker && !runActive && runtime.status !== "loading" && conversation.status === "open") {
      const start = conversationButton((conversation.workers || []).length ? "Continue chat · load model" : "Start conversation · load model", () => conversationAction(conversation.id, "start"), "button primary small");
      start.disabled = conversationBusy || Boolean(activeJob()) || runtime.can_start === false;
      actions.append(start);
    }
    if (!runActive && runtime.can_stop !== false && (worker || runtime.status === "loading" || runtime.status === "ready" || runtime.status === "running")) actions.append(conversationButton(runtime.owned ? "Stop chat / unload model" : "Stop chat", () => conversationAction(conversation.id, "stop")));
    heading.append(title, actions);
    $("#conversation-heading").replaceChildren(heading);
    const info = element("div");
    info.append(element("strong", "", worker ? `Working context: ${worker.label || worker.id}` : runActive ? "Waiting for the run’s next working context." : runtime.status === "loading" ? "Loading the conversation’s model…" : "No active working context."), element("p", "field-help", worker ? `Native OpenCode session: ${worker.session_id || "not yet recorded"}. Messages go to this worker; stages may switch contexts.` : runActive ? "The run is between active workers. Follow the run to stop the whole operation." : (conversation.workers || []).length ? "Continue explicitly reloads the recorded model and resumes its retained native session. Visiting this conversation does not load a model." : "Start explicitly loads the recorded model and opens its native session. Visiting this conversation does not load a model."));
    info.append(element("small", "", `Runtime: ${human(runtime.status)}${runtime.owned === true ? " · owned local runtime" : runtime.owned === false ? " · external runtime" : ""}`));
    if (runtime.error) info.append(element("p", "error-text", runtime.error));
    if ((conversation.workers || []).length) {
      const contexts = element("details", "conversation-workers");
      contexts.append(element("summary", "", "Working contexts and switches"));
      for (const item of conversation.workers) contexts.append(element("p", "field-help", `${item.label || item.id} · ${item.id === conversation.active_worker_id ? "current" : human(item.status)} · native session ${item.session_id || "unrecorded"}${item.reason ? ` · ${item.reason}` : ""}`));
      info.append(contexts);
    }
    $("#conversation-runtime").replaceChildren(info);
    const messages = $("#conversation-messages");
    const priorScroll = messages.scrollTop;
    const follow = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 80;
    const groups = conversationMessageGroups(conversation);
    function renderMessage(item, folded = false) {
      const article = element("article", `conversation-message ${item.role === "user" ? "from-user" : "from-model"}`);
      article.id = `conversation-message-${item.id}`;
      article.tabIndex = -1;
      const meta = element("div", "conversation-message-meta");
      const source = conversation.workers?.find((candidate) => candidate.id === item.worker_id);
      const labels = { queued: "Queued", delivered: "Delivered to worker", answered: "Answered", undelivered: "Not delivered", failed: "Failed" };
      const deliveryLabel = conversationMessageLabel(item);
      meta.append(element("strong", "", item.role === "user" ? "You" : name), element("span", `conversation-delivery delivery-${Object.hasOwn(labels, item.status) ? item.status : "unknown"}`, deliveryLabel));
      article.append(meta, element("p", "conversation-visible-text", item.text), element("small", "", [source?.label || item.worker_id, when(item.created_at)].filter(Boolean).join(" · ")));
      if (item.reason) article.append(element("p", "conversation-delivery-reason", item.reason));
      if (folded) article.append(element("small", "", `Record ${item.id} · same reply as ${item.duplicate_of}`));
      return article;
    }
    messages.replaceChildren(...groups.messages.map((item) => {
      const article = renderMessage(item);
      const duplicates = groups.duplicates.get(item.id) || [];
      if (duplicates.length) {
        const disclosure = element("details", "conversation-workers");
        disclosure.id = `conversation-duplicates-${item.id}`;
        disclosure.open = document.getElementById(disclosure.id)?.open || false;
        disclosure.append(element("summary", "", `Matching session ${duplicates.length === 1 ? "record" : "records"} · ${count(duplicates.length)}`), element("p", "field-help", "This reply was also recorded through a session tool. Both records remain in the archive."), ...duplicates.map((duplicate) => renderMessage(duplicate, true)));
        article.append(disclosure);
      }
      return article;
    }));
    if (!(conversation.messages || []).length) messages.append(element("p", "conversation-first-message", worker ? "The session is ready. Say hello, or ask what it is working on." : "Your visible conversation will appear here."));
    messages.scrollTop = follow ? messages.scrollHeight : priorScroll;
    renderConversationRecovery(conversation);
    markVisibleConversationRead();
  }
  function renderConversationRecovery(conversation) {
    const recovery = conversation.recovery || {};
    const wrap = $("#conversation-recovery");
    wrap.replaceChildren();
    if (!recovery.objective && !recovery.id) wrap.append(element("p", "field-help", "No recovery checkpoint has been recorded."));
    else {
      wrap.append(element("h3", "", "Recorded objective"), element("p", "conversation-visible-text", recovery.objective || "Not recorded."));
      for (const [key, label] of [["constraints", "Constraints"], ["completed", "Reported completed · not independently verified"], ["planned", "Planned work"], ["open_questions", "Open questions"]]) {
        const group = element("section", "conversation-recovery-group");
        group.append(element("h4", "", label));
        const values = recovery[key] || [];
        if (!values.length) group.append(element("p", "field-help", "None recorded."));
        else for (const item of values) {
          group.append(element("p", "conversation-visible-text", typeof item === "string" ? item : item.text));
          if (item.references?.length) group.append(element("pre", "profile-snapshot", JSON.stringify(item.references, null, 2)));
        }
        wrap.append(group);
      }
      if (recovery.decision_ids?.length) wrap.append(element("p", "field-help", `Recovery decision IDs: ${recovery.decision_ids.join(", ")}`));
    }
    const decisions = $("#conversation-decisions");
    decisions.replaceChildren(element("h3", "", "Consequential decisions"), element("p", "field-help", "A declared connection from conversation to a working decision. This does not establish a benefit or adopt a technical harness."));
    for (const decision of conversation.decisions || []) {
      const article = element("article", "conversation-decision");
      article.append(element("span", "soft-tag", human(decision.status)), element("p", "conversation-visible-text", decision.text), element("p", "field-help", decision.why));
      for (const id of decision.source_message_ids || []) article.append(conversationButton(`Source message ${id.slice(0, 8)} ↗`, () => focusConversationMessage(conversation, id), "text-button"));
      if (decision.supersedes_id) article.append(element("small", "", `Revises or reverts decision ${decision.supersedes_id}`));
      decisions.append(article);
    }
    if (!(conversation.decisions || []).length) decisions.append(element("p", "field-help", "No consequential decisions have been recorded. Casual conversation can stay casual."));
  }

  function capabilityState() { return state?.capabilities || { enabled: false, profile_access: {}, entries: [] }; }
  function capabilityEntries() { return Array.isArray(capabilityState().entries) ? capabilityState().entries : []; }
  function capabilityParents(entry) {
    return (entry.derived_from || []).map((item) => typeof item === "string" ? item : item?.capability_id || item?.id).filter(Boolean);
  }
  function capabilityButton(label, action, className = "button secondary small") {
    const button = element("button", className, label);
    button.type = "button";
    button.disabled = capabilityBusy;
    button.addEventListener("click", action);
    return button;
  }
  function capabilityField(form, label, name, { multiline = false, required = false, numeric = false } = {}) {
    const caption = element("label", "", label);
    const input = element(multiline ? "textarea" : "input");
    input.id = `capability-${name}`;
    input.name = name;
    input.required = required;
    caption.htmlFor = input.id;
    if (multiline) { input.rows = 3; input.maxLength = 4000; }
    else if (numeric) { input.type = "number"; input.min = "0"; input.step = "any"; }
    else input.maxLength = 200;
    form.append(caption, input);
    return input;
  }
  function capabilitySelect(form, label, name, choices) {
    const caption = element("label", "", label);
    const select = element("select");
    select.id = `capability-${name}`;
    select.name = name;
    caption.htmlFor = select.id;
    select.append(...choices.map(([value, title]) => { const option = element("option", "", title); option.value = value; return option; }));
    form.append(caption, select);
    return select;
  }
  function capabilityLines(input) { return input.value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean); }
  // Conservative Unicode matching may request consent for additional paths;
  // the archive still performs authoritative casefold collision validation.
  function capabilityPathKey(path) { return String(path).normalize("NFKD").toUpperCase().toLowerCase(); }
  async function capabilityAction(operation, body, success) {
    if (capabilityBusy) return null;
    capabilityBusy = true;
    $$("#view-capabilities button").forEach((button) => { button.disabled = true; });
    try {
      const result = await api(`/api/capabilities/${operation}`, { method: "POST", body });
      if (result.profile) cacheProfile(result.profile);
      if (state) state.capabilities = result.catalog || result;
      capabilitySignature = "";
      capabilityDetailSignature = "";
      capabilityBusy = false;
      renderCapabilities();
      renderProfiles();
      renderExperimentProfile();
      toast(success);
      return result;
    } catch (error) { toast(error.message, true); return null; }
    finally {
      capabilityBusy = false;
      $$("#view-capabilities button").forEach((button) => { button.disabled = false; });
    }
  }
  function selectCapability(id) {
    selectedCapabilityId = id;
    capabilitySignature = "";
    capabilityDetailSignature = "";
    renderCapabilities();
    loadCapability(id);
  }
  async function loadCapability(id) {
    if (!id || capabilityLoadingId === id) return;
    capabilityLoadingId = id;
    try {
      const result = await api(`/api/capabilities/${encodeURIComponent(id)}`);
      const entry = result.entry || result;
      capabilityCache.set(id, entry);
      if (selectedCapabilityId === id) {
        capabilityDetailSignature = "";
        renderCapabilityDetail(entry);
      }
    } catch (error) {
      if (selectedCapabilityId === id) $("#capability-detail").replaceChildren(empty("This capability is unavailable.", error.message, true));
      toast(error.message, true);
    } finally { if (capabilityLoadingId === id) capabilityLoadingId = null; }
  }
  function renderCapabilities() {
    if (!state || view !== "capabilities") return;
    const catalog = capabilityState();
    const profiles = state.profiles || [];
    const entries = capabilityEntries();
    const enabled = catalog.enabled === true;
    const signature = JSON.stringify([catalog, profiles]);
    if (capabilitySignature !== signature) {
      capabilitySignature = signature;
      const status = $("#capability-status");
      const copy = element("div");
      copy.append(element("span", "eyebrow", enabled ? "AVAILABLE WHEN A PROFILE OPTS IN" : "BUILT, AND RESTING FOR NOW"), element("h2", "", enabled ? "The catalog is enabled." : "The catalog is off."));
      copy.append(element("p", "", enabled ? "Profiles keep their own harnesses. Catalog access is a separate choice for each profile; importing creates a candidate for testing." : "New experiments receive no catalog entries, files, or references. You can inspect saved entries here. Existing recorded run snapshots stay unchanged. Enable the catalog when there is something useful to exchange."));
      const toggle = capabilityButton(enabled ? "Turn catalog off" : "Enable catalog", () => capabilityAction("settings", { enabled: !enabled }, enabled ? "Catalog off. Future experiments receive no catalog reference." : "Catalog enabled. Only profiles with explicit access receive references."), enabled ? "button secondary" : "button primary");
      status.replaceChildren(copy, toggle);
      status.classList.toggle("enabled", enabled);
      $("#capability-access-panel").hidden = !enabled;
      $("#capability-publish-panel").hidden = !enabled;
      const access = $("#capability-profile-access");
      access.replaceChildren(...profiles.map((profile) => {
        const row = element("div", "capability-access-row");
        const hasAccess = catalog.profile_access?.[profile.id] === true;
        const info = element("div");
        info.append(element("strong", "", profile.name), element("small", "", hasAccess ? "Reference access for future experiments" : "Access off · independent development"));
        row.append(info, capabilityButton(hasAccess ? "Turn access off" : "Allow reference access", () => capabilityAction("access", { profile_id: profile.id, enabled: !hasAccess }, `${profile.name}: catalog access ${hasAccess ? "off" : "on"}.`)));
        return row;
      }));
      if (!profiles.length) access.append(element("p", "field-help", "Create a profile first. Access starts off for each new profile."));
      if (enabled) renderCapabilityPublisher(profiles);
    }
    renderCapabilityList();
    if (!selectedCapabilityId || !entries.some((entry) => entry.id === selectedCapabilityId)) {
      selectedCapabilityId = null;
      $("#capability-detail").replaceChildren(empty(entries.length ? "A closer look at an idea." : "Room for something useful.", entries.length ? "Choose an entry to inspect its files, ancestry, and evidence." : enabled ? "Publish selected files from a profile when there is an idea worth testing elsewhere. Nothing is added automatically." : "The space is ready for later. No example capabilities have been added. Catalog access remains inactive while it is off.", true));
    } else if (capabilityCache.has(selectedCapabilityId)) renderCapabilityDetail(capabilityCache.get(selectedCapabilityId));
    else {
      $("#capability-detail").replaceChildren(empty("Reading this capability…", "Its saved source and provenance will appear here.", true));
      loadCapability(selectedCapabilityId);
    }
  }
  function renderCapabilityList() {
    const query = $("#capability-search").value.trim().toLocaleLowerCase();
    const entries = capabilityEntries().filter((entry) => [entry.name, entry.description, entry.why, entry.origin?.profile_name, entry.kind].join(" ").toLocaleLowerCase().includes(query));
    const list = $("#capability-list");
    list.replaceChildren(...entries.map((entry) => {
      const button = capabilityButton(entry.name, () => selectCapability(entry.id), `capability-list-item${entry.id === selectedCapabilityId ? " selected" : ""}`);
      button.replaceChildren(element("strong", "", entry.name), element("small", "", `${entry.origin?.profile_name || entry.origin?.profile_id || "Origin unrecorded"} · ${human(entry.kind)}`));
      if (entry.description) button.append(element("p", "", entry.description));
      button.append(element("span", "evidence-badge", `Declared: ${human(entry.evidence_status || "unclassified")}`));
      if (entry.id === selectedCapabilityId) button.setAttribute("aria-current", "true");
      return button;
    }));
    if (!entries.length) list.append(element("p", "field-help", query ? "No capabilities match this search." : "No published capabilities yet."));
  }
  function renderCapabilityPublisher(profiles) {
    const wrap = $("#capability-publish-form");
    const form = element("form", "capability-form");
    form.append(element("p", "field-help", "Publish a snapshot of selected files. Describe what they do and what supports them; publishing does not install anything."));
    if (!profiles.length) { wrap.replaceChildren(element("p", "field-help", "Create a profile and save a harness revision first.")); return; }
    const profileChoice = capabilitySelect(form, "Source profile", "publish-profile", profiles.map((profile) => [profile.id, profile.name]));
    profileChoice.value = profiles[0].id;
    const configurationChoice = capabilitySelect(form, "Origin configuration", "publish-configuration", []);
    const revisionChoice = capabilitySelect(form, "Source harness revision", "publish-revision", []);
    const fileChoices = element("fieldset", "profile-file-choices");
    fileChoices.append(element("legend", "", "Files to publish"));
    let selectedFiles = [];
    let sourceProfile = null;
    let sourceRequest = 0;
    const fillFiles = () => {
      const revision = sourceProfile?.revisions?.find((item) => item.id === revisionChoice.value);
      selectedFiles = [];
      fileChoices.replaceChildren(element("legend", "", "Files to publish"));
      for (const path of revisionFiles(revision)) {
        const line = element("label", "profile-checkbox");
        const checkbox = element("input");
        checkbox.type = "checkbox";
        checkbox.value = path;
        selectedFiles.push(checkbox);
        line.append(checkbox, element("span", "", path));
        fileChoices.append(line);
      }
      if (!selectedFiles.length) fileChoices.append(element("p", "field-help", "This revision has no publishable files."));
    };
    const fillSource = async () => {
      const request = ++sourceRequest;
      sourceProfile = null;
      selectedFiles = [];
      configurationChoice.replaceChildren();
      revisionChoice.replaceChildren();
      configurationChoice.value = "";
      revisionChoice.value = "";
      fileChoices.replaceChildren(element("legend", "", "Files to publish"), element("p", "field-help", "Reading the selected profile…"));
      const loaded = await loadProfile(profileChoice.value, { render: false, force: true });
      if (request !== sourceRequest) return;
      sourceProfile = loaded;
      configurationChoice.replaceChildren(...(sourceProfile?.configurations || []).map((item) => { const option = element("option", "", item.label || item.id); option.value = item.id; return option; }));
      revisionChoice.replaceChildren(...(sourceProfile?.revisions || []).map((item) => { const option = element("option", "", `${item.label || item.id}${item.retired ? " · retired" : ""}`); option.value = item.id; return option; }));
      configurationChoice.value = sourceProfile?.active_configuration_id || sourceProfile?.configurations?.[0]?.id || "";
      revisionChoice.value = sourceProfile?.active_revision_id || sourceProfile?.revisions?.[0]?.id || "";
      fillFiles();
    };
    profileChoice.addEventListener("change", fillSource);
    revisionChoice.addEventListener("change", fillFiles);
    form.append(fileChoices);
    const name = capabilityField(form, "Capability name", "publish-name", { required: true });
    const description = capabilityField(form, "What does it do?", "publish-description", { multiline: true, required: true });
    const why = capabilityField(form, "What problem or opportunity led to it?", "publish-why", { multiline: true, required: true });
    const kind = capabilitySelect(form, "Form", "publish-kind", [["workflow", "Workflow guidance"], ["skill", "On-demand skill"], ["script", "Deterministic script"], ["guardrail", "Guardrail"], ["plugin", "Plugin"], ["other", "Other"]]);
    const portability = capabilitySelect(form, "Expected portability", "publish-portability", [["unknown", "Unknown · needs testing"], ["model-sensitive", "Sensitive to a model’s behavior"], ["runtime-specific", "Tied to a runtime"], ["deterministic", "Deterministic · model-independent behavior"]]);
    const evidence = capabilitySelect(form, "Declared evidence status", "publish-evidence", [["suspected", "Suspected"], ["observed", "Observed"], ["replicated", "Replicated"], ["transferred", "Transferred"]]);
    const references = capabilityField(form, "Supporting run, file, or URL references · one per line", "publish-references", { multiline: true });
    form.append(element("p", "field-help", "These are the publisher’s claims. A receiving profile starts with this capability unvalidated locally."));
    form.append(element("p", "field-help", "Observed, replicated, and transferred labels require a supporting reference. The catalog records the claim; it does not certify the reference."));
    const costs = element("details", "profile-toolbox");
    costs.append(element("summary", "", "Costs, limits, and ancestry"));
    const context = capabilityField(costs, "Estimated context tokens", "publish-context", { numeric: true });
    const time = capabilityField(costs, "Estimated time in seconds", "publish-time", { numeric: true });
    const tools = capabilityField(costs, "Estimated tool calls", "publish-tools", { numeric: true });
    const incompatibilities = capabilityField(costs, "Known incompatibilities · one per line", "publish-incompatibilities", { multiline: true });
    const failures = capabilityField(costs, "Known failures · one per line", "publish-failures", { multiline: true });
    const ancestry = element("fieldset", "profile-file-choices");
    ancestry.append(element("legend", "", "Derived from a published capability?"));
    const parents = capabilityEntries().map((entry) => {
      const line = element("label", "profile-checkbox");
      const check = element("input");
      check.type = "checkbox";
      check.value = entry.id;
      line.append(check, element("span", "", `${entry.name} · ${entry.origin?.profile_name || entry.origin?.profile_id || entry.id}`));
      ancestry.append(line);
      return check;
    });
    if (!parents.length) ancestry.append(element("p", "field-help", "No published ancestors yet. Independent origins remain distinct from borrowed ideas."));
    costs.append(ancestry);
    form.append(costs);
    const submit = element("button", "button secondary profile-submit", "Publish selected files");
    submit.type = "submit";
    form.append(submit);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!form.reportValidity() || !capabilityState().enabled) return;
      const paths = selectedFiles.filter((input) => input.checked).map((input) => input.value);
      if (!paths.length || !configurationChoice.value || !revisionChoice.value) { toast("Choose a configuration, a revision, and at least one file to publish.", true); return; }
      const costValues = {};
      for (const [key, input] of [["context_tokens", context], ["time_seconds", time], ["tool_calls", tools]]) if (input.value !== "") costValues[key] = Number(input.value);
      const supporting = capabilityLines(references).map((reference) => ({ reference }));
      if (evidence.value !== "suspected" && !supporting.length) { toast("Add a supporting reference for this evidence label, or choose Suspected.", true); return; }
      capabilityAction("publish", { profile_id: profileChoice.value, configuration_id: configurationChoice.value, revision_id: revisionChoice.value, name: name.value.trim(), description: description.value.trim(), why: why.value.trim(), paths, kind: kind.value, portability: portability.value, evidence_status: evidence.value, evidence: supporting, costs: costValues, incompatibilities: capabilityLines(incompatibilities), failures: capabilityLines(failures), derived_from: parents.filter((input) => input.checked).map((input) => input.value) }, "Capability published. Profiles still choose what to test.");
    });
    wrap.replaceChildren(form);
    fillSource();
  }
  function renderCapabilityDetail(entry) {
    if (!entry?.id || entry.id !== selectedCapabilityId) return;
    const enabled = capabilityState().enabled === true;
    const signature = JSON.stringify([entry, enabled, state?.profiles, capabilityEntries()]);
    if (signature === capabilityDetailSignature) return;
    capabilityDetailSignature = signature;
    const wrap = $("#capability-detail");
    const heading = element("header", "profile-heading");
    const title = element("div");
    title.append(element("span", "eyebrow", "A PUBLISHED SNAPSHOT"), element("h2", "", entry.name), element("p", "", entry.description || ""));
    heading.append(title);
    const origin = entry.origin || {};
    const provenance = profileSection("Where it came from", "The source remains intact. A published entry records this specific configuration and revision.");
    const originLine = element("p", "profile-reason");
    originLine.append(element("strong", "", origin.profile_name || origin.profile_id || "Origin not recorded"));
    if (origin.profile_id && (state?.profiles || []).some((profile) => profile.id === origin.profile_id)) originLine.append(element("span", "", " · "), capabilityButton("Open profile ↗", () => selectProfile(origin.profile_id), "text-button"));
    provenance.append(originLine, element("p", "field-help", `Configuration ${origin.configuration_id || "unrecorded"} · harness ${origin.revision_id || "unrecorded"}`));
    provenance.append(element("p", "field-help", "The configuration is an origin association declared by the publisher; selecting it does not prove that it caused this capability."));
    if (entry.created_at) provenance.append(element("p", "field-help", `Published ${when(entry.created_at)}`));
    if (entry.why) provenance.append(element("h4", "", "Why it was made"), element("p", "profile-reason", entry.why));
    const evidence = profileSection("What is known", "Evidence and portability describe the publisher’s account. They do not establish that this will help another profile.");
    const tags = element("div", "capability-tags");
    tags.append(element("span", "soft-tag", human(entry.kind || "other")), element("span", "soft-tag", `Portability: ${human(entry.portability || "unknown")}`), element("span", "evidence-badge", `Declared: ${human(entry.evidence_status || "unclassified")}`));
    evidence.append(tags);
    const costValues = Object.entries(entry.costs || {}).filter(([, value]) => value !== null && value !== "");
    evidence.append(element("p", "field-help", costValues.length ? `Estimated costs: ${costValues.map(([key, value]) => `${human(key)} ${typeof value === "number" ? count(value) : String(value)}`).join(" · ")}` : "Costs have not been recorded."));
    for (const [key, label] of [["incompatibilities", "Known incompatibilities"], ["failures", "Known failures"]]) {
      evidence.append(element("h4", "", label));
      const values = Array.isArray(entry[key]) ? entry[key] : [];
      if (values.length) { const list = element("ul", "capability-notes"); list.append(...values.map((value) => element("li", "", typeof value === "string" ? value : JSON.stringify(value)))); evidence.append(list); }
      else evidence.append(element("p", "field-help", "None recorded. This does not establish that none exist."));
    }
    if (entry.evidence?.length || entry.supporting_runs?.length) {
      const details = element("details", "profile-evidence");
      details.append(element("summary", "", "Supporting evidence from the source"), element("pre", "profile-snapshot", JSON.stringify(entry.evidence || entry.supporting_runs, null, 2)));
      evidence.append(details);
    }
    const family = profileSection("A family of ideas", "Independent invention and transmitted changes keep different ancestry.");
    const appendRelative = (label, id) => {
      const relative = capabilityEntries().find((item) => item.id === id);
      const row = element("div", "capability-relative");
      row.append(element("small", "", label), capabilityButton(relative?.name || id, () => selectCapability(id), "text-button"));
      family.append(row);
    };
    const parents = capabilityParents(entry);
    if (parents.length) parents.forEach((id) => appendRelative("Derived from", id));
    else family.append(element("p", "field-help", "No published ancestor is recorded for this entry. This declaration does not certify independent invention."));
    capabilityEntries().filter((item) => capabilityParents(item).includes(entry.id)).forEach((child) => appendRelative("Later variant", child.id));
    const forks = capabilityEntries().find((item) => item.id === entry.id)?.forks || entry.forks || [];
    if (forks.length) {
      family.append(element("h4", "", "Forks in profiles"));
      const seen = new Set();
      for (const fork of forks) {
        const key = `${fork.profile_id}/${fork.revision_id}`;
        if (seen.has(key)) continue;
        seen.add(key);
        const row = element("div", "capability-relative");
        row.append(capabilityButton(`${fork.profile_name || fork.profile_id} · ${fork.revision_label || fork.revision_id}`, () => selectProfile(fork.profile_id), "text-button"), element("small", "", `${fork.active ? "Active harness" : "Saved revision"} · local validation: ${human(fork.local_validation || "unvalidated")}`));
        family.append(row);
      }
    }
    const filesSection = profileSection("The capability itself", "Saved files are shown as plain text. Reading or forking does not execute them.");
    const files = entry.files || {};
    for (const [path, value] of Object.entries(files)) {
      const file = element("details", "profile-card capability-file");
      file.append(element("summary", "", path), element("pre", "profile-snapshot", typeof value === "string" ? value : value?.content || ""));
      filesSection.append(file);
    }
    if (!Object.keys(files).length) filesSection.append(element("p", "field-help", "No readable files were returned for this entry."));
    wrap.replaceChildren(heading, provenance, evidence, family, filesSection);
    if (enabled) wrap.append(renderCapabilityFork(entry));
    else wrap.append(element("p", "profile-continuity-note", "The catalog is off. You are inspecting saved material. New experiments receive no catalog reference, and catalog imports are disabled. Existing run snapshots stay unchanged."));
  }
  function renderCapabilityFork(entry) {
    const section = profileSection("Try it in another profile", "Forks are candidates and start unvalidated locally. Nothing is executed or adopted; the receiving profile keeps its current active harness.");
    const profiles = (state?.profiles || []).filter((profile) => profile.id !== entry.origin?.profile_id);
    if (!profiles.length) { section.append(element("p", "field-help", "A second profile will give this idea somewhere new to go.")); return section; }
    const form = element("form", "capability-form");
    const target = capabilitySelect(form, "Receiving profile", "fork-profile", profiles.map((profile) => [profile.id, profile.name]));
    target.value = profiles[0].id;
    const base = capabilitySelect(form, "Start from this harness", "fork-parent", []);
    let targetProfile = null;
    let requestId = 0;
    const conflicts = element("fieldset", "profile-file-choices");
    const replacements = [];
    const edits = [];
    const fileEditors = element("details", "profile-toolbox");
    fileEditors.append(element("summary", "", "Adapt the borrowed files before saving · optional"), element("p", "field-help", "Changes belong to the new candidate. The published source is preserved. You can publish a tested variant later from its profile revision."));
    for (const [path, value] of Object.entries(entry.files || {})) {
      const line = element("label", "profile-checkbox");
      const check = element("input");
      check.type = "checkbox";
      line.append(check, element("span", "", `Edit ${path}`));
      const editor = element("textarea", "capability-file-editor");
      editor.value = typeof value === "string" ? value : value?.content || "";
      editor.rows = 7;
      editor.hidden = true;
      editor.setAttribute("aria-label", `Modified content for ${path}`);
      check.addEventListener("change", () => { editor.hidden = !check.checked; });
      edits.push({ path, check, editor });
      fileEditors.append(line, editor);
    }
    const fillConflicts = () => {
      replacements.length = 0;
      conflicts.replaceChildren(element("legend", "", "Review files already in the receiving harness"));
      const revision = targetProfile?.revisions?.find((item) => item.id === base.value);
      const existing = new Set(revisionFiles(revision).map(capabilityPathKey));
      for (const path of Object.keys(entry.files || {}).filter((path) => existing.has(capabilityPathKey(path)))) {
        const line = element("label", "profile-checkbox");
        const check = element("input");
        check.type = "checkbox";
        check.value = path;
        check.required = true;
        replacements.push(check);
        line.append(check, element("span", "", `Replace ${path} in this new candidate`));
        conflicts.append(line);
      }
      conflicts.hidden = !replacements.length;
    };
    const fillTarget = async () => {
      const request = ++requestId;
      targetProfile = null;
      base.replaceChildren();
      base.value = "";
      conflicts.hidden = true;
      replacements.length = 0;
      const loaded = await loadProfile(target.value, { render: false, force: true });
      if (request !== requestId) return;
      targetProfile = loaded;
      base.replaceChildren(...(targetProfile?.revisions || []).map((revision) => { const option = element("option", "", revision.label || revision.id); option.value = revision.id; return option; }));
      base.value = targetProfile?.active_revision_id || targetProfile?.revisions?.[0]?.id || "";
      fillConflicts();
    };
    target.addEventListener("change", fillTarget);
    base.addEventListener("change", fillConflicts);
    form.append(conflicts);
    const label = capabilityField(form, "Candidate name", "fork-label", { required: true });
    label.value = `${entry.name} · local candidate`;
    const why = capabilityField(form, "Why test this here?", "fork-why", { required: true, multiline: true });
    form.append(fileEditors);
    const submit = element("button", "button secondary profile-submit", "Save fork as a candidate");
    submit.type = "submit";
    form.append(submit);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!form.reportValidity() || !capabilityState().enabled) return;
      if (!base.value) { toast("Choose a receiving harness before saving a fork.", true); return; }
      if (replacements.some((input) => !input.checked)) { toast("Review each existing file before replacing it in the new candidate.", true); return; }
      const replace_files = Object.fromEntries(edits.filter((edit) => edit.check.checked).map((edit) => [edit.path, edit.editor.value]));
      const body = { capability_id: entry.id, target_profile_id: target.value, parent_revision_id: base.value, label: label.value.trim(), why: why.value.trim(), replace_paths: replacements.filter((input) => input.checked).map((input) => input.value) };
      if (Object.keys(replace_files).length) body.replace_files = replace_files;
      const result = await capabilityAction("fork", body, "Fork saved as a candidate, unvalidated locally. The active harness is unchanged.");
      if (result?.profile) selectProfile(result.profile.id);
    });
    section.append(form);
    fillTarget();
    section.append(element("p", "field-help", "Skill forks may add a discovery hint to the candidate’s guidance."));
    return section;
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
    text("#onboarding-kicker", jobs.some((job) => ["demo", "benchmark"].includes(job.kind)) ? "NEXT STEPS" : "YOUR FIRST EXPERIMENT");
    text("#key-status", state.settings?.key_configured ? "A key is saved locally. Leave the field empty to keep it." : "No key is saved. Local models usually do not need one.");
    fillSettings(state.settings || {});
    renderBenchmarks();
    renderTools();
    renderRecent();
    renderJobs();
    renderProfiles();
    renderCapabilities();
    renderConversationAttention();
    renderConversations();
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
      if (view === "conversations") loadConversation();
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
      selectTab(["demo", "benchmark", "judge", "report"].includes(job.kind) ? "overview" : "log");
    }
    $("#job-empty").hidden = true;
    $("#job-detail-content").hidden = false;
    text("#detail-title", job.title || kindLabels[job.kind] || "Run");
    text("#detail-kind", `${(kindLabels[job.kind] || "Run").toUpperCase()}${job.imported ? " · IMPORTED HISTORY" : ""}`);
    const badge = statusBadge(job.status);
    badge.id = "detail-status";
    $("#detail-status").replaceWith(badge);
    text("#detail-time", job.finished_at ? `Finished ${when(job.finished_at)}` : `${isActive(job.status) ? "Started" : "Created"} ${when(job.started_at || job.created_at)}`);
    $("#job-cancel").hidden = !isActive(job.status);
    $("#job-resume").hidden = Boolean(job.imported) || !["failed", "interrupted", "cancelled"].includes(job.status) || !["demo", "benchmark"].includes(job.kind);
    $("#job-resume").disabled = launching || Boolean(activeJob());
    $("#job-open-folder").disabled = !job.output_dir;
    $("#job-error").hidden = !job.error;
    text("#job-error", job.error || "");
    $("#imported-run-note").hidden = !job.imported;
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
    renderStory(job);
    renderTasks(job);
    renderRunProfileCapture(job);
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
  function renderStory(job) {
    const story = job.story && typeof job.story === "object" ? job.story : {};
    const overview = $("#job-overview");
    const paragraphs = Array.isArray(story.overview) ? story.overview.filter((value) => typeof value === "string" && value.trim()) : [];
    const summary = typeof story.model_summary === "string" ? story.model_summary.trim() : "";
    const modelWritten = story.summary_status === "model";
    const tasks = Array.isArray(job.tasks) ? job.tasks : [];
    const selections = tasks.flatMap((task) => Array.isArray(task.selections) ? task.selections : []);
    const graded = selections.filter((selection) => selection.graded && typeof selection.score === "number" && Number.isFinite(selection.score));
    const files = new Set(selections.flatMap((selection) => (selection.files || []).map((file) => file.id || file.name)));
    const facts = element("div", "report-facts");
    function fact(value, label) { const box = element("div"); box.append(element("strong", "", value), element("span", "", label)); facts.append(box); }
    fact(statusLabels[job.status] || human(job.status), "Run status");
    if (graded.length === 1) {
      const value = graded[0].score;
      fact(value >= 0 && value <= 1 ? value.toLocaleString(undefined, { style: "percent", maximumFractionDigits: 2 }) : value.toLocaleString(undefined, { maximumFractionDigits: 2 }), "Recorded score");
    } else if (graded.length) fact(count(graded.length), "Graded selections");
    if (files.size) fact(count(files.size), "Output files");
    overview.replaceChildren(facts);
    const narrative = element("section", "run-narrative");
    narrative.append(element("h3", "", "What happened"));
    if (paragraphs.length) paragraphs.slice(0, 24).forEach((paragraph) => narrative.append(element("p", "", paragraph)));
    else if (summary && !modelWritten) narrative.append(element("p", "", summary));
    else narrative.append(element("p", "", isActive(job.status) ? "The run is in progress. Its readable report will appear as results are recorded. Timeline and Activity show the work so far." : "This earlier run has no saved narrative report. Its recorded status, output files, and activity are available here."));
    overview.append(narrative);
    const warnings = Array.isArray(story.warnings) ? story.warnings.filter((value) => typeof value === "string" && value.trim()) : [];
    if (warnings.length) {
      const note = element("section", "report-notes");
      note.append(element("h3", "", "Keep in mind"));
      warnings.slice(0, 20).forEach((warning) => note.append(element("p", "", warning)));
      overview.append(note);
    }
    if (summary && modelWritten) {
      const wrapup = element("section", "run-wrapup");
      wrapup.append(element("span", "eyebrow", "IN THE MODEL’S OWN WORDS"), element("h3", "", "The model’s wrap-up"));
      wrapup.append(element("p", "field-help", "The model’s interpretation of the saved run, alongside the recorded results above."));
      wrapup.append(element("div", "wrapup-text", summary));
      overview.append(wrapup);
    } else if (story.summary_status) {
      const source = story.summary_status === "pending" ? "This overview uses the results recorded so far. A model-written wrap-up can follow grading and reporting." : story.summary_status === "unavailable" ? "This overview uses the recorded results. A model-written wrap-up could not be produced." : "This overview uses the recorded results. No model-written wrap-up is saved for this run.";
      overview.append(element("p", "field-help report-source", source));
    }
    const timeline = $("#job-timeline");
    const events = Array.isArray(story.events) ? story.events.filter((event) => event && typeof event === "object") : [];
    const source = events.length ? events : (job.phases || []).map((phase) => ({ time: phase.finished_at || phase.started_at, title: phase.label || human(phase.id), detail: statusLabels[phase.status] || human(phase.status || "pending"), kind: phase.status }));
    timeline.replaceChildren();
    if (source.length) {
      const list = element("ol", "run-timeline");
      source.slice(0, 150).forEach((event) => {
        const item = element("li");
        if (["warning", "error", "failed", "fallback", "timeout"].includes(event.kind)) item.classList.add("timeline-caution");
        const header = element("div", "timeline-heading");
        header.append(element("h3", "", event.title || "Recorded event"));
        const timestamp = event.time ? when(event.time) : "";
        const stamp = element("time", "", timestamp || "Time not recorded");
        if (timestamp) stamp.dateTime = String(event.time);
        header.append(stamp);
        item.append(header);
        if (event.detail) item.append(element("p", "", event.detail));
        list.append(item);
      });
      timeline.append(list);
    } else timeline.append(empty("No timeline recorded yet", isActive(job.status) ? "Events will appear as the work moves through its stages." : "The saved activity log may have more detail for this earlier run."));
  }
  function browseRuns() {
    $("#job-filter").value = "all";
    $("#job-search").value = "";
    jobsSignature = "";
    renderJobs();
    showView("runs");
    $(".jobs-panel").scrollIntoView({ behavior: "smooth", block: "start" });
    $("#job-search").focus({ preventScroll: true });
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
        ? selection.score.toLocaleString(undefined, { style: "percent", maximumFractionDigits: 2 })
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
    const settings = experimentSettings(params);
    $("#confirm-facts").replaceChildren(element("p", "", `${benchmark?.name || "Experiment"} · ${(params.domains || []).map(human).join(", ") || "Selected domains"}`), element("p", "", `Up to ${params.max_tasks || 1} task(s) per domain · ${params.k || 1} branch(es) · ${params.workers || 1} parallel domain(s)`), element("p", "", `${executions} planned full executions per task, plus harness editing, reflection, and judging. Saved work may reduce this; retries can add work.`), element("p", "", providerLabel(settings)));
    if (params.profile_id) {
      const profile = profileCache.get(params.profile_id);
      const configuration = profile?.configurations?.find((item) => item.id === (params.profile_configuration_id || profile.active_configuration_id));
      $("#confirm-facts").append(element("p", "", `${profile?.name || "Profile"} · ${configuration?.label || "saved configuration"} · ${revisionLabel(profile || {}, params.profile_revision_id || profile?.active_revision_id)}`), element("p", "", "This exact starting point is saved with the run. Any resulting harness remains a candidate until you choose to activate it."));
    }
    if (params.conversation_enabled) $("#confirm-facts").append(element("p", "", "Conversation during work is on. Your messages reach the active native worker. Human contact is recorded as context; benefit is unproven."));
    text("#confirm-compute", settings?.provider === "local" ? settings?.local_runtime === "managed" ? "Your local model loads for this run and unloads after grading and reporting, failure, or stop. You can stop the run from Runs." : "Your local model will use this machine’s computing resources. You can stop the run from Runs." : "This uses your connected provider and may incur charges. You can stop the run from Runs.");
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
      $("#local-runtime").value = settings.local_runtime || "external";
      localRuntimeChanged();
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
    if (local && settings.local_runtime === "managed") {
      message("#connection-test-message", "The model starts automatically when you begin a run. Save this connection to validate the server and model paths; this button does not load the model.");
      return;
    }
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
    ["overview", "timeline", "results", "log"].forEach((id) => {
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
  $("#profile-create-form").addEventListener("submit", createProfile);
  $("#conversation-create-form").addEventListener("submit", (event) => { event.preventDefault(); if ($("#conversation-create-form").reportValidity()) createConversation($("#conversation-profile").value, { title: $("#conversation-title").value.trim() }); });
  $("#conversation-compose-form").addEventListener("submit", sendConversationMessage);
  $("#conversation-draft").addEventListener("input", () => { if (selectedConversationId) conversationDrafts.set(selectedConversationId, $("#conversation-draft").value); });
  $("#conversation-draft").addEventListener("keydown", (event) => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) sendConversationMessage(event); });
  $("#conversation-messages").addEventListener("scroll", markVisibleConversationRead);
  window.addEventListener("focus", markVisibleConversationRead);
  document.addEventListener("visibilitychange", markVisibleConversationRead);
  $("#capability-search").addEventListener("input", renderCapabilityList);
  $("#experiment-profile").addEventListener("change", renderExperimentProfile);
  ["experiment-configuration", "experiment-revision"].forEach((id) => $("#" + id).addEventListener("change", renderExperimentProfile));
  $("#preview-experiment").addEventListener("click", () => { const params = formParams(); if (params) launch("preview", params); });
  $("#benchmark").addEventListener("change", renderDomains);
  ["domain", "substrate", "max-tasks", "workers", "branches", "mode"].forEach((id) => { $(`#${id}`).addEventListener("input", renderPlan); });
  $("#job-search").addEventListener("input", renderJobs);
  $("#job-filter").addEventListener("change", renderJobs);
  $("#provider").addEventListener("change", providerChanged);
  $("#local-runtime").addEventListener("change", localRuntimeChanged);
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
  $("#detail-all-runs").addEventListener("click", browseRuns);
  $("#follow-log").addEventListener("click", () => { const log = $("#job-log"); followingLog = true; log.scrollTop = log.scrollHeight; log.focus({ preventScroll: true }); });
  $("#job-log").addEventListener("scroll", () => { const log = $("#job-log"); if (log.clientHeight) followingLog = log.scrollHeight - log.scrollTop - log.clientHeight < 65; });
  const detailTabs = ["overview", "timeline", "results", "log"];
  detailTabs.forEach((id, index) => {
    $(`#tab-${id}`).addEventListener("click", () => selectTab(id));
    $(`#tab-${id}`).addEventListener("keydown", (event) => {
      if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        const next = event.key === "Home" ? 0 : event.key === "End" ? detailTabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + detailTabs.length) % detailTabs.length;
        selectTab(detailTabs[next], true);
      }
    });
  });
  showView("overview", false);
  pollState();
  pollTimer = setInterval(pollState, 2000);
})();
