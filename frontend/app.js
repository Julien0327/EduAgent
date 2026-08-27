// 请求后端健康检查接口，并把结果展示在页面上
async function checkHealth() {
  const dot = document.getElementById("status-dot");
  const statusText = document.getElementById("status-text");
  const statusJson = document.getElementById("status-json");

  try {
    const resp = await fetch("/api/health");
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    }
    const data = await resp.json();
    dot.classList.add("ok");
    statusText.textContent = `后端已连接（${data.service}）`;
    statusJson.textContent = JSON.stringify(data, null, 2);
  } catch (err) {
    dot.classList.add("error");
    statusText.textContent = "后端连接失败";
    statusJson.textContent = `无法访问 /api/health：${err.message}`;
  }
}

checkHealth();

// ===== Markdown + LaTeX 渲染（安全） =====
// 使用本地静态库：marked（Markdown→HTML）、DOMPurify（防 XSS）、KaTeX（数学公式）。
// 流程：先抽出 LaTeX 用 KaTeX 渲染为占位符 → marked 渲染 Markdown → 还原 KaTeX HTML → DOMPurify 清洗。

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function renderRichText(text) {
  const tokens = [];

  const pushToken = (expr, display) => {
    const id = "@@MATH" + tokens.length + "@@";
    let html;
    try {
      html = katex.renderToString(expr, {
        displayMode: display,
        throwOnError: false,
        output: "html",
      });
    } catch (err) {
      // KaTeX 渲染失败时回退为纯文本，避免页面出现原始 \[ \] 标记
      html = escapeHtml(expr);
    }
    tokens.push({ id: id, html: html });
    return id;
  };

  let src = text;

  // 块级公式：\[ ... \] 与 $$ ... $$
  src = src.replace(/\\\[([\s\S]*?)\\\]/g, (_, e) => pushToken(e.trim(), true));
  src = src.replace(/\$\$([\s\S]*?)\$\$/g, (_, e) => pushToken(e.trim(), true));
  // 行内公式：\( ... \) 与 $ ... $
  src = src.replace(/\\\(([\s\S]*?)\\\)/g, (_, e) => pushToken(e.trim(), false));
  src = src.replace(/\$([^$\n]+?)\$/g, (_, e) => pushToken(e.trim(), false));

  // Markdown → HTML
  let html = marked.parse(src);

  // 还原 KaTeX 渲染结果
  for (const t of tokens) {
    html = html.split(t.id).join(t.html);
  }

  // XSS 防护：清洗最终 HTML（去除脚本、危险属性等）
  return DOMPurify.sanitize(html);
}

// ===== AI 对话测试：POST /api/chat =====
async function sendChat() {
  const input = document.getElementById("chat-input");
  const statusEl = document.getElementById("chat-status");
  const replyEl = document.getElementById("chat-reply");

  const message = input.value.trim();
  if (!message) {
    statusEl.textContent = "请输入内容";
    return;
  }

  statusEl.textContent = "正在请求…";
  replyEl.textContent = "";

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      statusEl.textContent = `请求失败（HTTP ${resp.status}）：${data.detail || "未知错误"}`;
      return;
    }
    statusEl.textContent = `已回复（模型：${data.model}）`;
    replyEl.innerHTML = renderRichText(data.message);
  } catch (err) {
    statusEl.textContent = `请求失败：${err.message}`;
  }
}

document.getElementById("chat-send").addEventListener("click", sendChat);

// ===== Agent 任务：POST /api/tasks + SSE 实时进度（D4） =====

const NODE_LABELS = {
  understand_task: "理解任务",
  fetch_knowledge: "检索知识点",
  generate_exam: "出卷",
  build_exam_docx: "生成试卷",
  build_answer_sheet: "生成答题卡",
  load_student_answers: "加载学生作答",
  analyze_scores: "成绩分析",
  diagnose: "学情诊断",
  build_report_pdf: "生成学情报告",
  generate_lesson_plan_content: "生成教案正文",
  build_lesson_plan_docx: "生成教案文档",
};

const STATUS_LABELS = { running: "执行中…", completed: "已完成", failed: "失败" };
// 产物「名称 → 预览类型」；学情报告(PDF)走内联文件，浏览器原生渲染
const PREVIEW_KIND = { 试卷: "exam", 答题卡: "answer_sheet", 教案: "lesson" };

let currentTaskId = null;
let steps = [];
let artifacts = [];
let taskEventSource = null;

function $(id) {
  return document.getElementById(id);
}

function taskStatusEl() {
  return $("task-status");
}

function setTaskStatus(status) {
  const el = taskStatusEl();
  el.textContent = STATUS_LABELS[status] || status;
  el.className =
    "task-status " +
    (status === "failed" ? "task-failed" : status === "completed" ? "task-done" : "muted");
}

function renderSteps() {
  const list = $("task-timeline");
  list.innerHTML = "";
  for (const s of steps) {
    const li = document.createElement("li");
    li.className = "step step-" + s.status;
    const name = NODE_LABELS[s.node] || s.node;
    li.innerHTML =
      `<span class="step-dot"></span><span class="step-name">${escapeHtml(name)}</span>` +
      (s.message ? `<span class="step-msg">${escapeHtml(s.message)}</span>` : "");
    list.appendChild(li);
  }
}

