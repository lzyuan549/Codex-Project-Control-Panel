const loginView = document.querySelector("#loginView");
const appView = document.querySelector("#appView");
const loginForm = document.querySelector("#loginForm");
const loginError = document.querySelector("#loginError");
const passwordInput = document.querySelector("#passwordInput");
const logoutButton = document.querySelector("#logoutButton");
const gatewayBadge = document.querySelector("#gatewayBadge");
const uploadForm = document.querySelector("#uploadForm");
const inputPanelTitle = document.querySelector("#inputPanelTitle");
const uploadModePlan = document.querySelector("#uploadModePlan");
const uploadModeDocuments = document.querySelector("#uploadModeDocuments");
const projectGoalInput = document.querySelector("#projectGoalInput");
const workspaceNameInput = document.querySelector("#workspaceNameInput");
const workspaceRootValue = document.querySelector("#workspaceRootValue");
const projectZipInput = document.querySelector("#projectZipInput");
const projectZipName = document.querySelector("#projectZipName");
const constraintsInput = document.querySelector("#constraintsInput");
const constraintsName = document.querySelector("#constraintsName");
const documentImportFields = document.querySelector("#documentImportFields");
const planFileInput = document.querySelector("#planFileInput");
const handoffFileInput = document.querySelector("#handoffFileInput");
const testReportFileInput = document.querySelector("#testReportFileInput");
const planFileName = document.querySelector("#planFileName");
const handoffFileName = document.querySelector("#handoffFileName");
const testReportFileName = document.querySelector("#testReportFileName");
const uploadMessage = document.querySelector("#uploadMessage");
const uploadButton = document.querySelector("#uploadButton");
const planButton = document.querySelector("#planButton");
const revisePlanButton = document.querySelector("#revisePlanButton");
const startButton = document.querySelector("#startButton");
const stopButton = document.querySelector("#stopButton");
const stateValue = document.querySelector("#stateValue");
const pendingValue = document.querySelector("#pendingValue");
const completedValue = document.querySelector("#completedValue");
const roundValue = document.querySelector("#roundValue");
const lastMessage = document.querySelector("#lastMessage");
const logsList = document.querySelector("#logsList");
const filesList = document.querySelector("#filesList");
const refreshLogsButton = document.querySelector("#refreshLogsButton");
const downloadLink = document.querySelector("#downloadLink");
const planDocTab = document.querySelector("#planDocTab");
const handoffDocTab = document.querySelector("#handoffDocTab");
const testReportDocTab = document.querySelector("#testReportDocTab");
const documentBox = document.querySelector("#documentBox");
const revisionFeedbackInput = document.querySelector("#revisionFeedbackInput");
const refreshHistoryButton = document.querySelector("#refreshHistoryButton");
const historyList = document.querySelector("#historyList");
const historyTitle = document.querySelector("#historyTitle");
const historyMeta = document.querySelector("#historyMeta");
const historyDownloadLink = document.querySelector("#historyDownloadLink");
const historyPlanTab = document.querySelector("#historyPlanTab");
const historyHandoffTab = document.querySelector("#historyHandoffTab");
const historyTestReportTab = document.querySelector("#historyTestReportTab");
const historyLogsTab = document.querySelector("#historyLogsTab");
const historyFilesTab = document.querySelector("#historyFilesTab");
const historyNotice = document.querySelector("#historyNotice");
const historyDocumentBox = document.querySelector("#historyDocumentBox");
const historyLogsList = document.querySelector("#historyLogsList");
const historyFilesList = document.querySelector("#historyFilesList");
const promptTemplateTabs = Array.from(document.querySelectorAll("[data-prompt-template]"));
const promptTemplateTitle = document.querySelector("#promptTemplateTitle");
const promptTemplateMeta = document.querySelector("#promptTemplateMeta");
const promptTemplateEditor = document.querySelector("#promptTemplateEditor");
const promptPlaceholderList = document.querySelector("#promptPlaceholderList");
const promptTemplateMessage = document.querySelector("#promptTemplateMessage");
const refreshPromptTemplateButton = document.querySelector("#refreshPromptTemplateButton");
const savePromptTemplateButton = document.querySelector("#savePromptTemplateButton");
const sectionNavButtons = Array.from(document.querySelectorAll("[data-section-target]"));
const sectionPanels = Array.from(document.querySelectorAll("[data-section-panel]"));

