# EduMate（EduAgent）项目工作协议

面试级 AI 应用项目（比赛已结束，目标：打磨为 2027 寒假 AI 应用开发实习求职的核心弹药）。

## 开工协议（每次会话开始）
- 动手前必读：`docs/development/STATUS.md`（当前状态）→ `docs/development/ROADMAP.md`（阶段路线图）→ `docs/development/DECISIONS.md`（实施期决策 ⑬+）
- 设计基准以 `docs/技术方案.md` v2.1 与 `docs/产品设计方案.md` 为准；文档与代码冲突时以代码实际为准，并向用户回报差异
- 用中文与用户交流；先讨论对齐方向，再动手改代码（用户工作风格）
- 阶段性验收指标若在文档中标为【待实测】，禁止编造数字；证据产生后回填

## 部署与版本策略（决策⑬⑭）
- 本地 Windows 开发验收 → GitHub 版本中心（每阶段验收后 push）→ ECS 仅集中部署验收
- 不逐 Phase 同步服务器/重启服务/线上测试；任何 ECS 操作需用户逐次批准
- 正式域名 edumateaip.xyz（HTTPS 顺延至集中部署）

## 落盘纪律（对抗上下文丢失）
- 重要结论当场写入 docs/development/：决策→DECISIONS.md，进度→STATUS.md，计划变更→ROADMAP.md
- 每阶段收尾固定动作：更新 STATUS.md（完成证据 + commit hash + 下一步）→ commit → push → 建议用户新开会话进入下一阶段
- 跨会话需要的信息必须落盘；聊天记录不作存档依据

## 工程约定
- 测试：`.venv/Scripts/python.exe -m pytest`（Windows；基线 98 passed；根 `tests/` 为服务器侧 e2e，本地不收集——pytest.ini 已限定 testpaths）
- 启动：`python -m uvicorn backend.main:app --app-dir .`（仓库根执行）
- 导入：全仓 `backend.*` 绝对导入（决策⑮），新文件保持同风格
- git：禁止 force push、禁止重置远程历史；commit 信息以 `Co-Authored-By: Claude Code <noreply@anthropic.com>` 结尾

（技术决策详情见 docs/development/DECISIONS.md；本文件只放工作协议，不复制文档内容，避免双源漂移。）
