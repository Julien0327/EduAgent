"""引用注入机制（技术方案 §4.4，Phase 2 / S2，day-1 纪律项）。

目标：凡从题库 / 知识图谱取数，返回值必带 ``ref`` 字段；下游把引用装配进
任务账本（CitationLedger），任务收尾落盘 ``citations.json``，前端"查看引用"
面板与 ``GET /api/tasks/{id}/citations`` 读取。

**覆盖率 100% 不可事后补链**（决策㉚⑥）：取数入口不给 ref 视为代码缺陷，
``require_ref`` 在取数侧强制；消费侧丢弃引用由单测覆盖（工作流端到端断言
卷面每个 ref 都在账本中）。

ref 形态约定：
- 题库题（DB）：``q_id``（如 ``q_ape_230513``）；
- 内置种子题库题（演示数据）：``seed:<question_id>``（前缀明确区分来源，
  避免与 DB 题混淆）；
- 知识点：``kp_id``（初中 / 小学图谱统一）；
- LLM 生成题：**不是引用**（非取数）——页脚来源统计按"无 ref = AI 生成"
  计数，不进引用账本。

citations.json 格式（§4.4）::

    {"task_id": "...", "citations": [
        {"kind": "question", "ref": "q_00317", "used_in": ["01_试卷.docx#第3题", ...]},
        {"kind": "kp", "ref": "kp_quad_02", "used_in": ["03_学情报告.pdf#归因链"]}
    ]}
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

# ref 前缀：内置种子题库（演示数据）——与 DB 题 q_id 明确区分
REF_SEED_PREFIX = "seed:"

VALID_KINDS = ("question", "kp")


@dataclass
class Citation:
    kind: str              # 'question' / 'kp'
    ref: str               # q_id / seed:<id> / kp_id
    used_in: list[str] = field(default_factory=list)  # ["01_试卷.docx#第3题", ...]


class CitationLedger:
    """任务级引用账本（线程安全：工作流节点与并发场景下 record/annotate）。

    同一 (kind, ref) 多次 record 合并为一条，used_in 去重追加——装配点可以
    分散在多个节点（试卷/答题卡/教案），账本自动聚合。
    """

    def __init__(self, task_id: str):
        self.task_id = str(task_id)
        self._items: dict[tuple[str, str], Citation] = {}
        self._lock = threading.Lock()

    def record(self, kind: str, ref: str, used_in: str | None = None) -> None:
        """登记一次引用（幂等；used_in 去重追加）。"""
        if kind not in VALID_KINDS:
            raise ValueError(f"未知引用类型：{kind}（合法：{VALID_KINDS}）")
        if not ref:
            raise ValueError("ref 不能为空（取数入口必须提供 ref，§4.4）")
        with self._lock:
            item = self._items.setdefault((kind, ref), Citation(kind=kind, ref=ref))
            if used_in and used_in not in item.used_in:
                item.used_in.append(used_in)

    def annotate(self, kind: str, ref: str, used_in: str) -> None:
        """给已登记引用追加使用位置（装配点；未登记过则先登记）。"""
        self.record(kind, ref, used_in)

    def refs(self, kind: str) -> list[str]:
        """返回某类型的全部 ref（按登记顺序）。"""
        with self._lock:
            return [ref for (k, ref) in self._items if k == kind]

    def to_dict(self) -> dict:
        """§4.4 格式（citations.json 内容）。"""
        with self._lock:
            return {
                "task_id": self.task_id,
                "citations": [
                    {"kind": c.kind, "ref": c.ref, "used_in": list(c.used_in)}
                    for c in self._items.values()
                ],
            }

    def save(self, path) -> Path:
        """落盘 citations.json（任务收尾调用）。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path

    @classmethod
    def load(cls, path) -> "CitationLedger":
        """从 citations.json 恢复（API 读取走文件，不依赖进程内存）。"""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        ledger = cls(data.get("task_id", ""))
        for c in data.get("citations", []):
            used_list = c.get("used_in", [])
            if used_list:
                for used in used_list:
                    ledger.record(c["kind"], c["ref"], used)
            else:
                ledger.record(c["kind"], c["ref"])  # 无 used_in 的登记也要保留
        return ledger


def require_ref(row: dict, *, kind: str, ref_key: str = "ref") -> dict:
    """取数侧完整性强制点：返回行必须携带 ref（§4.4"下游丢弃视为代码缺陷"的
    上游对应——取数入口漏带 ref 同样是缺陷，在此 fail-fast）。

    用法：检索/题库/图谱取数返回前逐行调用；缺失抛 ReferenceError。
    """
    if kind not in VALID_KINDS:
        raise ValueError(f"未知引用类型：{kind}（合法：{VALID_KINDS}）")
    ref = row.get(ref_key)
    if not ref:
        raise ReferenceError(
            f"取数结果缺少 {ref_key}（kind={kind}）——凡题库/图谱取数必带引用（§4.4）"
        )
    return row


# 文档页脚来源统计（§4.4 注入点：试卷页脚"来源构成统计"）
def summarize_question_sources(questions: list[dict]) -> str:
    """按 ref 字段统计题目来源构成，返回页脚文案。

    规则：ref 以 seed: 开头=内置种子题库；ref 为其他非空值=题库（DB q_id）；
    无 ref=AI 生成（LLM 出题，非取数不进账本）。
    """
    seed = bank = generated = 0
    for q in questions:
        ref = q.get("ref")
        if not ref:
            generated += 1
        elif str(ref).startswith(REF_SEED_PREFIX):
            seed += 1
        else:
            bank += 1
    parts = []
    if bank:
        parts.append(f"题库检索 {bank} 题")
    if seed:
        parts.append(f"内置种子题库 {seed} 题")
    if generated:
        parts.append(f"AI 生成 {generated} 题")
    return "题目来源：" + ("；".join(parts) if parts else "无") + "（详见任务引用面板）"