let authenticated = false;
let pollTimer = null;
let currentState = "idle";
let selectedHistoryId = null;
let activeDocument = "plan";
let activeHistoryView = "plan";
let activeSection = "project";
let uploadMode = "plan";
let activePromptTemplate = "planning";
let promptTemplates = [];
let promptTemplateDirty = false;

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: options.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...options,
  });

  if (response.status === 401) {
    showLogin();
    throw new Error("请先登录。");
  }

  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const message = data && data.detail ? data.detail : "请求失败。";
    throw new Error(message);
  }
  return data;
}

function showLogin() {
  authenticated = false;
  loginView.hidden = false;
  appView.hidden = true;
  if (pollTimer) clearInterval(pollTimer);
}

function showApp() {
  authenticated = true;
  loginView.hidden = true;
  appView.hidden = false;
  setActiveSection(activeSection);
  startPolling();
  refreshPromptTemplates();
}

function stateLabel(state) {
  const labels = {
    idle: "空闲",
    uploaded: "已上传",
    queued: "已上传",
    planning: "规划中",
    awaiting_start: "等待开始",
    revising: "修订中",
    running: "执行中",
    stopping: "停止中",
    stopped: "已停止",
    completed: "已完成",
    failed: "失败",
    legacy: "历史记录",
  };
  return labels[state] || state;
}

function setBusy(state) {
  const active = ["planning", "revising", "running", "stopping"].includes(state);
  const hasJob = state !== "idle";
  uploadButton.disabled = active;
  planButton.disabled = active || !["uploaded", "failed", "stopped"].includes(state);
  revisePlanButton.disabled = active || state !== "awaiting_start";
  startButton.disabled = active || !["awaiting_start", "stopped"].includes(state);
  stopButton.disabled = !["planning", "revising", "running"].includes(state);
  downloadLink.classList.toggle("disabled-link", !hasJob);
}

