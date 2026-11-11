-- EduMate 业务库 V1 全量 DDL（技术方案 §2.3，2026-11-11 实施）
-- 范围：知识图谱 / 题库 / 班级·学生·考试·答题（F16）/ 会话·任务·Trace·评测——首次建库即含全部表。
-- 与 §2.3 的唯一差异（决策⑲，2026-11-11 用户确认）：
--   questions 表以 quality_state（processed/rejected，承接原 retired 语义）+ verified 标记
--   替代原 status(active/retired) 字段；raw 状态不入业务库，仅存在于原始数据文件。
-- 外键策略：连接时 PRAGMA foreign_keys=ON（db.connect 统一开启）；默认 RESTRICT，防误删级联。

-- ===== 知识图谱 =====
CREATE TABLE knowledge_points (
  kp_id        TEXT PRIMARY KEY,      -- 如 kp_quad_02（前缀=学段缩写）
  name         TEXT NOT NULL,
  subject      TEXT NOT NULL DEFAULT 'math',   -- schema 通用【决策②】
  stage        TEXT NOT NULL,         -- primary / junior
  grade        INTEGER,
  chapter      TEXT,                  -- 章节归属（初中以人教版教材为基准，决策 2026-11-11）
  difficulty   INTEGER DEFAULT 2,     -- 1-5
  description  TEXT,
  source       TEXT,                  -- 'curriculum' / 'ape210k_tag' / 'manual'
  embedding    BLOB                   -- bge-small-zh-v1.5, 512d float32 序列化（Phase 2 导出）
);
CREATE TABLE kp_edges (
  from_kp    TEXT NOT NULL,           -- 前置知识点
  to_kp      TEXT NOT NULL,           -- 后继（学 to 前需先会 from）
  relation   TEXT DEFAULT 'prerequisite',
  PRIMARY KEY (from_kp, to_kp)
);

-- ===== 题库 =====
CREATE TABLE questions (
  q_id       TEXT PRIMARY KEY,        -- 如 q_00317
  stage      TEXT NOT NULL,
  subject    TEXT NOT NULL DEFAULT 'math',
  type       TEXT NOT NULL,           -- choice/fill/short/proof/application
  stem       TEXT NOT NULL,
  options    TEXT,                    -- JSON 数组（选择题）
  answer     TEXT NOT NULL,
  analysis   TEXT,
  score      REAL NOT NULL,
  difficulty INTEGER NOT NULL,        -- 1-5
  source     TEXT NOT NULL,           -- ape210k / math23k / llm_gen / manual
  quality_state TEXT NOT NULL DEFAULT 'processed'
             CHECK(quality_state IN ('processed', 'rejected')),  -- 决策⑲四状态（raw 不入库）
  verified   INTEGER DEFAULT 0,      -- 人工核验标记：0/1（打标抽检或精修核验后置 1）
  verified_at TEXT,
  created_at TEXT
);
CREATE TABLE q_kp (                   -- 题-知识点多对多
  q_id   TEXT NOT NULL,
  kp_id  TEXT NOT NULL,
  PRIMARY KEY (q_id, kp_id)
);
CREATE VIRTUAL TABLE questions_fts USING fts5(
  q_id UNINDEXED, stem, answer, tokenize='unicode61'
);

