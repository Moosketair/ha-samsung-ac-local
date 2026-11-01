"""Read-only remote temperature diagnostic tests.

These tests must never send a CoAP POST or synthesize a control capability.
"""

from unittest.mock import patch

import pytest

from custom_components.samsung_ac_local.api import (
    ACStatus,
    SamsungACClient,
    SamsungACRequestError,
)
from custom_components.samsung_ac_local.const import COAP_GET, Resource


@pytest.fixture
def client() -> SamsungACClient:
    return SamsungACClient("192.168.1.100", "fake-cert", "fake-key")


@pytest.mark.parametrize(
    ("response", "state", "value", "unit"),
    [
        (("4.04", None), "not_supported", None, None),
        (
            (
                "2.05",
                {
                    "x.com.samsung.da.temperature": "",
                    "x.com.samsung.da.unit": "",
                    "x.com.samsung.da.error": "",
                },
            ),
            "present_empty",
            "",
            "",
        ),
        (
            (
                "2.05",
                {
                    "x.com.samsung.da.temperature": "19.5",
                    "x.com.samsung.da.unit": "C",
                    "x.com.samsung.da.error": "",
                },
            ),
            "present_with_value",
            "19.5",
            "C",
        ),
    ],
)
def test_probe_is_get_only_and_cached(
    client: SamsungACClient,
    response: tuple[str, dict | None],
    state: str,
    value: str | None,
    unit: str | None,
) -> None:
    with patch.object(client, "_request_locked", return_value=response) as request:
        first = ACStatus()
        client._poll_remote_temperature(first)
        second = ACStatus()
        client._poll_remote_temperature(second)

    request.assert_called_once_with(COAP_GET, Resource.REMOTE_TEMPERATURE.value)
    assert first.remote_temperature_probe == state
    assert second.remote_temperature_probe == state
    assert second.remote_temperature_value == value
    assert second.remote_temperature_unit == unit


def test_transport_failure_does_not_mark_resource_unsupported(
    client: SamsungACClient,
) -> None:
    with patch.object(
        client, "_request_locked", side_effect=SamsungACRequestError("timeout")
    ) as request:
        status = ACStatus()
        client._poll_remote_temperature(status)

    assert status.remote_temperature_probe == "connection_error"
    assert request.call_count == 1
    assert client._remote_temperature_probe_cache is None


def test_unexpected_reply_does_not_cache(
    client: SamsungACClient,
) -> None:
    with patch.object(
        client, "_request_locked", return_value=("4.00", {})
    ):
        status = ACStatus()
        client._poll_remote_temperature(status)

    assert status.remote_temperature_probe == "unexpected_response"
    assert client._remote_temperature_probe_cache is None
