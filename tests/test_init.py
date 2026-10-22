"""Tests for setting up, unloading and migrating the pCloud Backup integration."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.backup import DATA_MANAGER
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pcloud_backup import async_migrate_entry
from custom_components.pcloud_backup.api import PCloudAPI, PCloudAPIError, PCloudAuthError
from custom_components.pcloud_backup.backup import (
    BackupMetadataCache,
    PCloudBackupAgent,
    async_get_backup_agents,
    async_register_backup_agents_listener,
)
from custom_components.pcloud_backup.const import (
    CONF_BACKUP_FOLDER,
    DATA_BACKUP_AGENT_LISTENERS,
    DATA_METADATA_CACHE,
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
    """A failing connection test makes HA retry setup later (single attempt per try)."""
    mock_pcloud["async_test_connection"].side_effect = PCloudAPIError("unreachable")

    assert not await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    mock_pcloud["async_test_connection"].assert_awaited_once_with(retry=False)
    mock_pcloud["async_close"].assert_awaited_once()
    assert ENTRY_ID not in hass.data.get(DOMAIN, {})


async def test_setup_entry_auth_failure_starts_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_pcloud: dict[str, AsyncMock]
) -> None:
    """A rejected token fails setup and starts the reauth flow."""
    mock_pcloud["async_test_connection"].side_effect = PCloudAuthError(
        "pCloud authentication failed: Log in required."
    )

    assert not await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    (flow,) = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert flow["context"]["source"] == SOURCE_REAUTH
    assert flow["context"]["entry_id"] == ENTRY_ID
    assert flow["step_id"] == "reauth_confirm"


async def test_setup_entry_connection_failure_does_not_start_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_pcloud: dict[str, AsyncMock]
) -> None:
    """Non-auth connection failures never start reauth."""
    mock_pcloud["async_test_connection"].side_effect = PCloudAPIError("timed out")

    assert not await _setup(hass, config_entry)

    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


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
    assert ENTRY_ID not in hass.data[DATA_METADATA_CACHE]
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


async def test_metadata_cache_lifecycle(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_pcloud: dict[str, AsyncMock]
) -> None:
    """Setup creates a per-entry metadata cache; unload drops it, reload starts empty."""
    assert await _setup(hass, config_entry)
    cache = hass.data[DATA_METADATA_CACHE][ENTRY_ID]
    assert isinstance(cache, BackupMetadataCache)
    # The sensor refresh during setup already listed the backups and cached metadata.
    assert len(cache) == 1
    mock_pcloud["async_download_file"].assert_awaited_once()

    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    new_cache = hass.data[DATA_METADATA_CACHE][ENTRY_ID]
    assert new_cache is not cache
    assert len(cache) == 0
    assert mock_pcloud["async_download_file"].await_count == 2


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


# --- Persistent metadata cache (option) -------------------------------------


async def test_metadata_cache_persisted_when_enabled(
    hass: HomeAssistant, hass_storage, config_entry: MockConfigEntry, mock_pcloud
) -> None:
    """With the option on, parsed metadata is stored and reused after a reload."""
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    from custom_components.pcloud_backup.const import (
        CONF_CACHE_METADATA,
        METADATA_CACHE_SAVE_DELAY,
    )

    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_CACHE_METADATA: True}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    mock_pcloud["async_download_file"].assert_awaited_once()

    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=METADATA_CACHE_SAVE_DELAY + 1)
    )
    await hass.async_block_till_done()
    key = f"{DOMAIN}.metadata_cache.{ENTRY_ID}"
    assert len(hass_storage[key]["data"]["entries"]) == 1

    # Simulate a Home Assistant restart: the in-memory cache is gone, the stored one is loaded.
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.data[DATA_METADATA_CACHE][ENTRY_ID]) == 1
    mock_pcloud["async_download_file"].assert_awaited_once()


async def test_metadata_cache_store_removed_when_disabled(
    hass: HomeAssistant, hass_storage, config_entry: MockConfigEntry, mock_pcloud
) -> None:
    """With the option off (default), a stored cache is removed and metadata is fetched fresh."""
    key = f"{DOMAIN}.metadata_cache.{ENTRY_ID}"
    hass_storage[key] = {"version": 1, "minor_version": 1, "key": key, "data": {"entries": []}}

    assert await _setup(hass, config_entry)

    assert key not in hass_storage
    mock_pcloud["async_download_file"].assert_awaited_once()


async def test_metadata_cache_remove_failure_does_not_block_setup(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_pcloud, monkeypatch, caplog
) -> None:
    """A stored cache that cannot be removed is reported, but setup still succeeds."""
    store = MagicMock()
    store.async_remove = AsyncMock(side_effect=PermissionError("read-only"))
    monkeypatch.setattr("custom_components.pcloud_backup._metadata_store", lambda *_: store)

    assert await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    assert "Could not remove stored pCloud metadata cache" in caplog.text


def test_metadata_cache_round_trip_and_change_hook() -> None:
    """as_data/load restore the entries; set and a pruning prune report a change."""
    changes: list[int] = []
    cache = BackupMetadataCache(on_change=lambda: changes.append(1))
    cache.set(1, "h1", {"a": 1})
    cache.prune([1])  # nothing stale -> no change
    assert len(changes) == 1
    cache.set(2, "h2", {"b": 2})
    cache.prune([2])  # drops 1
    assert len(changes) == 3

    restored = BackupMetadataCache()
    restored.load(cache.as_data())
    assert restored.get(2, "h2") == {"b": 2}
    assert restored.get(1, "h1") is None

    broken = BackupMetadataCache()
    broken.load({"entries": [{"file_id": 3}, "x", {"file_id": 4, "hash": "h", "payload": []}]})
    broken.load(None)
    assert len(broken) == 0


async def _setup_with_cache(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    from custom_components.pcloud_backup.const import CONF_CACHE_METADATA

    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_CACHE_METADATA: True}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_metadata_cache_reload_before_save_delay_keeps_entries(
    hass: HomeAssistant, hass_storage, config_entry: MockConfigEntry, mock_pcloud
) -> None:
    """A reload within the save delay flushes the cache instead of losing or blanking it."""
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    await _setup_with_cache(hass, config_entry)
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=1))
    await hass.async_block_till_done()
    key = f"{DOMAIN}.metadata_cache.{ENTRY_ID}"
    assert len(hass_storage[key]["data"]["entries"]) == 1
    assert len(hass.data[DATA_METADATA_CACHE][ENTRY_ID]) == 1
    mock_pcloud["async_download_file"].assert_awaited_once()


async def test_metadata_cache_disable_before_save_delay_leaves_no_file(
    hass: HomeAssistant, hass_storage, config_entry: MockConfigEntry, mock_pcloud
) -> None:
    """Turning the option off right after a listing does not let an old writer recreate the file."""
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    from custom_components.pcloud_backup.const import CONF_CACHE_METADATA

    await _setup_with_cache(hass, config_entry)
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_CACHE_METADATA: False}
    )
    await hass.async_block_till_done()

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=1))
    await hass.async_block_till_done()
    assert f"{DOMAIN}.metadata_cache.{ENTRY_ID}" not in hass_storage


async def test_metadata_cache_removed_with_entry(
    hass: HomeAssistant, hass_storage, config_entry: MockConfigEntry, mock_pcloud
) -> None:
    """Deleting the config entry deletes its stored metadata cache."""
    await _setup_with_cache(hass, config_entry)
    assert await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert f"{DOMAIN}.metadata_cache.{ENTRY_ID}" not in hass_storage


def test_metadata_cache_json_round_trip_keeps_types() -> None:
    """Integer file ids and large pCloud hashes survive a JSON round trip unchanged."""
    import json

    cache = BackupMetadataCache()
    cache.set(108332615239, 2222222222222222222, {"backup": {"name": "x"}})
    restored = BackupMetadataCache()
    restored.load(json.loads(json.dumps(cache.as_data())))
    assert restored.get(108332615239, 2222222222222222222) == {"backup": {"name": "x"}}
