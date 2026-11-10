"""P2-7: Standard TaskResult parsing and consumption utilities.

Provides structured extraction of status, dataItems, and products
from acps_sdk TaskResult objects for downstream business logic.

This module is pure read-only transformation — no network calls,
no certificate operations, no identity binding policy changes.
"""
from __future__ import annotations

from typing import Any, Optional

try:  # acps-sdk 为可选依赖（AIP 互联，默认关闭）；未安装时保持本模块可导入（Phase 0 降级修复）
    from acps_sdk.aip.aip_base_model import TaskResult
except ImportError:  # pragma: no cover —— 最小环境/容器未安装 SDK
    TaskResult = None  # type: ignore[assignment,misc]


def extract_text_from_data_items(data_items: list[Any] | None) -> str:
    """Concatenate text content from a list of DataItem objects.

    Handles TextDataItem (type="text"), FileDataItem (type="file"),
    and StructuredDataItem (type="data") per AIP spec.

    Returns:
        Concatenated text string; empty string if no text items.
    """
    if not data_items:
        return ""
    parts: list[str] = []
    for item in data_items:
        # Support both SDK objects and plain dicts
        item_type = getattr(item, "type", None) or (item.get("type") if isinstance(item, dict) else None)
        if item_type == "text":
            text = getattr(item, "text", None) or (item.get("text") if isinstance(item, dict) else None)
            if text:
                parts.append(str(text))
        elif item_type == "file":
            name = getattr(item, "name", None) or (item.get("name") if isinstance(item, dict) else None)
            uri = getattr(item, "uri", None) or (item.get("uri") if isinstance(item, dict) else None)
            if name or uri:
                parts.append(f"[file: {name or uri}]")
        elif item_type == "data":
            data = getattr(item, "data", None) or (item.get("data") if isinstance(item, dict) else None)
            if data:
                parts.append(f"[structured: {type(data).__name__}]")
    return "\n".join(parts)


def parse_product(product: Any) -> dict[str, Any]:
    """Parse a single Product into a consumable dict.

    Returns:
        Dict with keys: id, name, description, text, data_items_count.
    """
    pid = getattr(product, "id", None) or (product.get("id") if isinstance(product, dict) else None)
    name = getattr(product, "name", None) or (product.get("name") if isinstance(product, dict) else None)
    desc = getattr(product, "description", None) or (product.get("description") if isinstance(product, dict) else None)
    data_items = getattr(product, "dataItems", None) or (product.get("dataItems") if isinstance(product, dict) else None)
    text = extract_text_from_data_items(data_items)
    return {
        "id": pid,
        "name": name,
        "description": desc,
        "text": text,
        "data_items_count": len(data_items) if data_items else 0,
    }


def parse_task_result(result: TaskResult) -> dict[str, Any]:
    """Parse a TaskResult into a structured dict for business consumption.

    Args:
        result: TaskResult object from acps_sdk.

    Returns:
        Dict with keys:
        - taskId: str
        - status: str (TaskState enum value)
        - status_changed_at: str | None
        - status_data_items_text: str (concatenated text from status.dataItems)
        - products: list[dict] (parsed products)
        - products_text: str (all product texts concatenated)
        - raw: TaskResult (original object for advanced use)
    """
    if TaskResult is None:
        raise RuntimeError(
            "官方 acps_sdk 未安装，无法解析 TaskResult（AIP 为可选能力）。"
            "如需 AIP 互联，请安装 acps-sdk==2.2.0（AtomGit 源/本地 wheel，非 PyPI）"
        )

    # Status fields
    status_obj = getattr(result, "status", None)
    status_state = getattr(status_obj, "state", None) if status_obj else None
    status_changed = getattr(status_obj, "stateChangedAt", None) if status_obj else None
    status_data_items = getattr(status_obj, "dataItems", None) if status_obj else None

    # Products
    products_raw = getattr(result, "products", None) or []
    parsed_products = [parse_product(p) for p in products_raw]
    all_product_text = "\n---\n".join(
        p["text"] for p in parsed_products if p["text"]
    )

    return {
        "taskId": getattr(result, "taskId", None),
        "status": str(status_state.value) if hasattr(status_state, "value") else str(status_state),
        "status_changed_at": status_changed,
        "status_data_items_text": extract_text_from_data_items(status_data_items),
        "products": parsed_products,
        "products_text": all_product_text,
        "raw": result,
    }
