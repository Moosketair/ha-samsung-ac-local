"""Regression tests for Samsung vendor setpoint fallback on OCF 4.04."""

from unittest.mock import patch

import pytest

from custom_components.samsung_ac_local.api import ACStatus, SamsungACClient
from custom_components.samsung_ac_local.const import Resource


@pytest.fixture
def client() -> SamsungACClient:
    """Create a client without making an actual DTLS connection."""
    return SamsungACClient("192.168.1.100", "fake-cert", "fake-key")


def test_standard_temperature_resource_is_preferred(client: SamsungACClient) -> None:
    """Keep using OCF where the device accepts it."""
    with (
        patch.object(client, "_post_with_code", return_value=(True, "2.04")) as post,
        patch.object(client, "_post") as fallback,
    ):
        assert client.set_target_temperature(21.0)

    post.assert_called_once_with(
        Resource.TEMPERATURE_DESIRED, {"temperature": 21.0}
    )
    fallback.assert_not_called()
    assert client._temperature_write_resource == Resource.TEMPERATURE_DESIRED


def test_404_falls_back_to_vendor_payload_and_caches(
    client: SamsungACClient,
) -> None:
    """Use Samsung's collection-based temperature write after 4.04."""
    expected = {
        "x.com.samsung.da.items": [
            {
                "x.com.samsung.da.id": "0",
                "x.com.samsung.da.desired": "22",
            }
        ]
    }
    with (
        patch.object(client, "_post_with_code", return_value=(False, "4.04")) as ocf,
        patch.object(client, "_post", return_value=True) as vendor,
    ):
        assert client.set_target_temperature(22.0)
        assert client.set_target_temperature(22.0)

    ocf.assert_called_once()
    assert vendor.call_count == 2
    vendor.assert_any_call(Resource.TEMPERATURES, expected)
    assert client._temperature_write_resource == Resource.TEMPERATURES


@pytest.mark.parametrize("code", ["4.00", "5.00", None, "2.04"])
def test_other_failures_do_not_repeat_write(
    client: SamsungACClient, code: str | None
) -> None:
    """Avoid duplicate commands after a potentially successful POST."""
    with (
        patch.object(client, "_post_with_code", return_value=(False, code)),
        patch.object(client, "_post") as fallback,
    ):
        assert not client.set_target_temperature(20.0)
    fallback.assert_not_called()


def test_failed_vendor_fallback_is_not_cached(client: SamsungACClient) -> None:
    """A rejected vendor write must be retried via OCF detection next time."""
    with (
        patch.object(client, "_post_with_code", return_value=(False, "4.04")),
        patch.object(client, "_post", return_value=False),
    ):
        assert not client.set_target_temperature(19.0)
    assert client._temperature_write_resource == Resource.TEMPERATURE_DESIRED


def test_vendor_temperature_read_recovers_missing_ocf_current(
    client: SamsungACClient,
) -> None:
    """The collection provides current and desired if OCF current is missing."""
    vendor = {
        "x.com.samsung.da.items": [
            {
                "x.com.samsung.da.id": "0",
                "x.com.samsung.da.current": "19.5",
                "x.com.samsung.da.desired": "21.0",
                "x.com.samsung.da.minimum": "16",
                "x.com.samsung.da.maximum": "30",
                "x.com.samsung.da.increment": "1",
            }
        ]
    }
    status = ACStatus()
    with patch.object(client, "_get", side_effect=[None, vendor]) as get:
        client._poll_temperatures(status)
    assert get.call_count == 2
    assert status.current_temperature == 19.5
    assert status.target_temperature == 21.0
    assert status.temperature_step == 1.0


def test_vendor_temperature_read_does_not_override_ocf_current(
    client: SamsungACClient,
) -> None:
    """Prefer OCF current when it is available and keep vendor desired."""
    ocf = {"temperature": 20.0}
    vendor = {
        "x.com.samsung.da.items": [
            {
                "x.com.samsung.da.current": "19.0",
                "x.com.samsung.da.desired": "22.0",
            }
        ]
    }
    status = ACStatus()
    with patch.object(client, "_get", side_effect=[ocf, vendor]):
        client._poll_temperatures(status)
    assert status.current_temperature == 20.0
    assert status.target_temperature == 22.0
