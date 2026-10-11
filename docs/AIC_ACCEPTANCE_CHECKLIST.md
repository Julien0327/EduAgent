# AIC 验收清单（EduMate-AIP）

> 提交前逐项勾选。**「平台验证完成」一行若未完成，必须明确写「未完成」**，不得含糊。

---

## 1. 代码与技术验收

- [ ] `interop/` 适配层已落地：config / models / aic_util / acs_builder / amp / discovery / aip_server
- [ ] 现有 34 项测试仍全绿（未破坏既有业务）
- [ ] 新增 interop 测试全绿（`test_interop_models / test_interop_aip / test_interop_amp`）
- [ ] ACS 产出（acs.json）字段与已核实 `AgentCapabilitySpec` 一致
- [ ] AMP 日志字段正确：`correlationId=sessionId`、`messageId` 收发一致、`eventType∈{send,receive}`
- [ ] AIC 解析/校验可用（10 级结构、CRC 逻辑与官方一致）
- [ ] 未用「本地 Mock / 两实例互访」冒充平台验证（代码与材料中无此声明）

## 2. 梧桐平台真实闭环（每项需截图/日志证据）

- [ ] **注册**：平台后台显示 EduMate-AIP，已分配 AIC（`docs/evidence/01_register.png`）
- [ ] **自动发现**：discover 命中 EduMate 能力，有响应 JSON（`02_discovery`）
- [ ] **互联**：Leader/CLI 经 `/rpc` 发 `start`，返回 `completed`（`03_interconnect`）
- [ ] **跨端访问**：其他端访问 EduMate 产物（`04_cross_access`）
- [ ] AMP 日志已上报平台监控面（或本地 JSON-lines 已留档，材料中如实说明）

## 3. 材料验收

- [ ] `AIC_UPGRADE_GAP_ANALYSIS.md`（差距分析）
- [ ] `AIC_TARGET_ARCHITECTURE.md`（目标架构）
- [ ] `AIC_IMPLEMENTATION_PLAN.md`（实施计划）
- [ ] `AIC_ACCEPTANCE_CHECKLIST.md`（本文件）
- [ ] `IMPLEMENTATION_STATUS.md`（每轮更新，最终版含真实状态）
- [ ] README / Demo 演练手册已加入 AIP 互联段

## 4. 合规与诚实性验收

- [ ] 未编造 SDK 类名/函数/协议字段/ACS Schema（全部来自官方源码核实）
- [ ] 未将通用 REST 包装后宣称为符合 AIP（AIP 走独立 `/rpc` JSON-RPC）
- [ ] 未编造时延/访问量/并发/成效（未测项明确标注「未测」）
- [ ] 未提交真实 API Key / 私钥 / 令牌到仓库
- [ ] 材料不含学校名称 / 学校 LOGO / 指导教师信息
- [ ] 官方 SDK 许可证已核实（MulanPSL-2.0），未复制/改写不允许使用的代码

---

## 5. 最终状态声明（提交前如实填写）

- 真实平台验证完成：**是 / 否**（勾选其一）
- 若「否」，列出具体未完成项与阻塞原因：____________________________
