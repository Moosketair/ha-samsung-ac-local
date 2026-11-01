"""Tests for the Samsung AC Local coordinator."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.samsung_ac_local.api import (
    ACStatus,
    SamsungACConnectionError,
    SamsungACRequestError,
)
from custom_components.samsung_ac_local.const import FanMode, Power
from custom_components.samsung_ac_local.coordinator import (
    DEFAULT_POLL_INTERVAL,
    SamsungACCoordinator,
)

CUSTOM_POLL_INTERVAL = 60


@pytest.fixture
def coordinator(
    hass: HomeAssistant,
    mock_client: MagicMock,
) -> SamsungACCoordinator:
    """Return a SamsungACCoordinator with a mocked client."""
    return SamsungACCoordinator(hass, mock_client)


async def test_successful_update(
    hass: HomeAssistant,
    coordinator: SamsungACCoordinator,
    mock_client: MagicMock,
    mock_status: ACStatus,
) -> None:
    """Successful poll populates coordinator.data."""
    mock_executor = AsyncMock(return_value=mock_status)
    with patch.object(hass, "async_add_executor_job", mock_executor):
        result = await coordinator._async_update_data()

    mock_executor.assert_called_once_with(mock_client.get_status)
    assert result is mock_status
    assert result.current_temperature == mock_status.current_temperature
    assert result.target_temperature == mock_status.target_temperature


async def test_update_connection_error(
    hass: HomeAssistant,
    coordinator: SamsungACCoordinator,
) -> None:
    """SamsungACConnectionError raises UpdateFailed."""
    mock_executor = AsyncMock(
        side_effect=SamsungACConnectionError("Connection lost"),
    )
    with (
        patch.object(hass, "async_add_executor_job", mock_executor),
        pytest.raises(UpdateFailed, match="Error communicating with AC"),
    ):
        await coordinator._async_update_data()


async def test_update_request_error(
    hass: HomeAssistant,
    coordinator: SamsungACCoordinator,
) -> None:
    """SamsungACRequestError raises UpdateFailed."""
    mock_executor = AsyncMock(
        side_effect=SamsungACRequestError("Bad response"),
    )
    with (
        patch.object(hass, "async_add_executor_job", mock_executor),
        pytest.raises(UpdateFailed, match="Error communicating with AC"),
    ):
        await coordinator._async_update_data()


async def test_optimistic_update(
    coordinator: SamsungACCoordinator,
    mock_status: ACStatus,
) -> None:
    """async_update_optimistic replaces fields and notifies listeners."""
    coordinator.async_set_updated_data(mock_status)
    assert coordinator.data.target_temperature == mock_status.target_temperature
    assert coordinator.data.power is Power.ON

    coordinator.async_update_optimistic(target_temperature=25.0, power=Power.OFF)

    assert coordinator.data.target_temperature != mock_status.target_temperature
    assert coordinator.data.power is Power.OFF
    # Unchanged fields remain intact.
    assert coordinator.data.current_temperature == mock_status.current_temperature
    assert coordinator.data.fan_mode is FanMode.AUTO


async def test_default_poll_interval(
    coordinator: SamsungACCoordinator,
) -> None:
    """Coordinator uses the default poll interval."""
    assert coordinator.update_interval is not None
    assert coordinator.update_interval.total_seconds() == DEFAULT_POLL_INTERVAL


async def test_custom_poll_interval(
    hass: HomeAssistant,
    mock_client: MagicMock,
) -> None:
    """Custom poll interval is applied."""
    coord = SamsungACCoordinator(hass, mock_client, poll_interval=CUSTOM_POLL_INTERVAL)

    assert coord.update_interval is not None
    assert coord.update_interval.total_seconds() == CUSTOM_POLL_INTERVAL


async def test_coordinator_name(
    coordinator: SamsungACCoordinator,
    mock_client: MagicMock,
) -> None:
    """Coordinator name includes the client host."""
    assert mock_client.host in coordinator.name


async def test_failed_mode_poll_retains_known_modes(
    hass: HomeAssistant,
    coordinator: SamsungACCoordinator,
    mock_client: MagicMock,
    mock_status: ACStatus,
) -> None:
    """A temporary mode read failure must not remove HVAC controls."""
    from custom_components.samsung_ac_local.const import HvacMode

    known_modes = [HvacMode.HEAT, HvacMode.COOL]
    coordinator.async_set_updated_data(
        ACStatus(supported_modes=known_modes, current_temperature=21.0)
    )
    partial = ACStatus(supported_modes=[], current_temperature=None)
    with patch.object(
        hass, "async_add_executor_job", AsyncMock(return_value=partial)
    ):
        result = await coordinator._async_update_data()

    assert result.supported_modes == known_modes
    # An actual missing temperature is not fabricated by the coordinator.
    assert result.current_temperature is None
