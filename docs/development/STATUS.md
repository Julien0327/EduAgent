# EduMate 当前状态（STATUS）

> 最后更新：2026-11-11（Phase 1 Step 4）。**新会话开工前必读本文件 + docs/技术方案.md §11/§12**，以项目文档、实际代码和本目录状态文档为准，不依赖旧会话聊天记录。

## 当前位置

Phase 0（打地基）✅ 全部完成（commit `8dbf573`）。**Phase 1（数据地基）进行中**（2026-11-11 开工，顺序与关口协议经用户逐条确认，见 DECISIONS.md ⑲–㉒）：

- **Step 1 存储层建库 ✅**：V1 全量 DDL（§2.3 全部表，questions 按⑲用 quality_state+verified）+ db 连接层（每连接强制 foreign_keys=ON）+ 8 项单测；`.gitignore` 增 data/raw|db|staging（⑳）；本地已 commit，push 集中在 Phase 1 收尾。
- **Step 2 知识图谱构建 ✅（用户已确认）**：初中 303 知识点 + 374 条前置边入库；用户抽检 61 条提出 4 处修正已全部处理（commit `03878ee`/`574c9ad`）；抽样 seed=42 复现验证不变。
- **Step 3 F16 导入服务 ✅（用户已确认）**：模板生成 + 七步管线 + 三端点 + 17 项真实 Excel 端到端测试 + demo 冒烟 232 行 committed（commit `26e943c`/`1b4f159`）。
- **Step 4 APE210K ETL ✅（含方案 A 修复，待用户确认）**：数据源核验（许可裁决=决策㉒）+ 流式清洗入库 + FTS5 + 小学 KP 规则聚合 + 100 题完整率抽样；用户复核发现 B 类清洗损坏后按**方案 A（决策㉓）修复并全量重建**：processed 207,858 题；全量 **159 passed**；全库逐行核验 0 不一致。证据见 DATA-SOURCES.md 与下表。
- **Step 5 LLM 打标 = 下一步**（两道关口：500 题试跑→人工抽检合格率 ≥90%→成本汇报→用户批准才全量；开工前先核验 DeepSeek API Key 存在且可用，不打印）。

## Step 4 完成证据（2026-11-11，全部实测）