function setSteps(list) {
  steps = (list || []).slice();
  renderSteps();
}

function upsertStep(node, status, message) {
  const i = steps.findIndex((s) => s.node === node);
  if (i >= 0) steps[i] = { node, status, message };
  else steps.push({ node, status, message });
  renderSteps();
}

function downloadUrl(file) {
  return `/api/tasks/${currentTaskId}/files/${encodeURIComponent(file.file_name)}`;
}

function previewUrl(file) {
  const kind = PREVIEW_KIND[file.name];
  if (kind) return `/api/tasks/${currentTaskId}/preview/${kind}`;
  return `/api/tasks/${currentTaskId}/files/${encodeURIComponent(file.file_name)}?inline=true`;
}

function renderArtifacts() {
  const box = $("task-artifacts");
  box.innerHTML = "";
  for (const a of artifacts) {
    const card = document.createElement("div");
    card.className = "artifact";
    card.innerHTML = `
      <div class="artifact-name">${escapeHtml(a.name)}</div>
      <div class="artifact-file">${escapeHtml(a.file_name)}</div>
      <div class="artifact-actions">
        <button class="artifact-btn" data-action="preview" data-url="${escapeHtml(previewUrl(a))}" data-title="${escapeHtml(a.name)}">预览</button>
        <a class="artifact-btn" href="${escapeHtml(downloadUrl(a))}">下载</a>
      </div>`;
    box.appendChild(card);
  }
}

function setArtifacts(list) {
  artifacts = (list || []).slice();
  renderArtifacts();
}

function addArtifact(a) {
  if (!artifacts.some((x) => x.file_name === a.file_name)) {
    artifacts.push(a);
    renderArtifacts();
  }
}

function applySnapshot(snap) {
  if (snap.status) setTaskStatus(snap.status);
  setSteps(snap.steps);
  setArtifacts(snap.artifacts);
}

function finishTask() {
  $("task-submit").disabled = false;
  if (taskEventSource) {
    taskEventSource.close();
    taskEventSource = null;
  }
}

function handleEvent(msg) {
  switch (msg.type) {
    case "hello":
      applySnapshot(msg);
      break;
    case "step":
      upsertStep(msg.node, msg.status, msg.message);
      break;
    case "artifact":
      addArtifact(msg);
      break;
    case "status":
      setTaskStatus(msg.status);
      break;
  }
  if (msg.type === "done") return true;
  if (msg.type === "status" && (msg.status === "completed" || msg.status === "failed")) {
    return true;
  }
  return false;
}

async function pollTask(taskId) {
  if (taskId !== currentTaskId) return;
  try {
    const resp = await fetch(`/api/tasks/${taskId}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    applySnapshot(data);
    if (data.status === "completed" || data.status === "failed") {
      finishTask();
      return;
    }
    setTimeout(() => pollTask(taskId), 1000);
  } catch (err) {
    taskStatusEl().textContent = "进度同步中断：" + err.message;
    taskStatusEl().className = "task-status task-failed";
    finishTask();
  }
}

function streamEvents(taskId) {
  taskEventSource = new EventSource(`/api/tasks/${taskId}/events`);
  taskEventSource.onmessage = (ev) => {
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch (e) {
      return;
    }
    if (handleEvent(msg)) finishTask();
  };
  taskEventSource.onerror = () => {
    if (taskEventSource) {
      taskEventSource.close();
      taskEventSource = null;
    }
    pollTask(taskId); // 连接中断时回退为轮询兜底
  };
}

async function submitTask() {
  const input = $("task-input");
  const prompt = input.value.trim();
  if (!prompt) {
    taskStatusEl().textContent = "请输入任务描述";
    taskStatusEl().className = "task-status muted";
    return;
  }

  currentTaskId = null;
  setSteps([]);
  setArtifacts([]);
  setTaskStatus("running");
  $("task-submit").disabled = true;

  try {
    const resp = await fetch("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      taskStatusEl().textContent = `提交失败（HTTP ${resp.status}）：${data.detail || "未知错误"}`;
      taskStatusEl().className = "task-status task-failed";
      $("task-submit").disabled = false;
      return;
    }
    currentTaskId = data.task_id;
    applySnapshot(data); // 用 POST 返回的快照做首屏渲染
    streamEvents(currentTaskId);
  } catch (err) {
    taskStatusEl().textContent = "提交失败：" + err.message;
    taskStatusEl().className = "task-status task-failed";
    $("task-submit").disabled = false;
  }
}

function openPreview(url, title) {
  $("preview-title").textContent = title;
  $("preview-frame").src = url;
  $("preview-modal").classList.remove("hidden");
}

function closePreview() {
  $("preview-modal").classList.add("hidden");
  $("preview-frame").src = "about:blank";
}

$("task-submit").addEventListener("click", submitTask);
$("task-artifacts").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-action='preview']");
  if (btn) openPreview(btn.dataset.url, btn.dataset.title);
});
$("preview-close").addEventListener("click", closePreview);
$("preview-modal").addEventListener("click", (e) => {
  if (e.target.classList.contains("modal-backdrop")) closePreview();
});
