"""Phase 4 / S2 评测执行层（backend.app.eval）。

分层：
- fake_llm.py：FakeLLM——复刻"当前实现"的确定性规则引擎（与 golden expect 完全独立）；
- models.py：结构化执行记录（失败归因 7 类）；
- runner.py：加载 golden → 逐条执行（fake 档）→ 产物校验 → 指标统计 → 落 eval_runs + 报告；
- artifacts.py：产物三层校验（存在/有效/内容断言）；
- db.fixture：gc_c01–c04 独立演示班数据 fixture（不依赖开发者本机库）。
"""