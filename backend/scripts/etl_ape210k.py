"""APE210K ETL 主脚本（Phase 1 / Step 4）。

流程（决策⑳复现命令链一环，全步幂等）：
  SHA-256 校验原始文件（与 DATA-SOURCES.md 记录一致，防数据被意外改动）
    -> 逐行流式读 train/valid/test JSON Lines（不整载内存）
    -> 清洗/映射（backend.app.data.ape210k 纯函数）
    -> 验算（equation 代入与 ans 比对；mismatch -> rejected 可追溯）
    -> 去重（完全重复剔除保留首条；同题干异答案=疑似变式，两题都保留）
    -> 分批事务写 questions + questions_fts（FTS5 仅 processed，幂等重建）
    -> 漏斗报告（stdout + data/staging/etl_ape210k_report.json）

幂等：**全量重建语义**——每次运行先删除本来源（q_ape_%）的 questions、
questions_fts、q_kp 挂接再按当前规则写入，重跑结果与首次一致（清洗规则
改进后重跑即生效，不存在旧状态残留）。q_id 确定性 = q_ape_<原始id>。
重跑本脚本后如需 KP 挂接，重跑 build_primary_kp.py。

用法（仓库根执行）：
    .venv/Scripts/python.exe -m backend.scripts.etl_ape210k [--db <路径>] [--skip-hash]
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from backend.app.data import ape210k
from backend.app.storage import db

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAW_DIR = REPO_ROOT / "data" / "raw" / "ape210k_src" / "data"
REPORT_PATH = REPO_ROOT / "data" / "staging" / "etl_ape210k_report.json"
BATCH_SIZE = 5000

# SHA-256 基线（2026-11-11 核验，与 docs/development/DATA-SOURCES.md 一致）
EXPECTED_SHA256 = {
    "train.ape.json": "e89a30eccedb2cd7226de5385c39fd16b438b95ebd13b4f8aecb1134429a9bea",
    "valid.ape.json": "1ea5029a6974b7cbf943c0b917d1613d9ac344fcf7a754244ab52aa29cb64b45",
    "test.ape.json": "851e8ca3d0ebb4db941d3722cea885a9b5443c9de5dfa50814ae80ec3550c81e",
}
SPLITS = ("train.ape.json", "valid.ape.json", "test.ape.json")

_INSERT_QUESTION = """
INSERT OR IGNORE INTO questions
  (q_id, stage, subject, type, stem, options, answer, analysis,
   score, difficulty, source, quality_state, verified, created_at)
VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, 0, ?)
"""
_INSERT_FTS = "INSERT INTO questions_fts (q_id, stem, answer) VALUES (?, ?, ?)"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_raw_files(raw_dir: Path) -> None:
    missing = [name for name in SPLITS if not (raw_dir / name).exists()]
    if missing:
        raise SystemExit(
            f"原始数据缺失：{missing}。请先执行 "
            f"git clone --depth 1 https://github.com/Chenny0808/ape210k data/raw/ape210k_src"
        )


def iter_rows(raw_dir: Path):
    """逐行流式产出 (split, raw_dict)；空行跳过。"""
    for name in SPLITS:
        with open(raw_dir / name, encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield name, json.loads(line)
                except json.JSONDecodeError:
                    yield name, {"__parse_error__": f"{name}:{line_no}"}


def run(db_path, raw_dir: Path = DEFAULT_RAW_DIR) -> dict:
    conn = db.connect(db_path)
    funnel = {
        "raw_total": 0,
        "parse_error": 0,
        "stem_invalid": 0,
        "id_missing": 0,
        "duplicate_exact": 0,
        "verify_match": 0,
        "verify_mismatch_rejected": 0,
        "verify_unverifiable": 0,
        "similar_variant_kept": 0,
        "written_processed": 0,
        "written_rejected": 0,
    }
    seen_dup_keys: set[str] = set()
    stem_seen: dict[str, str] = {}  # stem_key -> answer_key（首条），用于疑似变式判定
    t0 = time.time()

    with conn:
        # 全量重建语义：先清本来源的全部痕迹（业务表/FTS/KP 挂接）
        conn.execute("DELETE FROM q_kp WHERE q_id LIKE 'q_ape_%'")
        conn.execute("DELETE FROM questions WHERE q_id LIKE 'q_ape_%'")
        conn.execute("DELETE FROM questions_fts WHERE q_id LIKE 'q_ape_%'")

    buffer_q: list = []
    buffer_f: list = []

    def flush() -> None:
        if not buffer_q:
            return
        with conn:
            conn.executemany(_INSERT_QUESTION, buffer_q)
            conn.executemany(_INSERT_FTS, buffer_f)
        buffer_q.clear()
        buffer_f.clear()

    for split, raw in iter_rows(raw_dir):
        funnel["raw_total"] += 1
        if "__parse_error__" in raw:
            funnel["parse_error"] += 1
            continue

        mapped, drop_reason = ape210k.map_raw(raw)
        if mapped is None:
            funnel[drop_reason] += 1
            continue

        verdict = ape210k.verify_equation(mapped["analysis"], mapped["answer"])
        funnel[f"verify_{verdict}" + ("_rejected" if verdict == "mismatch" else "")] += 1
        quality_state = "rejected" if verdict == "mismatch" else "processed"

        key = ape210k.dedup_key(mapped["stem"], mapped["answer"])
        s_key, a_key = key.split("|", 1)
        if key in seen_dup_keys:
            funnel["duplicate_exact"] += 1
            continue
        seen_dup_keys.add(key)
        first_ans = stem_seen.get(s_key)
        if first_ans is None:
            stem_seen[s_key] = a_key
        elif first_ans != a_key:
            funnel["similar_variant_kept"] += 1  # 同题干异答案：疑似变式，保留不误删

        created_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        buffer_q.append((
            mapped["q_id"], mapped["stage"], mapped["subject"], mapped["type"],
            mapped["stem"], mapped["answer"], mapped["analysis"],
            mapped["score"], mapped["difficulty"], mapped["source"],
            quality_state, created_at,
        ))
        if quality_state == "processed":
            buffer_f.append((mapped["q_id"], mapped["stem"], mapped["answer"]))
            funnel["written_processed"] += 1
        else:
            funnel["written_rejected"] += 1
        if len(buffer_q) >= BATCH_SIZE:
            flush()

    flush()
    elapsed = time.time() - t0
    db_file = Path(db_path) if db_path else REPO_ROOT / "data" / "db" / "edumate.sqlite"
    report = {
        "source": "ape210k",
        "raw_dir": str(raw_dir),
        "files": {name: EXPECTED_SHA256[name] for name in SPLITS},
        "elapsed_seconds": round(elapsed, 1),
        "db_path": str(db_file),
        "db_size_mb": round(db_file.stat().st_size / 1e6, 1) if db_file.exists() else None,
        "funnel": funnel,
    }
    conn.close()
    return report


def print_report(report: dict) -> None:
    f = report["funnel"]
    print(f"ETL 完成：{report['elapsed_seconds']}s，库大小 {report['db_size_mb']}MB")
    print(f"漏斗：原始 {f['raw_total']}")
    print(f"  解析失败 {f['parse_error']} | 题干无效 {f['stem_invalid']} | id 缺失 {f['id_missing']}")
    print(f"  验算：一致 {f['verify_match']} / 不一致拒(入库rejected) {f['verify_mismatch_rejected']} / 无法验算 {f['verify_unverifiable']}")
    print(f"  完全重复剔除 {f['duplicate_exact']} | 疑似变式保留 {f['similar_variant_kept']}")
    print(f"  入库 processed {f['written_processed']} + rejected {f['written_rejected']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="APE210K 清洗入库（幂等，可重跑）")
    parser.add_argument("--db", default=None, help="业务库路径（默认 data/db/edumate.sqlite）")
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
    parser.add_argument("--skip-hash", action="store_true", help="跳过 SHA-256 校验（仅调试用）")
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    check_raw_files(raw_dir)
    if not args.skip_hash:
        for name in SPLITS:
            actual = sha256_of(raw_dir / name)
            if actual != EXPECTED_SHA256[name]:
                print(f"SHA-256 不一致：{name}\n  期望 {EXPECTED_SHA256[name]}\n  实际 {actual}\n拒绝处理，请核对数据来源。")
                return 1
        print("SHA-256 校验通过（3 个文件与核验记录一致）")

    report = run(args.db, raw_dir)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_report(report)
    print(f"报告已写入：{REPORT_PATH}")

    f = report["funnel"]
    if f["written_processed"] < 20000:
        print(f"⚠️ processed 仅 {f['written_processed']} < 20000（决策⑲）：不灌水凑数，等待用户裁决基线。")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
