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
