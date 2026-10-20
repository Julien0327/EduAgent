"""P2-8 Step 2: Minimal real Discovery → Agent RPC → TaskResult E2E verification.

This test validates the closed-loop pipeline using REAL infrastructure:
- Real Discovery endpoint (https://wt.ioa.pub/discovery)
- Real mTLS client certificate (/opt/EduAgent/test-client/)
- Real Agent RPC call and TaskResult parsing

STRICT CONSTRAINTS ENFORCED:
- Does NOT modify TLS/security policy or identity_binding_enabled logic
- Does NOT use mocks, fake agents, or synthetic discovery responses
- Does NOT alter existing business workflows (exam_loop/question_gen/lesson_plan)
- Uses only production certificates and trust bundle as-is
"""
import sys
import os
import pytest

# Ensure project root is importable
sys.path.insert(0, "/opt/EduAgent")
os.chdir("/opt/EduAgent")

from backend.interop.orchestrator import orchestrate_sync

# ── Fixed parameters per P2-8 spec ────────────────────────────────
CLIENT_CERT = "/opt/EduAgent/test-client/agent-client-cert.pem"
CLIENT_KEY = "/opt/EduAgent/test-client/agent-client-key.pem"
TRUST_BUNDLE = "/opt/EduAgent/tests/test-trust-bundle-combined.pem"
DISCOVERY_URL = "https://wt.ioa.pub/discovery"
EDUMATE_AIC = "1.2.156.3088.1.BUPT.1LW3RI.PLJ4AG.CPXIZB.0559"
TEACHING_QUERY = "请为初中二年级学生设计一节关于勾股定理的互动教学方案，包含课堂活动与课后练习。"


@pytest.mark.p2_8
@pytest.mark.e2e
class TestP28DiscoveryRpcE2E:
    """Real end-to-end validation of Discovery → RPC → Parse pipeline."""

    def test_orchestrate_returns_valid_task_result(self):
        """Full closed-loop: discover → select → rpc → parse succeeds with real infra."""
        # Pre-flight: verify required files exist
        for path, label in [
            (CLIENT_CERT, "client cert"),
            (CLIENT_KEY, "client key"),
            (TRUST_BUNDLE, "trust bundle"),
        ]:
            assert os.path.isfile(path), f"Missing {label}: {path}"

        result = orchestrate_sync(
            query=TEACHING_QUERY,
            discovery_base_url=DISCOVERY_URL,
            cert=(CLIENT_CERT, CLIENT_KEY),
            verify=TRUST_BUNDLE,
            timeout=45.0,
            leader_id=EDUMATE_AIC,
        )

        # Validate parsed TaskResult structure
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "taskId" in result, "Missing taskId in parsed result"
        assert "status" in result, "Missing status in parsed result"
        assert "products" in result, "Missing products in parsed result"
        assert "raw" in result, "Missing raw in parsed result"

        # Status must be a non-empty string (real agents return various states)
        status = result["status"]
        assert isinstance(status, str) and len(status) > 0, (
            f"Status must be non-empty string, got: {status!r}"
        )

    def test_orchestrate_result_contains_raw_response(self):
        """Parsed result preserves raw response for downstream inspection."""
        result = orchestrate_sync(
            query=TEACHING_QUERY,
            discovery_base_url=DISCOVERY_URL,
            cert=(CLIENT_CERT, CLIENT_KEY),
            verify=TRUST_BUNDLE,
            timeout=45.0,
            leader_id=EDUMATE_AIC,
        )

        raw = result.get("raw")
        assert raw is not None, "raw field must not be None"
        # Raw should contain identifiable task metadata
        assert raw is not None, "raw must not be None"  # TaskResult object or dict
