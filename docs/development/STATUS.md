# EduMate 当前状态（STATUS）

> 最后更新：2026-11-11（Phase 1 Step 1）。**新会话开工前必读本文件 + docs/技术方案.md §11/§12**，以项目文档、实际代码和本目录状态文档为准，不依赖旧会话聊天记录。

## 当前位置

Phase 0（打地基）✅ 全部完成（commit `8dbf573`）。**Phase 1（数据地基）进行中**（2026-11-11 开工，顺序与关口协议经用户逐条确认，见 DECISIONS.md ⑲–㉑）：

- **Step 1 存储层建库 ✅**：V1 全量 DDL（§2.3 全部表，questions 按⑲用 quality_state+verified）+ db 连接层（每连接强制 foreign_keys=ON）+ 8 项单测；`.gitignore` 增 data/raw|db|staging（⑳）；本地已 commit，push 集中在 Phase 1 收尾。
- **Step 2 知识图谱构建 = 下一步**（完成后停下，等用户复核 20% 抽样清单）。
- Phase 1 后续：F16 导入 → APE210K ETL → LLM 打标（两道关口，㉑）→ loader 切换。

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

1. 用户审核本轮 Phase 0 汇报 → 批准进入 Phase 1。
2. 集中部署窗口（未排期）：ECS 代码同步 + 新启动命令切换 + HTTPS 证书 + compose 上线（见 ROADMAP.md 末节）。
3. 无其他阻塞。

## 环境备忘

- 本地：Windows 11，Python 3.13.7（`.venv`），pytest 106 用例（98 基线 + 8 Phase1/Step1），Docker Desktop（基础镜像经 docker.m.daocloud.io 镜像源获取；pip 构建参数走清华源——Docker Hub auth.docker.io DNS 污染、PyPI 直连偶发哈希校验失败）。⚠️ **pytest 依赖 openssl 在 PATH**（test_interop_discovery 3 用例借 openssl 生成测试 CA；本机位于 `D:\Git\mingw64\bin`，PATH 缺失时报 WinError 2，2026-11-11 实测踩坑，补 PATH 后全绿）。
- 服务器：阿里云 ECS 2核2G（47.116.76.175），/opt/EduAgent，`.venv` Python 3.10，nginx 站点 "edumate"（client_max_body_size 100M，SSE 代理参数就绪）。
