"""Tests for backend.interop.discovery TLS verification fix.

Covers:
- build_ssl_context loads both system default CAs and platform CA bundle.
- SSLContext is passed through to httpx via discover().
- Missing / invalid CA bundle raises explicit errors (no silent fallback).
- TLS verification cannot be disabled (verify=False rejected).
- Existing discovery behavior (request building, response parsing) not regressed.
"""
from __future__ import annotations
import ssl
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _generate_test_ca(tmp_path: Path) -> Path:
    """Generate a real self-signed CA cert using openssl for testing."""
    cert_path = tmp_path / "test-ca.pem"
    key_path = tmp_path / "test-ca-key.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key_path), "-out", str(cert_path),
            "-days", "1", "-nodes", "-subj", "/CN=TestDiscoveryCA",
        ],
        check=True, capture_output=True,
    )
    return cert_path


# ---------------------------------------------------------------------------
# build_ssl_context tests
# ---------------------------------------------------------------------------

class TestBuildSslContext:
    def test_loads_system_and_platform_cas(self, tmp_path: Path):
        """SSLContext should have CERT_REQUIRED and load both CA sources."""
        fake_ca = _generate_test_ca(tmp_path)

        from backend.interop.discovery import build_ssl_context
        ctx = build_ssl_context(str(fake_ca))

        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is True

    def test_accepts_path_object(self, tmp_path: Path):
        """build_ssl_context should accept pathlib.Path as well as str."""
        fake_ca = _generate_test_ca(tmp_path)

        from backend.interop.discovery import build_ssl_context
        ctx = build_ssl_context(fake_ca)
        assert isinstance(ctx, ssl.SSLContext)

    def test_missing_bundle_raises_file_not_found(self):
        """Non-existent CA bundle must raise FileNotFoundError."""
        from backend.interop.discovery import build_ssl_context
        with pytest.raises(FileNotFoundError, match="CA bundle not found"):
            build_ssl_context("/nonexistent/path/ca-bundle.pem")

    def test_empty_bundle_raises_value_error(self):
        """Empty string CA bundle must raise ValueError."""
        from backend.interop.discovery import build_ssl_context
        with pytest.raises(ValueError, match="must not be empty"):
            build_ssl_context("")

    def test_invalid_pem_raises_ssl_error(self, tmp_path: Path):
        """Invalid PEM content must raise ssl.SSLError, not silently succeed."""
        bad_ca = tmp_path / "bad-ca.pem"
        bad_ca.write_text("NOT A VALID PEM FILE")

        from backend.interop.discovery import build_ssl_context
        with pytest.raises(ssl.SSLError, match="Failed to load CA bundle"):
            build_ssl_context(str(bad_ca))


# ---------------------------------------------------------------------------
# discover() TLS enforcement tests
# ---------------------------------------------------------------------------

class TestDiscoverTlsEnforcement:
    def test_verify_false_raises_value_error(self):
        """verify=False must be rejected; TLS cannot be disabled."""
        from backend.interop.discovery import discover
        with pytest.raises(ValueError, match="TLS verification cannot be disabled"):
            discover("test query", base_url="https://example.com", verify=False)

    def test_string_verify_builds_ssl_context(self, tmp_path: Path):
        """When verify is a string path, discover() should auto-build SSLContext."""
        fake_ca = _generate_test_ca(tmp_path)

        from backend.interop.discovery import discover
        # httpx is imported inside discover(), so we mock httpx.Client directly
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"result": {}}
            mock_resp.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            discover("test", base_url="https://example.com", verify=str(fake_ca))

            call_kwargs = mock_client_cls.call_args
            verify_arg = call_kwargs.kwargs.get("verify") or call_kwargs[1].get("verify")
            assert isinstance(verify_arg, ssl.SSLContext), (
                f"Expected ssl.SSLContext, got {type(verify_arg)}"
            )

    def test_ssl_context_passed_through_unchanged(self):
        """When verify is already an SSLContext, it should be used as-is."""
        custom_ctx = ssl.create_default_context()

        from backend.interop.discovery import discover
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"result": {}}
            mock_resp.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            discover("test", base_url="https://example.com", verify=custom_ctx)

            call_kwargs = mock_client_cls.call_args
            verify_arg = call_kwargs.kwargs.get("verify") or call_kwargs[1].get("verify")
            assert verify_arg is custom_ctx

    def test_verify_true_uses_default(self):
        """verify=True should pass through to httpx unchanged (default system CA)."""
        from backend.interop.discovery import discover
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"result": {}}
            mock_resp.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            discover("test", base_url="https://example.com", verify=True)

            call_kwargs = mock_client_cls.call_args
            verify_arg = call_kwargs.kwargs.get("verify") or call_kwargs[1].get("verify")
            assert verify_arg is True


# ---------------------------------------------------------------------------
# Regression: request building & response parsing
# ---------------------------------------------------------------------------

class TestDiscoveryRegression:
    def test_build_discovery_request_basic(self):
        from backend.interop.discovery import build_discovery_request
        req = build_discovery_request("math tutor")
        assert req["type"] == "explicit"
        assert req["query"] == "math tutor"
        assert req["limit"] == 5

    def test_build_discovery_request_with_filter(self):
        from backend.interop.discovery import build_discovery_request
        req = build_discovery_request(
            "science", type="filtered", limit=3,
            filter={"domain": "physics"},
        )
        assert req["type"] == "filtered"
        assert req["filter"] == {"domain": "physics"}
        assert req["limit"] == 3

    def test_response_agents_extracts_correctly(self):
        from backend.interop.discovery import response_agents
        response = {
            "result": {
                "acsMap": {
                    "AIC.001": {"name": "Agent-A", "endpoint": "https://a.example.com"},
                    "AIC.002": {"name": "Agent-B", "endpoint": "https://b.example.com"},
                },
                "agents": [
                    {
                        "group": "education",
                        "agentSkills": [
                            {"aic": "AIC.001", "skillId": "skill-a"},
                            {"aic": "AIC.002", "skillId": "skill-b"},
                        ],
                    }
                ],
            }
        }
        agents = response_agents(response)
        assert len(agents) == 2
        assert agents[0] == ("AIC.001", {"name": "Agent-A", "endpoint": "https://a.example.com"}, "education", "skill-a")
        assert agents[1] == ("AIC.002", {"name": "Agent-B", "endpoint": "https://b.example.com"}, "education", "skill-b")

    def test_response_agents_empty(self):
        from backend.interop.discovery import response_agents
        assert response_agents({}) == []
        assert response_agents({"result": {}}) == []
        assert response_agents({"result": {"agents": []}}) == []
