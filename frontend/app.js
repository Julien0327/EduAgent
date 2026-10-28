// 请求后端健康检查接口，并把结果展示在页面上
function setHealthIndicators(ok, label) {
  const dot = document.getElementById("status-dot");
  const statusText = document.getElementById("status-text");
  const sbDot = document.getElementById("sb-status-dot");
  const sbText = document.getElementById("sb-status-text");
  const rpDot = document.getElementById("rp-health-dot");
  const rpText = document.getElementById("rp-health-text");

  if (dot) {
    dot.classList.remove("ok", "error");
    dot.classList.add(ok ? "ok" : "error");
  }
  if (statusText) statusText.textContent = label;
  if (sbDot) {
    sbDot.classList.remove("ok", "error");
    sbDot.classList.add(ok ? "ok" : "error");
  }
  if (sbText) sbText.textContent = label;
  if (rpDot) {
    rpDot.classList.remove("ok", "error");
    rpDot.classList.add(ok ? "ok" : "error");
  }
  if (rpText) rpText.textContent = ok ? "已连接" : "连接失败";
}

async function checkHealth() {
  const statusJson = document.getElementById("status-json");
  try {
    const resp = await fetch("/api/health");
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    }
    const data = await resp.json();
    setHealthIndicators(true, `后端已连接（${data.service}）`);
    if (statusJson) statusJson.textContent = JSON.stringify(data, null, 2);
  } catch (err) {
    setHealthIndicators(false, "后端连接失败");
    if (statusJson) statusJson.textContent = `无法访问 /api/health：${err.message}`;
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
let currentWorkflow = null;   // 当前任务所属工作流（用于展示完整节点链路）
let lastParams = null;        // 最近一次快照的 params（stats / diagnosis / a2a_metadata）
let currentStatus = null;     // 任务级状态（running / completed / failed）
let lastResultKey = "";       // 结果面板内容签名，避免轮询时重复重绘

// 各工作流的完整节点顺序（与后端 WORKFLOW_NODES 保持一致，仅用于前端「待执行」占位展示）
const WORKFLOW_NODE_ORDER = {
  exam_loop: [
    "understand_task", "fetch_knowledge", "generate_exam", "build_exam_docx",
    "build_answer_sheet", "load_student_answers", "analyze_scores", "diagnose",
    "build_report_pdf",
  ],
  question_gen: [
    "understand_task", "fetch_knowledge", "generate_exam", "build_exam_docx",
    "build_answer_sheet",
  ],
  lesson_plan: [
    "understand_task", "generate_lesson_plan_content", "build_lesson_plan_docx",
  ],
};

function $(id) {
  return document.getElementById(id);
}

function taskStatusEl() {
  return $("task-status");
}

function setTaskStatus(status) {
  currentStatus = status;
  const el = taskStatusEl();
  el.textContent = STATUS_LABELS[status] || status;
  el.className =
    "task-status " +
    (status === "failed" ? "task-failed" : status === "completed" ? "task-done" : "muted");
  updateRightTask();
}

// 右侧「当前任务」实时同步（只反映真实任务状态）
function updateRightTask() {
  const el = $("rp-task");
  if (!el) return;
  const wfName =
    { exam_loop: "章节小测 + 学情分析", question_gen: "智能组卷", lesson_plan: "教案生成" }[
      currentWorkflow
    ] || "";
  const statusText =
    currentStatus === "completed" ? "已完成"
      : currentStatus === "failed" ? "失败"
      : currentStatus === "running" ? "执行中"
      : "";
  const done = steps.filter((s) => s.status === "done").length;
  const total = steps.length;
  const parts = [wfName, statusText, total ? `进度 ${done}/${total}` : ""].filter(Boolean);
  el.textContent = parts.join(" · ") || "暂无进行中的任务";
}

function renderSteps() {
  const list = $("task-timeline");
  list.innerHTML = "";
  const order = WORKFLOW_NODE_ORDER[currentWorkflow];
  // 已知工作流时展示完整节点链路（含「待执行」占位）；未知时仅展示已到达的步骤
  const nodes = order
    ? order.map(
        (node) =>
          steps.find((s) => s.node === node) || { node, status: "pending", message: "" }
      )
    : steps;

  nodes.forEach((s, i) => {
    const li = document.createElement("li");
    li.className = "step step-" + s.status;
    const name = NODE_LABELS[s.node] || s.node;
    const tag =
      { pending: "待执行", running: "执行中", done: "完成", failed: "失败" }[s.status] || "";
    const dotInner = s.status === "done" ? "✓" : s.status === "failed" ? "✕" : "";
    const isLast = i === nodes.length - 1;
    li.innerHTML =
      `<div class="step-rail">` +
      `<span class="step-dot">${dotInner}</span>` +
      (isLast ? "" : `<span class="step-line"></span>`) +
      `</div>` +
      `<div class="step-body">` +
      `<div class="step-head"><span class="step-name">${escapeHtml(name)}</span>` +
      `<span class="step-tag">${tag}</span></div>` +
      (s.message ? `<div class="step-msg">${escapeHtml(s.message)}</div>` : "") +
      `</div>`;
    list.appendChild(li);
  });
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
  refreshAux();
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
    refreshAux();
  }
}

