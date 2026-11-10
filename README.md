# 智教助手 EduMate（EduAgent）

> AI 教学任务自动化智能体 —— 用一句自然语言，完成「组卷 → 答题卡 → 学情报告」的完整教学闭环，产出可直接使用的 Word / Excel / PDF 文档。

面向中小学教师的智能教学助手：把「出卷、排版试卷、制作答题卡、批改统计、生成学情报告、撰写教案」等重复性教学事务，交给一个由自然语言驱动的 Agent 自动完成。教师只描述需求，系统自动规划并执行多步任务，最终交付规范、可用的文档产物。

## 核心功能

- **自然语言对话**：基于 DeepSeek，前端支持 Markdown 与 LaTeX 数学公式渲染（KaTeX）。
- **章节小测一条龙（核心闭环）**：自然语言 → 检索知识点 → 出卷 → 生成试卷（docx）→ 答题卡（xlsx）→ 加载预置答题 → 成绩分析 → 学情报告（pdf）。
- **智能组卷**：LLM 实时命制题目。
- **AI 教案生成**：LLM 生成结构化教案（docx）。
- **任务可视化**：SSE 流式步骤进度（展示 Agent 的思考与工具调用）+ 产物预览 / 下载。
- **工程加固**：统一日志（控制台 + 滚动文件）、启动期种子数据校验（fail-fast）、LLM 超时与指数退避重试、全局工作流超时。

## 技术架构

```
前端（原生 HTML / CSS / JS + marked / KaTeX / DOMPurify）
        │  REST + SSE（步骤进度）
        ▼
FastAPI（api 层：chat / health / tasks，静态托管前端）
        ▼
Agent 引擎（Planner 意图解析 → Workflow 固定编排 → Executor 工具执行）
        │                                    │
        ▼                                    ▼
   LLM 客户端（DeepSeek，OpenAI 兼容）      Tool 注册表 + 文档服务层
   （llm_client / llm_utils，含重试）        （exam_docx / answer_sheet / score_analysis / report_pdf / lesson_plan）
        │                                    │
        └──────────────┬─────────────────────┘
                       ▼
        内置种子数据（knowledge / questions / demo）+ 产物存储 data/outputs/
```

- **Agent 引擎手写**：Plan-and-Execute——该稳的地方稳（固定工作流编排，流程不跑飞），该灵活的地方灵活（出题、诊断、教案等单步内容由 LLM 自主生成）。**零重型框架**：不引入 LangChain / LangGraph / CrewAI。
- **LLM**：DeepSeek（OpenAI 兼容 API，官方 SDK）；结构化输出用「合成工具调用 + 校验重试」而非裸 JSON。
- **文档处理**：`python-docx`（试卷 / 教案）、`openpyxl`（答题卡）、`pandas`（成绩统计）、`reportlab`（学情报告 PDF）、`jinja2`（HTML 预览模板）。
- **智能体互联适配层（可选）**：`backend/interop/` 实现 ACPs 协议（AIP Partner `/rpc`、ACS 能力声明、AMP 心跳与访问日志），对接梧桐平台；官方 `acps-sdk` 为可选依赖，未安装 / 未开启（`ACP_ENABLED` 默认关闭）不影响业务运行。

## 快速开始

### 方式一：本地运行

> 以下以 Windows 为例；macOS / Linux 将 `.venv\Scripts` 换成 `.venv/bin`。

```bash
# 1. 安装依赖
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 2. 配置环境变量（必填 DEEPSEEK_API_KEY，仅从环境变量读取，绝不写入代码）
copy .env.example .env

# 3. 启动（在仓库根目录执行）
.venv\Scripts\python.exe -m uvicorn backend.main:app --app-dir . --host 127.0.0.1 --port 8000
```

启动时会校验种子数据（缺失 / 非法即中止启动）。打开浏览器访问 http://127.0.0.1:8000 使用前端。

### 方式二：Docker 一键启动

