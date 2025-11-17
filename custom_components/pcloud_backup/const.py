"""Constants for the pCloud Backup integration."""
from __future__ import annotations

from collections.abc import Callable

from homeassistant.util.hass_dict import HassKey

DOMAIN = "pcloud_backup"

# API Endpoints
API_BASE_US = "https://api.pcloud.com"
API_BASE_EU = "https://eapi.pcloud.com"

# OAuth2
OAUTH2_AUTHORIZE = "https://my.pcloud.com/oauth2/authorize"
OAUTH2_TOKEN = "https://api.pcloud.com/oauth2_token"

# OAuth2 Client Credentials
# These should be set when you register your app with pCloud
# TODO: Replace with your actual client_id and client_secret from pCloud
OAUTH2_CLIENT_ID = "k6BcOqqGPmS"  # Set your client_id here
OAUTH2_CLIENT_SECRET = "9CXagIbK88kcoTRHvuiovXNH2rgV"  # Set your client_secret here

# Default folder for backups
DEFAULT_BACKUP_FOLDER = "/HomeAssistant/Backups"

# Platforms
PLATFORMS = ["sensor"]

# Configuration keys
CONF_REGION = "region"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_BACKUP_FOLDER = "backup_folder"

# Sensor attributes
ATTR_REMOTE_BACKUP_COUNT = "remote_backup_count"
ATTR_LAST_REMOTE_BACKUP = "last_remote_backup"
ATTR_LAST_SYNC_STATUS = "last_sync_status"

# Backup agent listener storage key
DATA_BACKUP_AGENT_LISTENERS: HassKey[list[Callable[[], None]]] = HassKey(
    f"{DOMAIN}.backup_agent_listeners"
)

