"""D3 Agent 端到端演示脚本。

把自然语言需求交给 Agent：Planner 解析 → 固定 Workflow 编排 → 节点内单工具调用，
最终产出 Word / Excel / PDF 文档。LLM 负责智能判断（规划、出题、诊断、教案），
确定性文档服务继续由 D2 承担，需要真实 DEEPSEEK_API_KEY。

运行方式（在仓库根目录执行）：
    .venv/Scripts/python.exe backend/scripts/run_agent_demo.py "请对《一元二次方程》做章节小测并生成学情报告"
    .venv/Scripts/python.exe backend/scripts/run_agent_demo.py "帮我智能组一套《一元二次方程》的卷子"
    .venv/Scripts/python.exe backend/scripts/run_agent_demo.py "帮我写一份《一元二次方程》的教案"
"""
import sys
from pathlib import Path

# 让 app 包可导入（把 backend/ 目录加入 sys.path）
BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.agent.context import STATUS_COMPLETED
from app.agent.workflow import run_task
from app.tools import registry  # noqa: F401 导入即注册全部工具

DEFAULT_PROMPT = "请对《一元二次方程》做一次章节小测并生成学情报告"


def _status_mark(status: str) -> str:
    return {"done": "[OK]", "failed": "[FAIL]", "running": "[...]"}.get(status, "?")


def main() -> None:
    prompt = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PROMPT

    print("=" * 60)
    print("智教助手 EduMate — Agent 端到端演示")
    print("=" * 60)
    print(f"\n任务：{prompt}\n")

    ctx = run_task(prompt)

    print(f"工作流：{ctx.workflow}")
    print(f"状态：{ctx.status}")
    print("\n[执行步骤]")
    for s in ctx.steps:
        print(f"  {_status_mark(s.status)} {s.node}: {s.message}")

    if ctx.status == STATUS_COMPLETED:
        print("\n[生成产物]")
        for a in ctx.artifacts:
            p = Path(a.path)
            size = p.stat().st_size if p.exists() else 0
            print(f"  - {a.name}：{a.file_name}（{size} bytes）")
    else:
        print(f"\n[失败原因] {ctx.error}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
