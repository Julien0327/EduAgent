# 部署手册（阿里云 ECS + Docker + nginx + HTTPS）

> 适用版本：Phase 6 / S3。任何 ECS 操作需用户逐次批准；本文件是执行清单，不代表已执行。

## 1. 拓扑

```
Internet
  │ 80/443
  ▼
nginx（宿主机）
  │ 127.0.0.1:8000
  ▼
docker compose: web（非 root uid 10001）
  ├─ data/db/edumate.sqlite       纯合成演示库
  ├─ data/outputs/                任务产物
  └─ data/models/                 BGE 缓存（只读）
```

- 应用不直接暴露 8000；安全组只放行 80/443（SSH 按需）。
- 线上数据库必须是**纯合成演示库**（`data/staging/edumate_online_demo.sqlite`），不得上传含 APE210K 内容的开发库。

## 2. 前置条件

- ECS：2 vCPU / 2 GB 起；Docker 24+、Docker Compose v2.24+。
- 域名 `edumateaip.xyz` A 记录指向实例公网 IP。
- 服务器可访问 PyPI 或国内镜像（构建时用 `PIP_INDEX_URL`）。
- 本地已生成：
  - `data/staging/edumate_online_demo.sqlite`（约 35 MB）
  - `data/models/`（BGE 缓存，约 204 MB）
  - `.env`（含 Key/Token，不提交）

## 3. 本地准备与校验

```bash
# 全量测试
.venv/Scripts/python.exe -m pytest -q

# 构建线上演示库（纯合成，APE210K 残留必须为 0）
.venv/Scripts/python.exe -m backend.scripts.build_online_demo_db

# 构建镜像
docker build -f deploy/Dockerfile -t edumate:latest .
```

## 4. 服务器目录与权限

以 `/opt/EduMate` 为例：

```bash
sudo mkdir -p /opt/EduMate
cd /opt/EduMate
# 同步代码（Git 或压缩包）
sudo chown -R 10001:10001 data/db data/outputs
sudo mkdir -p data/models && sudo chown -R root:root data/models
```

容器以 uid/gid 10001 运行；`data/db`、`data/outputs` 必须可写，`data/models` 只读即可。

## 5. 上传数据与配置

```bash
# 演示库：本地文件改名为 edumate.sqlite 后上传
scp data/staging/edumate_online_demo.sqlite user@ECS:/opt/EduMate/data/db/edumate.sqlite

# 模型缓存（204 MB，可打包传输）
scp -r data/models user@ECS:/opt/EduMate/data/

# 环境变量：在服务器本地创建，权限 600
sudo tee /opt/EduMate/.env >/dev/null <<'EOF'
DEEPSEEK_API_KEY=<server-key>
EDUMATE_API_TOKEN=<long-random-token>
ALLOWED_ORIGINS=https://edumateaip.xyz
EDUMATE_MCP_ALLOW_SENSITIVE=0
# 不配置 EDUMATE_MCP_ADMIN_TOKEN：线上不开放敏感 MCP 工具
EOF
sudo chmod 600 /opt/EduMate/.env
```

## 6. 启动应用

```bash
cd /opt/EduMate
docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml ps
curl -fsS http://127.0.0.1:8000/api/health
```

## 7. nginx 与 HTTPS

```bash
sudo apt-get update && sudo apt-get install -y nginx certbot python3-certbot-nginx

# 1) 先用 nginx 默认站点签发证书（此时自定义 443 配置尚未启用）
sudo certbot certonly --nginx -d edumateaip.xyz
sudo systemctl enable --now certbot.timer   # 自动续期

# 2) 证书就位后启用自定义配置
sudo cp deploy/nginx.conf /etc/nginx/sites-available/edumate
sudo ln -sf /etc/nginx/sites-available/edumate /etc/nginx/sites-enabled/edumate
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

# 3) 验证续期
sudo certbot renew --dry-run
```

若证书路径不同，修改 `deploy/nginx.conf` 中 `ssl_certificate` 两行后重载。

## 8. 安全组

- 放行：22（限定来源 IP）、80、443。
- 不放行：8000（仅回环）。
- 不在公网开放 MCP 敏感工具；管理员 Token 不配置。

## 9. 上线验收清单

```bash
# 1) HTTPS 与健康检查
curl -I https://edumateaip.xyz
curl -fsS https://edumateaip.xyz/api/health

# 2) 认证：无 Token 401，有 Token 200
curl -i https://edumateaip.xyz/api/classes
curl -H "Authorization: Bearer $TOKEN" https://edumateaip.xyz/api/classes

# 3) 演示班级诊断（JSON）
curl -H "Authorization: Bearer $TOKEN" \
  https://edumateaip.xyz/api/diagnosis/class/cls_c6a3a5de3f

# 4) 前端页面
curl -fsS https://edumateaip.xyz/ | grep -q "EduMate"

# 5) MCP 普通工具（Streamable HTTP，注意尾部斜杠与 Accept）
curl -i -X POST https://edumateaip.xyz/mcp/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"ops","version":"1.0"}}}'
```

浏览器人工点验：首页、评测中心、学情分析（合成演示班）、运行观测、数据导入页。

## 10. 备份

```bash
# 每日 02:00 备份，保留 7 份（脚本仅用标准库）
0 2 * * * cd /opt/EduMate && /usr/bin/python3 -m backend.scripts.backup_demo_db \
  --db data/db/edumate.sqlite --out-dir data/backups --keep 7 >> logs/backup.log 2>&1
```

恢复：停 compose → 解压备份覆盖 `data/db/edumate.sqlite` → 校验 `PRAGMA integrity_check` → 启动。

## 11. 回滚

1. `docker compose -f deploy/docker-compose.yml down`
2. 恢复上一份数据库备份（或重新上传上一版演示库）
3. `git checkout <上一版 tag>` / 使用上一版镜像
4. `docker compose ... up -d --build`
5. 重跑第 9 节验收

## 12. 已知限制（如实）

- 线上演示库只含合成题与合成班级，不含 APE210K；因此线上推荐与本地开发库的结果可能不同。
- 演示班级共 40 道卷面题覆盖 41 个 KP（含辅助证据），部分 KP 因样本 <3 显示为 `insufficient`，这是诊断规则的真实边界，不是 bug。
- MCP 默认只开放普通工具；敏感工具需管理员 Token + 环境开关，线上不启用。
- SSE / MCP 长连接依赖 nginx 关闭缓冲与长超时（模板已配置）。
