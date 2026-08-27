"""pytest 配置：把 backend 加入 sys.path，使测试能导入 app / config 顶层模块。"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