| 项 | 结果 | 证据 |
|---|---|---|
| 数据源核验 | ✅ 官方仓库 `Chenny0808/ape210k` clone（commit 52c52eb），三文件 SHA-256 校验并硬编码进 ETL 脚本（不匹配拒绝处理）；字段结构/规模/许可状态如实记录 | `docs/development/DATA-SOURCES.md`；许可模糊（无 LICENSE、README 仅覆盖代码、原论文撤稿称数据不公开）经用户裁决=决策㉒：本地使用不设限制、GitHub 不提交数据集本身 |
| ETL 漏斗 | ✅ 原始 210,488 → 完全重复剔除 2,539 / 验算不一致 rejected 91 / 疑似变式保留 244 → **processed 207,858（98.7%）+ rejected 91（入库可追溯）**；验算一致率 99.96%（可验算部分），无法验算仅 1,941（0.92%，不惩罚题目）。⚠️ 方案 A 修复后"题干无效 6→0"：原 6 行（`16*25．` 等巧算题）是被旧规则删星号变短才误判无效——修复后正确放行（+6），非灌水 | `data/staging/etl_ape210k_report.json` + stdout 实录（修复前后各 2 轮数字一致） |
| 清洗能力 | ✅ 百分数（7,202 行）、带分数 `n(a/b)` 记法解析；AST 白名单安全求值（`__import__` 注入拒绝、大指数防护）；验算 mismatch 才判 rejected；**星号规则已按方案 A 修复（决策㉓）：乘号 `*` 与指数 `**`（含 `cm**3`/`米**2`/`(-1)**2`）原样保留，仅清行首/句末残留 3 行，不做记法转换** | `backend/app/data/ape210k.py` 纯函数 + 28 项单测（含 B 类真实样本回归 11 例） |
| 小学 KP 聚合 | ✅ **82 个规则推导 KP**（kp_p_001..082，source='ape210k_rule'，独立初中图谱）；覆盖 204,231 题（**98.3%**），均 2.8 KP/题；映射不确定不硬挂；零命中 2 个（和倍/差倍，奥数术语不入课标题干）如实报告 | `backend/app/data/primary_kp.py` 规则表入 Git；`data/staging/primary_kp_report.json`；初中 303 KP 重跑前后不变 |
| FTS5 | ✅ processed 一一对应 207,858 行；rejected 不进索引（测试断言）；全量重建语义；MATCH 查询 0.2–0.4ms。⚠️ unicode61 分词：连续中文串整体成 token，中文子词不可查（完整串/数字可查），语义级中文检索属 Phase 2 分词优化 | `test_etl_ape210k.py` 同步测试 + 实测基准 |
| 幂等性 | ✅ **全量重建语义**（非 INSERT OR IGNORE）：每轮先清 q_ape_% 的 questions/FTS/q_kp 再按当前规则写入，清洗规则改进重跑即生效；修复后 2 轮重跑终态逐数一致 | 修复后第二轮断言 PASS：processed 207,858 / rejected 91 / fts 207,858 / 挂接 571,364 / KP 82 / 初中 303 |
| 完整率抽样 | ✅ **seed=42、同一样本集（v3 起固定沿用 v1/v2 的 100 q_id——总体 +6 会使重抽偏移，按验收要求保持原样本集）；完整率 100/100**（判定项 A–F，D 项 v3 起按"无残留形态星号"判定——乘号/指数为合法记法）；99/100 题至少挂 1 个小学知识点；星号保留 10/10 | 清单 `data/staging/ape210k_sample100.md`（v3 方案 A 修复后版，含题目内容与 B 类恢复前后对照，**不入 Git**）；`backend/scripts/sample_questions_audit.py` 可复现 |
| 性能 | ✅ ETL 全程 17.4s（21 万行流式）；库 182.7MB；FTS MATCH 0.2–0.4ms、KP JOIN 1.7ms、全库 KP 聚合 GROUP BY 234ms、processed 计数 43ms | 实测基准（各 3 次取均值） |
| 测试基线 | ✅ **159 passed / 0 failed**（98 基线 + 8 存储 + 8 图谱 + 17 导入 + 28 ETL，含方案 A 回归 13 例） | 全量 pytest（2026-11-12 修复后） |

**实现要点（需知悉）**：①rejected 题**入库**（quality_state='rejected'，决策⑲可追溯语义），FTS5 只索引 processed——检索/组卷天然只见 processed；②幂等是全量重建语义：重跑 ETL 后需重跑 `build_primary_kp.py`（KP 挂接随重建被清），两脚本顺序固定即复现链；③难度 1–5 为规则粗标（按 equation 运算符个数）、score 统一占位 1.0（数据集无分值字段）——**Step 5 LLM 打标将覆盖难度**，分值占位不代表卷面语义；④技术方案 §3.1"APE210K 自带知识点标签"预设落空（实测无标签字段），小学 KP 口径改为规则推导（决策㉒）；⑤两个零命中 KP（和倍/差倍问题）保留在规则表中（合法知识点，供未来 LLM 打标复用），未硬凑映射。

## Step 4 复核发现与修正（2026-11-12，用户抽样反馈）

用户复核 v1 抽样报告发现"题干截断"，对**同一样本集**（seed=42，与 v1 q_id 集合校验一致）做原始数据 vs 库内数据逐字段全量核查，结论：