// ===== 学情分析结果面板 =====
// 依据后端返回的真实 params（stats / weak_knowledge_points / diagnosis / a2a_metadata）
// 将学情分析结果产品化展示；无任何分析数据时不渲染内容。

function formatValue(v) {
  if (typeof v === "number") {
    // 保留最多 2 位小数，去掉多余的 0
    return Number(v.toFixed(2)).toString();
  }
  return v == null ? "—" : String(v);
}

function renderResultPanel(snap) {
  const box = $("task-result");
  const params = snap.params || {};
  const stats = params.stats;
  const weak = params.weak_knowledge_points;
  const diagnosis = params.diagnosis;

  // 内容签名：结果相关字段未变化时跳过重建，避免轮询反复重绘 / 重放入场动画
  const key = JSON.stringify([
    stats || null,
    weak || null,
    diagnosis || null,
    params.a2a_metadata || null,
  ]);
  if (key === lastResultKey) return;
  lastResultKey = key;

  box.innerHTML = "";

  // 没有任何分析数据时不渲染
  if (!stats && !Array.isArray(weak) && !diagnosis) return;

  const panel = document.createElement("div");
  panel.className = "result-panel";

  const title = document.createElement("h3");
  title.className = "result-panel-title";
  title.textContent = "📊 学情分析结果";
  panel.appendChild(title);

  // 1. 班级整体表现
  if (stats) {
    const block = document.createElement("div");
    block.className = "result-block";
    block.innerHTML = `<h4 class="result-block-title">班级整体表现</h4>`;
    const grid = document.createElement("div");
    grid.className = "result-stat-grid";
    const items = [
      ["平均分", stats.avg],
      ["最高分", stats.max],
      ["最低分", stats.min],
      ["中位数", stats.median],
    ];
    // 及格率 / 优秀率为 0-1 小数，换算成百分比
    if (typeof stats.pass_rate === "number") {
      items.push(["及格率", (stats.pass_rate * 100).toFixed(1) + "%"]);
    }
    if (typeof stats.excellent_rate === "number") {
      items.push(["优秀率", (stats.excellent_rate * 100).toFixed(1) + "%"]);
    }
    for (const [label, num] of items) {
      const cell = document.createElement("div");
      cell.className = "result-stat";
      cell.innerHTML =
        `<div class="result-stat-num">${escapeHtml(formatValue(num))}</div>` +
        `<div class="result-stat-label">${escapeHtml(label)}</div>`;
      grid.appendChild(cell);
    }
    block.appendChild(grid);

    // 分数段分布
    const dist = stats.score_distribution;
    if (Array.isArray(dist) && dist.length) {
      const distBlock = document.createElement("div");
      distBlock.className = "result-block";
      distBlock.innerHTML = `<h4 class="result-block-title">分数段分布</h4>`;
      const bars = document.createElement("div");
      bars.className = "result-distribution";
      const maxCount = Math.max(1, ...dist.map((d) => d.count || 0));
      for (const d of dist) {
        const bar = document.createElement("div");
        bar.className = "result-dist-bar";
        const pct = Math.max(4, Math.round(((d.count || 0) / maxCount) * 100));
        bar.innerHTML =
          `<div class="result-dist-count">${escapeHtml(d.count ?? 0)}</div>` +
          `<div class="result-dist-fill" style="height:${pct}%"></div>` +
          `<div class="result-dist-label">${escapeHtml(d.label || "")}</div>`;
        bars.appendChild(bar);
      }
      distBlock.appendChild(bars);
      panel.appendChild(distBlock);
    }

    panel.appendChild(block);
  }

  // 2. 薄弱知识点
  if (Array.isArray(weak) && weak.length) {
    const block = document.createElement("div");
    block.className = "result-block";
    block.innerHTML = `<h4 class="result-block-title">薄弱知识点</h4>`;
    const list = document.createElement("ul");
    list.className = "result-weak-list";
    for (const kp of weak) {
      const li = document.createElement("li");
      li.className = "result-weak-item";
      // accuracy 为 0-1 小数
      const acc =
        typeof kp.accuracy === "number"
          ? `<span class="result-weak-pct">正确率 ${(kp.accuracy * 100).toFixed(1)}%</span>`
          : "";
      li.innerHTML = `<span>${escapeHtml(kp.name || "")}</span>` + acc;
      list.appendChild(li);
    }
    block.appendChild(list);
    panel.appendChild(block);
  }

  // 3. 诊断结论 / 教学建议
  if (diagnosis) {
    const block = document.createElement("div");
    block.className = "result-block";
    block.innerHTML = `<h4 class="result-block-title">诊断与教学建议</h4>`;

    if (typeof diagnosis.content === "string" && diagnosis.content) {
      // 外部 Agent 返回的 content 结构
      const p = document.createElement("p");
      p.className = "result-summary";
      p.textContent = diagnosis.content;
      block.appendChild(p);
    } else {
      // 本地 / 兜底结构：summary / diagnosis / suggestions
      const text = diagnosis.diagnosis || diagnosis.summary || "";
      if (text) {
        const p = document.createElement("p");
        p.className = "result-summary";
        p.textContent = text;
        block.appendChild(p);
      }
      const sugg = diagnosis.suggestions;
      if (Array.isArray(sugg) && sugg.length) {
        const ol = document.createElement("ol");
        ol.className = "result-suggestions";
        for (const s of sugg) {
          const li = document.createElement("li");
          li.textContent = s;
          ol.appendChild(li);
        }
        block.appendChild(ol);
      }
    }
    panel.appendChild(block);
  }

  // 4. Agent 协作状态（a2a_metadata）
  const a2a = params.a2a_metadata;
  if (a2a) {
    const badge = document.createElement("div");
    badge.className = "result-a2a" + (a2a.fallback_used ? " is-fallback" : "");
    if (a2a.fallback_used) {
      badge.textContent = "🤖 外部 Agent 协作暂未可用，已自动回退本地诊断（不影响任务完成）";
    } else if (a2a.task_id) {
      badge.textContent =
        `🤖 已通过 A2A 调用外部 Agent 完成学情诊断（taskId=${String(a2a.task_id).slice(0, 8)}…）`;
    } else {
      badge.textContent = "🤖 Agent 协作已启用";
    }
    panel.appendChild(badge);
  }

  // 各区块依次淡入上滑，形成「数据逐步生成」的视觉节奏
  panel.querySelectorAll(".result-block, .result-a2a").forEach((el, i) => {
    el.classList.add("result-in");
    el.style.animationDelay = (0.05 + i * 0.12) + "s";
  });

  box.appendChild(panel);
}

