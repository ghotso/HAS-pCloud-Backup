"""The pCloud Backup integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.event import async_call_later

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
    backup_agent = PCloudBackupAgent(hass, entry.entry_id)
    hass.data[DOMAIN][f"{entry.entry_id}_backup_agent"] = backup_agent
    
    # Register backup agent after startup is complete
    # The backup manager may not be ready during integration setup
    async def register_backup_agent_at_startup(event: Event | None = None) -> None:
        """Register backup agent when Home Assistant has started."""
        try:
            # Method 1: Try to get backup manager from 'backup' key (most common)
            backup_manager = hass.data.get("backup")
            if backup_manager:
                # The backup manager might be a dict with 'manager' key
                if isinstance(backup_manager, dict):
                    manager = backup_manager.get("manager") or backup_manager.get("backup_manager")
                    if manager:
                        backup_manager = manager
                
                # Check if backup_agents is a collection we can add to
                if hasattr(backup_manager, "backup_agents"):
                    backup_agents = backup_manager.backup_agents
                    # Try to add the agent to the collection
                    if hasattr(backup_agents, "add"):
                        backup_agents.add(backup_agent)
                        _LOGGER.info("Registered pCloud backup agent via backup_agents.add()")
                        return
                    elif hasattr(backup_agents, "append"):
                        backup_agents.append(backup_agent)
                        _LOGGER.info("Registered pCloud backup agent via backup_agents.append()")
                        return
                    elif isinstance(backup_agents, dict):
                        # If it's a dict, use slug as key
                        backup_agents[backup_agent.slug] = backup_agent
                        _LOGGER.info("Registered pCloud backup agent via backup_agents dict")
                        return
                
                # Try standard registration methods
                if hasattr(backup_manager, "async_register_agent"):
                    await backup_manager.async_register_agent(backup_agent)
                    _LOGGER.info("Registered pCloud backup agent via async_register_agent")
                    return
                elif hasattr(backup_manager, "register_agent"):
                    backup_manager.register_agent(backup_agent)
                    _LOGGER.info("Registered pCloud backup agent via register_agent")
                    return
                elif hasattr(backup_manager, "add_agent") or hasattr(backup_manager, "async_add_agent"):
                    if hasattr(backup_manager, "async_add_agent"):
                        await backup_manager.async_add_agent(backup_agent)
                    else:
                        backup_manager.add_agent(backup_agent)
                    _LOGGER.info("Registered pCloud backup agent via add_agent")
                    return
                
                # If we get here, log debug info
                _LOGGER.warning("Could not find registration method. backup_agents type: %s, has add: %s, has append: %s",
                              type(getattr(backup_manager, "backup_agents", None)).__name__ if hasattr(backup_manager, "backup_agents") else "None",
                              hasattr(getattr(backup_manager, "backup_agents", None), "add") if hasattr(backup_manager, "backup_agents") else False,
                              hasattr(getattr(backup_manager, "backup_agents", None), "append") if hasattr(backup_manager, "backup_agents") else False)
            
            # Method 1b: Try to get backup manager from backup domain
            try:
                from homeassistant.components.backup import DOMAIN as BACKUP_DOMAIN
                backup_manager = hass.data.get(BACKUP_DOMAIN)
                if backup_manager:
                    if hasattr(backup_manager, "async_register_agent"):
                        await backup_manager.async_register_agent(backup_agent)
                        _LOGGER.info("Registered pCloud backup agent via backup domain")
                        return
                    elif hasattr(backup_manager, "register_agent"):
                        backup_manager.register_agent(backup_agent)
                        _LOGGER.info("Registered pCloud backup agent (sync)")
                        return
            except ImportError:
                pass
            except Exception as err:
                _LOGGER.debug("Method 1b failed: %s", err)
            
            # Method 2: Try direct component import
            try:
                from homeassistant.components.backup import async_register_backup_agent
                async_register_backup_agent(hass, backup_agent)
                _LOGGER.info("Registered pCloud backup agent via component function")
                return
            except ImportError:
                pass
            except Exception as err:
                _LOGGER.debug("Method 2 failed: %s", err)
            
            # Method 3: Try backup_manager key
            backup_manager = hass.data.get("backup_manager")
            if backup_manager:
                if hasattr(backup_manager, "async_register_agent"):
                    await backup_manager.async_register_agent(backup_agent)
                    _LOGGER.info("Registered pCloud backup agent via backup_manager key")
                    return
                elif hasattr(backup_manager, "register_agent"):
                    backup_manager.register_agent(backup_agent)
                    _LOGGER.info("Registered pCloud backup agent (sync)")
                    return
            
            # Debug: Log all backup-related keys in hass.data
            backup_keys = [k for k in hass.data.keys() if "backup" in str(k).lower()]
            _LOGGER.debug("Available backup-related keys in hass.data: %s", backup_keys)
            _LOGGER.warning("Backup manager not found. Keys: %s", backup_keys)
            
            # Fallback: try again after a delay
            _LOGGER.warning("Backup manager not available, trying delayed registration")
            async def retry_registration(_now):
                await register_backup_agent_at_startup(event)
            async_call_later(hass, 5, retry_registration)
        except AttributeError as err:
            _LOGGER.warning("Backup manager does not have register_agent method: %s", err)
        except Exception as err:
            _LOGGER.warning("Failed to register backup agent: %s", err)
    
    # Register when Home Assistant has fully started
    hass.bus.async_listen_once("homeassistant_started", register_backup_agent_at_startup)
    
    # Also try immediately in case Home Assistant is already started
    if hass.is_running:
        hass.async_create_task(register_backup_agent_at_startup(None))

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

