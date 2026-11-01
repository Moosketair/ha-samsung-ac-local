"""Tests for the read-only standard OCF energy power fallback."""

from unittest.mock import patch

import pytest

from custom_components.samsung_ac_local.api import ACStatus, SamsungACClient
from custom_components.samsung_ac_local.const import Resource


@pytest.fixture
def client() -> SamsungACClient:
    return SamsungACClient("192.168.1.100", "fake-cert", "fake-key")


def test_vendor_power_is_preferred(client: SamsungACClient) -> None:
    data = {
        Resource.ENERGY: {
            "x.com.samsung.da.instantaneousPower": "126.0",
            "x.com.samsung.da.instantaneousPowerUnit": "W",
            "x.com.samsung.da.cumulativePower": "485964",
        }
    }
    with patch.object(client, "_get", side_effect=data.get) as read:
        status = ACStatus()
        client._poll_energy(status)

    assert status.energy_wh == 485964
    assert status.instantaneous_power_w == 126.0
    assert status.instantaneous_power_source == "samsung_vendor"
    read.assert_called_once_with(Resource.ENERGY)


def test_falls_back_to_standard_endpoint(client: SamsungACClient) -> None:
    data = {
        Resource.ENERGY: {
            "x.com.samsung.da.cumulativePower": "485964",
        },
        Resource.ENERGY_STANDARD: {"power": 338.0},
    }
    with patch.object(client, "_get", side_effect=data.get) as read:
        status = ACStatus()
        client._poll_energy(status)

    assert status.energy_wh == 485964
    assert status.instantaneous_power_w == 338.0
    assert status.instantaneous_power_source == "ocf_standard"
    assert read.call_count == 2
    read.assert_any_call(Resource.ENERGY_STANDARD)


@pytest.mark.parametrize("standard", [None, {}, {"power": None}, {"power": "nan"}])
def test_missing_standard_power_does_not_fabricate_activity(
    client: SamsungACClient, standard: dict | None
) -> None:
    data = {
        Resource.ENERGY: {"x.com.samsung.da.cumulativePower": "485964"},
        Resource.ENERGY_STANDARD: standard,
    }
    with patch.object(client, "_get", side_effect=data.get):
        status = ACStatus()
        client._poll_energy(status)

    assert status.energy_wh == 485964
    assert status.instantaneous_power_w is None
    assert status.instantaneous_power_source is None


@pytest.mark.parametrize("invalid", ["-500", "65278", "65535", "inf", "nan"])
def test_vendor_invalid_power_uses_standard_fallback(
    client: SamsungACClient, invalid: str
) -> None:
    data = {
        Resource.ENERGY: {
            "x.com.samsung.da.instantaneousPower": invalid,
            "x.com.samsung.da.cumulativePower": "485964",
        },
        Resource.ENERGY_STANDARD: {"power": "90"},
    }
    with patch.object(client, "_get", side_effect=data.get):
        status = ACStatus()
        client._poll_energy(status)

    assert status.instantaneous_power_w == 90
    assert status.instantaneous_power_source == "ocf_standard"
