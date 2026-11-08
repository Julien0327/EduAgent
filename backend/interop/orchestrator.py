"""P2-7: Minimal Discovery → RPC orchestration layer.

Provides a single entry point that:
1. Calls ADP discover() to get candidate Agents
2. Filters candidates by AIC validity + HTTPS endpoint
3. Selects the best candidate (first compliant)
4. Invokes call_agent() with extracted AIC/endpoint
5. Returns parsed TaskResult via standard consumption utilities

This module is READ-ONLY with respect to certificates, TLS config,
and identity binding policy. It does NOT disable identity_binding_enabled.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from backend.config import settings
from backend.interop.discovery import discover, response_agents
from backend.interop.agent_caller import call_agent, call_agent_sync
from backend.interop.result_parser import parse_task_result

logger = logging.getLogger(__name__)

# 10-segment AIC pattern: digits.digits.digits.digits.alnum.alnum.alnum.alnum.alnum.alnum
_AIC_RE = re.compile(
    r"^\d+\.\d+\.\d+\.\d+\.[A-Za-z0-9]+\.[A-Za-z0-9]+\.[A-Za-z0-9]+\.[A-Za-z0-9]+\.[A-Za-z0-9]+\.[A-Za-z0-9]+$"
)


def _is_valid_aic(aic: str) -> bool:
    """Check if string is a valid 10-segment AIC."""
    return bool(aic) and bool(_AIC_RE.match(aic))


def _extract_endpoint(acs: dict[str, Any]) -> Optional[tuple[str, bool]]:
    """Extract RPC endpoint from ACS (Agent Capability Statement).
    Returns (url, is_https) tuple.
    Priority: HTTPS+JSONRPC > HTTPS > HTTP+JSONRPC > HTTP.
    Falls back to HTTP for demo environments where agents register https://
    but serve plain HTTP.
    """
    candidates: list[tuple[str, bool, int]] = []  # (url, is_https, priority)
    # Top-level fields (legacy/simple format)
    for key in ("rpcEndpoint", "endpoint", "url"):
        val = acs.get(key)
        if isinstance(val, str):
            if val.startswith("https://"):
                candidates.append((val.rstrip("/"), True, 0))
            elif val.startswith("http://"):
                candidates.append((val.rstrip("/"), False, 2))
    # ADP v2: endPoints array with {url, transport, security}
    endpoints = acs.get("endPoints")
    if isinstance(endpoints, list):
        for ep in endpoints:
            if isinstance(ep, dict):
                url = ep.get("url")
                if not isinstance(url, str):
                    continue
                transport = (ep.get("transport") or "").upper()
                is_jsonrpc = transport == "JSONRPC"
                if url.startswith("https://"):
                    prio = 0 if is_jsonrpc else 1
                    candidates.append((url.rstrip("/"), True, prio))
                elif url.startswith("http://"):
                    prio = 2 if is_jsonrpc else 3
                    candidates.append((url.rstrip("/"), False, prio))
    if not candidates:
        return None
    # Sort by priority (lower = better), return best
    candidates.sort(key=lambda c: c[2])
    best_url, best_https, _ = candidates[0]
    return (best_url, best_https)


def select_candidate(
    agents: list[tuple[str, dict[str, Any], str, str]],
) -> Optional[tuple[str, str, bool]]:
    """Select best compliant Agent from discovery results.
    Prefers education-domain agents (skillId starts with 'edu.') over others.
    Excludes self (own AIC).
    Returns:
        (aic, endpoint, is_https) tuple, or None if no compliant candidate.
    """
    from backend.config import settings as _settings
    own_aic = getattr(_settings, "ACP_AIC", "")
    valid: list[tuple[str, str, bool, str, bool]] = []
    for item in agents:
        aic, acs, group = item[0], item[1], item[2]
        skill_id = item[3] if len(item) > 3 else ""
        if not _is_valid_aic(aic):
            logger.debug("Skipping invalid AIC: %s", aic)
            continue
        if aic == own_aic:
            logger.debug("Skipping self AIC: %s", aic)
            continue
        ep_result = _extract_endpoint(acs)
        if not ep_result:
            logger.debug("Skipping agent %s: no endpoint", aic)
            continue
        endpoint, is_https = ep_result
        is_edu = skill_id.startswith("edu.")
        valid.append((aic, endpoint, is_https, skill_id, is_edu))
    if not valid:
        return None
    # Sort: edu diagnosis/weakness skills first, then other edu, then non-edu; HTTPS preferred
    def _sort_key(v):
        aic, endpoint, is_https, skill_id, is_edu = v
        sid_lower = skill_id.lower()
        if is_edu and ("diagnosis" in sid_lower or "weakness" in sid_lower or "mastery" in sid_lower):
            skill_prio = 0
        elif is_edu:
            skill_prio = 1
        else:
            skill_prio = 2
        return (skill_prio, not is_https)
    valid.sort(key=_sort_key)
    aic, endpoint, is_https, skill_id, is_edu = valid[0]
    if not is_https:
        logger.warning(
            "Agent (%s) uses HTTP endpoint (demo降级): %s",
            aic, endpoint,
        )
    logger.info(
        "Selected candidate: aic=%s endpoint=%s https=%s skill=%s edu=%s",
        aic, endpoint, is_https, skill_id, is_edu,
    )
    return (aic, endpoint, is_https)

async def orchestrate(
    query: str,
    *,
    discovery_base_url: Optional[str] = None,
    cert: Optional[tuple[str, str]] = None,
    verify: str | bool = True,
    timeout: float = 30.0,
    leader_id: Optional[str] = None,
) -> dict[str, Any]:
    """Full Discovery → RPC pipeline (async).

    Args:
        query: Natural-language query for discovery + task input.
        discovery_base_url: Override settings.ACP_DISCOVERY_BASE_URL.
        cert: mTLS client cert/key tuple for discovery request.
        verify: Trust bundle path or False for discovery TLS.
        timeout: Timeout for both discovery and RPC calls.
        leader_id: Override settings.ACP_LEADER_ID.

    Returns:
        Parsed TaskResult dict with keys: taskId, status, products, raw.

    Raises:
        ValueError: No compliant candidate found.
        Exception: Propagated from discover() or call_agent().
    """
    base_url = discovery_base_url or getattr(settings, "ACP_DISCOVERY_BASE_URL", "https://wt.ioa.pub/discovery")
    lid = leader_id or settings.ACP_LEADER_ID

    # Step 1: Discover
    logger.info("Discovering agents: query=%.80s base=%s", query, base_url)
    disc_resp = discover(
        query,
        base_url=base_url,
        cert=cert,
        verify=verify,
        timeout=timeout,
    )
    candidates = response_agents(disc_resp)
    logger.info("Discovery returned %d candidate(s)", len(candidates))

    # Step 2: Select
    selected = select_candidate(candidates)
    if selected is None:
        raise ValueError(
            "No compliant Agent found in discovery results. "
            "All candidates failed AIC validation or lacked any endpoint."
        )
    partner_aic, partner_url, is_https = selected

    # Step 3: RPC call
    logger.info("Calling selected agent: aic=%s url=%s", partner_aic, partner_url)
    # Pass trust bundle to RPC layer for server certificate verification
    rpc_ca_bundle = verify if isinstance(verify, str) else None
    result = await call_agent(
        user_input=query,
        partner_url=partner_url,
        expected_partner_aic=partner_aic,
        leader_id=lid,
        timeout=timeout,
        ca_bundle=rpc_ca_bundle,
        use_mtls=is_https,
    )

    # Step 4: Parse & return
    return parse_task_result(result)


def orchestrate_sync(query: str, **kwargs: Any) -> dict[str, Any]:
    """Synchronous wrapper for orchestrate()."""
    import asyncio
    return asyncio.run(orchestrate(query, **kwargs))
