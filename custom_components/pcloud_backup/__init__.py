"""The pCloud Backup integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .api import PCloudAPI
from .auth import create_auth
from .backup import PCloudBackupAgent
from .const import CONF_PASSWORD, CONF_REGION, CONF_USERNAME, DOMAIN, PLATFORMS

try:
    from homeassistant.components.backup.manager import BackupManager
except ImportError:
    BackupManager = None

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up pCloud Backup from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Create auth instance
    region = entry.data.get(CONF_REGION, "us")
    
    # Support both OAuth2 (future) and digest auth (current)
    if "token" in entry.data:
        # OAuth2 authentication
        auth = create_auth(
            hass=hass,
            region=region,
            access_token=entry.data["token"]["access_token"],
        )
    else:
        # Digest authentication (current)
        auth = create_auth(
            hass=hass,
            region=region,
            username=entry.data[CONF_USERNAME],
            password=entry.data[CONF_PASSWORD],
        )

    # Create API instance
    api = PCloudAPI(hass=hass, region=region, auth=auth)

    # Verify connection
    try:
        await api.async_test_connection()
    except Exception as err:
        _LOGGER.error("Failed to connect to pCloud: %s", err)
        return False

    # Store API instance
    hass.data[DOMAIN][entry.entry_id] = api

    # Create backup agent
    # Backup agents register themselves automatically when instantiated
    # if the backup component is loaded (which it should be due to dependencies)
    backup_agent = PCloudBackupAgent(hass, entry.entry_id)
    hass.data[DOMAIN][f"{entry.entry_id}_backup_agent"] = backup_agent
    _LOGGER.info("Created pCloud backup agent")

    # Register update listener
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    # Forward entry setup to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        api = hass.data[DOMAIN].pop(entry.entry_id)
        if api:
            await api.async_close()

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)

