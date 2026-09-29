"use strict";

const $ = (id) => document.getElementById(id);
const PAGE_LINES = 160;
const STATUS_LABELS = {
  created: "已创建", planning_failed: "规划失败", awaiting_plan_approval: "待审批计划",
  plan_rejected: "计划被拒", ready_to_execute: "可执行", executing: "执行中",
  execution_failed: "执行失败", execution_interrupted: "执行中断",
  validation_failed: "验证失败", awaiting_diff_approval: "待审批 diff",
  diff_rejected: "diff 被拒", approved_for_pr: "diff 已批准",
  awaiting_publish_approval: "待审批发布", publish_rejected: "发布请求被拒",
  ready_to_publish: "已准备发布", draft_pr_created: "Draft PR 已记录",
};
const ACTION_LABELS = {
  search_code: "检索代码", read_file: "读取文件", read_symbol: "读取符号",
  write_file: "写入文件", replace_text: "修改文本", run_command: "运行命令",
  finish: "Coder 结束", review_finish: "Reviewer 结束", plan_finish: "规划结束",
  progress_decision: "进度判断", no_progress_intervention: "无进展干预",
  controller_finish: "控制器终止", llm_error: "模型调用错误", agent_exit: "Agent 轮次结束",
};

const state = {
  tasks: [], task: null, events: [], eventSequences: new Set(), metadata: new Set(),
  artifacts: {}, tab: "plan", createMode: "local", fakePublisher: false,
  pollTimer: null, pollToken: 0, pollCursor: 0, loadingGeneration: 0,
  diffPage: 0, seenDiffPages: new Set(), fullDiffShown: false,
};

function text(node, value) { node.textContent = value == null ? "" : String(value); }
function element(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined) text(node, value);
  return node;
}
function fmtTime(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", {hour12: false});
}
function shortHash(value) { return value ? `${value.slice(0, 12)}…${value.slice(-8)}` : "无"; }
function statusLabel(value) { return STATUS_LABELS[value] || value || "未知"; }

function notice(message, error = false) {
  const box = $("notice");
  text(box, message);
  box.classList.toggle("error", error);
  box.hidden = !message;
}
function connection(online) {
  const node = $("connection-status");
  text(node, online ? "本地服务已连接" : "本地服务不可用");
  node.classList.toggle("online", online);
  node.classList.toggle("offline", !online);
}
async function api(method, path, payload) {
  let response;
  try {
    response = await fetch(path, {
      method, credentials: "same-origin",
      headers: payload === undefined ? {} : {"Content-Type": "application/json"},
      body: payload === undefined ? undefined : JSON.stringify(payload),
    });
  } catch (_) {
    connection(false);
    throw new Error("无法连接本地服务。请检查服务是否仍在运行；刷新页面不会自动重启执行。 ");
  }
  connection(true);
  let body;
  try { body = await response.json(); } catch (_) { body = {}; }
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `请求失败（HTTP ${response.status}）`);
  return {body, status: response.status};
}
function report(error) { notice(error instanceof Error ? error.message : String(error), true); }
function setBusy(button, busy, idleLabel) {
  button.disabled = busy;
  text(button, busy ? "处理中…" : idleLabel);
}

