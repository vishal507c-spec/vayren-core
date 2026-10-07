"""Contract tests: envelope shape, validation, size/version discipline."""

import pytest

from remote.protocol import (
    ERROR_CODES,
    SCHEMA_VERSION,
    Envelope,
    ProtocolError,
    decode_message,
    encode_message,
    error_envelope,
    make_envelope,
)


def test_envelope_roundtrip_client_message() -> None:
    raw = {
        "type": "hello",
        "v": 1,
        "ts": "2026-10-06T00:00:00+00:00",
        "payload": {"token": "x" * 32},
    }
    message = decode_message(encode_message(raw), from_client=True)
    assert message.msg_type == "hello"
    assert message.version == 1
    assert message.payload == {"token": "x" * 32}


def test_envelope_roundtrip_server_message_with_seq() -> None:
    wire = make_envelope("event", {"name": "FILL"}, seq=7)
    message = decode_message(encode_message(wire), from_client=False)
    assert (message.msg_type, message.seq) == ("event", 7)


def test_wire_object_carries_metadata() -> None:
    wire = make_envelope("command_result", {"ok": True}, seq=3, request_id="r-1")
    assert wire["v"] == SCHEMA_VERSION
    assert wire["seq"] == 3
    assert wire["request_id"] == "r-1"
    assert isinstance(wire["ts"], str) and wire["ts"]


def test_rejects_non_object() -> None:
    with pytest.raises(ProtocolError):
        decode_message(b"[1,2]", from_client=True)


def test_rejects_invalid_json() -> None:
    with pytest.raises(ProtocolError) as exc:
        decode_message(b"{nope", from_client=True)
    assert exc.value.code == "BAD_MESSAGE"


def test_rejects_non_utf8() -> None:
    with pytest.raises(ProtocolError):
        decode_message(b"\xff\xfe", from_client=True)


def test_rejects_unknown_type() -> None:
    with pytest.raises(ProtocolError):
        decode_message(b'{"type":"trade_now","v":1,"payload":{}}', from_client=True)


def test_rejects_server_type_from_client() -> None:
    with pytest.raises(ProtocolError):
        decode_message(b'{"type":"snapshot","v":1,"payload":{}}', from_client=True)


def test_rejects_unsupported_version() -> None:
    with pytest.raises(ProtocolError) as exc:
        decode_message(b'{"type":"hello","v":99,"payload":{}}', from_client=True)
    assert exc.value.code == "UNSUPPORTED_VERSION"


def test_rejects_oversize_message() -> None:
    with pytest.raises(ProtocolError):
        decode_message(b"x" * (1_000_000 + 1), from_client=True)


def test_rejects_bad_payload_and_seq() -> None:
    with pytest.raises(ProtocolError):
        Envelope.from_dict({"type": "hello", "v": 1, "payload": [1]}, from_client=True)
    with pytest.raises(ProtocolError):
        Envelope.from_dict({"type": "hello", "v": 1, "payload": {}, "seq": -1}, from_client=True)


def test_make_envelope_rejects_unknown_server_type() -> None:
    with pytest.raises(ProtocolError):
        make_envelope("hello", {})


def test_error_envelope_shape_and_codes() -> None:
    wire = error_envelope("NOT_AUTHORIZED", "nope", request_id="r-9")
    assert wire["type"] == "error"
    assert wire["payload"] == {"code": "NOT_AUTHORIZED", "detail": "nope"}
    assert wire["request_id"] == "r-9"
    assert error_envelope("ANYTHING", "x")["payload"]["code"] == "SERVER_ERROR"
    assert set(ERROR_CODES) >= {
        "NOT_AUTHENTICATED",
        "AUTH_FAILED",
        "NOT_AUTHORIZED",
        "BAD_MESSAGE",
        "UNSUPPORTED_VERSION",
        "UNKNOWN_COMMAND",
        "COMMAND_REJECTED",
        "RATE_LIMITED",
        "SERVER_ERROR",
    }
