# Phase 6 执行计划：工程完善、公开合规与生产部署

> 制定：2026-11-20；依据：决策 55（Phase 6 重设）。
> 执行方式：本地 Windows 开发与验收 → GitHub（版本中心）→ 阿里云 ECS 集中部署；任何 ECS 操作需用户逐次批准（决策⑬⑭）。

## 1. 定位

本阶段把项目从「功能完整」推进到「可公开审阅、可复现构建、可稳定上线」的工程状态。

仓库保持 **PUBLIC**，内容边界为纯工程交付：代码、架构与部署文档、评测证据。非工程类个人材料不进入本仓库。

## 2. 起点与差距

| 项 | 现状 | 差距 |
|---|---|---|
| 功能 | Phase 0–5 全部关账；542 passed；`main = origin/main = a63f417` | — |
| 合规 | 工作区与全历史扫描 high=0；无 `.env`/DB/raw/staging/models 入库 | 工作区仍有 34 处非工程类措辞；无 LICENSE |
| 仓库可读性 | README 停留在 Phase 2 视角 | 缺六器官结构、评测指标、已知限制、部署说明 |
| 依赖/镜像 | requirements 仅部分锁定；Dockerfile 为 `python:3.11-slim`；本机 Python 3.13.7 | 版本不一致、构建不可复现 |
| 部署 | compose `8000:8000` 全网卡；无 nginx / HTTPS / 部署文档 | 未上线 |

## 3. 步骤

### S0 开工与合规基线（2026-11-20）

交付物：
- 本计划、决策 55、ROADMAP / 技术方案 §12 的 Phase 6 口径改写；
- `backend/scripts/audit_public_repo.py`：工作区 + 全历史 blob 扫描，模式匹配、输出脱敏、high 级发现返回退出码 1；
- 审计报告 `data/staging/phase6_compliance_scan_20261120.*`（不入 Git）。

验收：
- 审计 high=0；历史唯一候选经人工复核为占位符（`LLM_API_KEY=sk-xxxx`）；
- 文档口径一致，无遗留旧阶段定义。

### S1 公开内容清理与许可

- `git rm docs/development/RESUME-AMMO.md`，副本移至仓库外（不提交）；
- 改写 `CLAUDE.md` 首段、设计文档与代码注释中的非工程类措辞（工作区共 34 处）；
- 补 `LICENSE`（MIT）；在 README / `DATA-SOURCES.md` 明确数据许可边界：APE210K 仅研究用途，不随仓库分发、不随公开产品向公众提供；
- 根目录游离脚本 `test_p2_7b_*.py`、`test_p2_8_closed_loop.py` 移入 `tests/manual/`（服务器侧 e2e，`pytest.ini` 不收集），保留可追溯性。

验收：
- 审计 `career_content` 工作区命中 = 0（历史保留，不重写历史）；
- 全量 pytest 542 passed。

### S2 工程完善

- README 重写：定位、六器官架构、快速开始、评测与指标、已知限制、数据许可、部署入口；
- 依赖可复现：锁定运行依赖（`requirements-lock.txt`），CI 与 Docker 使用同一集合；
- 镜像对齐：`python:3.13-slim`（与本机/CI 一致）、非 root 用户、`.dockerignore` 复核；
- compose 加固：`127.0.0.1:8000:8000`、`security_opt: no-new-privileges`、保留健康检查；
- `DEPLOYMENT.md`：拓扑、前置条件、命令、验收、回滚、备份；
- 备份脚本：SQLite 在线备份 + 保留策略；
- CI 增加镜像构建 smoke（不推送）；
- `CHANGELOG.md` + 发布 tag（部署验收后）。

验收：
- 542 passed、CI 绿、`docker build` 成功、fake smoke 20 条通过。

### S3 阿里云 ECS 部署（需用户提供信息并逐次批准）

前置信息：实例规格/系统、SSH 方式、Docker 状态、安全组、DNS 解析、证书方式。

步骤：nginx 反代 + HTTPS → compose 上线 → `.env` 服务器本地注入 → 数据准备 → 分级验收。

安全默认：MCP 敏感工具关闭、管理员 Token 不配、应用仅监听回环、限流保持 60/min、独立 LLM Key + 消费上限。

验收：
- HTTPS 200、`/api/health` 通过、无 Token 401、限流 429；
- 导入 → 诊断 → 报告核心链路可用；
- Trace 可回放、MCP 普通工具可用；
- 真实 LLM 调用允许（受预算与限流约束）。

交付：部署验收报告（不含密钥）、回滚步骤。

### S4 关账

- STATUS / ROADMAP / DECISIONS 回填；commit + push；发布 tag；
- 验收：证据完整、下一步明确。

## 4. 开放决策

1. **线上数据形态（S3 前必须定）**
   - A（推荐）：线上使用演示库（合成数据 + 少量自有题）；完整题库只留在本地开发与评测环境。
   - B：线上使用完整库 + Token 访问。
   - 推荐理由：决策㉒/㉓ 限定 APE210K 不随公开产品向公众提供；B 存在许可与分发风险。B 的代价还需额外做访问审计与撤回预案。
2. 服务器信息（S3 前由用户提供）。
3. 根目录游离脚本处置（S1，默认移入 `tests/manual/`）。
4. 依赖锁定粒度（S2，默认锁定运行依赖全版本）。

## 5. 风险与应对

| 风险 | 应对 |
|---|---|
| APE210K 公开部署许可 | 线上演示库；README/ DATA-SOURCES 显式声明 |
| 公网 LLM Key 滥用 | 独立 Key + 消费上限 + Token + 60/min + 敏感工具关闭 |
| ECS 2C2G 内存（BGE / onnxruntime） | 上线前实测；必要时限制并发或懒加载 |
| SQLite 单文件并发与备份 | 确认 WAL；部署前在线备份；保留策略 |
| Python 3.13 镜像依赖可用性 | 本地/CI 同版本先验证；失败回退 3.11 并记录原因 |

## 6. 明确不做

- 非工程类个人材料；
- 新业务功能；
- 大规模重构、全仓 ruff 类格式化（噪声 diff，收益低）；
- Phase 4/5 遗留（全量 online、gc_e03 修复、评测页浏览器点验）；
- Git 历史重写（遵守禁止 force push 纪律）。

## 7. 验收总表

| 项 | 达标线 |
|---|---|
| 合规 | 工作区 + 历史扫描 high=0；工作区无非工程类内容 |
| 许可 | 代码 MIT LICENSE；数据许可边界成文 |
| 测试 | 全量 pytest 542 passed；CI 绿 |
| 构建 | `docker build` 成功；依赖版本可复现 |
| 部署 | HTTPS 可访问，核心链路验收通过，runbook + 回滚可用 |
| 文档 | README 可在 5 分钟内让陌生人跑起来；DEPLOYMENT 可照做 |
