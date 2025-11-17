"""The pCloud Backup integration."""
from __future__ import annotations

import logging

from pathlib import Path

from homeassistant.components import frontend
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.components.http import StaticPathConfig

from .api import PCloudAPI
from .auth import create_auth
from .const import (
    CONF_REGION,
    DATA_BACKUP_AGENT_LISTENERS,
    DOMAIN,
    PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)

ICON_MODULE_URL = "/pcloud-backup/icon_patch.js"
ICON_MODULE_NAME = "__icon_module_registered__"


@callback
def _notify_backup_agent_listeners(hass: HomeAssistant) -> None:
    """Notify backup agent listeners about changes."""
    for listener in hass.data.get(DATA_BACKUP_AGENT_LISTENERS, []):
        listener()


async def _ensure_frontend_module(hass: HomeAssistant) -> None:
    """Expose the frontend helper module exactly once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(ICON_MODULE_NAME):
        return

    source_path = Path(__file__).parent / "frontend" / "icon_patch.js"

    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                ICON_MODULE_URL,
                str(source_path),
                cache_headers=False,
            )
        ]
    )

    frontend.add_extra_js_url(hass, ICON_MODULE_URL)
    domain_data[ICON_MODULE_NAME] = True


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old config entries to new format."""
    if config_entry.version == 1:
        # Old entries used digest auth (username/password)
        # These need to be removed and re-added with OAuth2
        _LOGGER.warning(
            "Config entry %s uses old digest authentication. "
            "Please remove and re-add the integration to use OAuth2.",
            config_entry.title
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
        access_token=entry.data["token"]["access_token"],
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
    entry.runtime_data = api

    await _ensure_frontend_module(hass)

    # Notify backup manager listeners that agents may have changed
    _notify_backup_agent_listeners(hass)

    # Register update listener
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    # Forward entry setup to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        api = hass.data[DOMAIN].pop(entry.entry_id, None)
        if api:
            await api.async_close()
        entry.runtime_data = None
        _notify_backup_agent_listeners(hass)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)