// ===== 轻量级 Agent 执行日志（只展示真实收到的事件） =====
function renderLog() {
  const wrap = $("task-log-wrap");
  const box = $("task-log");
  const live = $("task-log-live");
  box.innerHTML = "";

  const lines = [];
  for (const s of steps) {
    const name = NODE_LABELS[s.node] || s.node;
    if (s.status === "running") {
      lines.push({ icon: "◷", text: `正在${name}…`, kind: "running" });
    } else if (s.status === "done") {
      lines.push({ icon: "✓", text: `${name}${s.message ? " · " + s.message : ""}`, kind: "done" });
    } else if (s.status === "failed") {
      lines.push({ icon: "✕", text: `${name} 失败${s.message ? " · " + s.message : ""}`, kind: "failed" });
    }
  }
  for (const a of artifacts) {
    lines.push({ icon: "📦", text: `生成「${a.name}」${a.file_name}`, kind: "artifact" });
  }
  if (currentStatus === "completed") {
    lines.push({ icon: "✅", text: "任务完成", kind: "complete" });
  } else if (currentStatus === "failed") {
    lines.push({ icon: "❌", text: "任务失败", kind: "error" });
  }

  if (!lines.length) {
    wrap.classList.add("hidden");
    return;
  }
  wrap.classList.remove("hidden");

  for (const { icon, text, kind } of lines) {
    const li = document.createElement("li");
    li.className = "task-log-line" + (kind ? " task-log-" + kind : "");
    li.innerHTML =
      `<span class="task-log-icon">${icon}</span><span class="task-log-text">${escapeHtml(text)}</span>`;
    box.appendChild(li);
  }

  const ended = currentStatus === "completed" || currentStatus === "failed";
  live.textContent = ended ? "● 已结束" : "● 实时";
  box.scrollTop = box.scrollHeight;
}