| 类别 | 样本 100 题 | 全库 210,488 | 说明 |
|---|---|---|---|
| C 报告展示截断 | 53 题 | — | v1 审计代码 `stem[:40]` 展示截断（**仅报告缺陷，数据无损**）；v2 已改为完整展示并重生成报告（`sample_questions_audit.py` 重写：逐题块状、原始/库内全字段对照、三类自动分类） |
| B ETL 清洗损坏 | 10 题 | **11,032 行（5.24%）** | 清洗规则 `\*+` 全量删星号，误删合法**乘号**（`27*324`，7,773 行）与**指数记法**（`cm**3`/`1**2`，3,399 行）；仅损 stem 文本，answer/equation/验算/KP 挂接均无损；FTS 中这些题的 stem 索引同受影响 |
| A 原始数据质量 | 1 题（q_ape_72366：题干不完整 + x=1 退化占位） | 真残留 3 行 + 非标准记法 150 行 | 源数据自身缺陷，与 ETL 无关 |
| X 意外不一致 | 0 | — | 库内 = normalize(原始) 100/100 成立——损坏源于清洗**规则本身**，非管线实现 bug |

- B 类样本 q_id：q_ape_678403、q_ape_1060185、q_ape_58143、q_ape_209048、q_ape_230513、q_ape_290121、q_ape_942935、q_ape_1200515、q_ape_634755、q_ape_87500
- **v1 "3,368 行 Markdown 残留"定性错误已修正**：题干 `**` 绝大多数是指数记法（与 equation 幂语法一致），真残留仅 3 行——DATA-SOURCES.md 已更新
- 完整率结论不受影响：100/100 判定基于完整题干（A–F 字段口径），与展示截断无关；KP 挂接 99/100 复核不变
- ~~修复方案待用户裁决~~ → **用户已裁决采用方案 A（2026-11-12，决策㉓）并已实施完成**，见下节

## Step 4 方案 A 修复完成证据（2026-11-12，全部实测）

| 项 | 结果 | 证据 |
|---|---|---|
| 清洗规则 | ✅ `RESIDUE_STAR_RE = (^|[。．？！？])\*{2,}`：仅清行首/句末 ** 残留（全库实测 3 行：95177/128301 校名打码、36130 尾部），乘号与指数（含 `(-1)**2` 括号底数、`米**2` 中文底数）原样保留；不做记法转换 | `backend/app/data/ape210k.py`；docstring 记录规则与全库实测依据 |
| 回归测试 | ✅ 用户指定 7 记法用例（27*324/123*456/cm**3/2**3/3.14/75%/1/2）+ 残留清除用例 + **B 类真实样本 11 例逐字回归**（抽样 10 题 + 库内同形态 325488）全过 | `backend/tests/test_etl_ape210k.py`（16→28 项） |
| 全量重建 | ✅ 从原始文件重跑（SHA-256 门禁通过；全量重建语义；Schema 未动；原始数据文件未动）；ETL 18.8s + KP 9.7s；**漏斗唯一变化：题干无效 6→0、processed 207,852→207,858（+6）**——原 6 行是被旧规则删星号变短才误判"无效"的巧算题（`16*25．`/`36*5*4．` 等，验算全 match），修复后正确放行 | ETL/KP stdout 两轮一致 |
| 全量数据审计 | ✅ **21 万行独立重放 + 逐行比对 0 不一致**（stem/answer/analysis/difficulty/quality_state 全字段）；题干差异分类：无变化 207,944 / 仅残留清除 3 / 仅空白归一 2（**再无其他任何差异**）；**恢复 11,054 行**（旧规则删星号、新规则保留）；含星号行 11,057 = 乘号 7,701 + 指数 3,359 + 非标准记法 162（循环小数 `**(•)`、车牌打码 `川A*****` 等，保守保留，q_id 清单见 JSON）+ 残留清除 3（去重有重叠） | `backend/scripts/verify_etl_integrity.py`（新增，可复现）+ `data/staging/etl_integrity_report.json` |
| 汇总核对 | ✅ 库内总量 207,949 = 漏斗重算；FTS 207,858 = processed；B 类 10 题 FTS 同步且含星号；小学 KP 82 / 挂接 571,364；初中 303 不变 | verifier 6 项 checks 全 ✓ |
| 数据层恢复证据 | ✅ 10 道 B 类题库内实测全部恢复（如 q_ape_230513 库内 = `因为27*324=8,748，所以0.27*3.24=多少`；q_ape_678403 = `…cm**3？`）；FTS 短语查询 `"8 748"` 命中 q_ape_230513 | v3 报告"B 类恢复核验"节（修复前/后逐字对照）；上文 stdout 实录 |
| 抽样复核（v3） | ✅ 同一样本集（固定沿用 100 q_id，校验一致）：完整率 100/100、KP 99/100、星号保留 10/10、**B 类 0**（修复前 10）、A 类 1（q_ape_72366 源数据缺陷）、C 类 53（v1 展示截断史，数据无损）、X 类 0 | `data/staging/ape210k_sample100.md` v3 |
| 测试 | ✅ **159 passed / 0 failed**；幂等 2 轮逐数一致 | 全量 pytest + ETL/KP 重跑 |