function setActiveSection(section) {
  const nextPanel = sectionPanels.find((panel) => panel.dataset.sectionPanel === section);
  if (!nextPanel) return;

  activeSection = section;
  sectionNavButtons.forEach((button) => {
    const active = button.dataset.sectionTarget === section;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  sectionPanels.forEach((panel) => {
    const active = panel.dataset.sectionPanel === section;
    panel.hidden = !active;
    panel.classList.toggle("active", active);
  });
}

function setFileName(input, target, fallback) {
  target.textContent = input.files[0] ? input.files[0].name : fallback;
}

function updateProjectZipName() {
  const fallback =
    uploadMode === "documents"
      ? "可选上传 auth-only.zip 或现有项目 ZIP"
      : "可上传 auth-only.zip 或包含 auth-only/README.md 的项目 ZIP";
  setFileName(projectZipInput, projectZipName, fallback);
}

function setUploadMode(mode) {
  uploadMode = mode;
  uploadMessage.textContent = "";
  uploadModePlan.classList.toggle("active", mode === "plan");
  uploadModeDocuments.classList.toggle("active", mode === "documents");
  uploadModePlan.setAttribute("aria-pressed", String(mode === "plan"));
  uploadModeDocuments.setAttribute("aria-pressed", String(mode === "documents"));
  documentImportFields.hidden = mode !== "documents";
  planButton.hidden = mode === "documents";
  inputPanelTitle.textContent = mode === "documents" ? "执行文档与项目" : "基础项目与题目";
  uploadButton.textContent = mode === "documents" ? "导入文档" : "上传项目";
  projectGoalInput.placeholder =
    mode === "documents"
      ? "可选：给这次导入的执行任务起一个名字"
      : "校园综合服务网页，课程表/失物/二手/公告等全整合，适配手机端";
  updateProjectZipName();
}

function renderWorkspaceRoot(value) {
  const workspaceRoot = value || "/www/wwwroot";
  workspaceRootValue.textContent = workspaceRoot.replace(/[\\/]+$/, "");
}

function updateStatus(payload) {
  const state = payload.state || "idle";
  currentState = state;
  const job = payload.job || {};
  stateValue.textContent = stateLabel(state);
  pendingValue.textContent = job.pending_tasks ?? 0;
  completedValue.textContent = job.completed_tasks ?? 0;
  roundValue.textContent = job.current_round ?? 0;
  lastMessage.textContent = job.failure_reason || job.last_message || "等待运行。";
  setBusy(state);
}

async function refreshStatus() {
  if (!authenticated) return;
  try {
    const status = await api("/api/job/status");
    updateStatus(status);
    await Promise.all([refreshLogs(), refreshFiles(), refreshCurrentDocument(), refreshHistory(false)]);
  } catch (error) {
    uploadMessage.textContent = error.message;
  }
}

async function refreshCurrentDocument() {
  if (!authenticated || currentState === "idle") {
    documentBox.textContent = "等待生成规划。";
    return;
  }
  try {
    const data = await api(`/api/documents/${activeDocument}`);
    documentBox.textContent = data.available ? data.content : `${data.filename} 暂未生成。`;
  } catch (error) {
    documentBox.textContent = error.message;
  }
}

async function refreshLogs() {
  if (!authenticated) return;
  const data = await api("/api/job/logs");
  renderLogs(logsList, data.logs);
}

async function refreshFiles() {
  if (!authenticated) return;
  const data = await api("/api/files");
  renderFiles(filesList, data.files);
}

function currentPromptTemplate() {
  return promptTemplates.find((template) => template.id === activePromptTemplate);
}

async function refreshPromptTemplates() {
  if (!authenticated) return;
  if (promptTemplateDirty && !window.confirm("当前提示词还没有保存，确定刷新吗？")) return;
  promptTemplateMessage.textContent = "";
  try {
    const data = await api("/api/prompt-templates");
    promptTemplates = data.templates;
    if (!currentPromptTemplate() && promptTemplates.length) {
      activePromptTemplate = promptTemplates[0].id;
    }
    renderPromptTemplate();
  } catch (error) {
    promptTemplateMessage.textContent = error.message;
  }
}

function renderPromptTemplate() {
  const template = currentPromptTemplate();
  promptTemplateTabs.forEach((button) => {
    button.classList.toggle("active", button.dataset.promptTemplate === activePromptTemplate);
  });

  if (!template) {
    promptTemplateTitle.textContent = "未加载";
    promptTemplateMeta.textContent = "没有可编辑的提示词模板";
    promptTemplateEditor.value = "";
    promptPlaceholderList.innerHTML = "";
    return;
  }

  promptTemplateTitle.textContent = template.label;
  promptTemplateMeta.textContent = `${template.filename} · ${formatSize(template.size || 0)} · ${formatTime(template.updated_at)}`;
  promptTemplateEditor.value = template.content;
  promptPlaceholderList.innerHTML = (template.required_placeholders || [])
    .map((placeholder) => `<span>{{${escapeHtml(placeholder)}}}</span>`)
    .join("");
  promptTemplateDirty = false;
}

function selectPromptTemplate(templateId) {
  if (templateId === activePromptTemplate) return;
  if (promptTemplateDirty && !window.confirm("当前提示词还没有保存，确定切换吗？")) return;
  activePromptTemplate = templateId;
  promptTemplateMessage.textContent = "";
  renderPromptTemplate();
}

async function savePromptTemplate() {
  const template = currentPromptTemplate();
  if (!template) return;
  promptTemplateMessage.textContent = "";
  savePromptTemplateButton.disabled = true;
  try {
    const data = await api(`/api/prompt-templates/${encodeURIComponent(activePromptTemplate)}`, {
      method: "PUT",
      body: JSON.stringify({ content: promptTemplateEditor.value }),
    });
    const index = promptTemplates.findIndex((item) => item.id === data.template.id);
    if (index >= 0) {
      promptTemplates[index] = data.template;
    }
    promptTemplateDirty = false;
    renderPromptTemplate();
    promptTemplateMessage.textContent = "已保存。";
  } catch (error) {
    promptTemplateMessage.textContent = error.message;
  } finally {
    savePromptTemplateButton.disabled = false;
  }
}

function renderLogs(container, logs) {
  if (!logs.length) {
    container.innerHTML = '<p class="muted-line">还没有轮次日志。</p>';
    return;
  }
  container.innerHTML = logs
    .map((entry, index) => {
      const body = [
        entry.final_message && `FINAL\n${entry.final_message}`,
        entry.stderr && `STDERR\n${entry.stderr}`,
        entry.stdout && `STDOUT\n${entry.stdout}`,
        entry.prompt && `PROMPT\n${entry.prompt}`,
      ]
        .filter(Boolean)
        .join("\n\n");
      return `
        <details class="log-card" ${index === logs.length - 1 ? "open" : ""}>
          <summary>${escapeHtml(entry.round)}</summary>
          <pre>${escapeHtml(body || "暂无内容。")}</pre>
        </details>
      `;
    })
    .join("");
}

function renderFiles(container, files) {
  if (!files.length) {
    container.innerHTML = '<p class="muted-line">工作区暂无文件。</p>';
    return;
  }
  container.innerHTML = files
    .map((item) => {
      const icon = item.type === "directory" ? "dir" : "file";
      const size = item.type === "file" ? formatSize(item.size || 0) : "";
      return `<div class="file-row"><span>${icon}</span><strong>${escapeHtml(item.path)}</strong><small>${size}</small></div>`;
    })
    .join("");
}

async function refreshHistory(reloadDetail = true) {
  if (!authenticated) return;
  const data = await api("/api/history");
  renderHistoryList(data.jobs);
  if (!data.jobs.length) {
    selectedHistoryId = null;
    resetHistoryDetail();
    return;
  }

  if (!selectedHistoryId || !data.jobs.some((job) => job.id === selectedHistoryId)) {
    selectedHistoryId = data.jobs[0].id;
    activeHistoryView = "plan";
    reloadDetail = true;
  }
  if (reloadDetail) {
    await loadHistoryDetail();
  }
}

function renderHistoryList(jobs) {
  if (!jobs.length) {
    historyList.innerHTML = '<p class="muted-line">暂无历史上传。</p>';
    return;
  }
  historyList.innerHTML = jobs
    .map((job) => {
      const active = job.id === selectedHistoryId ? " active" : "";
      const title = job.project_goal ? job.project_goal.slice(0, 44) : job.id;
      return `
        <button class="history-item${active}" type="button" data-job-id="${escapeHtml(job.id)}">
          <strong>${escapeHtml(title)}</strong>
          <small>${escapeHtml(formatTime(job.created_at))} · ${escapeHtml(job.id)}</small>
          <div class="history-stats">
            <span>${escapeHtml(stateLabel(job.state))}</span>
            <span>剩 ${job.pending_tasks ?? 0}</span>
            <span>完 ${job.completed_tasks ?? 0}</span>
            <span>${job.round_count ?? 0} 轮</span>
          </div>
        </button>
      `;
    })
    .join("");

  historyList.querySelectorAll("[data-job-id]").forEach((button) => {
    button.addEventListener("click", () => selectHistory(button.dataset.jobId));
  });
}

async function selectHistory(jobId) {
  selectedHistoryId = jobId;
  activeHistoryView = "plan";
  await refreshHistory(true);
}

function resetHistoryDetail() {
  historyTitle.textContent = "未选择历史任务";
  historyMeta.textContent = "选择左侧记录查看文档、日志和产物。";
  historyDownloadLink.href = "#";
  historyDownloadLink.classList.add("disabled-link");
  historyDocumentBox.textContent = "暂无历史记录。";
  historyLogsList.innerHTML = "";
  historyFilesList.innerHTML = "";
  historyNotice.textContent = "";
}

async function loadHistoryDetail() {
  if (!selectedHistoryId) {
    resetHistoryDetail();
    return;
  }

  const data = await api(`/api/history/${encodeURIComponent(selectedHistoryId)}`);
  const job = data.job;
  historyTitle.textContent = `${formatTime(job.created_at)} · ${stateLabel(job.state)}`;
  const workspaceText = job.workspace_path ? ` · ${job.workspace_path}` : "";
  historyMeta.textContent = `剩余 ${job.pending_tasks ?? 0} / 完成 ${job.completed_tasks ?? 0} / 共 ${job.total_tasks ?? 0}，${job.round_count ?? 0} 轮${workspaceText}`;
  historyDownloadLink.href = `/api/history/${encodeURIComponent(selectedHistoryId)}/download`;
  historyDownloadLink.classList.remove("disabled-link");
  await showHistoryView(activeHistoryView);
}

async function showHistoryView(view) {
  if (!selectedHistoryId) return;
  activeHistoryView = view;
  for (const button of [historyPlanTab, historyHandoffTab, historyTestReportTab, historyLogsTab, historyFilesTab]) {
    button.classList.remove("active");
  }
  historyPlanTab.classList.toggle("active", view === "plan");
  historyHandoffTab.classList.toggle("active", view === "handoff");
  historyTestReportTab.classList.toggle("active", view === "test_report");
  historyLogsTab.classList.toggle("active", view === "logs");
  historyFilesTab.classList.toggle("active", view === "files");

  historyDocumentBox.hidden = view === "logs" || view === "files";
  historyLogsList.hidden = view !== "logs";
  historyFilesList.hidden = view !== "files";
  historyNotice.textContent = "";

  if (["plan", "handoff", "test_report"].includes(view)) {
    const data = await api(`/api/history/${encodeURIComponent(selectedHistoryId)}/documents/${view}`);
    historyDocumentBox.textContent = data.available ? data.content : `${data.filename} 暂未生成。`;
    return;
  }
  if (view === "logs") {
    const data = await api(`/api/history/${encodeURIComponent(selectedHistoryId)}/logs`);
    renderLogs(historyLogsList, data.logs);
    return;
  }
  const data = await api(`/api/history/${encodeURIComponent(selectedHistoryId)}/files`);
  renderFiles(historyFilesList, data.files);
}

function setActiveDocument(documentName) {
  activeDocument = documentName;
  for (const button of [planDocTab, handoffDocTab, testReportDocTab]) {
    button.classList.remove("active");
  }
  planDocTab.classList.toggle("active", documentName === "plan");
  handoffDocTab.classList.toggle("active", documentName === "handoff");
  testReportDocTab.classList.toggle("active", documentName === "test_report");
  refreshCurrentDocument();
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatSize(size) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function formatTime(seconds) {
  if (!seconds) return "-";
  return new Date(seconds * 1000).toLocaleString("zh-CN", { hour12: false });
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  refreshStatus();
  pollTimer = setInterval(refreshStatus, 3000);
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  loginError.textContent = "";
  try {
    await api("/api/login", {
      method: "POST",
      body: JSON.stringify({ password: passwordInput.value }),
    });
    passwordInput.value = "";
    await bootstrap();
  } catch (error) {
    loginError.textContent = error.message;
  }
});

logoutButton.addEventListener("click", async () => {
  await api("/api/logout", { method: "POST", body: "{}" }).catch(() => {});
  showLogin();
});

uploadModePlan.addEventListener("click", () => setUploadMode("plan"));
uploadModeDocuments.addEventListener("click", () => setUploadMode("documents"));
sectionNavButtons.forEach((button) => {
  button.addEventListener("click", () => setActiveSection(button.dataset.sectionTarget));
});
promptTemplateTabs.forEach((button) => {
  button.addEventListener("click", () => selectPromptTemplate(button.dataset.promptTemplate));
});
promptTemplateEditor.addEventListener("input", () => {
  promptTemplateDirty = true;
  promptTemplateMessage.textContent = "有未保存修改。";
});
refreshPromptTemplateButton.addEventListener("click", refreshPromptTemplates);
savePromptTemplateButton.addEventListener("click", savePromptTemplate);
projectZipInput.addEventListener("change", updateProjectZipName);
planFileInput.addEventListener("change", () => setFileName(planFileInput, planFileName, "选择执行计划文档"));
handoffFileInput.addEventListener("change", () => setFileName(handoffFileInput, handoffFileName, "选择交接文档"));
testReportFileInput.addEventListener("change", () =>
  setFileName(testReportFileInput, testReportFileName, "选择测试记录文档")
);

constraintsInput.addEventListener("change", () => {
  const count = constraintsInput.files.length;
  constraintsName.textContent = count ? `已选择 ${count} 个约束文件` : "可多选 .md / .txt 约束文件";
});

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  uploadMessage.textContent = "";
  const importingDocuments = uploadMode === "documents";
  if (!importingDocuments && !projectGoalInput.value.trim()) {
    uploadMessage.textContent = "请填写项目题目。";
    return;
  }
  if (!importingDocuments && !projectZipInput.files[0]) {
    uploadMessage.textContent = "请先选择基础项目 ZIP。";
    return;
  }
  if (importingDocuments && (!planFileInput.files[0] || !handoffFileInput.files[0] || !testReportFileInput.files[0])) {
    uploadMessage.textContent = "请上传 PLAN.md、HANDOFF.md 和 TEST_REPORT.md。";
    return;
  }

  const form = new FormData();
  form.append("project_goal", projectGoalInput.value.trim());
  const rawWorkspaceName = workspaceNameInput.value;
  const workspaceName = rawWorkspaceName.trim();
  if (rawWorkspaceName && !workspaceName) {
    uploadMessage.textContent = "工作区文件夹不能只包含空格。";
    return;
  }
  if (workspaceName) {
    form.append("workspace_name", workspaceName);
  }
  if (projectZipInput.files[0]) {
    form.append("project_zip", projectZipInput.files[0]);
  }
  if (importingDocuments) {
    form.append("plan", planFileInput.files[0]);
    form.append("handoff", handoffFileInput.files[0]);
    form.append("test_report", testReportFileInput.files[0]);
  }
  for (const file of constraintsInput.files) {
    form.append("constraints", file);
  }

  try {
    const data = await api(importingDocuments ? "/api/upload-documents" : "/api/upload", { method: "POST", body: form });
    const workspaceText = data.job && data.job.workspace_path ? ` 工作区：${data.job.workspace_path}` : "";
    uploadMessage.textContent = importingDocuments ? `文档已导入，可开始执行。${workspaceText}` : `上传完成，可以生成规划。${workspaceText}`;
    await refreshStatus();
    if (importingDocuments) {
      setActiveDocument("plan");
      setActiveSection("documents");
    }
  } catch (error) {
    uploadMessage.textContent = error.message;
  }
});

