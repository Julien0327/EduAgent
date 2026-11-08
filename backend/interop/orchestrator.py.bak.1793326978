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


def _extract_endpoint(acs: dict[str, Any]) -> Optional[str]:
    """Extract HTTPS endpoint from ACS (Agent Capability Statement).
    Checks top-level fields first, then endPoints array (ADP v2 format).
    Endpoint must start with https://.
    """
    # Top-level fields (legacy/simple format)
    for key in ("rpcEndpoint", "endpoint", "url"):
        val = acs.get(key)
        if isinstance(val, str) and val.startswith("https://"):
            return val.rstrip("/")
    # ADP v2: endPoints array with {url, transport, security}
    endpoints = acs.get("endPoints")
    if isinstance(endpoints, list):
        for ep in endpoints:
            if isinstance(ep, dict):
                url = ep.get("url")
                if isinstance(url, str) and url.startswith("https://"):
                    return url.rstrip("/")
    return None


def select_candidate(
    agents: list[tuple[str, dict[str, Any], str]],
) -> Optional[tuple[str, str]]:
    """Select first compliant Agent from discovery results.

    Compliance criteria (identity-binding safe):
    - Valid 10-segment AIC
    - HTTPS endpoint present in ACS

    Returns:
        (aic, endpoint) tuple, or None if no compliant candidate.
    """
    for aic, acs, group in agents:
        if not _is_valid_aic(aic):
            logger.debug("Skipping invalid AIC: %s", aic)
            continue
        endpoint = _extract_endpoint(acs)
        if not endpoint:
            logger.debug("Skipping agent %s: no HTTPS endpoint", aic)
            continue
        logger.info(
            "Selected candidate: aic=%s endpoint=%s group=%s",
            aic, endpoint, group,
        )
        return (aic, endpoint)
    return None


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
            "All candidates failed AIC validation or lacked HTTPS endpoint."
        )
    partner_aic, partner_url = selected

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
    )

    # Step 4: Parse & return
    return parse_task_result(result)


def orchestrate_sync(query: str, **kwargs: Any) -> dict[str, Any]:
    """Synchronous wrapper for orchestrate()."""
    import asyncio
    return asyncio.run(orchestrate(query, **kwargs))
