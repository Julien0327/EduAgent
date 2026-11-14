"""LLM 打标注解层（Phase 1 / Step 5-B1）。

模块组成（设计依据：docs/development/STEP5-ANNOTATION-DESIGN.md 第二版）：
- client：ChatClient 协议 + OpenAI 兼容实现（真实 Key 仅在 CLI 装配层读取，不打印）
- pricing：官方单价表（2026-11-13 核实）+ 高峰/空闲判定 + 预算预留基准
- prompts：Prompt 文件装配（KP 清单注入）+ 版本指纹校验
- recall：B 臂候选召回协议（embedding 实现待 5-B2，测试注入 mock）
- executor：批次管理 / AIMD 并发 / 预算闸门 / 幂等断点 / manifest（决策①②③④落实）
"""
