"""The pCloud Backup integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.storage import Store

from .api import PCloudAPI, PCloudAuthError
from .auth import create_auth
from .backup import BackupMetadataCache
from .const import (
    CONF_CACHE_METADATA,
    CONF_REGION,
    DATA_BACKUP_AGENT_LISTENERS,
    DATA_METADATA_CACHE,
    DEFAULT_CACHE_METADATA,
    DOMAIN,
    METADATA_CACHE_SAVE_DELAY,
    METADATA_CACHE_STORAGE_VERSION,
    PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)


@callback
def _notify_backup_agent_listeners(hass: HomeAssistant) -> None:
    """Notify backup agent listeners about changes."""
    for listener in hass.data.get(DATA_BACKUP_AGENT_LISTENERS, []):
        listener()


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old config entries to new format."""
    if config_entry.version == 1:
        # Old entries used digest auth (username/password)
        # These need to be removed and re-added with OAuth2
        _LOGGER.warning(
            "Config entry %s uses old digest authentication. "
            "Please remove and re-add the integration to use OAuth2.",
            config_entry.title,
        )
        # Return False to indicate migration failed (user needs to re-add)
        return False

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up pCloud Backup from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Create auth instance
    region = entry.data.get(CONF_REGION, "us")

    # OAuth2 authentication
    if "token" not in entry.data:
        _LOGGER.error("Config entry missing OAuth2 token. Please re-add the integration.")
        return False

    auth = create_auth(
        hass=hass,
        region=region,
        config_entry_id=entry.entry_id,
    )

    # Create API instance
    api = PCloudAPI(hass=hass, region=region, auth=auth)

    # Verify connection. Single attempt: on failure Home Assistant retries the
    # setup in the background (ConfigEntryNotReady) instead of blocking startup
    # and reloads with in-line retries, or leaving the entry failed for good.
    try:
        await api.async_test_connection(retry=False)
    except PCloudAuthError as err:
        # Revoked/invalid token: let Home Assistant start the reauth flow.
        await api.async_close()
        raise ConfigEntryAuthFailed(str(err)) from err
    except Exception as err:
        await api.async_close()
        raise ConfigEntryNotReady(f"Failed to connect to pCloud: {err}") from err

    # Store API instance
    hass.data[DOMAIN][entry.entry_id] = api
    entry.runtime_data = api
    # Backup agents are re-created whenever HA reloads agents; keep the parsed
    # metadata cache per entry so it survives that and is dropped on unload.
    hass.data.setdefault(DATA_METADATA_CACHE, {})[entry.entry_id] = await _async_metadata_cache(
        hass, entry
    )

    # Notify backup manager listeners on every state change. Notifying only
    # during setup is too early: the entry is still SETUP_IN_PROGRESS, so
    # async_get_backup_agents (which uses async_loaded_entries) would skip it
    # and the agent would disappear after a reload (e.g. options change).
    entry.async_on_unload(entry.async_on_state_change(lambda: _notify_backup_agent_listeners(hass)))

    # Register update listener
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    # Forward entry setup to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


def _metadata_store(hass: HomeAssistant, entry: ConfigEntry) -> Store[dict[str, Any]]:
    return Store(hass, METADATA_CACHE_STORAGE_VERSION, f"{DOMAIN}.metadata_cache.{entry.entry_id}")


async def _async_metadata_cache(hass: HomeAssistant, entry: ConfigEntry) -> BackupMetadataCache:
    """Create the metadata cache; optionally backed by persistent storage.

    With the option off (default) the cache lives in memory only and every Home Assistant
    start downloads the metadata files again; a previously stored copy is removed.
    """
    store = _metadata_store(hass, entry)
    if not entry.options.get(CONF_CACHE_METADATA, DEFAULT_CACHE_METADATA):
        try:
            await store.async_remove()
        except OSError as err:
            # Caching is off anyway; a leftover file must not block setup.
            _LOGGER.warning("Could not remove stored pCloud metadata cache: %s", err)
        return BackupMetadataCache()

    cache: BackupMetadataCache

    def _save() -> None:
        store.async_delay_save(cache.as_data, METADATA_CACHE_SAVE_DELAY)

    cache = BackupMetadataCache(on_change=_save)
    cache.store = store
    try:
        cache.load(await store.async_load())
    except Exception as err:  # noqa: BLE001 - a broken cache file must not block setup
        _LOGGER.warning("Ignoring unreadable pCloud metadata cache: %s", err)
    _LOGGER.debug("Loaded %d cached pCloud metadata entries", len(cache))
    return cache


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        api = hass.data[DOMAIN].pop(entry.entry_id, None)
        if api:
            await api.async_close()
        if (cache := hass.data.get(DATA_METADATA_CACHE, {}).pop(entry.entry_id, None)) is not None:
            if cache.store is not None:
                # Write the current entries now: this also cancels a pending delayed save,
                # so no writer from this (unloaded) cache can run later and overwrite or
                # recreate the file (e.g. after a reload or after disabling the option).
                await cache.store.async_save(cache.as_data())
            cache.clear()
        entry.runtime_data = None

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the persistent metadata cache of a deleted config entry."""
    await _metadata_store(hass, entry).async_remove()
