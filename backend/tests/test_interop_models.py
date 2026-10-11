"""interop 数据契约单测（离线，不依赖官方 SDK / 网络）。

对照已核实 schema（acps_sdk 源码）逐字段断言：
- AIC：10 级点分结构、前缀、Base36 段、本体/实体判定。
- ACS：构建产物字段齐全、protocolVersion 02.02、三条 skill、不含学校信息。
- AIP：TaskCommand/TaskResult/Rpc* 线格式（camelCase）。
- AMP：顶层 snake_case、body camelCase（见 test_interop_amp.py 详测）。
"""
from datetime import datetime, timezone

from interop.acs_builder import build_acs, validate_acs, PROTOCOL_VERSION, SKILLS
from interop.aic_util import (
    AIC_PREFIX,
    base36_encode,
    is_entity_aic,
    is_ontology_aic,
    parse_aic,
    validate_aic_format,
)
from interop.config import AcpSettings
from interop.models import (
    FileDataItem,
    RpcRequest,
    RpcRequestParams,
    TaskCommand,
    TaskCommandType,
    TextDataItem,
    to_wire,
)

VALID_AIC = "1.2.156.3088.1.ABC.XYZ.12345.0000.ABCD"


# ---------- AIC ----------

def test_aic_valid_format():
    ok, err = validate_aic_format(VALID_AIC)
    assert ok is True and err is None


def test_aic_rejects_wrong_prefix():
    ok, err = validate_aic_format("2.2.156.3088.1.ABC.XYZ.12345.0000.ABCD")
    assert ok is False and "前缀" in err


def test_aic_rejects_wrong_segment_count():
    ok, err = validate_aic_format("1.2.156.3088.1.ABC.XYZ.12345.0000")
    assert ok is False and "10 段" in err


def test_aic_rejects_invalid_base36():
    ok, err = validate_aic_format("1.2.156.3088.1.ABC.XYZ.12-45.0000.ABCD")
    assert ok is False and "Base36" in err


def test_parse_aic_fields():
    info = parse_aic(VALID_AIC)
    assert info is not None
    assert info.prefix == "1.2.156.3088"
    assert info.version == "1"
    assert info.arsp == "ABC"
    assert info.vendor == "XYZ"
    assert info.ontology_serial == "12345"
    assert info.instance_serial == "0000"
    assert info.checksum == "ABCD"
    assert info.normalized == VALID_AIC


def test_parse_aic_invalid_returns_none():
    assert parse_aic("not-an-aic") is None


def test_ontology_vs_entity():
    assert is_ontology_aic(VALID_AIC) is True
    assert is_entity_aic(VALID_AIC) is False
    entity = "1.2.156.3088.1.ABC.XYZ.12345.0001.ABCD"
    assert is_entity_aic(entity) is True
    assert is_ontology_aic(entity) is False


def test_base36_encode():
    assert base36_encode(0) == "0"
    assert base36_encode(35) == "Z"
    assert base36_encode(36) == "10"
    assert base36_encode(7, 4) == "0007"


def test_aic_prefix_constant():
    assert AIC_PREFIX == "1.2.156.3088"


# ---------- ACS ----------

def test_build_acs_required_fields():
    settings = AcpSettings()
    acs = build_acs(
        settings,
        aic=VALID_AIC,
        now=datetime(2026, 10, 10, tzinfo=timezone.utc),
    )
    assert validate_acs(acs) == []
    assert acs["aic"] == VALID_AIC
    assert acs["protocolVersion"] == PROTOCOL_VERSION == "02.02"
    assert acs["name"] == "EduMate-AIP"
    assert acs["active"] is True
    assert acs["endPoints"][0]["transport"] == "JSONRPC"
    assert acs["endPoints"][0]["url"].endswith("/rpc")


def test_build_acs_endpoint_uses_public_base_url():
    """设置 ACP_PUBLIC_BASE_URL 后，ACS endPoints.url 与 /rpc 服务地址一致。"""
    settings = AcpSettings(public_base_url="https://edu.example.com:9443")
    acs = build_acs(settings)
    assert acs["endPoints"][0]["url"] == "https://edu.example.com:9443/rpc"
    assert acs["endPoints"][0]["url"] == settings.rpc_url


def test_build_acs_endpoint_falls_back_to_service_address():
    """未设公开基址时，endPoints.url 回退到 service_host:service_port（无证书→http）。"""
    settings = AcpSettings(service_host="10.0.0.5", service_port=8443)
    acs = build_acs(settings)
    assert acs["endPoints"][0]["url"] == "http://10.0.0.5:8443/rpc"


def test_build_acs_endpoint_shares_public_base_with_product_uri():
    """ACS 端点与产物下载 URI 使用同一公开基址（同 scheme/host/port）。"""
    from urllib.parse import urlsplit

    from interop.aip_server import product_file_uri

    settings = AcpSettings(public_base_url="https://edu.example.com:9443")
    endpoint = urlsplit(build_acs(settings)["endPoints"][0]["url"])
    product = urlsplit(product_file_uri(settings, "t1", "a.pdf"))
    assert (endpoint.scheme, endpoint.netloc) == (product.scheme, product.netloc)
    assert endpoint.path == "/rpc"
    assert product.path == "/api/tasks/t1/files/a.pdf"


def test_build_acs_three_skills():
    acs = build_acs(AcpSettings())
    assert [s["id"] for s in acs["skills"]] == [
        "edumate.exam-loop",
        "edumate.question-gen",
        "edumate.lesson-plan",
    ]
    assert len(SKILLS) == 3


def test_build_acs_no_school_info():
    """材料要求：不得出现参赛团队学校名称 / LOGO / 指导教师。"""
    acs = build_acs(AcpSettings())
    blob = str(acs)
    assert acs["provider"]["organization"] == "EduMate 项目组"
    assert "大学" not in blob and "中学" not in blob and "小学" not in blob


def test_build_acs_empty_aic_placeholder():
    acs = build_acs(AcpSettings())  # 未传 aic → 占位空串，注册后由平台回填
    assert acs["aic"] == ""


def test_validate_acs_detects_missing_fields():
    problems = validate_acs({"name": "x"})
    assert problems and any("aic" in p for p in problems)


# ---------- AIP 线格式（camelCase） ----------

def test_dataitem_wire_camelcase():
    file_item = FileDataItem(name="试卷", mimeType="application/pdf", uri="/api/tasks/t1/files/a.pdf")
    wire = to_wire(file_item)
    assert wire["type"] == "file"
    assert wire["mimeType"] == "application/pdf"
    assert wire["uri"] == "/api/tasks/t1/files/a.pdf"


def test_task_command_roundtrip():
    cmd = TaskCommand(
        type="task-command",
        id="cmd-1",
        sentAt="2026-10-10T00:00:00Z",
        senderRole="leader",
        senderId="leader-aic",
        command=TaskCommandType.Start,
        taskId="t-1",
        sessionId="s-1",
        dataItems=[TextDataItem(text="请对《一元二次方程》做章节小测")],
    )
    rpc = RpcRequest(id=1, params=RpcRequestParams(command=cmd))
    parsed = RpcRequest.model_validate(rpc.model_dump())
    assert parsed.params.command.command == TaskCommandType.Start
    assert parsed.params.command.taskId == "t-1"
    assert parsed.params.command.dataItems[0].text == "请对《一元二次方程》做章节小测"
    assert parsed.jsonrpc == "2.0" and parsed.method == "rpc"
