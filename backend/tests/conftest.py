"""pytest 配置：把仓库根加入 sys.path，使测试能以 backend.* 顶层包导入（Phase 0/I1 统一导入路径）。"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