async function refreshList() {
  const {body} = await api("GET", "/tasks?limit=100&offset=0");
  state.tasks = Array.isArray(body.tasks) ? body.tasks : [];
  renderList();
}
function renderList() {
  const list = $("task-list");
  list.replaceChildren();
  if (!state.tasks.length) {
    list.append(element("p", "muted empty-list", "还没有任务。点击右上角“新建任务”开始。"));
    return;
  }
  for (const task of state.tasks) {
    const button = element("button", `task-item${state.task?.id === task.id ? " active" : ""}`);
    button.type = "button";
    button.setAttribute("role", "listitem");
    button.setAttribute("aria-label", `${task.issue_summary || task.id}，${statusLabel(task.status)}`);
    button.append(element("span", "task-item-title", task.issue_summary || "未命名任务"));
    const meta = element("span", "task-item-meta");
    meta.append(element("span", "", statusLabel(task.status)));
    meta.append(element("code", "", task.id.slice(0, 8)));
    button.append(meta);
    button.addEventListener("click", () => openTask(task.id).catch(report));
    list.append(button);
  }
}
function showCreate() {
  stopPolling();
  state.task = null;
  $("create-view").hidden = false;
  $("detail-view").hidden = true;
  history.replaceState(null, "", location.pathname);
  renderList();
  $("repo-path").focus();
}
function setCreateMode(mode) {
  state.createMode = mode;
  const github = mode === "github";
  $("local-fields").hidden = github;
  $("github-fields").hidden = !github;
  $("mode-local").classList.toggle("active", !github);
  $("mode-github").classList.toggle("active", github);
  $("mode-local").setAttribute("aria-selected", String(!github));
  $("mode-github").setAttribute("aria-selected", String(github));
}
async function createTask(event) {
  event.preventDefault();
  const button = $("create-submit");
  const repoPath = $("repo-path").value.trim();
  let path, payload;
  if (!repoPath) return report(new Error("请填写本地仓库路径。"));
  if (state.createMode === "local") {
    const issue = $("issue-text").value.trim();
    if (!issue) return report(new Error("请填写 Issue 描述。"));
    path = "/tasks";
    payload = {repo_path: repoPath, issue};
  } else {
    const repository = $("github-repository").value.trim();
    const issueNumber = Number($("github-number").value);
    if (!repository || !Number.isInteger(issueNumber) || issueNumber <= 0)
      return report(new Error("请填写 owner/repo 和正整数 Issue 编号。"));
    path = "/tasks/github";
    payload = {repo_path: repoPath, repository, issue_number: issueNumber};
  }
  setBusy(button, true, "创建任务并生成计划");
  text($("create-feedback"), "规划可能需要一些时间，任务创建后会显示计划。 ");
  try {
    const {body} = await api("POST", path, payload);
    await refreshList();
    await openTask(body.task_id, "plan");
    notice("任务已创建。请先阅读并审批计划。 ");
  } catch (error) { report(error); }
  finally { setBusy(button, false, "创建任务并生成计划"); text($("create-feedback"), ""); }
}

function preferredTab(status) {
  if (["created", "planning_failed", "awaiting_plan_approval", "plan_rejected"].includes(status)) return "plan";
  if (["ready_to_execute", "executing", "execution_failed", "execution_interrupted", "validation_failed"].includes(status)) return "progress";
  if (["awaiting_diff_approval", "diff_rejected"].includes(status)) return "diff";
  return "draft";
}
async function fetchArtifact(kind) {
  const taskId = state.task?.id;
  if (!taskId || !state.metadata.has(kind)) return null;
  if (state.artifacts[kind]) return state.artifacts[kind];
  const {body} = await api("GET", `/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(kind)}`);
  if (state.task?.id !== taskId) return null;
  state.artifacts[kind] = body;
  return body;
}
async function openTask(taskId, tab = null) {
  if (state.task?.id !== taskId) {
    stopPolling();
    $("draft-form").reset();
  }
  const generation = ++state.loadingGeneration;
  const encoded = encodeURIComponent(taskId);
  const [{body: detail}, {body: listing}] = await Promise.all([
    api("GET", `/tasks/${encoded}`), api("GET", `/tasks/${encoded}/artifacts`),
  ]);
  if (generation !== state.loadingGeneration) return;
  state.task = detail.task;
  state.events = Array.isArray(detail.events) ? detail.events : [];
  state.eventSequences = new Set(state.events.map((event) => event.sequence));
  state.metadata = new Set((listing.artifacts || []).map((artifact) => artifact.kind));
  state.artifacts = {};
  state.diffPage = 0;
  state.seenDiffPages = new Set();
  state.fullDiffShown = false;
  state.tab = tab || preferredTab(state.task.status);
  $("create-view").hidden = true;
  $("detail-view").hidden = false;
  history.replaceState(null, "", `#task=${encodeURIComponent(taskId)}`);
  await Promise.all([fetchArtifact("plan"), fetchArtifact("draft_pr_request"), fetchArtifact("draft_pr")]);
  if (generation !== state.loadingGeneration) return;
  renderTask();
  await setTab(state.tab);
  renderList();
}
function renderTask() {
  const task = state.task;
  if (!task) return;
  text($("detail-id"), task.id.slice(0, 12));
  const firstLine = task.issue.split(/\r?\n/).find((line) => line.trim()) || "未命名任务";
  text($("detail-title"), firstLine.trim().length > 160 ? `${firstLine.trim().slice(0, 159)}…` : firstLine.trim());
  text($("detail-issue"), task.issue.trim());
  const status = $("detail-status");
  text(status, statusLabel(task.status));
  status.className = "status-badge";
  if (/failed|rejected|interrupted/.test(task.status)) status.classList.add("danger");
  else if (/awaiting|executing/.test(task.status)) status.classList.add("warning");
  renderPlan();
  renderProgress();
  renderDraft();
}
async function setTab(tab) {
  const taskId = state.task?.id;
  state.tab = tab;
  for (const button of document.querySelectorAll(".detail-tab")) button.classList.toggle("active", button.dataset.tab === tab);
  for (const name of ["plan", "progress", "diff", "draft"]) $("panel-" + name).hidden = name !== tab;
  if (tab === "diff") {
    await Promise.all([fetchArtifact("diff"), fetchArtifact("test_report"), fetchArtifact("diff_review")]);
    if (state.task?.id !== taskId || state.tab !== tab) return;
    renderDiff();
  }
}

