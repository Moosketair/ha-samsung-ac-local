"""Regression tests for instantaneous power readings from Samsung OCF energy."""

from unittest.mock import patch

import pytest

from custom_components.samsung_ac_local.api import ACStatus, SamsungACClient


@pytest.fixture
def client() -> SamsungACClient:
    """Create an unconnected CoAP client for status parsing tests."""
    return SamsungACClient("192.168.1.100", "fake-cert", "fake-key")


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        ("685", "W", 685.0),
        ("0", "W", 0.0),
        ("1.25", "KW", 1250.0),
        ("-500", "W", None),
        ("invalid", "W", None),
        (None, "W", None),
    ],
)
def test_instantaneous_power(
    client: SamsungACClient,
    value: str | None,
    unit: str,
    expected: float | None,
) -> None:
    """Unknown/invalid values must never be reported as an idle compressor."""
    rep = {"x.com.samsung.da.cumulativePower": "485832"}
    if value is not None:
        rep["x.com.samsung.da.instantaneousPower"] = value
    rep["x.com.samsung.da.instantaneousPowerUnit"] = unit

    status = ACStatus()
    with patch.object(client, "_get", return_value=rep):
        client._poll_energy(status)

    assert status.energy_wh == 485832
    assert status.instantaneous_power_w == expected


def test_instantaneous_power_missing_resource(client: SamsungACClient) -> None:
    """Unavailable energy resource leaves power unknown."""
    status = ACStatus()
    with patch.object(client, "_get", return_value=None):
        client._poll_energy(status)

    assert status.instantaneous_power_w is None