// ===== A2A 协作状态（只反映真实后端数据） =====
function updateA2aStatus() {
  const el = $("a2a-status");
  const text = $("a2a-status-text");
  if (!el || !text) return;

  // diagnose 节点执行中 = 真实尝试 A2A 的阶段（含超时与回退）
  const a2aRunning = steps.some((s) => s.node === "diagnose" && s.status === "running");
  const a2a = lastParams && lastParams.a2a_metadata;

  if (a2aRunning) {
    el.dataset.state = "active";
    text.textContent = "Agent 协作中 · 正在请求外部专业 Agent";
  } else if (a2a && a2a.fallback_used) {
    el.dataset.state = "fallback";
    text.textContent = "Agent 暂未响应 · 已回退本地能力";
  } else if (a2a && a2a.task_id) {
    el.dataset.state = "success";
    text.textContent = "Agent 协作完成 · 学情诊断由外部 Agent 生成";
  } else {
    el.dataset.state = "idle";
    text.textContent = "能力展示 · 提交学情分析任务后实时联动";
  }

  // 右侧信息栏「Agent 协作」同步
  const rpDot = $("rp-a2a-dot");
  const rpText = $("rp-a2a-text");
  if (rpDot) {
    rpDot.className = "rp-dot";
    if (a2aRunning) rpDot.classList.add("active");
    else if (a2a && a2a.fallback_used) rpDot.classList.add("fallback");
    else if (a2a && a2a.task_id) rpDot.classList.add("success");
  }
  if (rpText) {
    rpText.textContent = a2aRunning
      ? "协作中"
      : a2a && a2a.fallback_used
      ? "已回退"
      : a2a && a2a.task_id
      ? "已完成"
      : "待命";
  }
}

function refreshAux() {
  renderLog();
  updateA2aStatus();
}