function addList(parent, heading, items, ordered = false) {
  const box = element("div");
  box.append(element("h3", "", heading));
  const list = element(ordered ? "ol" : "ul");
  for (const item of items) list.append(element("li", "", item));
  if (!items.length) list.append(element("li", "muted", "无"));
  box.append(list);
  parent.append(box);
}
function renderPlan() {
  const artifact = state.artifacts.plan;
  const body = $("plan-content");
  const actions = $("plan-actions");
  body.replaceChildren();
  actions.hidden = true;
  text($("plan-state"), statusLabel(state.task.status));
  if (!artifact) {
    body.append(element("p", "muted", "尚无计划。若规划失败，可重新创建任务或使用计划重试接口。"));
    return;
  }
  let plan;
  try { plan = JSON.parse(artifact.content); } catch (_) {
    body.append(element("p", "muted", "计划内容无法解析；请勿审批。"));
    return;
  }
  body.className = "panel-body";
  body.append(element("h3", "", "目标"));
  body.append(element("p", "", plan.goal || "未填写目标"));
  const grid = element("div", "plan-summary");
  addList(grid, "受影响文件", Array.isArray(plan.affected_files) ? plan.affected_files : []);
  addList(grid, "实施步骤", Array.isArray(plan.steps) ? plan.steps : [], true);
  addList(grid, "风险", Array.isArray(plan.risks) ? plan.risks : []);
  const commands = Array.isArray(plan.allowed_commands) ? plan.allowed_commands.map((argv) => JSON.stringify(argv)) : [];
  addList(grid, "获准命令", commands);
  body.append(grid);
  const full = element("details", "plan-full");
  full.append(element("summary", "", "查看完整计划 JSON"));
  full.append(element("pre", "", artifact.content));
  body.append(full);
  text($("plan-hash"), `计划审批对象 SHA-256：${artifact.approval_subject_sha256 || "无"}`);
  actions.hidden = state.task.status !== "awaiting_plan_approval";
  $("approve-plan-button").disabled = !artifact.approval_subject_sha256;
  $("reject-plan-button").disabled = !artifact.approval_subject_sha256;
}
async function decidePlan(decision) {
  const subject = state.artifacts.plan?.approval_subject_sha256;
  if (!subject || state.task?.status !== "awaiting_plan_approval") return;
  try {
    await api("POST", `/tasks/${state.task.id}/approvals/plan`, {subject_hash: subject, actor: "local-ui", decision});
    await refreshList();
    await openTask(state.task.id, decision === "approved" ? "progress" : "plan");
    notice(decision === "approved" ? "计划已批准。可以选择目标文件并开始执行。" : "计划已拒绝。 ");
  } catch (error) { report(error); }
}

