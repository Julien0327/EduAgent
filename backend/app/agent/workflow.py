"""工作流编排（Workflow）。

预定义工作流 + 节点内单工具调用（对应技术方案 §4 / §6）。三个工作流：
    - exam_loop：章节小测一条龙（种子题库出卷 + 成绩分析 + 学情报告）
    - question_gen：智能组卷（LLM 实时出题）
    - lesson_plan：AI 教案生成

工作流由 Python 函数按固定顺序组成；任一节点失败即中止（fail-fast），
配合全局超时上限，杜绝无限循环（对应技术方案 §22）。出卷与成绩分析两条腿
都用种子题库，保证试卷 / 答题卡 / 报告数据一致（技术方案 §14「两者互不依赖」）。
"""
import time

from pydantic import BaseModel, Field

from app.agent.context import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    STEP_DONE,
    STEP_FAILED,
    STEP_RUNNING,
    TaskContext,
    task_store,
)
from app.agent.executor import run_tool
from app.agent.llm_utils import structured_completion
from app.agent.planner import plan
from app.knowledge import loader
from app.services import answer_sheet, exam_docx, lesson_plan, report_pdf, score_analysis
from app.storage import file_store

WORKFLOW_TIMEOUT = 180  # 秒，全局任务上限（§22）

# 产物文件名
EXAM_FILE = "01_试卷.docx"
ANSWER_SHEET_FILE = "02_答题卡.xlsx"
REPORT_FILE = "03_学情报告.pdf"
LESSON_PLAN_FILE = "01_教案.docx"


class ProcessStep(BaseModel):
    """教学过程中的一个环节。"""

    step: str
    detail: str


class LessonPlanContent(BaseModel):
    """教案正文（结构化，匹配 lesson_plan.create_lesson_plan_docx 的 content）。"""

    objectives: list[str]
    key_points: str
    difficult_points: str
    process: list[ProcessStep]
    homework: str


# ---------- 节点 ----------

def _understand_task(ctx: TaskContext) -> str:
    p = plan(ctx.prompt)
    ctx.workflow = p.workflow
    ctx.params = p.params.model_dump()
    return f"识别为 {p.workflow}，章节「{p.params.chapter}」"


def _fetch_knowledge(ctx: TaskContext) -> str:
    params = ctx.params
    r = run_tool("search_knowledge_points", {
        "subject": params.get("subject", "数学"),
        "chapter": params.get("chapter", ""),
    })
    kps = r["knowledge_points"]
    ctx.params["knowledge_points"] = [kp["name"] for kp in kps]
    return f"检索到 {len(kps)} 个知识点"


def _generate_exam_seed(ctx: TaskContext) -> str:
    """一条龙（exam_loop）出卷：用种子题库，保证与成绩分析一致。"""
    bank = loader.load_question_bank()
    ctx.params["title"] = bank["title"]
    ctx.params["questions"] = bank["questions"]
    ctx.params["question_bank"] = bank
    return f"加载题库 {len(bank['questions'])} 道题（总分 {bank['total_score']}）"


def _generate_questions_llm(ctx: TaskContext) -> str:
    """智能组卷（question_gen）出题：LLM 实时命制。"""
    params = ctx.params
    r = run_tool("generate_questions", {
        "knowledge_points": params.get("knowledge_points", []),
        "count": params.get("count", 10),
        "difficulty": params.get("difficulty", "混合"),
        "question_types": params.get("question_types", ["选择题", "填空题", "解答题"]),
        "total_score": params.get("total_score", 100),
    })
    ctx.params["title"] = f"{params.get('chapter', '')} 课堂小测"
    ctx.params["questions"] = r["questions"]
    return f"LLM 出题 {len(r['questions'])} 道（总分 {r['total_score']}）"


def _build_exam_docx(ctx: TaskContext) -> str:
    path = file_store.build_output_path(ctx.task_id, EXAM_FILE)
    exam_docx.create_exam_docx(ctx.params["title"], ctx.params["questions"], path)
    ctx.add_artifact("试卷", EXAM_FILE, path)
    return f"生成试卷 {EXAM_FILE}"


def _build_answer_sheet(ctx: TaskContext) -> str:
    path = file_store.build_output_path(ctx.task_id, ANSWER_SHEET_FILE)
    answer_sheet.create_answer_sheet_xlsx(ctx.params["title"], ctx.params["questions"], path)
    ctx.add_artifact("答题卡", ANSWER_SHEET_FILE, path)
    return f"生成答题卡 {ANSWER_SHEET_FILE}"


def _load_student_answers(ctx: TaskContext) -> str:
    answers = loader.load_student_answers()
    ctx.params["student_answers"] = answers
    return f"加载预置答题 {len(answers['students'])} 名学生"


def _analyze_scores(ctx: TaskContext) -> str:
    result = score_analysis.analyze_scores(
        ctx.params["student_answers"], ctx.params["question_bank"]
    )
    ctx.params["stats"] = result["stats"]
    ctx.params["weak_knowledge_points"] = result["weak_knowledge_points"]
    stats = result["stats"]
    return f"平均分 {stats['avg']}，及格率 {stats['pass_rate'] * 100:.1f}%"