function resetTaskViews() {
  currentWorkflow = null;
  lastParams = null;
  lastResultKey = "";
  setSteps([]);
  setArtifacts([]);
  $("task-result").innerHTML = "";
  $("task-empty").classList.add("hidden");
  setTaskStatus("running");
  renderLog();
  updateA2aStatus();
}

function applySnapshot(snap) {
  currentWorkflow = snap.workflow || null;
  lastParams = snap.params || null;
  if (snap.status) setTaskStatus(snap.status);
  setSteps(snap.steps);
  setArtifacts(snap.artifacts);
  renderResultPanel(snap);
  refreshAux();
}

function setSubmitLoading(loading) {
  const b = $("task-submit");
  if (!b) return;
  b.disabled = loading;
  b.textContent = loading ? "正在分析任务…" : "开始任务 →";
}

function finishTask() {
  setSubmitLoading(false);
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
      refreshAux();
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
    if (handleEvent(msg)) {
      finishTask();
      // SSE 结束后拉取一次最终快照，确保结果面板（学情数据 / A2A 状态）以真实最终数据渲染
      pollTask(taskId);
    }
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
  resetTaskViews();
  setSubmitLoading(true);
  // 让用户立刻看到「Agent 开始工作」的执行区
  $("task-progress").scrollIntoView({ behavior: "smooth", block: "start" });

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
      setSubmitLoading(false);
      return;
    }
    currentTaskId = data.task_id;
    applySnapshot(data); // 用 POST 返回的快照做首屏渲染
    streamEvents(currentTaskId);
  } catch (err) {
    taskStatusEl().textContent = "提交失败：" + err.message;
    taskStatusEl().className = "task-status task-failed";
    setSubmitLoading(false);
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

// 示例任务：点击后填充任务输入框（不自动提交，交由用户确认）
document.querySelectorAll(".example-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    $("task-input").value = chip.dataset.example || "";
    $("task-input").focus();
  });
});

// ===== 欢迎语：根据当前时间段问候 =====
function initGreeting() {
  const el = $("welcome-title");
  if (!el) return;
  const h = new Date().getHours();
  const g =
    h < 5 ? "晚上好" : h < 11 ? "早上好" : h < 14 ? "中午好" : h < 18 ? "下午好" : "晚上好";
  el.textContent = `${g}，教师 👋`;
}

// ===== 任务输入框动态 placeholder（用户开始输入后停止） =====
const PLACEHOLDERS = [
  "例如：请针对《一元二次方程》生成一次章节小测并分析学情",
  "例如：请根据本次考试结果生成班级学情报告",
  "例如：请根据薄弱知识点制定下一阶段教学方案",
  "例如：请为《勾股定理》生成一份教案",
];
function initPlaceholderCycle() {
  const input = $("task-input");
  if (!input) return;
  let idx = 0;
  let stopped = false;
  const timer = setInterval(() => {
    if (stopped) return;
    idx = (idx + 1) % PLACEHOLDERS.length;
    input.classList.add("ph-out");
    setTimeout(() => {
      input.placeholder = PLACEHOLDERS[idx];
      input.classList.remove("ph-out");
    }, 200);
  }, 3200);
  input.addEventListener("input", () => {
    if (stopped) return;
    stopped = true;
    clearInterval(timer);
    input.classList.remove("ph-out");
  });
}

// ===== 侧边导航滚动高亮（scrollspy） =====
function initNavScrollspy() {
  const links = Array.from(document.querySelectorAll(".side-nav a[href^='#']"));
  const items = links
    .map((a) => ({ a, el: document.querySelector(a.getAttribute("href")) }))
    .filter((x) => x.el);
  if (!items.length) return;
  const onScroll = () => {
    const y = window.scrollY + 120;
    let current = items[0];
    for (const item of items) {
      if (item.el.offsetTop <= y) current = item;
    }
    links.forEach((a) => a.classList.remove("active"));
    current.a.classList.add("active");
  };
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();
}

initGreeting();
initPlaceholderCycle();
initNavScrollspy();