function parsePayload(event) {
  try { return JSON.parse(event.payload || "{}"); } catch (_) { return {}; }
}
function stepNode(event) {
  const payload = parsePayload(event);
  const row = element("li", `step-item${event.event_type === "agent_step" ? "" : " system"}`);
  row.append(element("span", "step-index", `#${event.sequence}`));
  const main = element("span", "step-main");
  if (event.event_type === "agent_step") {
    main.append(element("strong", "", ACTION_LABELS[payload.action] || payload.action || "步骤"));
    if (payload.path) main.append(element("code", "", payload.path));
    const extra = [payload.phase, payload.round != null ? `轮 ${payload.round}` : null, payload.exit_reason].filter(Boolean).join(" · ");
    if (extra) main.append(element("span", "muted", `  ${extra}`));
  } else {
    main.append(element("strong", "", ({execution_started: "执行已开始", execution_finished: "执行已结束", execution_failed: "执行失败", execution_interrupted: "执行中断", agent_trace_truncated: "步骤流已截断"})[event.event_type] || event.event_type));
    if (event.event_type === "agent_trace_truncated") main.append(element("span", "muted", "  后续进度步骤已省略，状态和终态事件仍会保留。"));
  }
  row.append(main);
  const outcome = payload.status || (event.event_type === "execution_failed" ? "error" : "");
  row.append(element("span", `step-status ${outcome}`, outcome || fmtTime(event.created_at)));
  return row;
}
function appendVisibleEvent(event) {
  if (!["agent_step", "execution_started", "execution_finished", "execution_failed", "execution_interrupted", "agent_trace_truncated"].includes(event.event_type)) return;
  const list = $("step-list");
  list.append(stepNode(event));
  list.scrollTop = list.scrollHeight;
}
function renderProgress() {
  const task = state.task;
  if (!task) return;
  const controls = $("execute-controls");
  const ready = task.status === "ready_to_execute";
  controls.hidden = !ready;
  const select = $("target-file");
  const previous = select.value;
  select.replaceChildren();
  let files = [];
  try {
    const plan = JSON.parse(state.artifacts.plan?.content || "{}");
    files = Array.isArray(plan.affected_files) ? plan.affected_files.filter((value) => typeof value === "string" && value.trim()) : [];
  } catch (_) { /* No usable plan means execution stays disabled. */ }
  for (const file of files) {
    const option = element("option", "", file);
    option.value = file;
    select.append(option);
  }
  if (files.includes(previous)) select.value = previous;
  else if (files.length) select.value = files[0];
  $("execute-button").disabled = !ready || !files.length;
  const hint = task.status === "executing"
    ? (state.pollTimer !== null ? "执行中：每秒读取新的步骤。" : "执行可能仍在后台进行。刷新状态可查看已记录的步骤；页面刷新不会自动启动轮询。")
    : ready && !files.length ? "批准的计划没有 affected_files，无法从界面安全选择目标文件。"
      : statusLabel(task.status);
  text($("progress-hint"), hint);
  text($("progress-state"), statusLabel(task.status));
  const list = $("step-list");
  list.replaceChildren();
  for (const event of state.events) appendVisibleEvent(event);
  if (!list.children.length) list.append(element("li", "muted", "还没有执行步骤。"));
}
function stopPolling() {
  state.pollToken += 1;
  if (state.pollTimer !== null) clearTimeout(state.pollTimer);
  state.pollTimer = null;
}
function startPolling(taskId) {
  stopPolling();
  const token = state.pollToken;
  state.pollCursor = 0;
  state.pollTimer = setTimeout(() => pollOnce(taskId, token), 0);
}
async function pollOnce(taskId, token) {
  if (token !== state.pollToken) return;
  try {
    const {body} = await api("GET", `/tasks/${taskId}/events?after=${state.pollCursor}&limit=100`);
    if (token !== state.pollToken || state.task?.id !== taskId) return;
    state.pollCursor = body.next_after;
    for (const event of body.events || []) {
      if (state.eventSequences.has(event.sequence)) continue;
      state.eventSequences.add(event.sequence);
      state.events.push(event);
      if ($("step-list").children.length === 1 && $("step-list").firstChild.classList.contains("muted")) $("step-list").replaceChildren();
      appendVisibleEvent(event);
    }
    if (body.status && state.task.status !== body.status) {
      state.task.status = body.status;
      text($("detail-status"), statusLabel(body.status));
      text($("progress-state"), statusLabel(body.status));
    }
    if (body.execution_done === true) {
      stopPolling();
      await refreshList();
      await openTask(taskId, preferredTab(body.status));
      notice(`执行结束：${statusLabel(body.status)}。请检查补丁和验证结果。`);
      return;
    }
    state.pollTimer = setTimeout(() => pollOnce(taskId, token), body.has_more ? 0 : 900);
  } catch (error) {
    stopPolling();
    report(error);
  }
}
async function executeTask() {
  const taskId = state.task?.id;
  const targetFile = $("target-file").value;
  const maxRounds = Number($("max-rounds").value);
  if (!taskId || state.task.status !== "ready_to_execute" || !targetFile) return;
  const button = $("execute-button");
  setBusy(button, true, "开始执行");
  try {
    const response = await api("POST", `/tasks/${taskId}/execute`, {target_file: targetFile, max_rounds: maxRounds});
    if (response.status !== 202) throw new Error("执行请求未返回 202，未启动轮询。 ");
    await openTask(taskId, "progress");
    startPolling(taskId); // This is the only call site: a successful POST /execute 202.
    notice("执行已被接受，正在读取步骤流。 ");
  } catch (error) { report(error); }
  finally { setBusy(button, false, "开始执行"); }
}

