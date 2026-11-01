"""Regression tests for delayed Samsung CoAP responses and DTLS recovery."""

from unittest.mock import MagicMock, patch

import pytest

from custom_components.samsung_ac_local.api import (
    SamsungACClient,
    SamsungACRequestError,
)
from custom_components.samsung_ac_local.const import COAP_GET, COAP_POST

FAKE_CERT = "test cert"
FAKE_KEY = "test key"


def _response(token: int) -> bytes:
    """Build a CoAP 2.05 response carrying one token byte."""
    return bytes([0x61, 0x45, 0x00, token, token])


def test_three_stale_responses_do_not_exhaust_wait_budget() -> None:
    """The matching fourth response must be accepted within the deadline."""
    client = SamsungACClient("192.168.1.100", FAKE_CERT, FAKE_KEY)
    client._mid = 5
    client._conn = MagicMock()
    client._sock = MagicMock()
    client._conn.recv.side_effect = [
        _response(1),
        _response(2),
        _response(3),
        _response(5),
    ]
    with patch(
        "custom_components.samsung_ac_local.api.select.select",
        return_value=([client._sock], [], []),
    ):
        code, _data = client._receive_response()

    assert code == "2.05"
    assert client._conn.recv.call_count == 4


def test_invalid_coap_packet_is_skipped() -> None:
    """Malformed packets do not consume the budget as successful replies."""
    client = SamsungACClient("192.168.1.100", FAKE_CERT, FAKE_KEY)
    client._mid = 7
    client._conn = MagicMock()
    client._sock = MagicMock()
    client._conn.recv.side_effect = [b"\\x00\\x01", _response(7)]
    with patch(
        "custom_components.samsung_ac_local.api.select.select",
        return_value=([client._sock], [], []),
    ):
        code, _data = client._receive_response()
    assert code == "2.05"


def test_request_timeout_disconnects_and_get_is_retried() -> None:
    """Timed-out GETs are retried exactly once after connection reset."""
    client = SamsungACClient("192.168.1.100", FAKE_CERT, FAKE_KEY)
    client._conn = MagicMock()
    client._sock = MagicMock()
    with (
        patch.object(client, "_ensure_connected") as connect,
        patch.object(client, "_disconnect_locked") as disconnect,
        patch.object(
            client, "_receive_response",
            side_effect=[SamsungACRequestError("timeout"), ("2.05", {})],
        ) as recv,
    ):
        assert client._request_locked(COAP_GET, "mode/vs/0") == ("2.05", {})
    assert recv.call_count == 2
    assert connect.call_count == 2
    disconnect.assert_called_once()


def test_request_timeout_does_not_replay_post() -> None:
    """POST commands must not be resent if their reply is lost."""
    client = SamsungACClient("192.168.1.100", FAKE_CERT, FAKE_KEY)
    client._conn = MagicMock()
    client._sock = MagicMock()
    with (
        patch.object(client, "_ensure_connected") as connect,
        patch.object(client, "_disconnect_locked") as disconnect,
        patch.object(
            client, "_receive_response",
            side_effect=SamsungACRequestError("timeout"),
        ) as recv,
        pytest.raises(SamsungACRequestError, match="timeout"),
    ):
        client._request_locked(COAP_POST, "power/vs/0", {"power": "On"})
    recv.assert_called_once()
    connect.assert_called_once()
    disconnect.assert_called_once()
