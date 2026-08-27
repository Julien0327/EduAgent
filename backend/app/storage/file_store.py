"""产物与文件存储（路径管理）。

D2 阶段：只做产物路径管理，供文档服务写入生成文件。
上传 / 下载 / 预览接口属于 D4，本阶段不实现。

安全约定（技术方案 §11）：所有文件路径由后端生成，不接受前端传入的
绝对路径，文件名只取 basename，杜绝路径穿越。
"""
from pathlib import Path

# 仓库根目录：backend/app/storage/file_store.py -> 上溯 3 级到 backend -> 再上一级到仓库根
REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUTS_ROOT = REPO_ROOT / "data" / "outputs"


def get_outputs_dir(task_id: str = "demo") -> Path:
    """返回（并确保存在）指定任务的产物目录。"""
    directory = OUTPUTS_ROOT / task_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def build_output_path(task_id: str, filename: str) -> Path:
    """根据任务与文件名，返回安全的产物路径。

    只取文件名的 basename 部分，丢弃任何夹带的目录分隔符，避免路径穿越。
    """
    safe_name = Path(filename).name
    return get_outputs_dir(task_id) / safe_name
