"""学情诊断工具（LLM 生成诊断文字）。

出参结构 {summary, diagnosis, suggestions} 与 D2 已验收的
report_pdf.create_report_pdf 的 diagnosis 参数保持一致（不修改 D2）。
"""
from pydantic import BaseModel, Field

from app.agent.llm_utils import structured_completion
from app.tools.registry import tool


class GenerateDiagnosisParams(BaseModel):
    stats: dict = Field(..., description="班级成绩统计（平均分/及格率/分数段分布等）")
    weak_knowledge_points: list[str] = Field(
        ..., description="薄弱知识点名称（按正确率升序）"
    )


class Diagnosis(BaseModel):
    summary: str = Field(..., min_length=1, description="班级总体小结")
    diagnosis: str = Field(..., min_length=1, description="薄弱知识点诊断")
    suggestions: list[str] = Field(..., min_length=1, description="分层教学建议")


def _pct(x) -> str:
    return f"{x * 100:.1f}%" if isinstance(x, (int, float)) else str(x)


@tool(
    name="generate_diagnosis",
    description="根据成绩统计与薄弱知识点生成学情诊断与分层教学建议。",
    params_model=GenerateDiagnosisParams,
)
def generate_diagnosis(params: GenerateDiagnosisParams) -> dict:
    stats = params.stats
    # 传摘要版 stats，避免把逐题正确率全量塞进 prompt
    summary_lines = [
        f"班级：{stats.get('class_name', '')}，人数：{stats.get('student_count', 0)}",
        f"平均分 {stats.get('avg')}，中位数 {stats.get('median')}，最高 {stats.get('max')}，"
        f"最低 {stats.get('min')}，标准差 {stats.get('std')}",
        f"及格率 {_pct(stats.get('pass_rate'))}，优秀率 {_pct(stats.get('excellent_rate'))}",
        "分数段分布：" + "，".join(
            f"{d['label']}={d['count']}人" for d in stats.get("score_distribution", [])
        ),
    ]
    weak = "、".join(params.weak_knowledge_points) or "无"

    system = (
        "你是教学分析专家，根据班级成绩统计与薄弱知识点，生成简洁、可操作的学情诊断。"
        "务必调用 submit_diagnosis 工具提交结果。"
    )
    user = (
        "成绩统计：\n" + "\n".join(summary_lines)
        + f"\n薄弱知识点（按正确率升序）：{weak}\n"
        "请给出：班级总体小结(summary)、薄弱知识点诊断(diagnosis)、"
        "分层教学建议(suggestions，3-5 条)。"
    )

    result = structured_completion(system, user, "submit_diagnosis", Diagnosis)
    return result.model_dump()
