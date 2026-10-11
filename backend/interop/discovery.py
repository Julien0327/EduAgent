"""ADP（Agent Discovery Protocol）发现客户端。

按已核实 schema（acps_sdk/adp/models.py 的 DiscoveryRequest/DiscoveryResponse）构造请求、
解析响应。线格式为 camelCase（官方 to_dict(by_alias=True) 输出）。

真实发现请求走梧桐平台 `POST {base_url}/acps-adp-v2/discover`，需要 mTLS 证书；
本模块的 `discover` 用 httpx 发起真实网络请求，不提供本地假实现。
"""
from __future__ import annotations

from typing import Any


def build_discovery_request(
    query: str,
    *,
    type: str = "explicit",
    limit: int | None = 5,
    context: dict[str, Any] | None = None,
    filter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 ADP 发现请求（camelCase 线格式）。

    字段语义对齐 acps_sdk/adp/models.py DiscoveryRequest：
    type ∈ explicit/exploratory/trending/filtered；explicit 时 query 必填。
    """
    req: dict[str, Any] = {"type": type, "query": query}
    if limit is not None:
        req["limit"] = limit
    if context is not None:
        req["context"] = context
    if filter is not None:
        req["filter"] = filter
    return req


def discover(
    query: str,
    *,
    base_url: str,
    cert: tuple[str, str] | None = None,  # (cert_file, key_file)，mTLS
    verify: str | bool = True,  # trust bundle 路径 或 False
    timeout: float = 30.0,
    **request_kwargs: Any,
) -> dict[str, Any]:
    """向发现服务发起真实 discover 请求，返回响应 JSON（result/error）。"""
    import httpx

    payload = build_discovery_request(query, **request_kwargs)
    url = base_url.rstrip("/") + "/acps-adp-v2/discover"
    with httpx.Client(cert=cert, verify=verify, timeout=timeout) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()


def response_agents(response: dict[str, Any]) -> list[tuple[str, dict[str, Any], str]]:
    """从发现响应中提取候选智能体 (aic, acs, group) 三元组。

    对齐 DiscoveryResult：result.acsMap{AIC→ACS} + result.agents[].agentSkills[]。
    """
    result = response.get("result") or {}
    acs_map = result.get("acsMap") or {}
    out: list[tuple[str, dict[str, Any], str]] = []
    for group in result.get("agents") or []:
        for skill in group.get("agentSkills") or []:
            aic = skill.get("aic", "")
            out.append((aic, acs_map.get(aic, {}), group.get("group", "")))
    return out
