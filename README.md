# EduMate（EduAgent）

面向小学数学教师的 AI 教学任务自动化智能体。用自然语言下达教学任务，系统完成
**检索选题 / 实时组卷 / 质检复核 / 试卷与答题卡 / 成绩分析 / 学情归因 / 教案生成**，
全程引用可溯源、步骤可回放、成本可核算；班级数据经 Excel 模板导入，能力经 MCP 开放。

> 运行时聚焦**小学单学段**：82 个知识点、87 条教学前置边；开发库含 207,949 道
> APE210K 小学题（仅本地开发/评测，不随仓库分发）；公开演示使用自主合成的
> **410 道演示题**（82 KP × 难度 1–5）。

## 当前状态

| Phase | 内容 | 状态 |
|---|---|---|
| 0 | 工程地基（统一导入、Docker、CORS、CI 前身） | ✅ |
| 1 | 数据地基（图谱、APE210K ETL、F16 导入） | ✅ |
| 2 | 可信检索（三阶段检索、FTS5、引用、认证限流） | ✅ |
| 3 | 学情归因 + Trace/成本可观测 | ✅ |
| 4 | 评测体系（golden、runner、judge 校准、评测页、CI） | ✅ |
| 5 | 质检环 critique + MCP 能力开放 | ✅ |
| 6 | 公开合规、工程完善、合成演示数据、生产部署 | 🔄 |

全量测试：**555 passed**。CI：pytest + fake smoke + 公开仓库合规扫描 + 生产镜像构建。

## 六个能力面

| 能力面 | 实现与关键数字 |
|---|---|
| 数据工程 | APE210K ETL：210,488 → processed 207,858；独立重放核验 0 差异；82 KP / 87 边；F16 五表导入（模板/校验/错误报告/事务） |
| 可信检索 | 知识点召回 → 题目扩展 → 重排；中文 FTS5 预分词 + BGE ONNX；正式基线（49 条）：R@1 32.65% / R@5 55.10% / MRR 0.4335 / NDCG@5 0.4201；引用覆盖率 100% |
| 学情诊断 | 确定性掌握度（分值加权 × 近期 2:1 × 主/辅权重 λ=0.5）+ 前置图根因回溯 + 真实题库推荐；10 例 GT 一致率 10/10（仅代表冻结案例算法一致性） |
| 评测体系 | 111 条 golden（core 80 / boundary 31）；Fake/Online runner；预算三线；judge 4 轮校准 64.0% 未达 80% → 按规格回退规则评分并记录 |
| 可观测 | Trace 覆盖工作流步骤；逐次 LLM 调用记录 tokens/成本/延迟/价格版本；成本可复算；重启后 `/api/tasks` + `/trace` 库直读回放 |
| 多智能体 | critique 质检环（仅 question_gen）：三维规则 + LLM 一题一验 + ≤2 轮回流 + 三态降级 + 6 指标；MCP 3 工具（stdio + Streamable HTTP、双 Token、敏感工具开关、DNS Rebinding 防护、60/min） |

## 架构

```
前端（原生 HTML/CSS/JS + marked + KaTeX + DOMPurify）
        │ REST + SSE
        ▼
FastAPI（chat / tasks / import / diagnosis / eval / MCP）
        ▼
Agent 引擎（Planner → Workflow → Executor；手写编排，零重型框架）
        │                         │
        ▼                         ▼
LLM 客户端（DeepSeek，OpenAI 兼容）  工具/服务层（检索、组卷、文档、诊断、质检）
        │                         │
        └────────────┬────────────┘
                     ▼
SQLite（图谱 / 题库 / 学生六表 / Trace / 评测）+ 本地 BGE ONNX
```

设计取舍：不引入 LangChain/LangGraph/CrewAI、向量数据库、消息队列、前端框架；
Agent 编排手写，检索向量索引用纯 numpy 内存内积（千级 KP 规模）。

## 快速开始

### 本地运行（Windows 示例）

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

copy .env.example .env          # 填 DEEPSEEK_API_KEY 与 EDUMATE_API_TOKEN
.venv\Scripts\python.exe -m uvicorn backend.main:app --app-dir . --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000 。启动时会校验种子数据，缺失/非法即中止。

### Docker

```bash
docker compose -f deploy/docker-compose.yml up -d --build
curl -fsS http://127.0.0.1:8000/api/health
```

生产部署（aliyun ECS + nginx + HTTPS）见 [DEPLOYMENT.md](DEPLOYMENT.md)。

## 配置

主要环境变量（完整见 `.env.example`）：

| 变量 | 说明 |
|---|---|
| `DEEPSEEK_API_KEY` | LLM Key（不入库、不进日志） |
| `EDUMATE_API_TOKEN` | API Access Token；未配置则 fail-closed（仅 `/api/health` 可用） |
| `ALLOWED_ORIGINS` | CORS 白名单（逗号分隔） |
| `EDUMATE_MCP_ADMIN_TOKEN` | 敏感 MCP 工具的管理员 Token（线上不配置） |
| `EDUMATE_MCP_ALLOW_SENSITIVE` | 敏感工具总开关（默认 0） |
| `EDUMATE_DB_PATH` | 覆盖 SQLite 路径（测试/部署） |

