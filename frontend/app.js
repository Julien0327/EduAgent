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


// ===== API 认证与引用/会话支持（Phase 2 / S6-S7，§10.5/§4.4/§7） =====
// Token 存 localStorage（仅本机浏览器便利，非服务端状态）；后端未配置 token
// 时 API 返回 503 fail-closed——页面顶部可填 token。
function apiToken() {
  try { return localStorage.getItem("edumate_api_token") || ""; } catch { return ""; }
}
function apiHeaders(extra) {
  const h = Object.assign({ "Content-Type": "application/json" }, extra || {});
  const t = apiToken();
  if (t) h["X-API-Token"] = t;
  return h;
}
function authErrorHint(status, detail) {
  if (status === 401) return "认证失败（401）：请在页面上方填入正确的 API Token。";
  if (status === 503) return "服务端未配置 EDUMATE_API_TOKEN（503 fail-closed）：请联系管理员或在服务端设置。";
  return detail || ("HTTP " + status);
}
function sseUrl(url) {
  const t = apiToken();
  return t ? url + (url.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(t) : url;
}

// 会话（多轮对话，§7）：chat 返回 session_id 后存内存复用
let currentSessionId = null;

// ===== 引用面板（§4.4）：读取任务的 citations.json =====
async function showCitations(taskId) {
  const panel = document.getElementById("citations-panel");
  const body = document.getElementById("citations-body");
  if (!panel || !body) return;
  body.textContent = "加载中…";
  panel.style.display = "block";
  try {
    const resp = await fetch(`/api/tasks/${taskId}/citations`, { headers: apiHeaders() });
    const data = await resp.json();
    if (!resp.ok) { body.textContent = authErrorHint(resp.status, data.detail); return; }
    if (!data.citations || data.citations.length === 0) {
      body.textContent = "该任务没有题库/图谱引用（如纯 AI 生成任务）。";
      return;
    }
    body.innerHTML = data.citations.map((c) => {
      const used = (c.used_in || []).map(escapeHtml).join("；") || "（未装配使用位置）";
      return `<div class="cite-item"><b>${escapeHtml(c.kind)}</b> · <code>${escapeHtml(c.ref)}</code><div class="cite-used">${used}</div></div>`;
    }).join("");
  } catch (err) {
    body.textContent = "加载引用失败：" + err.message;
  }
}

// ===== 会话回放（§7.2）：会话列表 → 消息回放 =====
async function showSessions() {
  const panel = document.getElementById("sessions-panel");
  const body = document.getElementById("sessions-body");
  if (!panel || !body) return;
  body.textContent = "加载中…";
  panel.style.display = "block";
  try {
    const resp = await fetch("/api/sessions", { headers: apiHeaders() });
    const data = await resp.json();
    if (!resp.ok) { body.textContent = authErrorHint(resp.status, data.detail); return; }
    if (!data.sessions.length) { body.textContent = "暂无会话。"; return; }
    body.innerHTML = data.sessions.map((s) =>
      `<div class="session-row"><a href="#" onclick="openSession('${s.session_id}');return false;">` +
      `${escapeHtml(s.title || s.session_id)}</a> <span class="muted">(${s.msg_count} 条消息)</span></div>`
    ).join("");
  } catch (err) {
    body.textContent = "加载会话失败：" + err.message;
  }
}

async function openSession(sessionId) {
  const replyEl = document.getElementById("chat-reply");
  const statusEl = document.getElementById("chat-status");
  try {
    const resp = await fetch(`/api/sessions/${sessionId}`, { headers: apiHeaders() });
    const data = await resp.json();
    if (!resp.ok) { statusEl.textContent = authErrorHint(resp.status, data.detail); return; }
    currentSessionId = sessionId;
    statusEl.textContent = `已切换到会话 ${sessionId}（后续对话延续该会话）`;
    replyEl.innerHTML = data.messages.map((m) => {
      const who = m.role === "user" ? "我" : "助手";
      return `<div class="replay-msg"><span class="muted">${who}：</span>${renderRichText(m.content)}</div>`;
    }).join("<hr class='soft'>");
    const taskCards = data.tasks || [];
    if (taskCards.length) {
      replyEl.innerHTML += `<hr class='soft'><div class="muted">关联任务：${taskCards.map(
        (t) => `${t.task_id}（${t.status}）`).join("，")}</div>`;
    }
  } catch (err) {
    statusEl.textContent = "回放失败：" + err.message;
  }
}
window.openSession = openSession;

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
    const body = { message };
    if (currentSessionId) body.session_id = currentSessionId;
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!resp.ok) {
      statusEl.textContent = authErrorHint(resp.status, data.detail);
      return;
    }
    if (data.session_id) currentSessionId = data.session_id;
    if (data.model === "scope-guard") {
      statusEl.textContent = "当前仅支持小学学段";
    } else {
      statusEl.textContent = `已回复（模型：${data.model}，会话：${data.session_id.slice(0, 8)}…）`;
    }
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
    lines.push({ icon: "✅", text: "任务完成（引用见下方面板）", kind: "complete" });
    if (currentTaskId) showCitations(currentTaskId);  // §4.4 引用面板
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
    const resp = await fetch(`/api/tasks/${taskId}`, { headers: apiHeaders() });
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
  taskEventSource = new EventSource(sseUrl(`/api/tasks/${taskId}/events`));
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
    const body = { prompt };
    if (currentSessionId) body.session_id = currentSessionId;
    const resp = await fetch("/api/tasks", {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!resp.ok) {
      taskStatusEl().textContent = `提交失败（HTTP ${resp.status}）：${data.detail || "未知错误"}`;
      taskStatusEl().className = "task-status task-failed";
      setSubmitLoading(false);
      return;
    }
    // 学段守卫（初中请求）：不建任务、不调 LLM，直接展示友好提示
    if (data.scope_notice) {
      taskStatusEl().textContent = data.scope_notice;
      taskStatusEl().className = "task-status muted";
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
  "例如：请针对《分数与百分数》生成一次章节小测并分析学情",
  "例如：请根据本次考试结果生成班级学情报告",
  "例如：请根据薄弱知识点制定下一阶段教学方案",
  "例如：请为《圆的面积》生成一份教案",
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

// ===== Token 保存与面板按钮（S6/S7 前端接线） =====
document.addEventListener("DOMContentLoaded", () => {
  const inp = document.getElementById("api-token-input");
  const save = document.getElementById("token-save");
  const bs = document.getElementById("btn-sessions");
  const bc = document.getElementById("btn-citations");
  if (inp) inp.value = apiToken();
  if (save) save.addEventListener("click", () => {
    try { localStorage.setItem("edumate_api_token", (inp.value || "").trim()); } catch {}
    if (inp) inp.placeholder = "API Token（已保存）";
  });
  if (bs) bs.addEventListener("click", showSessions);
  if (bc) bc.addEventListener("click", () => currentTaskId && showCitations(currentTaskId));
});

// ===== Phase 3 / S5：班级诊断（学情分析页） =====
// 全部数据来自统一诊断服务 API（/api/classes、/api/diagnosis/class/{id}、
// /api/diagnosis/class/{id}/report）；前端只做展示，不重复计算掌握度/归因/推荐。

const STATE_META = {
  normal: { label: "正常", cls: "st-normal" },
  weak: { label: "薄弱", cls: "st-weak" },
  insufficient: { label: "样本不足", cls: "st-insuf" },
  no_coverage: { label: "无覆盖", cls: "st-nocov" },
};
const CONF_META = {
  high: { label: "高", cls: "conf-high" },
  mid: { label: "中", cls: "conf-mid" },
  low: { label: "低", cls: "conf-low" },
};
const USAGE_META = {
  provider: { label: "真实用量", cls: "badge-ok" },
  partial: { label: "部分用量", cls: "badge-warn" },
  unavailable: { label: "用量不可用", cls: "badge-bad" },
  none: { label: "无模型调用", cls: "badge-muted" },
  "n/a": { label: "—", cls: "badge-muted" },
};
const WORKFLOW_LABELS = {
  exam_loop: "章节小测一条龙",
  question_gen: "智能组卷",
  lesson_plan: "教案生成",
  class_diagnosis: "班级诊断",
};
const TASK_STATUS_LABELS = {
  completed: "已完成", failed: "失败", running: "执行中", pending: "等待中",
};

async function fetchJSON(url, options) {
  const resp = await fetch(url, Object.assign({ headers: apiHeaders() }, options || {}));
  let data = {};
  try { data = await resp.json(); } catch { data = {}; }
  if (!resp.ok) {
    const err = new Error(authErrorHint(resp.status, data.detail));
    err.status = resp.status;
    err.data = data;
    throw err;
  }
  return data;
}

// NULL 不伪装为 0：用量不可用时 tokens/cost 为 null → 显示「—」
function fmtNum(v) {
  return v === null || v === undefined ? "—" : String(v);
}
function fmtCost(v) {
  return v === null || v === undefined ? "—" : "¥" + Number(v).toFixed(6);
}
function fmtTok(v, source) {
  if (v === null || v === undefined) {
    return source === "unavailable" ? "—(未返回)" : "—";
  }
  return String(v);
}
function stateChip(st) {
  const m = STATE_META[st] || { label: st || "—", cls: "st-nocov" };
  return `<span class="state-chip ${m.cls}">${escapeHtml(m.label)}</span>`;
}
function confChip(c) {
  const m = CONF_META[c] || { label: c || "—", cls: "conf-mid" };
  return `<span class="conf-chip ${m.cls}">置信度：${escapeHtml(m.label)}</span>`;
}
function usageChip(u) {
  const m = USAGE_META[u] || { label: u || "—", cls: "badge-muted" };
  return `<span class="usage-badge ${m.cls}">${escapeHtml(m.label)}</span>`;
}

function setDiagStatus(msg, isErr) {
  const el = $("diag-status");
  if (!el) return;
  el.textContent = msg || "";
  el.className = "diag-status" + (isErr ? " diag-err" : "");
}

async function initDiagClasses() {
  const sel = $("diag-class");
  if (!sel) return;
  try {
    const classes = await fetchJSON("/api/classes");
    sel.innerHTML = "";
    if (!classes.length) {
      sel.innerHTML = '<option value="">（暂无导入班级）</option>';
      setDiagStatus("暂无导入班级数据：请先通过 F16 导入班级/考试/答题数据", true);
      return;
    }
    for (const c of classes) {
      const opt = document.createElement("option");
      opt.value = c.class_id;
      opt.textContent = `${c.name}（${c.grade}年级 · ${c.students}学生 · ${c.exams}考试 · ${c.questions}题）`;
      sel.appendChild(opt);
    }
    setDiagStatus("");
  } catch (err) {
    setDiagStatus(err.message, true);
  }
}

async function loadDiagnosis() {
  const classId = $("diag-class").value;
  const box = $("diag-content");
  if (!classId) { setDiagStatus("请先选择班级", true); return; }
  box.innerHTML = '<p class="muted">诊断计算中…（确定性规则，不调用 LLM）</p>';
  setDiagStatus("加载中…");
  try {
    const d = await fetchJSON(`/api/diagnosis/class/${encodeURIComponent(classId)}`);
    renderDiagnosis(d, box);
    setDiagStatus("诊断完成（数据来自统一诊断服务）");
  } catch (err) {
    box.innerHTML = `<p class="diag-err">${escapeHtml(err.message)}</p>`;
    setDiagStatus(err.message, true);
  }
}

function renderDiagnosis(d, box) {
  const mastery = d.mastery || {};
  const kps = Object.values(mastery);
  const counts = { normal: 0, weak: 0, insufficient: 0, no_coverage: 0 };
  for (const m of kps) counts[m.state] = (counts[m.state] || 0) + 1;
  const scope = d.data_scope || {};
  const cls = d.class || {};
  const isDemo = String(cls.school || "").includes("演示") || String(cls.name || "").includes("演示");
  const kpName = (id) => (d.kp_names || {})[id] || id;

  let html = "";
  html += `<div class="diag-scope">数据范围：${fmtNum(scope.exam_count)} 场考试 · ${fmtNum(scope.question_count)} 道题 · ${fmtNum(scope.student_count)} 名学生 · ${fmtNum(scope.answer_count)} 条答题 · 覆盖 ${fmtNum(scope.kp_covered_count)} 个知识点${isDemo ? ' <span class="demo-badge">合成演示数据（非真实学生）</span>' : ""}</div>`;

  html += `<div class="diag-state-cards">
    <div class="state-card st-weak"><b>${counts.weak}</b><span>薄弱</span></div>
    <div class="state-card st-insuf"><b>${counts.insufficient}</b><span>样本不足</span></div>
    <div class="state-card st-normal"><b>${counts.normal}</b><span>正常</span></div>
    <div class="state-card st-nocov"><b>${counts.no_coverage}</b><span>无覆盖</span></div>
  </div>`;

  // 归因链
  html += `<h3 class="diag-h">根因归因链（${(d.chains || []).length}）</h3>`;
  if (!(d.chains || []).length) {
    html += `<p class="muted">无——本班未发现样本充足的薄弱知识点。</p>`;
  }
  for (const c of d.chains || []) {
    const auxWarn = c.target_aux_share !== null && c.target_aux_share !== undefined && c.target_aux_share > 0.5
      ? `<div class="diag-warn">辅助证据占比 ${Number(c.target_aux_share).toFixed(3)} &gt; 0.5——薄弱判定主要由辅助证据驱动，建议补充主知识点测评后再下结论。</div>` : "";
    const lowNote = c.confidence === "low"
      ? `<div class="diag-warn">低置信度结论：存在阻断前置 / 分支冲突 / 辅助证据主导 / 样本临界，请结合课堂观察使用，不作为确定性结论。</div>` : "";
    const blocked = (c.blocked_prereq_names || []).length
      ? `<div class="diag-blocked">阻断前置（证据不足，未猜测根因）：${escapeHtml(c.blocked_prereq_names.join("、"))}</div>` : "";
    const evRows = (c.evidence || []).map((e) =>
      `<li><code>${escapeHtml(e.q_id)}</code>［${e.role === "primary" ? "主" : "辅"}］考试${escapeHtml(e.exam_label)}#${fmtNum(e.q_no)}</li>`).join("");
    html += `<div class="chain-card">
      <div class="chain-head"><b>${escapeHtml(c.target_name || c.target_kp)}</b> ← 根因：<b>${escapeHtml(c.root_name || c.root_kp)}</b> ${confChip(c.confidence)} ${c.conflict ? '<span class="conf-chip conf-low">多根因并存</span>' : ""}</div>
      <div class="chain-path">前置路径：${(c.path_names || []).map(escapeHtml).join(" → ")}</div>
      <div class="chain-meta">目标掌握度 ${fmtNum(c.target_mastery)} · 路径最小样本 ${fmtNum(c.min_samples)}</div>
      ${auxWarn}${lowNote}${blocked}
      <details class="chain-ev"><summary>证据追溯（${(c.evidence || []).length} 条：题目 ↔ 主/辅角色 ↔ 考试）</summary><ul>${evRows}</ul></details>
    </div>`;
  }

  // 掌握度明细（展示排序：薄弱 → 样本不足 → 正常；组内按掌握度升序）
  html += `<h3 class="diag-h">知识点掌握度明细（覆盖 ${kps.filter((m) => m.state !== "no_coverage").length} 个）</h3>`;
  const order = { weak: 0, insufficient: 1, normal: 2, no_coverage: 3 };
  const covered = kps.filter((m) => m.state !== "no_coverage")
    .sort((a, b) => (order[a.state] - order[b.state]) || ((a.mastery ?? 9) - (b.mastery ?? 9)));
  html += `<table class="diag-table"><thead><tr><th>知识点</th><th>状态</th><th>掌握度</th><th>样本数</th><th>主/辅样本</th><th>辅助证据占比</th></tr></thead><tbody>`;
  for (const m of covered) {
    html += `<tr><td>${escapeHtml(kpName(m.kp_id))}</td><td>${stateChip(m.state)}</td><td>${fmtNum(m.mastery)}</td><td>${fmtNum(m.sample_count)}</td><td>${fmtNum(m.primary_sample_count)}/${fmtNum(m.aux_sample_count)}</td><td>${fmtNum(m.aux_share)}</td></tr>`;
  }
  html += `</tbody></table>`;
  if (counts.no_coverage) {
    const nocov = kps.filter((m) => m.state === "no_coverage").map((m) => kpName(m.kp_id));
    html += `<details class="nocov"><summary>无覆盖知识点 ${counts.no_coverage} 个（本班考试未涉及，不输出任何结论）</summary><p class="muted">${escapeHtml(nocov.join("、"))}</p></details>`;
  }

  // 推荐练习
  html += `<h3 class="diag-h">推荐练习（真实题库）</h3>`;
  const recs = d.recommendations || [];
  if (!recs.length) html += `<p class="muted">无推荐（未发现可归因的薄弱知识点）。</p>`;
  for (const g of recs) {
    const roleLabel = g.role === "root" ? "根因巩固" : "目标针对性";
    const relax = g.relaxation
      ? `<div class="diag-note">放宽策略：${escapeHtml(g.relaxation.reason || "")}（采用：${escapeHtml(g.relaxation.final_strategy || "")}）</div>` : "";
    const qRows = (g.questions || []).map((q) =>
      `<tr><td><code>${escapeHtml(q.q_id)}</code>${q.completed ? ' <span class="demo-badge">已完成</span>' : ""}</td><td>${fmtNum(q.difficulty)}</td><td>${escapeHtml(q.source || "unknown")}</td><td class="stem">${escapeHtml((q.stem || "").slice(0, 60))}</td></tr>`).join("");
    html += `<div class="rec-group">
      <div class="rec-head">【${roleLabel}】${escapeHtml(g.kp_name || g.kp_id)}
        <span class="muted">掌握度 ${fmtNum(g.mastery)} · 难度带 ${g.difficulty_range ? g.difficulty_range[0] + "-" + g.difficulty_range[1] : "—"} · 候选 ${fmtNum(g.candidate_count)} · ${confChip(g.confidence)}</span></div>
      ${relax}
      ${qRows ? `<table class="diag-table"><thead><tr><th>题目 ID</th><th>难度</th><th>来源</th><th>题干</th></tr></thead><tbody>${qRows}</tbody></table>` : '<p class="muted">题库候选不足，未推荐题目（如实报告，不以编题补齐）。</p>'}
    </div>`;
  }

  // 叙述区（报告任务完成后填充）
  html += `<h3 class="diag-h">诊断叙述</h3><div id="diag-narration"><p class="muted">叙述在「生成诊断报告」任务中产出（LLM 仅解释确定性结果；失败时模板兜底，来源将明确标注）。</p></div>`;

  box.innerHTML = html;
}

async function generateDiagReport() {
  const classId = $("diag-class").value;
  if (!classId) { setDiagStatus("请先选择班级", true); return; }
  setDiagStatus("报告任务已提交，执行中…");
  try {
    const resp = await fetch(`/api/diagnosis/class/${encodeURIComponent(classId)}/report`, {
      method: "POST", headers: apiHeaders(),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(authErrorHint(resp.status, data.detail));
    if (data.scope_notice) { setDiagStatus(data.scope_notice, true); return; }
    pollDiagTask(data.task_id);
  } catch (err) {
    setDiagStatus("提交失败：" + err.message, true);
  }
}

function pollDiagTask(taskId, tries) {
  tries = tries || 0;
  if (!taskId) return;
  fetchJSON(`/api/tasks/${encodeURIComponent(taskId)}`).then((snap) => {
    if (snap.status === "completed") {
      setDiagStatus("报告已生成");
      renderDiagReportResult(taskId, snap);
      loadObserve();  // 观测页同步刷新（新任务含真实 Trace）
    } else if (snap.status === "failed") {
      setDiagStatus("报告任务失败：" + (snap.error || "未知错误"), true);
    } else if (tries < 60) {
      setTimeout(() => pollDiagTask(taskId, tries + 1), 2000);
    } else {
      setDiagStatus("报告任务超时，请稍后在「运行观测」页查看", true);
    }
  }).catch((err) => setDiagStatus("查询任务失败：" + err.message, true));
}

async function renderDiagReportResult(taskId, snap) {
  const box = $("diag-narration");
  if (!box) return;
  const arts = (snap.artifacts || []).map((a) =>
    `<a class="artifact-btn" href="${escapeHtml(sseUrl(`/api/tasks/${taskId}/files/${encodeURIComponent(a.file_name)}`))}">下载 ${escapeHtml(a.name)}（${escapeHtml(a.file_name)}）</a>`).join(" ");
  let html = `<div class="diag-report-result"><div class="diag-arts">${arts}</div>`;
  try {
    const payload = await fetchJSON(`/api/tasks/${taskId}/files/${encodeURIComponent("04_诊断结果.json")}?inline=true`);
    const n = payload.narration || {};
    const srcBadge = n.narration_source === "llm"
      ? '<span class="usage-badge badge-ok">AI 叙述（LLM 生成，仅解释性文字）</span>'
      : '<span class="usage-badge badge-muted">模板兜底（未调用 LLM 或调用失败）</span>';
    html += `<h4 class="diag-h">叙述 ${srcBadge}</h4>` +
      `<p>${escapeHtml(n.summary || "")}</p>` +
      `<p>${escapeHtml(n.diagnosis || "").replace(/\n/g, "<br>")}</p><ul>` +
      (n.suggestions || []).map((s) => `<li>${escapeHtml(s)}</li>`).join("") + "</ul>" +
      `<p class="muted">事实数值（掌握度/根因/推荐）均以确定性计算为准，见上方各节。</p>`;
  } catch (err) {
    html += `<p class="diag-err">读取叙述失败：${escapeHtml(err.message)}</p>`;
  }
  html += "</div>";
  box.innerHTML = html;
}

// ===== Phase 3 / S5：运行观测页（数据来自 S4 真实 API） =====

function setObsStatus(msg, isErr) {
  const el = $("obs-status");
  if (!el) return;
  el.textContent = msg || "";
  el.className = "diag-status" + (isErr ? " diag-err" : "");
}

async function loadObserve() {
  if (!$("obs-cost")) return;
  const days = $("obs-days") ? $("obs-days").value : 30;
  setObsStatus("加载中…");
  try {
    const [cost, tasks] = await Promise.all([
      fetchJSON(`/api/stats/cost?days=${days}`),
      fetchJSON(`/api/tasks?limit=50`),
    ]);
    renderCostPanel(cost);
    renderTaskList(tasks);
    setObsStatus(`已加载（${new Date().toLocaleTimeString()}）`);
  } catch (err) {
    setObsStatus(err.message, true);
    renderCostPanel(null);
    renderTaskList(null);
  }
}

function renderCostPanel(c) {
  const box = $("obs-cost");
  if (!c) { box.innerHTML = '<p class="diag-err">成本数据加载失败（检查 Token / 服务状态）</p>'; return; }
  const t = c.totals || {};
  const rows = (c.by_day || []).map((r) =>
    `<tr><td>${escapeHtml(r.day || "—")}</td><td>${fmtNum(r.tasks)}</td><td>${fmtNum(r.steps)}</td><td>${fmtTok(r.tokens_in)} / ${fmtTok(r.tokens_out)}</td><td>${fmtCost(r.cost_cny)}</td></tr>`).join("");
  box.innerHTML = `
    <div class="obs-cards">
      <div class="obs-card"><b>${fmtNum(t.steps)}</b><span>步骤数（窗口内）</span></div>
      <div class="obs-card"><b>${fmtTok(t.tokens_in)}</b><span>输入 Tokens</span></div>
      <div class="obs-card"><b>${fmtTok(t.tokens_out)}</b><span>输出 Tokens</span></div>
      <div class="obs-card"><b>${fmtCost(t.cost_cny)}</b><span>成本（牌价估算）</span></div>
      <div class="obs-card"><b>${fmtNum(t.steps_usage_unavailable)}</b><span>用量不可用步骤</span></div>
    </div>
    <div class="obs-note">口径：仅业务任务节点（steps 表）；标注批次费用在独立台账、不混入；cost=NULL（供应商未返回 usage）不计入汇总、不以 0 冒充。窗口起点 ${escapeHtml(c.cutoff || "—")}。</div>
    ${rows ? `<table class="diag-table"><thead><tr><th>日期</th><th>任务数</th><th>步骤</th><th>Tokens in/out</th><th>成本</th></tr></thead><tbody>${rows}</tbody></table>` : '<p class="muted">窗口内无执行记录。</p>'}`;
}

function renderTaskList(t) {
  const box = $("obs-tasks");
  if (!t) { box.innerHTML = '<p class="diag-err">任务列表加载失败（检查 Token / 服务状态）</p>'; return; }
  const tasks = t.tasks || [];
  if (!tasks.length) { box.innerHTML = '<p class="muted">暂无任务。</p>'; return; }
  box.innerHTML = `<h3 class="diag-h">任务列表（共 ${fmtNum(t.total)}，库直读，重启后可查）</h3>
  <table class="diag-table obs-task-table"><thead><tr><th>任务</th><th>类型</th><th>状态</th><th>创建时间</th><th>步骤</th><th>模型调用</th><th>Tokens in/out</th><th>成本</th></tr></thead><tbody>
  ${tasks.map((x) => `<tr data-tid="${escapeHtml(x.task_id)}" class="obs-task-row">
      <td class="stem" title="${escapeHtml(x.prompt || "")}">${escapeHtml((x.prompt || "").slice(0, 24))}</td>
      <td>${escapeHtml(WORKFLOW_LABELS[x.workflow] || x.workflow || "—")}</td>
      <td>${escapeHtml(TASK_STATUS_LABELS[x.status] || x.status || "—")}</td>
      <td>${escapeHtml(x.created_at || "—")}</td>
      <td>${fmtNum(x.step_count)}</td>
      <td>${fmtNum(x.llm_call_count)}</td>
      <td>${fmtTok(x.tokens_in)} / ${fmtTok(x.tokens_out)}</td>
      <td>${fmtCost(x.cost)}</td>
    </tr>`).join("")}
  </tbody></table>`;
  box.querySelectorAll(".obs-task-row").forEach((tr) =>
    tr.addEventListener("click", () => loadTrace(tr.dataset.tid)));
}

async function loadTrace(taskId) {
  const box = $("obs-trace");
  if (!box) return;
  box.innerHTML = '<p class="muted">加载 Trace…</p>';
  try {
    const d = await fetchJSON(`/api/tasks/${encodeURIComponent(taskId)}/trace`);
    renderTrace(d, box);
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    box.innerHTML = `<p class="diag-err">${escapeHtml(err.message)}</p>`;
  }
}

function renderTrace(d, box) {
  const tr = d.trace || {};
  const tot = tr.totals || {};
  const steps = tr.steps || [];
  let html = `<h3 class="diag-h">任务 Trace：${escapeHtml(d.task_id || "")}</h3>
  <div class="trace-head">
    <span>类型：${escapeHtml(WORKFLOW_LABELS[d.workflow] || d.workflow || "—")}</span>
    <span>状态：${escapeHtml(TASK_STATUS_LABELS[d.status] || d.status || "—")}</span>
    <span>创建：${escapeHtml(d.created_at || "—")}</span>
    <span>步骤 ${fmtNum(tot.step_count)} · LLM 调用 ${fmtNum(tot.llm_call_count)} · 工具调用 ${fmtNum(tot.tool_call_count)}</span>
    <span>Tokens ${fmtTok(tot.tokens_in)} / ${fmtTok(tot.tokens_out)}</span>
    <span>成本 ${fmtCost(tot.cost_cny)}</span>
    <span class="usage-badge ${tr.cost_recompute_ok ? "badge-ok" : "badge-bad"}">${tr.cost_recompute_ok ? "成本可复算 ✓（Σ明细=Σ步骤）" : "复算不一致 ⚠"}</span>
  </div>`;
  if (d.error) html += `<div class="diag-blocked">任务错误：${escapeHtml(d.error)}</div>`;
  for (const s of steps) {
    const calls = s.llm_calls || [];
    const callRows = calls.map((c) => {
      if (c.kind === "tool") {
        return `<tr><td>tool</td><td>—</td><td>${escapeHtml(c.tool || "")}</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td>${fmtNum(c.latency_ms)}ms</td><td class="diag-err">${escapeHtml(c.error || "")}</td></tr>`;
      }
      return `<tr><td>llm</td><td>${fmtNum(c.attempt)}</td><td>${escapeHtml(c.tool || "—")}</td><td>${escapeHtml(c.model || "—")}</td><td>${fmtTok(c.tokens_in, c.usage_source)} / ${fmtTok(c.tokens_out, c.usage_source)}</td><td>${usageChip(c.usage_source)}</td><td>${escapeHtml(c.price_version || "—")}</td><td>${escapeHtml(c.price_period || "—")}${c.unit_price ? " · " + escapeHtml(JSON.stringify(c.unit_price)) : ""}</td><td>${fmtCost(c.cost_cny)} / ${fmtNum(c.latency_ms)}ms</td><td class="diag-err">${escapeHtml(c.error || "")}</td></tr>`;
    }).join("");
    const stCls = s.status === "done" ? "st-normal" : (s.status === "failed" ? "st-weak" : "st-insuf");
    html += `<details class="trace-step"${s.status === "failed" ? " open" : ""}>
      <summary>
        <b>#${fmtNum(s.step_no)} ${escapeHtml(s.node)}</b>
        <span class="state-chip ${stCls}">${escapeHtml(s.status)}</span>
        <span class="muted">${fmtNum(s.latency_ms)}ms</span>
        ${s.llm_model ? `<span class="muted">${escapeHtml(s.llm_model)}</span>` : ""}
        ${usageChip(s.usage_source)}
        <span class="muted">tokens ${fmtTok(s.tokens_in, s.usage_source)}/${fmtTok(s.tokens_out, s.usage_source)} · cost ${fmtCost(s.cost)}</span>
      </summary>
      <div class="trace-body">
        ${s.output_summary ? `<div class="muted">输出：${escapeHtml(s.output_summary)}</div>` : ""}
        ${s.error ? `<div class="diag-err">错误：${escapeHtml(s.error)}</div>` : ""}
        <div class="muted">开始 ${escapeHtml(s.started_at || "—")} · 结束 ${escapeHtml(s.ended_at || "—")}</div>
        ${callRows ? `<table class="diag-table trace-calls"><thead><tr><th>类型</th><th>次</th><th>工具</th><th>模型</th><th>tokens in/out</th><th>用量来源</th><th>价格版本</th><th>时段·单价(元/1M)</th><th>成本/延迟</th><th>错误</th></tr></thead><tbody>${callRows}</tbody></table>` : '<p class="muted">本节点无模型/工具调用记录。</p>'}
      </div>
    </details>`;
  }
  box.innerHTML = html;
}

// ===== 数据导入页（复用后端导入 API；前端零业务计算） =====

const IMPORT_RECENT_KEY = "edumate_import_recent";
const IMPORT_KIND_LABELS = {
  full: "整簿导入", classes: "仅班级", students: "仅学生",
  exam: "仅考试/卷面", answers: "仅答题记录",
};
const IMPORT_STATUS_LABELS = {
  committed: "已提交", partial: "部分成功（含错误行）", failed: "失败", validating: "校验中",
};
const IMPORT_SHEET_ORDER = ["班级", "学生", "考试", "考试题目", "答题记录"];
const IMP_ERROR_LABELS = {
  E_REQUIRED: "必填缺失", E_TYPE: "类型错误", E_ENUM: "取值非法", E_RANGE: "范围越界",
  E_DATE: "日期格式", E_REF: "关联定位失败", E_DUP: "重复", E_CONFLICT: "与库冲突",
  E_INTEGRITY: "完整性",
};
const IMP_WARNING_LABELS = {
  W_TOTAL_SCORE: "总分与分值和", W_KP: "知识点未命中", W_HEADER: "未识别表头列",
};

function impRecent() {
  try { return JSON.parse(localStorage.getItem(IMPORT_RECENT_KEY) || "[]"); } catch { return []; }
}
function saveImpRecent(list) {
  try { localStorage.setItem(IMPORT_RECENT_KEY, JSON.stringify(list.slice(0, 8))); } catch {}
}
function setImpStatus(msg, isErr) {
  const el = $("imp-status");
  if (!el) return;
  el.textContent = msg || "";
  el.className = "diag-status" + (isErr ? " diag-err" : "");
}
function pushImpRecent(batchId) {
  const list = impRecent().filter((b) => b !== batchId);
  list.unshift(batchId);
  saveImpRecent(list);
  renderImpRecent();
}
function renderImpRecent() {
  const el = $("imp-recent");
  if (!el) return;
  const list = impRecent();
  if (!list.length) { el.innerHTML = '<span class="muted">（本地上传过的批次可一键回查）</span>'; return; }
  el.innerHTML = list.map((b) =>
    `<a href="#" data-batch="${escapeHtml(b)}" class="imp-chip">${escapeHtml(b.slice(0, 20))}…</a>`
  ).join("");
  el.querySelectorAll("a[data-batch]").forEach((a) =>
    a.addEventListener("click", (e) => {
      e.preventDefault();
      $("imp-batch-id").value = a.dataset.batch;
      loadImportBatch(a.dataset.batch);
    }));
}

async function downloadImportTemplate() {
  setImpStatus("正在下载模板…");
  try {
    const resp = await fetch("/api/import/template", { headers: { "X-API-Token": apiToken() } });
    if (!resp.ok) {
      const d = await resp.json().catch(() => ({}));
      setImpStatus(authErrorHint(resp.status, typeof d.detail === "string" ? d.detail : ""), true);
      return;
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "edumate_import_template.xlsx";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    setImpStatus("模板已下载（5 个数据工作表 + 填写说明，表头为标准字段名）");
  } catch (err) {
    setImpStatus("下载失败：" + err.message, true);
  }
}

async function uploadImportFile() {
  const file = $("imp-file").files && $("imp-file").files[0];
  if (!file) { setImpStatus("请先选择 xlsx 文件", true); return; }
  const kind = $("imp-kind").value;
  const force = $("imp-force").checked;
  const btn = $("imp-upload");
  setImpStatus("上传解析中…（校验按 F16 七步管线执行）");
  if (btn) btn.disabled = true;
  try {
    const buf = await file.arrayBuffer();
    let url = `/api/import?kind=${encodeURIComponent(kind)}&file_name=${encodeURIComponent(file.name)}`;
    if (force) url += "&force=true";
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream", "X-API-Token": apiToken() },
      body: buf,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const detail = data.detail;
      if (resp.status === 409 && detail && detail.reason === "duplicate_fingerprint" && detail.original_batch) {
        renderImportReport(detail.original_batch);
        pushImpRecent(detail.original_batch.batch_id);
        setImpStatus("重复文件已拒绝：该文件此前已导入过（原批次见下方报告）", true);
      } else if (resp.status === 409 && detail && detail.reason === "import_busy" && detail.rejected_batch) {
        renderImportReport(detail.rejected_batch);
        pushImpRecent(detail.rejected_batch.batch_id);
        setImpStatus("导入忙：已有批次在处理中，本次已拒绝（409）", true);
      } else {
        setImpStatus(authErrorHint(resp.status, typeof detail === "string" ? detail : ""), true);
        $("imp-report").innerHTML = `<p class="diag-err">${escapeHtml(
          typeof detail === "string" ? detail : JSON.stringify(detail || {}))}</p>`;
      }
      return;
    }
    renderImportReport(data);
    pushImpRecent(data.batch_id);
    if (data.status === "committed" || data.status === "partial") {
      $("imp-file").value = "";
      setImpStatus(
        data.status === "committed"
          ? `导入成功（批次 ${data.batch_id.slice(0, 22)}…）`
          : `导入完成，但有 ${(data.errors || []).filter((e) => e.level === "error").length} 行错误未写入（详见下方报告）`);
      // 新班出现在「学情分析」下拉（S5 班级列表已随库刷新；不改诊断计算）
      const hint = "；已刷新「学情分析」班级下拉，可到该页加载新班诊断";
      initDiagClasses().then(() => setImpStatus($("imp-status").textContent + hint));
    } else {
      setImpStatus("导入未写入任何行（详见下方报告）", true);
    }
  } catch (err) {
    setImpStatus("上传失败：" + err.message, true);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function loadImportBatch(batchId) {
  const id = (batchId || $("imp-batch-id").value).trim();
  if (!id) { setImpStatus("请输入批次 ID", true); return; }
  setImpStatus("查询批次中…");
  try {
    const resp = await fetch(`/api/import/${encodeURIComponent(id)}`, { headers: apiHeaders() });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      setImpStatus(authErrorHint(resp.status, data.detail), true);
      return;
    }
    renderImportReport(data);
    pushImpRecent(data.batch_id);
    setImpStatus(`批次 ${data.batch_id}：${IMPORT_STATUS_LABELS[data.status] || data.status}`);
  } catch (err) {
    setImpStatus("查询失败：" + err.message, true);
  }
}

function importStatusBadge(status) {
  const cls = status === "committed" ? "badge-ok"
    : status === "failed" ? "badge-bad"
    : "badge-warn";
  return `<span class="usage-badge ${cls}">${escapeHtml(IMPORT_STATUS_LABELS[status] || status)}</span>`;
}

function renderImpIssues(items, codeLabels) {
  if (!items.length) return '<p class="muted">无。</p>';
  return `<table class="diag-table imp-issue-table"><thead><tr><th>行号</th><th>字段</th><th>错误码</th><th>说明</th></tr></thead><tbody>${
    items.map((i) => {
      const label = codeLabels[i.code] || "";
      return `<tr class="imp-issue-${i.level}"><td>${fmtNum(i.row)}</td>` +
        `<td><code>${escapeHtml(i.field || "—")}</code></td>` +
        `<td><code title="${escapeHtml(label)}">${escapeHtml(i.code)}</code></td>` +
        `<td>${escapeHtml(i.message)}</td></tr>`;
    }).join("")
  }</tbody></table>`;
}

function renderImportReport(report) {
  const box = $("imp-report");
  if (!box) return;
  if (!report) { box.innerHTML = '<p class="muted">（暂无导入记录）</p>'; return; }
  const errors = report.errors || [];
  const warnings = report.warnings || [];
  const errGroups = IMPORT_SHEET_ORDER
    .map((s) => ({ sheet: s, items: errors.filter((e) => e.sheet === s) }))
    .filter((g) => g.items.length);

  let html = "";
  html += `<div class="imp-meta">${importStatusBadge(report.status)}` +
    `<span>批次 <code>${escapeHtml(report.batch_id || "")}</code></span>` +
    `<span>${escapeHtml(IMPORT_KIND_LABELS[report.kind] || report.kind || "—")}</span>` +
    `<span>文件 ${escapeHtml(report.file_name || "—")}</span>` +
    `<span>总行 ${fmtNum(report.total_rows)} · 成功 ${fmtNum(report.ok_rows)} · 失败 ${fmtNum(report.failed_rows)}</span>` +
    `<span>sha256 <code>${escapeHtml((report.file_sha256 || "").slice(0, 12))}…</code></span>` +
    `<span>创建 ${escapeHtml(report.created_at || "—")}</span></div>`;

  html += `<div class="imp-sheet"><b>错误（${errors.filter((e) => e.level === "error").length}）</b>`;
  if (report.fatal) html += `<div class="imp-fatal">${escapeHtml(report.fatal)}</div>`;
  if (errGroups.length) {
    for (const g of errGroups) {
      html += `<div class="imp-sheet-block"><b>工作表「${escapeHtml(g.sheet)}」</b>` +
        renderImpIssues(g.items, IMP_ERROR_LABELS) + `</div>`;
    }
  } else {
    html += "<p class=\"muted\">无——校验全部通过，所有数据行均已写入。</p>";
  }
  html += "</div>";

  const warnGroup = IMPORT_SHEET_ORDER
    .map((s) => ({ sheet: s, items: warnings.filter((e) => e.sheet === s && e.level === "warning") }))
    .filter((g) => g.items.length);
  html += `<div class="imp-sheet"><b>警告（${warnings.length}）</b>`;
  if (warnGroup.length) {
    for (const g of warnGroup) {
      html += `<div class="imp-sheet-block"><b>工作表「${escapeHtml(g.sheet)}」</b>` +
        renderImpIssues(g.items, IMP_WARNING_LABELS) + `</div>`;
    }
  } else {
    html += "<p class=\"muted\">无。</p>";
  }
  html += "</div>";

  html += '<p class="muted">说明：error = 该行不写入；warning = 已写入并记录。' +
    '批次状态 — 已提交：全部成功；部分成功：含错误行；失败：无可写入数据或关联大面积断裂/写入异常。</p>';

  box.innerHTML = html;
}

function initImport() {
  const tpl = $("imp-template"), up = $("imp-upload"), q = $("imp-query"),
        kind = $("imp-kind"), force = $("imp-force");
  if (tpl) tpl.addEventListener("click", downloadImportTemplate);
  if (up) up.addEventListener("click", uploadImportFile);
  if (q) q.addEventListener("click", () => loadImportBatch());
  if (kind && force) kind.addEventListener("change", () => {
    force.disabled = kind.value !== "answers";
    if (kind.value !== "answers") force.checked = false;
  });
  renderImpRecent();
}

function initDiagnosis() {
  const load = $("diag-load"), rep = $("diag-report");
  if (load) load.addEventListener("click", loadDiagnosis);
  if (rep) rep.addEventListener("click", generateDiagReport);
  initDiagClasses();
}

function initObserve() {
  const btn = $("obs-refresh"), sel = $("obs-days");
  if (btn) btn.addEventListener("click", loadObserve);
  if (sel) sel.addEventListener("change", loadObserve);
  loadObserve();
}

initDiagnosis();
initObserve();
initImport();

/* ===================== 评测中心（Phase 4 / S4） ===================== */
function initEval() {
  const btn = $("eval-refresh");
  if (btn) btn.addEventListener("click", loadEvalReport);
  loadEvalReport();
}

function setEvalStatus(text, isErr) {
  const el = $("eval-status");
  if (!el) return;
  el.textContent = text || "";
  el.classList.toggle("diag-err", !!isErr);
}

function pctFmt(v) {
  return (v === null || v === undefined) ? "—" : (v * 100).toFixed(2) + "%";
}

async function loadEvalReport() {
  if (!$("eval-cards")) return;
  setEvalStatus("加载中…");
  try {
    const data = await fetchJSON("/api/eval/report?limit=5");
    renderEvalCards(data);
    renderEvalRuns(data.recent_runs || []);
    renderEvalBaseline(data);
    setEvalStatus(`已加载（${new Date().toLocaleTimeString()}）`);
  } catch (err) {
    setEvalStatus(err.message, true);
    const box = $("eval-cards");
    if (box) box.innerHTML = '<p class="diag-err">评测数据加载失败（检查 Token / 服务状态）</p>';
  }
}

function renderEvalCards(d) {
  const box = $("eval-cards");
  if (!box) return;
  const l = d.latest_run;
  if (!l) {
    box.innerHTML = '<p class="diag-note">暂无评测记录（先运行 python -m backend.app.eval.evaluate 写入 eval_runs）</p>';
    return;
  }
  const card = (label, val) =>
    `<div class="state-card"><b>${val}</b><span>${label}</span></div>`;
  box.innerHTML =
    card("最近任务成功率", pctFmt(l.pass_rate)) +
    card("行为正确率", pctFmt(l.behavior_accuracy)) +
    card("已知成本（元）", fmtCost(l.known_cost_cny)) +
    card("用例数", fmtNum(l.cases));
}

function renderEvalRuns(runs) {
  const box = $("eval-runs");
  if (!box) return;
  if (!runs.length) { box.innerHTML = '<p class="diag-note">暂无 run</p>'; return; }
  const rows = runs.map((r) => `<tr>
    <td><code>${escapeHtml(r.run_id)}</code></td>
    <td>${escapeHtml(r.mode || "—")}</td>
    <td>${fmtNum(r.cases)}</td>
    <td>${pctFmt(r.pass_rate)}</td>
    <td>${pctFmt(r.behavior_accuracy)}</td>
    <td>${fmtCost(r.known_cost_cny)}</td>
    <td>${r.unknown_cost_cases ? fmtNum(r.unknown_cost_cases) + " 未知" : "0"}</td>
    <td>${r.p95_latency_ms === null || r.p95_latency_ms === undefined ? "—" : r.p95_latency_ms + " ms"}</td>
  </tr>`).join("");
  box.innerHTML = `<table class="diag-table"><thead><tr>
    <th>run_id</th><th>模式</th><th>用例</th><th>任务成功率</th><th>行为正确率</th>
    <th>已知成本</th><th>成本未知任务</th><th>p95 延迟</th>
  </tr></thead><tbody>${rows}</tbody></table>`;
}

function renderEvalBaseline(d) {
  const box = $("eval-baseline");
  if (!box) return;
  const b = d.baselines || {};
  const ret = b.retrieval || {};
  const diag = b.diagnosis || {};
  const fake = b.fake_runner || {};
  const jc = d.judge_calibration || {};
  const qi = d.quality_inspection || {};
  const adoptedTxt = jc.adopted
    ? `<span class="diag-status">已采信 judge（${pctFmt(jc.best_agreement)}）</span>`
    : `<span class="diag-warn">judge 未采信（best ${pctFmt(jc.best_agreement)} &lt; 阈值 ${pctFmt(jc.threshold)}）→ 规则评分回退（${escapeHtml(jc.fallback || "rule_score")}）</span>`;
  const versions = (jc.versions || []).map((v) =>
    `<span class="diag-note">${escapeHtml(v.version)}：${pctFmt(v.agreement)}${v.note ? "（" + escapeHtml(v.note) + "）" : ""}</span>`).join("；");
  const qiTxt = qi.status === "pending_phase5"
    ? `<span class="diag-note">质检拦截率 / 回流收敛轮次：${escapeHtml(qi.note || "待 Phase 5")}</span>`
    : `<span class="diag-note">${escapeHtml(JSON.stringify(qi))}</span>`;
  box.innerHTML = `
    <table class="diag-table"><thead><tr><th>指标</th><th>基线值</th><th>说明</th></tr></thead><tbody>
      <tr><td>检索 R@1 / R@5 / MRR / NDCG@5</td>
          <td>${pctFmt(ret["R@1"])} / ${pctFmt(ret["R@5"])} / ${ret.MRR ?? "—"} / ${ret["NDCG@5"] ?? "—"}</td>
          <td>${escapeHtml(ret.source || "")}（${fmtNum(ret.cases)} 条）</td></tr>
      <tr><td>归因一致率</td><td>${pctFmt(diag.consistency)}</td>
          <td>${escapeHtml(diag.source || "")}（${fmtNum(diag.cases)} 例）</td></tr>
      <tr><td>Fake Runner 任务成功率 / 行为正确率 / Planner 准确率</td>
          <td>${pctFmt(fake.task_success_rate)} / ${pctFmt(fake.behavior_accuracy)} / ${pctFmt(fake.planner_field_accuracy)}</td>
          <td>${escapeHtml(fake.source || "")}（${fmtNum(fake.cases)} 条）</td></tr>
      <tr><td>judge 校准</td><td>${adoptedTxt}</td><td>${versions}</td></tr>
      <tr><td>质检拦截率 / 回流收敛</td><td>待 Phase 5</td><td>${qiTxt}</td></tr>
    </tbody></table>`;
}

initEval();