**注意事项**：①修复前 STATUS 曾预判"漏斗计数不变（dedup 键不含星号）"——dedup 确实不变，但题干长度校验与星号保留相互作用使 +6 行放行，属修复的正确效果（旧"题干无效 6"本身是损坏假象）；②审计 v3 起样本集固定沿用上一版报告 q_id（总体变化会使 seed 重抽偏移），`--fresh-sample` 可重抽；③完整率判定项 D 语义更新为"无残留形态星号"（乘号/指数不再判不完整），口径意图不变。

## Step 3 完成证据（2026-11-11，用户已确认）

| 项 | 结果 | 证据 |
|---|---|---|
| 模板 | ✅ GET /api/import/template 生成中文表头五工作簿+填写说明 | 端点测试 + openpyxl 回读校验 |
| 五类用例 | ✅ 合法导入 committed 11/11；含错文件 partial 12/8/4 错误定位 sheet+行+字段；同文件重传 409 指纹拒绝返回原批次；force 覆盖重导 answers-only 分数更新总数不变；写入中断整批回滚（业务表全 0） | `backend/tests/test_import_api.py` 17 项，真实 xlsx 字节流经 TestClient→importer→SQLite，无 Mock 文件层 |
| 业务归属校验 | ✅ 班级同名跨年级歧义 / 学生不属于所填班级 / 考试绑班不符 / q_no 不在卷面 均拒 | 3 项专属用例（含联考不绑班放行） |
| 事务原子性 | ✅ BEGIN IMMEDIATE 单事务；错误行不进写入集合；写入期定位失败跳行报错；意外异常整批回滚；批次状态独立事务 | crash 注入用例：写入全量后崩溃→业务表计数 0→批次 failed→同文件可重导 |
| 重复导入语义 | ✅ 指纹=sha256 拒 committed/partial 重复；force 仅 answers 单表（其余 400）；库内重导被 E_CONFLICT（考试）/E_DUP（答题）双防，零重复写入 | 用例 3/4 + 重导防御用例 |
| 中断恢复 | ✅ 启动清扫滞留 validating→failed（lifespan 实测）；存在 validating 时新导入 409 import_busy 留痕 | 2 项专属用例 |
| demo 冒烟 | ✅ `generate_demo_import.py`：真实演示数据（20 学生×10 题）生成 232 行工作簿→全 committed→库内 1/20/1/10/200；重放被防重且数据不变 | 实际运行输出（batch imp_20261111115806_0e2a4e） |

## Phase 0 完成证据（2026-11-10，全部实测）

| 项 | 结果 | 证据 |
|---|---|---|
| I1 导入统一 | ✅ 34 文件统一 `backend.*`；仓库根=唯一 sys.path 根 | 全仓 grep 无残留 flat 导入；测试全绿；conftest/scripts 均以仓库根引导 |
| I2 acps-sdk 注释 | ✅ 统一为 2.2.0（AtomGit 源/本地 wheel，非 PyPI） | interop 各文件 docstring/报错文案核对 |
| I3 CORS 白名单 | ✅ `ALLOWED_ORIGINS` env 注入，默认仅本机源 | 白名单内响应带 ACAO 头；白名单外无 CORS 头（浏览器拦截）——本地起服实测 |
| I4 HTTPS 核实 | ✅ 证据已回填技术方案 §11.1-4 | edumateaip.xyz A→47.116.76.175（本地 nslookup + 服务器 getent + ECS metadata 三方一致）；nginx 仅 80；无 certbot/acme.sh。HTTPS 顺延至集中部署 |
| I5 Docker | ✅ 构建通过 | 镜像 `eduagent:phase0`（576MB，构建 1264s）；容器无 acps-sdk 环境 `import backend.main` 冒烟通过；`.env` 不在镜像；`docker compose config` 有效 |
| I6 gitignore | ✅ `*.bak.*` + `models/` | commit f7af0e7 |
| I7 杂物清理 | ✅ 仓库 6 + 服务器 7 个 `.bak` 全部删除 | 六步流程（检查→备份→审核→执行→验证→收尾），commit f7af0e7 |
| I9 README | ✅ 重写（去赛化叙事 + 双启动方式 + Docker 用法） | commit 8dbf573 |

