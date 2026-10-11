"""AMP（监控日志）字段契约单测。

重点断言官方要求的三个关键不变量：
- correlation_id == session_id（发送/接收双方一致）
- message_id 在 send / receive 两侧一致
- event_type 经 camelCase alias 落到 body["eventType"]
"""
import json

from interop.amp import AmpEmitter, JsonLinesSink, build_access_record, build_message_record

AIC = "1.2.156.3088.1.ABC.XYZ.12345.0001.ABCD"


def test_message_record_correlation_and_message_id():
    rec = build_message_record(
        event_type="send",
        system="rabbitmq",
        destination_name="group.edumate",
        message_id="m-100",
        aic=AIC,
        session_id="sess-42",
        payload_size_bytes=128,
    )
    assert rec["log_type"] == "message"
    assert rec["correlation_id"] == "sess-42"
    assert rec["aic"] == AIC
    assert rec["timestamp"] and rec["log_id"]

    body = rec["body"]
    assert body["eventType"] == "send"
    assert body["messageId"] == "m-100"
    assert body["system"] == "rabbitmq"
    assert body["payloadSizeBytes"] == 128
    assert body["destination"]["name"] == "group.edumate"


def test_send_receive_share_message_id_and_correlation():
    send = build_message_record(
        event_type="send", system="rabbitmq", destination_name="g",
        message_id="m-1", aic=AIC, session_id="sess-1",
    )
    recv = build_message_record(
        event_type="receive", system="rabbitmq", destination_name="g",
        message_id="m-1", aic=AIC, session_id="sess-1",
    )
    assert send["body"]["eventType"] == "send"
    assert recv["body"]["eventType"] == "receive"
    # 同一条消息的 send / receive 用相同 messageId + correlationId 归并
    assert send["body"]["messageId"] == recv["body"]["messageId"] == "m-1"
    assert send["correlation_id"] == recv["correlation_id"] == "sess-1"


def test_access_record_fields():
    rec = build_access_record(
        aic=AIC,
        method="POST",
        url="https://host/rpc",
        status_code=200,
        duration_ms=12.5,
        caller_aic="1.2.156.3088.1.ABC.XYZ.9999.0001.ABCD",
        caller_service="demo-leader",
        callee_service="EduMate-AIP",
        session_id="sess-9",
    )
    assert rec["log_type"] == "access"
    assert rec["correlation_id"] == "sess-9"
    body = rec["body"]
    assert body["durationMs"] == 12.5
    assert body["request"]["method"] == "POST"
    assert body["request"]["url"] == "https://host/rpc"
    assert body["response"]["statusCode"] == 200
    assert body["caller"]["aic"] == "1.2.156.3088.1.ABC.XYZ.9999.0001.ABCD"
    assert body["callee"]["aic"] == AIC


def test_jsonlines_sink_appends(tmp_path):
    sink = JsonLinesSink(tmp_path / "amp.jsonl")
    emitter = AmpEmitter(aic=AIC, sink=sink)
    emitter.emit_message(
        event_type="send", system="rabbitmq", destination_name="g",
        message_id="m-2", session_id="sess-2",
    )
    emitter.emit_access(method="POST", url="https://host/rpc", status_code=200)

    lines = (tmp_path / "amp.jsonl").read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    records = [json.loads(line) for line in lines]
    assert [r["log_type"] for r in records] == ["message", "access"]
    assert records[0]["correlation_id"] == "sess-2"
