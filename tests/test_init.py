"""Tests for setting up, unloading and migrating the pCloud Backup integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.backup import DATA_MANAGER
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pcloud_backup import async_migrate_entry
from custom_components.pcloud_backup.api import PCloudAPI, PCloudAPIError
from custom_components.pcloud_backup.backup import (
    PCloudBackupAgent,
    async_get_backup_agents,
    async_register_backup_agents_listener,
)
from custom_components.pcloud_backup.const import (
    CONF_BACKUP_FOLDER,
    DATA_BACKUP_AGENT_LISTENERS,
    DOMAIN,
)

from .common import AGENT_ID, ENTRY_ID


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> bool:
    entry.add_to_hass(hass)
    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return result


def _backup_agent_ids(hass: HomeAssistant) -> set[str]:
    return set(hass.data[DATA_MANAGER].backup_agents)


async def test_setup_entry(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_pcloud: dict[str, AsyncMock]
) -> None:
    """Setup stores the API, sets runtime_data and forwards the sensor platform."""
    assert await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    api = hass.data[DOMAIN][ENTRY_ID]
    assert isinstance(api, PCloudAPI)
    assert config_entry.runtime_data is api
    assert api.region == "eu"
    mock_pcloud["async_test_connection"].assert_awaited_once()
    entities = er.async_entries_for_config_entry(er.async_get(hass), config_entry.entry_id)
    assert {entity.unique_id for entity in entities} == {
        f"{ENTRY_ID}_{key}"
        for key in (
            "remote_backup_count",
            "last_remote_backup",
            "last_sync_status",
            "free_space",
            "used_space",
            "account_used_space",
        )
    }


async def test_setup_entry_missing_token(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_pcloud: dict[str, AsyncMock]
) -> None:
    """Entries without an OAuth2 token fail setup without contacting pCloud."""
    data = dict(config_entry.data)
    del data["token"]
    entry = MockConfigEntry(domain=DOMAIN, entry_id=ENTRY_ID, version=2, data=data)

    assert not await _setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    mock_pcloud["async_test_connection"].assert_not_awaited()
    assert ENTRY_ID not in hass.data.get(DOMAIN, {})


async def test_setup_entry_connection_failure(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_pcloud: dict[str, AsyncMock]
) -> None:
    """A failing connection test makes setup return False."""
    mock_pcloud["async_test_connection"].side_effect = PCloudAPIError("unreachable")

    assert not await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert ENTRY_ID not in hass.data.get(DOMAIN, {})


async def test_unload_entry(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_pcloud: dict[str, AsyncMock]
) -> None:
    """Unloading closes the API, clears state and notifies backup listeners."""
    assert await _setup(hass, config_entry)
    listener = MagicMock()
    remove = async_register_backup_agents_listener(hass, listener=listener)

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert ENTRY_ID not in hass.data[DOMAIN]
    # HA removes runtime_data after unload; the integration also resets it.
    assert getattr(config_entry, "runtime_data", None) is None
    mock_pcloud["async_close"].assert_awaited_once()
    listener.assert_called()
    remove()


async def test_migrate_v1_entry_fails(hass: HomeAssistant, mock_pcloud) -> None:
    """Legacy digest-auth (v1) entries cannot be migrated and must be re-added."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={"username": "user@example.com", "password": "x", "region": "us"},
    )

    assert not await _setup(hass, entry)

    assert entry.state is ConfigEntryState.MIGRATION_ERROR
    mock_pcloud["async_test_connection"].assert_not_awaited()


async def test_migrate_current_version_is_noop(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Current (v2) entries need no migration."""
    assert await async_migrate_entry(hass, config_entry) is True


# --- Backup agent registration ---------------------------------------------


async def test_get_backup_agents_only_for_loaded_entries(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_pcloud
) -> None:
    """Agents are only returned for loaded entries that have an API."""
    config_entry.add_to_hass(hass)
    assert await async_get_backup_agents(hass) == []

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    (agent,) = await async_get_backup_agents(hass)
    assert isinstance(agent, PCloudBackupAgent)
    assert agent.slug == AGENT_ID
    assert agent.name == config_entry.title
    assert agent.api is config_entry.runtime_data


async def test_backup_agents_listener_registration(hass: HomeAssistant) -> None:
    """Listeners are stored in hass.data and fully removed again."""
    first, second = MagicMock(), MagicMock()
    remove_first = async_register_backup_agents_listener(hass, listener=first)
    remove_second = async_register_backup_agents_listener(hass, listener=second)
    assert hass.data[DATA_BACKUP_AGENT_LISTENERS] == [first, second]

    remove_first()
    assert hass.data[DATA_BACKUP_AGENT_LISTENERS] == [second]
    remove_first()  # removing twice is harmless
    remove_second()
    assert DATA_BACKUP_AGENT_LISTENERS not in hass.data


async def test_agent_registered_with_backup_manager(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_pcloud
) -> None:
    """The agent is registered with the backup manager after setup."""
    assert await async_setup_component(hass, "backup", {})
    assert await _setup(hass, config_entry)

    assert AGENT_ID in _backup_agent_ids(hass)


async def test_agent_survives_options_reload(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_pcloud
) -> None:
    """Regression #28: the agent stays registered after an options-triggered reload."""
    assert await async_setup_component(hass, "backup", {})
    assert await _setup(hass, config_entry)
    assert AGENT_ID in _backup_agent_ids(hass)

    hass.config_entries.async_update_entry(
        config_entry, options={CONF_BACKUP_FOLDER: "/New Folder"}
    )
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert AGENT_ID in _backup_agent_ids(hass), "agent vanished after options reload"

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert AGENT_ID not in _backup_agent_ids(hass)
