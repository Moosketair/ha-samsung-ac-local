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
    baseline_response = ("2.05", {"if": ["oic.if.r"], "rt": ["x.samsung.test"]})
    responses = [response]
    if state != "not_supported":
        responses.append(baseline_response)

    with patch.object(client, "_request_locked", side_effect=responses) as request:
        first = ACStatus()
        client._poll_remote_temperature(first)
        second = ACStatus()
        client._poll_remote_temperature(second)

    assert request.call_count == len(responses)
    request.assert_any_call(COAP_GET, Resource.REMOTE_TEMPERATURE.value)
    if state != "not_supported":
        request.assert_any_call(
            COAP_GET,
            Resource.REMOTE_TEMPERATURE.value,
            query="if=oic.if.baseline",
        )
        assert second.remote_temperature_metadata_status == "advertised"
        assert second.remote_temperature_interfaces == "oic.if.r"
        assert second.remote_temperature_types == "x.samsung.test"
    else:
        assert second.remote_temperature_metadata_status is None
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


def test_baseline_query_encoding_is_coap_option_15(
    client: SamsungACClient,
) -> None:
    """GET baseline uses URI-Query option with extended length, never a POST."""
    raw = client._build_message(
        COAP_GET, Resource.REMOTE_TEMPERATURE.value, query="if=oic.if.baseline"
    )
    assert raw[1] == COAP_GET
    query = b"if=oic.if.baseline"
    assert bytes([(4 << 4) | 13, len(query) - 13]) + query in raw


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (("2.05", {"x.com.samsung.da.temperature": ""}), "no_metadata"),
        (("4.04", None), "baseline_not_supported"),
        (("4.00", {}), "unexpected_response"),
    ],
)
def test_baseline_without_advertised_interfaces(
    client: SamsungACClient,
    reply: tuple[str, dict | None],
    expected: str,
) -> None:
    """No baseline metadata is not evidence that sensor injection is possible."""
    first = ("2.05", {"x.com.samsung.da.temperature": ""})
    with patch.object(client, "_request_locked", side_effect=[first, reply]) as request:
        status = ACStatus()
        client._poll_remote_temperature(status)
    assert request.call_count == 2
    assert status.remote_temperature_probe == "present_empty"
    assert status.remote_temperature_metadata_status == expected
    assert status.remote_temperature_interfaces is None
