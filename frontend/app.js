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