-- ===== 班级 / 学生 / 考试 / 答题（F16 Excel 导入，Phase 1）=====
-- 应用层完整性兜底：0 ≤ 得分 ≤ 该题分值等跨表校验在导入校验器与事务写入时双重核对
-- （SQLite CHECK 无法跨表，技术方案 §2.3/§3.4）。
CREATE TABLE classes (
  class_id   TEXT PRIMARY KEY,        -- cls_<hash8>（导入时生成）
  name       TEXT NOT NULL,           -- 如 "初一（1）班"
  grade      INTEGER NOT NULL CHECK(grade BETWEEN 1 AND 12),
  stage      TEXT NOT NULL,           -- primary / junior / senior
  school     TEXT,                    -- 可选；演示/开源数据禁用真实校名（§0.2）
  import_batch_id TEXT,               -- 来源批次（软追踪，不建 FK）
  created_at TEXT NOT NULL,
  UNIQUE(name, grade, stage)          -- 班级识别键：同名同年级同学段视为同一班
);
CREATE TABLE students (
  student_id TEXT PRIMARY KEY,        -- stu_<hash8>（系统内 ID，与来源学号分离）
  class_id   TEXT NOT NULL REFERENCES classes(class_id),
  student_no TEXT NOT NULL,           -- 学号（关联标识，班内唯一）
  name       TEXT NOT NULL,
  import_batch_id TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(class_id, student_no)
);
CREATE TABLE exams (
  exam_id     TEXT PRIMARY KEY,       -- exam_<hash8>
  title       TEXT NOT NULL,
  class_id    TEXT REFERENCES classes(class_id),   -- 可空 = 多班联考
  exam_date   TEXT,                   -- ISO 日期
  subject     TEXT NOT NULL DEFAULT 'math',
  stage       TEXT,
  total_score REAL,
  import_batch_id TEXT,
  created_at  TEXT NOT NULL
);
CREATE TABLE exam_questions (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  exam_id   TEXT NOT NULL REFERENCES exams(exam_id),
  q_no      INTEGER NOT NULL CHECK(q_no > 0),      -- 卷面题号
  q_id      TEXT REFERENCES questions(q_id),       -- 可空 = 外部题（未挂题库）
  kp_ids    TEXT,                                   -- JSON 数组：导入时人工关联
  score     REAL NOT NULL CHECK(score > 0),        -- 卷面分值
  stem      TEXT,                                   -- q_id 为空时必填（校验器保证）
  answer    TEXT,
  UNIQUE(exam_id, q_no)
);
CREATE TABLE student_answers (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  exam_id    TEXT NOT NULL REFERENCES exams(exam_id),
  student_id TEXT NOT NULL REFERENCES students(student_id),
  exam_q_id  INTEGER NOT NULL REFERENCES exam_questions(id),
  score      REAL NOT NULL CHECK(score >= 0),      -- 0 ≤ 得分 ≤ 该题分值（应用层校验）
  is_correct INTEGER,                              -- 0/1，可空（主观题未判）
  UNIQUE(exam_id, student_id, exam_q_id)           -- 数据库级防重复累计
);
CREATE TABLE import_batches (
  batch_id     TEXT PRIMARY KEY,      -- imp_<hash8>
  kind         TEXT NOT NULL,         -- full / classes / students / exam / answers
  file_name    TEXT,
  file_sha256  TEXT NOT NULL,         -- 指纹：与 kind 联合判重
  status       TEXT NOT NULL DEFAULT 'pending',
               -- pending / validating / committed / partial / failed
  total_rows   INTEGER DEFAULT 0,
  ok_rows      INTEGER DEFAULT 0,
  failed_rows  INTEGER DEFAULT 0,
  error_report TEXT,                  -- JSON：[{sheet,row,field,level,code,message}]
  created_at   TEXT NOT NULL,
  committed_at TEXT
);

-- ===== 会话与消息（F1/F14）=====
CREATE TABLE sessions (
  session_id TEXT PRIMARY KEY,
  title      TEXT,
  created_at TEXT, updated_at TEXT
);
CREATE TABLE messages (
  msg_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  role       TEXT NOT NULL,           -- user / assistant
  content    TEXT NOT NULL,
  task_id    TEXT,                    -- 关联产生的任务
  created_at TEXT
);

-- ===== 任务与 Trace（F10/F14，替换内存 TaskStore）=====
CREATE TABLE tasks (
  task_id    TEXT PRIMARY KEY,        -- uuid4 hex[:12]，沿用现格式
  session_id TEXT,
  prompt     TEXT NOT NULL,
  status     TEXT NOT NULL,           -- pending/running/done/failed
  workflow   TEXT,
  params     TEXT,                    -- JSON
  error      TEXT,
  created_at TEXT, started_at TEXT, ended_at TEXT
);
CREATE TABLE steps (
  task_id        TEXT NOT NULL,
  step_no        INTEGER NOT NULL,
  node           TEXT NOT NULL,       -- 节点名，与 SSE 事件一致
  status         TEXT NOT NULL,
  input_summary  TEXT,                -- 截断 500 字符
  output_summary TEXT,                -- 截断 500 字符
  tokens_in      INTEGER DEFAULT 0,
  tokens_out     INTEGER DEFAULT 0,
  cost           REAL DEFAULT 0,      -- 按 token 单价表核算
  latency_ms     INTEGER DEFAULT 0,
  error          TEXT,
  started_at     TEXT, ended_at TEXT,
  PRIMARY KEY (task_id, step_no)
);

-- ===== 评测（F12）=====
CREATE TABLE eval_cases (
  case_id TEXT PRIMARY KEY,
  prompt  TEXT NOT NULL,
  expect  TEXT NOT NULL,              -- JSON：{workflow, params, artifacts_min}
  tags    TEXT                        -- JSON 数组：core/boundary/retrieval...
);
CREATE TABLE eval_runs (
  run_id    TEXT PRIMARY KEY,
  case_id   TEXT NOT NULL,
  mode      TEXT NOT NULL,            -- fake / online
  passed    INTEGER NOT NULL,
  metrics   TEXT,                     -- JSON：该用例各指标结果
  detail    TEXT,
  created_at TEXT
);

-- ===== 关键索引 =====
CREATE INDEX idx_students_class   ON students(class_id);
CREATE INDEX idx_exams_class      ON exams(class_id);
CREATE INDEX idx_examq_exam       ON exam_questions(exam_id);
CREATE INDEX idx_answers_exam_stu ON student_answers(exam_id, student_id);
CREATE INDEX idx_answers_q        ON student_answers(exam_q_id);
CREATE INDEX idx_batches_status   ON import_batches(status);
CREATE INDEX idx_messages_session ON messages(session_id);
CREATE INDEX idx_tasks_session    ON tasks(session_id);