## 合成演示数据（公开可分发）

```bash
# 1) 重建演示题库（需要 Key；固定 seed，模板可复现）
python -m backend.scripts.build_demo_bank --generate --allow-live-api --budget-cap 5

# 2) 写入业务库（只写 q_demo_*，不触碰 APE210K）
python -m backend.scripts.load_demo_bank

# 3) 构建演示班级（20 学生 × 2 考试 × 20 题）并跑诊断闭环
python -m backend.scripts.build_synthetic_demo_class

# 4) 验收诊断/推荐/PDF
python -m backend.scripts.verify_synthetic_demo_class --class-id cls_c6a3a5de3f

# 5) 生成线上纯合成库（APE210K 残留必须为 0）
python -m backend.scripts.build_online_demo_db
```

冻结题库：`data/demo/demo_bank_v1.jsonl`（410 题，sha256 `f938c95a…`），
人工抽检 41/41 通过；演示班级：20 学生 / 2 考试 / 40 卷面题 / 800 答题，
A 卷均分 72.0、B 卷 65.4，诊断产生 3 个薄弱 KP、3 条归因链、5 组推荐。
线上演示库只含合成题与合成班级，**不含 APE210K 内容**。

## 评测与质量

```bash
# Fake 档（零 API 成本）
python -m backend.app.eval.evaluate --mode fake

# 子集 smoke
python -m backend.app.eval.evaluate --mode fake --cases gc_q01,gc_q07,gc_e03

# 全量测试
.venv/Scripts/python.exe -m pytest -q
```

**口径边界（如实）**：

- 检索 R@5 55.10%，未达目标 80%；已知限制与 backlog 记录在 `docs/development/STATUS.md`。
- judge 校准最高 64.0% < 80%，未采信，已回退规则评分并全量应用。
- critique 真实指标基于 10 条冻结 question_gen（78 题）；修复后 78/78 通过、0 unverified，
  但仍为小样本、含 LLM 主观复核，不宣称教学正确率或统计代表性。
- 归因 10/10 只代表冻结案例上的算法行为一致性，不代表真实教学诊断准确率。
- 演示班级部分 KP 因样本 <3 显示 `insufficient`，是诊断规则的真实边界。

## API 与 MCP

- 认证：`Authorization: Bearer <token>` 或 `X-API-Token`；限流按端点（如 `/api/eval` 60/min）。
- 主要端点：`/api/chat`、`/api/tasks`、`/api/tasks/{id}/trace`、`/api/stats/cost`、
  `/api/import`、`/api/classes`、`/api/diagnosis/class/{id}`、`/api/eval/report`、`/mcp/`。
- MCP：3 工具复用服务层；stdio 本地 + Streamable HTTP 远程；普通 Token 只能调检索/题库，
  敏感工具需管理员 Token + 环境开关；Host/Origin 白名单独立于认证（421/403）。

## 仓库结构

```
backend/
  app/
    agent/        手写 Plan-and-Execute、工作流、LLM 客户端
    retrieval/    三阶段检索、FTS5、引用
    diagnosis/    掌握度、归因、推荐、叙述、PDF
    critique/     质检规则、LLM 复核、回流
    mcp/          MCP 工具与传输
    eval/         golden/runner/judge/预算/聚合
    demo/         合成题库模板、生成、校验、演示班级设计
    storage/      SQLite、迁移、F16 导入
  scripts/        数据、评测、演示、审计、备份
  tests/          单测与集成测试
frontend/         原生前端
data/             图谱、种子、合成题库、映射（不含 DB/原始数据）
deploy/           Dockerfile、compose、nginx 模板
docs/             产品/技术/开发状态文档
```

## 已知限制

- 浏览器端自动化测试未建立；前端以起服冒烟 + HTTP/脚本 E2E 覆盖。
- 重启后 `/api/tasks/{id}` 单任务快照、`/citations`、`/files` 依赖进程内缓存（已知限制）。
- 检索长查询改写、KP 描述扩充、单位词泛化未做（Round2 backlog）。
- 线上演示使用合成题库；与本地 APE210K 开发库的结果可能不同。

## 许可

- 代码：[MIT License](LICENSE)
- 数据边界：[DATA-NOTICE.md](DATA-NOTICE.md)（仓库不包含 APE210K 数据集或其派生完整题库；
  演示数据为合成数据）

## 文档

- [docs/产品设计方案.md](docs/产品设计方案.md)
- [docs/技术方案.md](docs/技术方案.md)
- [docs/development/STATUS.md](docs/development/STATUS.md)：当前状态与证据
- [docs/development/DECISIONS.md](docs/development/DECISIONS.md)：实施期决策
- [DEPLOYMENT.md](DEPLOYMENT.md)：部署手册