def _diagnose(ctx: TaskContext) -> str:
    weak_names = [kp["name"] for kp in ctx.params["weak_knowledge_points"]]
    r = run_tool("generate_diagnosis", {
        "stats": ctx.params["stats"],
        "weak_knowledge_points": weak_names,
    })
    ctx.params["diagnosis"] = r
    return "生成学情诊断"


def _build_report_pdf(ctx: TaskContext) -> str:
    path = file_store.build_output_path(ctx.task_id, REPORT_FILE)
    report_pdf.create_report_pdf(
        ctx.params["stats"], ctx.params["diagnosis"], ctx.params["weak_knowledge_points"], path
    )
    ctx.add_artifact("学情报告", REPORT_FILE, path)
    return f"生成学情报告 {REPORT_FILE}"


def _generate_lesson_plan_content(ctx: TaskContext) -> str:
    topic = ctx.params.get("topic") or ctx.params.get("chapter", "")
    system = (
        "你是资深教师，请生成一份结构完整、可操作的教案。"
        "务必调用 submit_lesson_plan 工具提交结果。"
    )
    user = (
        f"主题：{topic}。请给出：教学目标(objectives，3 条)、教学重点(key_points)、"
        "教学难点(difficult_points)、教学过程(process，3-4 步，每步含 step 与 detail 字段)、"
        "课后作业(homework)。"
    )
    content = structured_completion(system, user, "submit_lesson_plan", LessonPlanContent)
    ctx.params["lesson_content"] = {
        "objectives": content.objectives,
        "key_points": content.key_points,
        "difficult_points": content.difficult_points,
        "process": [(p.step, p.detail) for p in content.process],
        "homework": content.homework,
    }
    return "生成教案正文"


def _build_lesson_plan_docx(ctx: TaskContext) -> str:
    path = file_store.build_output_path(ctx.task_id, LESSON_PLAN_FILE)
    lesson_plan.create_lesson_plan_docx(
        ctx.params.get("topic", "") or ctx.params.get("chapter", ""),
        ctx.params["lesson_content"],
        path,
    )
    ctx.add_artifact("教案", LESSON_PLAN_FILE, path)
    return f"生成教案 {LESSON_PLAN_FILE}"


# 各工作流的剩余节点（① understand_task 之后）
WORKFLOW_NODES = {
    "exam_loop": [
        ("fetch_knowledge", _fetch_knowledge),
        ("generate_exam", _generate_exam_seed),
        ("build_exam_docx", _build_exam_docx),
        ("build_answer_sheet", _build_answer_sheet),
        ("load_student_answers", _load_student_answers),
        ("analyze_scores", _analyze_scores),
        ("diagnose", _diagnose),
        ("build_report_pdf", _build_report_pdf),
    ],
    "question_gen": [
        ("fetch_knowledge", _fetch_knowledge),
        ("generate_exam", _generate_questions_llm),
        ("build_exam_docx", _build_exam_docx),
        ("build_answer_sheet", _build_answer_sheet),
    ],
    "lesson_plan": [
        ("generate_lesson_plan_content", _generate_lesson_plan_content),
        ("build_lesson_plan_docx", _build_lesson_plan_docx),
    ],
}


def _run_node(ctx: TaskContext, name: str, fn) -> None:
    step = ctx.add_step(name)
    ctx.push_event({"type": "step", "node": name, "status": STEP_RUNNING, "message": ""})
    try:
        message = fn(ctx)
        step.status = STEP_DONE
        step.message = message or "完成"
        ctx.push_event({"type": "step", "node": name, "status": STEP_DONE, "message": step.message})
    except Exception as exc:
        step.status = STEP_FAILED
        step.message = str(exc)
        ctx.push_event({"type": "step", "node": name, "status": STEP_FAILED, "message": step.message})
        raise


def run_workflow(ctx: TaskContext) -> None:
    """执行任务：① 理解任务 → 按工作流执行剩余节点。失败即中止。"""
    ctx.status = STATUS_RUNNING
    ctx.push_event({"type": "status", "status": STATUS_RUNNING})
    start = time.time()
    try:
        _run_node(ctx, "understand_task", _understand_task)
        nodes = WORKFLOW_NODES.get(ctx.workflow)
        if nodes is None:
            raise ValueError(f"未知工作流：{ctx.workflow}")
        for name, fn in nodes:
            _run_node(ctx, name, fn)
            if time.time() - start > WORKFLOW_TIMEOUT:
                raise TimeoutError(f"任务超过 {WORKFLOW_TIMEOUT} 秒上限")
        ctx.status = STATUS_COMPLETED
        ctx.push_event({"type": "status", "status": STATUS_COMPLETED, "error": ""})
    except Exception as exc:
        ctx.status = STATUS_FAILED
        ctx.error = str(exc)
        ctx.push_event({"type": "status", "status": STATUS_FAILED, "error": str(exc)})


def run_task(prompt: str) -> TaskContext:
    """创建任务上下文并同步执行（供 CLI / 测试使用）。"""
    ctx = task_store.create(prompt)
    run_workflow(ctx)
    return ctx