**测试基线**：98 passed / 0 failed —— 改动前与改动后各跑一次全量，数量与结果一致（本地 Windows `.venv`，约 4–9s）。根目录 `tests/` 为服务器侧 e2e（硬编码 /opt/EduAgent），经 pytest.ini `testpaths` 排除本地收集，文件保留。

**AIP 降级修复**（本轮新增）：agent_caller/rpc_client/result_parser 的顶层 `acps_sdk` 导入改为可选导入 + 运行时 RuntimeError 守卫——容器最小镜像不装非 PyPI 的 SDK，主业务正常启动；调用 AIP 时报明确安装指引而非 ImportError。

**本轮排障记录**（Docker 构建 3 次失败→修复）：Docker Hub DNS 污染→经 docker.m.daocloud.io 镜像源拉基础镜像后 retag；pip 下载哈希校验失败→Dockerfile 增 `PIP_INDEX_URL` build-arg（默认官方源，国内传清华源）；容器冒烟暴露急切导入链→上述降级修复。

## 代码状态

- **本轮提交**：`8dbf573`（代码+基础设施，43 文件 +300/−196）已 push GitHub main；文档（技术方案回填 + 本目录三件）随下一个 docs commit 入库。
- 工作区：干净（文档提交后）。

## ECS 服务器状态（按新策略本轮保持不动）

- 版本：`f7af0e7`，落后 GitHub——**有意为之**，集中部署窗口统一同步。
- 运行：旧启动命令 `uvicorn main:app --app-dir backend`（旧代码，服务正常，/api/health 200）。
- ⚠️ 下次重启必须用新命令 `python -m uvicorn backend.main:app --app-dir .`（仓库根执行，PYTHONPATH 不再必需）。
- nginx：仅监听 80；域名已解析到位；HTTPS 未配置。

## 待办 / 阻塞

1. 用户审核 **Step 4 方案 A 修复验收报告（2026-11-12，见"Step 4 方案 A 修复完成证据"）**——含全量审计数据与数据层恢复证据。
2. 验收通过后进入 Step 5 LLM 打标（两道关口协议见决策㉑）。
3. 集中部署窗口（未排期）：ECS 代码同步 + 新启动命令切换 + HTTPS 证书 + compose 上线（见 ROADMAP.md 末节）。
4. 无其他阻塞。

## 环境备忘

- 本地：Windows 11，Python 3.13.7（`.venv`），pytest **159 用例**（98 基线 + 8 Step1 存储 + 8 Step2 图谱 + 17 Step3 导入 + 28 Step4 ETL），Docker Desktop（基础镜像经 docker.m.daocloud.io 镜像源获取；pip 构建参数走清华源——Docker Hub auth.docker.io DNS 污染、PyPI 直连偶发哈希校验失败）。⚠️ **pytest 依赖 openssl 在 PATH**（test_interop_discovery 3 用例借 openssl 生成测试 CA；本机位于 `D:\Git\mingw64\bin`，PATH 缺失时报 WinError 2，2026-11-11 实测踩坑，补 PATH 后全绿）。
- 服务器：阿里云 ECS 2核2G（47.116.76.175），/opt/EduAgent，`.venv` Python 3.10，nginx 站点 "edumate"（client_max_body_size 100M，SSE 代理参数就绪）。