function diffClass(line) {
  if (line.startsWith("+++ ") || line.startsWith("--- ") || line.startsWith("diff ") || line.startsWith("index ")) return "meta";
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+")) return "added";
  if (line.startsWith("-")) return "deleted";
  return "context";
}
function diffLines() { return state.artifacts.diff?.content.split(/\r?\n/) || []; }
function renderDiffPage() {
  const lines = diffLines();
  const pages = Math.max(1, Math.ceil(lines.length / PAGE_LINES));
  state.diffPage = Math.min(Math.max(state.diffPage, 0), pages - 1);
  const view = $("diff-lines");
  view.replaceChildren();
  for (const line of lines.slice(state.diffPage * PAGE_LINES, (state.diffPage + 1) * PAGE_LINES)) {
    view.append(element("div", `diff-line ${diffClass(line)}`, line || " "));
  }
  state.seenDiffPages.add(state.diffPage);
  if (state.seenDiffPages.size >= pages) state.fullDiffShown = true;
  text($("diff-page-label"), `${state.diffPage + 1} / ${pages}`);
  $("diff-prev").disabled = state.diffPage === 0;
  $("diff-next").disabled = state.diffPage >= pages - 1;
  text($("diff-size"), `${lines.length.toLocaleString()} 行 · ${state.artifacts.diff.size_bytes.toLocaleString()} 字节`);
  updateDiffButtons();
}
function addEvidence(title, value) {
  const box = element("div", "evidence-card");
  box.append(element("strong", "", title));
  box.append(element("p", "", value));
  $("review-evidence").append(box);
}
function renderDiff() {
  const artifact = state.artifacts.diff;
  $("diff-empty").hidden = !!artifact;
  $("diff-content").hidden = !artifact;
  if (!artifact) {
    text($("diff-state"), "暂无补丁");
    return;
  }
  text($("diff-state"), statusLabel(state.task.status));
  text($("diff-content-hash"), artifact.content_sha256 || "无");
  text($("diff-approval-hash"), artifact.approval_subject_sha256 || "无（审查已过期）");
  const evidence = $("review-evidence");
  evidence.replaceChildren();
  const report = state.artifacts.diff_review;
  if (report) {
    try {
      const data = JSON.parse(report.content);
      addEvidence("Reviewer", `结果：${data.reviewer?.passed === true ? "通过" : "未通过"}\n轮次：${data.reviewer?.rounds ?? "未知"}\n问题：${JSON.stringify(data.reviewer?.issues ?? [])}`);
    } catch (_) { addEvidence("Reviewer", "审查报告无法解析"); }
  } else addEvidence("Reviewer", "没有通过验证的审查报告");
  const test = state.artifacts.test_report;
  if (test) {
    const box = element("div", "evidence-card");
    box.append(element("strong", "", "公开测试结果"));
    const details = element("details");
    details.append(element("summary", "", "查看完整测试输出"));
    details.append(element("pre", "", test.content));
    box.append(details);
    evidence.append(box);
  }
  renderDiffPage();
}
function updateDiffButtons() {
  const artifact = state.artifacts.diff;
  const canApprove = !!artifact && state.task?.status === "awaiting_diff_approval"
    && !!artifact.approval_subject_sha256 && state.fullDiffShown;
  $("approve-diff-button").disabled = !canApprove;
  $("reject-diff-button").disabled = !canApprove;
  text($("diff-gate-hint"), !artifact?.approval_subject_sha256
    ? "没有当前有效的 diff_review 审批对象，不能审批。"
    : !state.fullDiffShown ? "请查看全部分页，或打开“查看完整 diff”，然后才能审批。"
      : state.task?.status !== "awaiting_diff_approval" ? "当前任务状态不允许审批 diff。" : "完整 diff 已展示，可以审批当前 diff/test 组合。 ");
}
function showFullDiff() {
  const artifact = state.artifacts.diff;
  if (!artifact) return;
  text($("full-diff-text"), artifact.content);
  $("full-diff-dialog").showModal();
  state.fullDiffShown = true;
  updateDiffButtons();
}
async function decideDiff(decision) {
  const artifact = state.artifacts.diff;
  if (!state.fullDiffShown || !artifact?.approval_subject_sha256 || state.task?.status !== "awaiting_diff_approval") return;
  try {
    await api("POST", `/tasks/${state.task.id}/approvals/diff`, {
      subject_hash: artifact.approval_subject_sha256, actor: "local-ui", decision,
    });
    await refreshList();
    await openTask(state.task.id, decision === "approved" ? "draft" : "diff");
    notice(decision === "approved" ? "diff/test 组合已批准。下一步冻结 Draft PR 请求。" : "diff 已拒绝。 ");
  } catch (error) { report(error); }
}

