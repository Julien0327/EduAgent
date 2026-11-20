"""SQLite 在线备份（Phase 6 / S2-C）。

- 使用 sqlite3 在线备份 API，避免直接复制正在写入的文件；
- gzip 压缩 + sha256；按保留数量清理旧备份；
- 可用于 cron：每天备份 data/db/edumate.sqlite。

用法：
    python -m backend.scripts.backup_demo_db --db data/db/edumate.sqlite \
        --out-dir data/backups --keep 7
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="SQLite 在线备份")
    parser.add_argument("--db", default="data/db/edumate.sqlite")
    parser.add_argument("--out-dir", default="data/backups")
    parser.add_argument("--keep", type=int, default=7)
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"数据库不存在：{db_path}")
        return 1
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    tmp = out_dir / f"edumate_{stamp}.sqlite"
    gz = out_dir / f"edumate_{stamp}.sqlite.gz"

    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(tmp))
    try:
        src.backup(dst)
        integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        src.close()
        dst.close()

    with tmp.open("rb") as fh_in, gzip.open(gz, "wb", compresslevel=6) as fh_out:
        fh_out.write(fh_in.read())
    tmp.unlink()
    sha = hashlib.sha256(gz.read_bytes()).hexdigest()

    backups = sorted(out_dir.glob("edumate_*.sqlite.gz"), key=lambda p: p.stat().st_mtime)
    removed = []
    while len(backups) > max(1, args.keep):
        old = backups.pop(0)
        old.unlink()
        removed.append(old.name)

    result = {
        "source": str(db_path),
        "backup": str(gz),
        "bytes": gz.stat().st_size,
        "sha256": sha,
        "integrity": integrity,
        "removed": removed,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if integrity == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
