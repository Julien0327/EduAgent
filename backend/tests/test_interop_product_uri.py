"""产物绝对 URI 生成测试（针对 ACP_PUBLIC_BASE_URL 最小修复）。

覆盖：绝对 HTTP(S) URL、路径与下载端点一致、文件名 percent-encoding、
公开基址含端口/尾斜杠时的正确拼接、以及未配置公开基址时的本地回退。
"""
from pathlib import Path
from urllib.parse import urlsplit

from interop.aip_server import product_file_uri
from interop.config import AcpSettings


def test_product_uri_is_absolute_https_url():
    settings = AcpSettings(public_base_url="https://edu.example.com:9443")
    uri = product_file_uri(settings, "t1", "01_试卷.docx")
    parts = urlsplit(uri)
    assert parts.scheme == "https"
    assert parts.netloc == "edu.example.com:9443"
    assert parts.path == "/api/tasks/t1/files/01_%E8%AF%95%E5%8D%B7.docx"


def test_product_uri_path_matches_download_route():
    # 必须与 GET /api/tasks/{task_id}/files/{file_name} 路由一致
    settings = AcpSettings(public_base_url="http://localhost:8000")
    uri = product_file_uri(settings, "abc123", "report.pdf")
    assert urlsplit(uri).path == "/api/tasks/abc123/files/report.pdf"


def test_product_uri_percent_encodes_filename():
    settings = AcpSettings(public_base_url="http://localhost:8000")
    uri = product_file_uri(settings, "t1", "01 试卷/答案.docx")
    # 空格与斜杠都要编码，避免破坏 URL 路径结构
    assert urlsplit(uri).path.endswith(
        "/files/01%20%E8%AF%95%E5%8D%B7%2F%E7%AD%94%E6%A1%88.docx"
    )


def test_public_base_url_trailing_slash_is_stripped():
    settings = AcpSettings(public_base_url="https://edu.example.com:9443/")
    uri = product_file_uri(settings, "t1", "a.pdf")
    assert uri == "https://edu.example.com:9443/api/tasks/t1/files/a.pdf"
    assert "//api" not in uri


def test_public_base_url_without_port():
    settings = AcpSettings(public_base_url="https://edu.example.com")
    uri = product_file_uri(settings, "t1", "a.pdf")
    assert uri == "https://edu.example.com/api/tasks/t1/files/a.pdf"


def test_fallback_to_service_address_when_public_base_unset():
    settings = AcpSettings(service_host="10.0.0.5", service_port=8443)
    uri = product_file_uri(settings, "t1", "a.pdf")
    assert uri == "http://10.0.0.5:8443/api/tasks/t1/files/a.pdf"


def test_fallback_scheme_is_https_when_cert_present():
    settings = AcpSettings(
        service_host="h.example", service_port=9443, cert_path=Path("/tmp/c.pem")
    )
    uri = product_file_uri(settings, "t1", "a.pdf")
    assert uri.startswith("https://h.example:9443/")


def test_rpc_url_and_service_base_share_public_base():
    settings = AcpSettings(public_base_url="https://edu.example.com:9443")
    assert settings.service_base_url == "https://edu.example.com:9443"
    assert settings.rpc_url == "https://edu.example.com:9443/rpc"
