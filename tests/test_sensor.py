"""Tests for the pCloud Backup sensors and their coordinator."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.pcloud_backup.api import PCloudAPIError, PCloudAuthError
from custom_components.pcloud_backup.backup import PCloudBackupAgent
from custom_components.pcloud_backup.const import DOMAIN

from .common import ENTRY_ID


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _state(hass: HomeAssistant, key: str) -> State:
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{ENTRY_ID}_{key}")
    assert entity_id is not None, key
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state


async def test_sensor_values(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_pcloud
) -> None:
    """The coordinator maps backups + userinfo to sensor states."""
    await _setup(hass, config_entry)

    assert _state(hass, "remote_backup_count").state == "2"
    assert _state(hass, "last_remote_backup").state == "2025-01-02T03:00:00+00:00"
    assert _state(hass, "last_sync_status").state == "OK"
    assert _state(hass, "last_sync_status").attributes.get("error") is None

    # Sizes are shown in binary units: 1 MiB + 512 KiB of backups,
    # 10 GiB quota with 2 GiB used.
    used = _state(hass, "used_space")
    assert (used.state, used.attributes["unit_of_measurement"]) == ("1.5", "MiB")
    free = _state(hass, "free_space")
    assert (free.state, free.attributes["unit_of_measurement"]) == ("8.0", "GiB")
    account = _state(hass, "account_used_space")
    assert (account.state, account.attributes["unit_of_measurement"]) == ("2.0", "GiB")


async def test_sensor_names_are_translated(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_pcloud
) -> None:
    """Sensor names come from the entity.sensor translations."""
    await _setup(hass, config_entry)

    assert _state(hass, "used_space").attributes["friendly_name"] == "Used Space by Backups"
    assert _state(hass, "remote_backup_count").attributes["friendly_name"] == "Remote Backup Count"


async def test_userinfo_failure_keeps_backup_sensors(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_pcloud
) -> None:
    """A failing userinfo call only blanks the quota sensors."""
    mock_pcloud["async_get_userinfo"].side_effect = PCloudAPIError("quota unavailable")

    await _setup(hass, config_entry)

    assert _state(hass, "remote_backup_count").state == "2"
    assert _state(hass, "last_sync_status").state == "OK"
    assert _state(hass, "free_space").state == STATE_UNKNOWN
    assert _state(hass, "account_used_space").state == STATE_UNKNOWN


async def test_api_error_does_not_break_setup(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_pcloud
) -> None:
    """An API error during the first refresh is reported via the status sensor."""
    with patch.object(
        PCloudBackupAgent,
        "async_list_backups",
        AsyncMock(side_effect=PCloudAPIError("listing failed")),
    ):
        await _setup(hass, config_entry)

    status = _state(hass, "last_sync_status")
    assert status.state == "Failed"
    assert status.attributes["error"] == "listing failed"
    assert _state(hass, "remote_backup_count").state == "0"


async def test_failed_refresh_keeps_previous_values(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_pcloud
) -> None:
    """A later failure keeps the last known values and flags the status."""
    await _setup(hass, config_entry)

    with patch.object(
        PCloudBackupAgent,
        "async_list_backups",
        AsyncMock(side_effect=RuntimeError("unexpected")),
    ):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=6))
        await hass.async_block_till_done()

    assert _state(hass, "last_sync_status").state == "Failed"
    assert _state(hass, "last_sync_status").attributes["error"] == "unexpected"
    assert _state(hass, "remote_backup_count").state == "2"
    assert _state(hass, "free_space").state == "8.0"


def _reauth_flows(hass: HomeAssistant) -> list:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"]["source"] == SOURCE_REAUTH
    ]


async def test_auth_error_on_userinfo_starts_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_pcloud
) -> None:
    """A rejected token during a refresh raises ConfigEntryAuthFailed (reauth)."""
    await _setup(hass, config_entry)
    mock_pcloud["async_get_userinfo"].side_effect = PCloudAuthError("Log in required.")

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=6))
    await hass.async_block_till_done()

    (flow,) = _reauth_flows(hass)
    assert flow["context"]["entry_id"] == ENTRY_ID
    assert _state(hass, "remote_backup_count").state == STATE_UNAVAILABLE


async def test_auth_error_on_listing_starts_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_pcloud
) -> None:
    """A rejected token while listing backups also starts reauth."""
    await _setup(hass, config_entry)
    mock_pcloud["async_get_folder_id"].side_effect = PCloudAuthError("Log in required.")

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=6))
    await hass.async_block_till_done()

    assert len(_reauth_flows(hass)) == 1
    assert _state(hass, "remote_backup_count").state == STATE_UNAVAILABLE


async def test_api_error_does_not_start_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_pcloud
) -> None:
    """Non-auth failures only flag the status sensor."""
    await _setup(hass, config_entry)
    mock_pcloud["async_get_userinfo"].side_effect = PCloudAPIError("timed out")
    mock_pcloud["async_get_folder_id"].side_effect = PCloudAPIError("timed out")

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=6))
    await hass.async_block_till_done()

    assert _reauth_flows(hass) == []
    assert _state(hass, "remote_backup_count").state != STATE_UNAVAILABLE