planButton.addEventListener("click", async () => {
  uploadMessage.textContent = "";
  try {
    await api("/api/job/plan", { method: "POST", body: "{}" });
    uploadMessage.textContent = "规划已开始生成。";
    await refreshStatus();
    setActiveSection("documents");
  } catch (error) {
    uploadMessage.textContent = error.message;
  }
});

revisePlanButton.addEventListener("click", async () => {
  uploadMessage.textContent = "";
  const feedback = revisionFeedbackInput.value.trim();
  if (!feedback) {
    uploadMessage.textContent = "请先填写规划反馈。";
    return;
  }
  try {
    await api("/api/job/revise-plan", {
      method: "POST",
      body: JSON.stringify({ feedback }),
    });
    revisionFeedbackInput.value = "";
    uploadMessage.textContent = "规划修订已开始。";
    await refreshStatus();
    setActiveSection("documents");
  } catch (error) {
    uploadMessage.textContent = error.message;
  }
});

startButton.addEventListener("click", async () => {
  uploadMessage.textContent = "";
  try {
    await api("/api/job/start", { method: "POST", body: "{}" });
    uploadMessage.textContent = "执行已开始。";
    await refreshStatus();
    setActiveSection("logs");
  } catch (error) {
    uploadMessage.textContent = error.message;
  }
});