```bash
# 1. 准备环境变量（同上）
copy .env.example .env

# 2. 构建并启动
docker compose -f deploy/docker-compose.yml up -d --build
```

数据卷：`data/db`（数据库，Phase 1 起使用）与 `data/outputs`（任务产物）挂载到宿主机，容器重建不丢数据。健康检查：容器内定期访问 `/api/health`。

## 运行测试

```bash
.venv\Scripts\python.exe -m pytest        # 收集 backend/tests，当前 98 个用例
```

演示脚本（可选）：

```bash
# 确定性文档服务演示（无需 API Key）
.venv\Scripts\python.exe backend\scripts\generate_demo.py

# Agent 端到端演示（需 DEEPSEEK_API_KEY）
.venv\Scripts\python.exe backend\scripts\run_agent_demo.py "请对《一元二次方程》做章节小测并生成学情报告"
```

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查，返回 `{status, service}` |
| POST | `/api/chat` | 自然语言对话，请求体 `{message}` |
| POST | `/api/tasks` | 提交任务，请求体 `{prompt}`，返回 `202` + 任务快照 |
| GET | `/api/tasks/{id}` | 回查任务结果（轮询兜底） |
| GET | `/api/tasks/{id}/events` | SSE 实时进度流 |
| GET | `/api/tasks/{id}/files/{file_name}` | 下载产物（`?inline=true` 内联预览） |
| GET | `/api/tasks/{id}/preview/{kind}` | HTML 预览，`kind` ∈ `exam` / `answer_sheet` / `lesson` |

## 项目目录结构

```
EduAgent/
├── README.md
├── .env.example                 # 环境变量样例（勿提交真实密钥）
├── pytest.ini                   # 测试入口（收集 backend/tests）
├── requirements.txt
├── deploy/                      # Dockerfile + docker-compose.yml（数据卷 data/db、data/outputs）
├── docs/
│   ├── 产品设计方案.md           # v2.1：产品定位 / 功能规格 / 里程碑
│   ├── 技术方案.md               # v2.1：架构 / 数据模型 / RAG / MCP / 安全 / Phase 计划
│   └── Demo演练手册.md
├── backend/
│   ├── main.py                  # FastAPI 入口 + 静态挂载 + 启动 seed 校验
│   ├── config.py                # 环境变量集中读取（含 CORS 白名单）
│   ├── logging_config.py        # 日志系统（控制台 + 滚动文件）
│   ├── app/
│   │   ├── api/                 # chat.py / health.py / tasks.py
│   │   ├── agent/               # planner / workflow / executor / llm_client / llm_utils / context
│   │   ├── tools/               # registry + 3 个 LLM 工具（知识点检索 / 出题 / 诊断）
│   │   ├── services/            # 5 个文档服务 + preview（HTML 预览）
│   │   ├── knowledge/           # loader.py（seed 加载 + validate_seed 校验）
│   │   ├── storage/             # file_store.py（产物路径管理）
│   │   └── templates/           # Jinja2 HTML 预览模板
│   ├── interop/                 # ACPs/AIP 智能体互联适配层（可选能力，默认关闭）
│   ├── scripts/                 # generate_demo.py / run_agent_demo.py
│   └── tests/                   # 98 个用例（工具 / 工作流 / 日志 / seed / interop）
├── data/                        # 种子数据（只读）+ 运行产物（outputs/）
└── frontend/                    # 原生前端三件套 + vendor（marked / KaTeX / DOMPurify）
```

## 设计文档

- [docs/产品设计方案.md](docs/产品设计方案.md)：产品定位、用户与场景、功能规格（F1–F16）、里程碑。
- [docs/技术方案.md](docs/技术方案.md)：系统架构、数据模型（SQLite 16 表）、三阶段 RAG 检索、MCP 能力开放、API 安全、Phase 0–6 实施计划与验收标准。

## 许可证

暂未指定开源许可证；如需对外开源，将先完成合规检查并补充 `LICENSE` 文件（如 MIT）。