function addRequestField(key, value) {
  const box = element("div", `request-field${key === "body" ? " wide" : ""}`);
  box.append(element("small", "", key));
  box.append(element(key === "body" ? "pre" : "div", "", typeof value === "string" ? value : JSON.stringify(value)));
  $("draft-request-fields").append(box);
}
function renderDraft() {
  const task = state.task;
  if (!task) return;
  const canFreeze = ["approved_for_pr", "publish_rejected"].includes(task.status);
  $("draft-form-wrap").hidden = !canFreeze;
  text($("draft-state"), statusLabel(task.status));
  if (canFreeze && !$("draft-head").value) $("draft-head").value = `agent/issue-${task.id.slice(0, 8)}`;
  if (canFreeze && !$("draft-title-input").value) $("draft-title-input").value = (task.issue.split(/\r?\n/).find((line) => line.trim()) || "Fix issue").trim().slice(0, 256);
  const artifact = state.artifacts.draft_pr_request;
  $("draft-preview").hidden = !artifact;
  if (artifact) {
    $("draft-request-fields").replaceChildren();
    let requestValid = false;
    try {
      const request = JSON.parse(artifact.content);
      requestValid = request && typeof request === "object" && request.draft === true;
      for (const [key, value] of Object.entries(request)) addRequestField(key, value);
    } catch (_) { addRequestField("error", "请求内容无法解析，不能审批。"); }
    text($("publish-hash"), `发布审批对象 SHA-256：${artifact.approval_subject_sha256 || "无"}`);
    $("approve-publish-button").disabled = task.status !== "awaiting_publish_approval" || !artifact.approval_subject_sha256 || !requestValid;
    $("reject-publish-button").disabled = $("approve-publish-button").disabled;
    $("draft-preview").querySelector(".action-bar").hidden = task.status !== "awaiting_publish_approval";
  }
  $("ready-panel").hidden = task.status !== "ready_to_publish";
  $("simulate-button").hidden = !state.fakePublisher;
  const simulated = state.fakePublisher && task.status === "draft_pr_created" && (() => {
    try { return JSON.parse(state.artifacts.draft_pr?.content || "null")?.simulated === true; }
    catch (_) { return false; }
  })();
  $("simulated-panel").hidden = !simulated;
  if (simulated) text($("simulated-detail"), "已记录模拟 Draft PR。没有 Git commit、push 或 GitHub PR。 ");
}
async function freezeRequest(event) {
  event.preventDefault();
  if (!state.task || !["approved_for_pr", "publish_rejected"].includes(state.task.status)) return;
  const button = $("freeze-request-button");
  const payload = {
    repository: $("draft-repository").value.trim(), base_branch: $("draft-base").value.trim(),
    head_branch: $("draft-head").value.trim(), remote: $("draft-remote").value.trim(),
    title: $("draft-title-input").value.trim(),
  };
  if ($("draft-body").value.trim()) payload.body = $("draft-body").value;
  if ($("draft-commit").value.trim()) payload.commit_message = $("draft-commit").value.trim();
  setBusy(button, true, "冻结 Draft PR 请求");
  try {
    await api("POST", `/tasks/${state.task.id}/draft-pr`, payload);
    await refreshList();
    await openTask(state.task.id, "draft");
    notice("Draft PR 请求已冻结。请核对全部字段和审批对象哈希。 ");
  } catch (error) { report(error); }
  finally { setBusy(button, false, "冻结 Draft PR 请求"); }
}
async function decidePublish(decision) {
  const subject = state.artifacts.draft_pr_request?.approval_subject_sha256;
  if (!subject || state.task?.status !== "awaiting_publish_approval") return;
  try {
    await api("POST", `/tasks/${state.task.id}/approvals/publish`, {subject_hash: subject, actor: "local-ui", decision});
    await refreshList();
    await openTask(state.task.id, "draft");
    notice(decision === "approved" ? "发布请求已批准，流程停在 ready_to_publish；不会自动创建真实 PR。" : "发布请求已拒绝。 ");
  } catch (error) { report(error); }
}
async function simulateDraft() {
  const subject = state.artifacts.draft_pr_request?.approval_subject_sha256;
  if (!state.fakePublisher || !subject || state.task?.status !== "ready_to_publish") return;
  const button = $("simulate-button");
  setBusy(button, true, "模拟发布 Draft PR");
  try {
    const {body} = await api("POST", `/tasks/${state.task.id}/draft-pr/simulate`, {subject_hash: subject});
    if (body.simulated !== true) throw new Error("服务未确认 simulated=true，不能显示模拟发布成功。 ");
    await refreshList();
    await openTask(state.task.id, "draft");
    notice("模拟发布已记录。没有提交、推送或创建 GitHub PR。 ");
  } catch (error) { report(error); }
  finally { setBusy(button, false, "模拟发布 Draft PR"); }
}

