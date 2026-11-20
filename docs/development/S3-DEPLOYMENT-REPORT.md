# S3 阿里云部署报告（2026-11-20）

> 执行方式：`workbench exec/upload` 通过阿里云 Workbench CLI；每一步经用户确认后执行。
> 本文只记录部署事实与证据，不包含任何密钥/Token 值。

## 1. 目标环境

| 项 | 值 |
|---|---|
| 实例 | `i-uf6dcrndq12k8eqz1quy` |
| 公网 IP / 域名 | `47.116.76.175` / `edumateaip.xyz` |
| 系统 | Ubuntu 22.04.5 LTS，x86_64 |
| 规格 | 2 vCPU / 1.7 GB RAM / 40 GB 磁盘（部署后 31 GB 可用） |
| 代码 | `6bb4bad`（含 `86db1b1` 的 APT_MIRROR 修复） |

## 2. 部署结果

| 项 | 结果 |
|---|---|
| Docker | 29.10.0；compose v5.8.0 |
| 镜像 | `edumate:latest`，798 MB |
| 容器 | `deploy-web-1`，healthy；仅 `127.0.0.1:8000->8000` |
| 线上库 | 纯合成演示库 34,906,112 B；82 KP / 87 边 / 410 题 / 1 班 / 20 生 / 2 考 / 40 题 / 800 答；integrity ok |
| 模型 | `data/models` 205 MB，只读挂载 |
| nginx | 1.18.0；80 → 301 → 443；443 TLS |
| 证书 | Let's Encrypt，CN `edumateaip.xyz`，2026-11-20 至 **2027-02-18**；`certbot.timer` active，`renew --dry-run` 成功 |
| 内存 | 容器 318.1 MiB；宿主机 available 739 MB |
| A2A | `edumate-aip.service` 仍 active（9443，旧代码，未改动） |
| fluent-bit | 已停止并 disable，释放内存 |

## 3. HTTPS 验收（实测）

| 检查 | 结果 |
|---|---|
| `GET https://edumateaip.xyz/api/health` | 200 `{"status":"ok","service":"EduAgent"}` |
| 无 Token `GET /api/classes` | **401** |
| 带 Token `GET /api/classes` | **200**，返回 `cls_c6a3a5de3f`（20 学生/2 考试/40 题/800 答） |
| `GET /api/diagnosis/class/cls_c6a3a5de3f` | 200 |
| `GET /api/tasks` | 200 |
| 首页 `GET /` | 200 |
| HTTP 80 | **301** → HTTPS |
| MCP `POST /mcp/` initialize | 200 |
| 证书链（公网 curl，未加 `-k`） | 通过 |

## 4. 遇到的问题与处理（避免拆东墙补西墙）

1. **Docker Hub 基础镜像拉取超时**（`registry-1.docker.io` 443 i/o timeout）
   - 处理：改用 `docker.m.daocloud.io/library/python:3.13-slim`，拉取后 `docker tag` 为 `python:3.13-slim`；Dockerfile 不变。
   - 影响：仅基础镜像来源变化，镜像仍为标准 Python 3.13 slim。

2. **构建阶段 Debian 官方 apt 源长时间无进展**
   - 处理：Dockerfile 新增 `ARG APT_MIRROR`（默认官方源），部署构建传 `mirrors.aliyun.com`；提交 `86db1b1` 并推送。
   - 影响：默认构建/CI 不受影响；国内构建可显式使用镜像。

3. **`docker compose up` 触发重复构建并再次卡在官方源**
   - 处理：compose 固定 `image: edumate:latest`、暴露 `build.args`、`pull_policy: never`；提交 `6bb4bad`；改用 `up -d --no-build`。
   - 影响：不再重复构建；生产机使用已构建镜像，构建与启动解耦。

## 5. 回滚资产

| 资产 | 路径 |
|---|---|
| 项目备份 | `/opt/EduAgent-backup-phase6-step2-20261120-192034.tar.gz`（76 MB，600） |
| 配置备份 | `/opt/EduAgent-config-backup-20261120-192034/`（systemd/nginx/.env） |
| 旧代码 commit | `f7af0e7`（Phase 0） |
| 旧 unit | `edumate.service`、`edumate-aip.service`、`fluent-bit-amp.service` 文件保留 |
| nginx 备份 | `state` 前配置在配置备份目录；本次新配置前另存 `nginx-edumate.conf` |

回滚顺序：`docker compose down` → 恢复项目/数据库 → `systemctl enable --now edumate.service` → 恢复 nginx 旧配置 → `nginx -t && systemctl reload nginx`。

## 6. 安全与边界

- 8000 仅回环；公网只开 80/443/9443（9443 为保留的 A2A，旧能力）。
- 线上库不含 APE210K；推荐/诊断只使用合成题与合成班级。
- MCP 敏感工具关闭（`EDUMATE_MCP_ALLOW_SENSITIVE=0`），未配置管理员 Token。
- `.env` 权限 600；Token/Key 不写入文档、不回显聊天。
- A2A 证书 2026-12-04 到期；按用户决定暂不处理（过去比赛用途，当前不使用）。

## 7. 待办

1. 配置每日 SQLite 备份 cron（`backend/scripts/backup_demo_db.py`）。
2. 浏览器人工点验：首页、评测中心、学情分析（合成演示班）、运行观测、数据导入页。
3. 可选：为容器/A2A 配置内存监控与告警；当前 available 739 MB，余量偏紧。