stopButton.addEventListener("click", async () => {
  uploadMessage.textContent = "";
  try {
    await api("/api/job/stop", { method: "POST", body: "{}" });
    await refreshStatus();
  } catch (error) {
    uploadMessage.textContent = error.message;
  }
});

refreshLogsButton.addEventListener("click", refreshLogs);
refreshHistoryButton.addEventListener("click", () => refreshHistory(true));
planDocTab.addEventListener("click", () => setActiveDocument("plan"));
handoffDocTab.addEventListener("click", () => setActiveDocument("handoff"));
testReportDocTab.addEventListener("click", () => setActiveDocument("test_report"));
historyPlanTab.addEventListener("click", () => showHistoryView("plan"));
historyHandoffTab.addEventListener("click", () => showHistoryView("handoff"));
historyTestReportTab.addEventListener("click", () => showHistoryView("test_report"));
historyLogsTab.addEventListener("click", () => showHistoryView("logs"));
historyFilesTab.addEventListener("click", () => showHistoryView("files"));
downloadLink.addEventListener("click", (event) => {
  if (!authenticated || currentState === "idle") event.preventDefault();
});
historyDownloadLink.addEventListener("click", (event) => {
  if (!authenticated || !selectedHistoryId) event.preventDefault();
});

async function bootstrap() {
  try {
    const session = await api("/api/session");
    gatewayBadge.textContent = session.gateway_configured ? `模型 ${session.model}` : "网关未完整配置";
    gatewayBadge.style.color = session.gateway_configured ? "#075e55" : "#b23b3b";
    renderWorkspaceRoot(session.workspace_root);
    showApp();
  } catch {
    showLogin();
  }
}

setUploadMode("plan");
setActiveSection("project");
bootstrap();