function wireEvents() {
  $("new-task-button").addEventListener("click", showCreate);
  $("refresh-list-button").addEventListener("click", () => refreshList().catch(report));
  $("refresh-task-button").addEventListener("click", () => state.task && openTask(state.task.id, state.tab).catch(report));
  $("mode-local").addEventListener("click", () => setCreateMode("local"));
  $("mode-github").addEventListener("click", () => setCreateMode("github"));
  $("create-form").addEventListener("submit", createTask);
  for (const button of document.querySelectorAll(".detail-tab")) button.addEventListener("click", () => setTab(button.dataset.tab).catch(report));
  $("approve-plan-button").addEventListener("click", () => decidePlan("approved"));
  $("reject-plan-button").addEventListener("click", () => decidePlan("rejected"));
  $("execute-button").addEventListener("click", executeTask);
  $("diff-prev").addEventListener("click", () => { state.diffPage -= 1; renderDiffPage(); });
  $("diff-next").addEventListener("click", () => { state.diffPage += 1; renderDiffPage(); });
  $("diff-full-button").addEventListener("click", showFullDiff);
  $("close-diff-dialog").addEventListener("click", () => $("full-diff-dialog").close());
  $("approve-diff-button").addEventListener("click", () => decideDiff("approved"));
  $("reject-diff-button").addEventListener("click", () => decideDiff("rejected"));
  $("draft-form").addEventListener("submit", freezeRequest);
  $("approve-publish-button").addEventListener("click", () => decidePublish("approved"));
  $("reject-publish-button").addEventListener("click", () => decidePublish("rejected"));
  $("simulate-button").addEventListener("click", simulateDraft);
}
async function initialize() {
  wireEvents();
  try {
    const {body: config} = await api("GET", "/ui-config");
    state.fakePublisher = config.fake_publisher === true;
    await refreshList();
    let requested = null;
    try { requested = new URLSearchParams(location.hash.slice(1)).get("task"); } catch (_) { /* Ignore malformed hash. */ }
    const initial = state.tasks.find((task) => task.id === requested) || state.tasks[0];
    if (initial) await openTask(initial.id);
    else showCreate();
    // Do not start polling here. Only a successful POST /execute 202 may do that.
  } catch (error) { report(error); showCreate(); }
}

initialize();
