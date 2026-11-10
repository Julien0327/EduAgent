# EduMate 技术决策记录（DECISIONS）

> 设计基准决策 ①–⑫ 见 docs/技术方案.md §14（变更记录见 §15）。本文件记录**实施期新增决策**，编号自 ⑬ 顺延；与基线决策同效力，变更需经用户确认并回记本表。

| # | 日期 | 决策 | 理由 | 影响 |
|---|---|---|---|---|
| ⑬ | 2026-11-10 | **开发/部署策略**：本地 Windows 开发验收 → GitHub 版本中心（每 Phase 验收后 push）→ ECS 仅集中部署验收；不再逐 Phase 同步服务器/重启服务/线上测试；任何 ECS 操作需用户逐次批准 | 避免逐阶段动线上造成不稳定；降低每轮验收成本；服务器作为阶段性集成验收环境而非开发反馈环 | 全部后续 Phase 工作流；技术方案 §12 各 Phase 验收中的"线上"项集中到部署窗口执行 |
| ⑭ | 2026-11-10 | **正式域名 edumateaip.xyz**；HTTPS 证书签发与 nginx 443 配置放到集中部署阶段 | 域名已定且 A 记录已指向 ECS（三方核实）；HTTPS 依赖证书签发与安全组变更，属部署窗口事项 | 技术方案 §11.1-4 证据已回填；§12 Phase 0 验收的 HTTPS 项顺延至集中部署 |
| ⑮ | 2026-11-10 | **导入路径统一方向**：全仓 `backend.*` 绝对导入，仓库根=唯一 sys.path 根（I1 落地）；启动命令 `python -m uvicorn backend.main:app --app-dir .` | 消除 flat/prefixed 双风格并存与服务器 PYTHONPATH=/opt/EduAgent 隐性依赖；Docker/CI/Phase 1 新模块的导入写法从此唯一 | 34 文件迁移；conftest/scripts 启动引导；README/Demo手册/AIC_IMPLEMENTATION_PLAN 命令同步 |
| ⑯ | 2026-11-10 | **AIP 可选依赖降级模式**：agent_caller/rpc_client/result_parser 顶层 `acps_sdk` 导入改为 try/except 可选导入 + 运行时 RuntimeError 守卫 | acps-sdk 非 PyPI（AtomGit 源），Docker 最小镜像不装它；主业务必须能在无 SDK 环境启动（与 interop/__init__.py 既有设计声明一致） | 容器无 SDK 环境 `import backend.main` 冒烟通过；无 SDK 时调用 AIP 报明确安装指引而非 ImportError |
| ⑰ | 2026-11-10 | **Docker 依赖获取方式**：基础镜像 Docker Hub 直连不可达时经 docker.m.daocloud.io 镜像源拉取后 retag；pip 依赖经 Dockerfile `PIP_INDEX_URL` build-arg（默认官方源，国内构建传清华源） | 本机实测：auth.docker.io DNS 污染（解析到错误 IP）、files.pythonhosted.org 下载哈希校验失败 | deploy/Dockerfile 保持可移植（默认官方源不改）；集中部署 ECS 时按同样方式处理 |
| ⑱ | 2026-11-10 | **测试分层**：pytest.ini `testpaths=backend/tests`（98 用例，本地可跑）；根 `tests/` 定位为服务器侧 e2e（硬编码 /opt/EduAgent），排除本地收集、文件保留 | 根 e2e 依赖服务器路径，本地收集必炸；分层后本地基线稳定 | 本地 `python -m pytest` 即 98 用例；服务器侧 e2e 留待部署窗口在服务器上执行 |
