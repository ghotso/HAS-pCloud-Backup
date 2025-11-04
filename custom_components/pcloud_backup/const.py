"""Constants for the pCloud Backup integration."""
from __future__ import annotations

DOMAIN = "pcloud_backup"

# API Endpoints
API_BASE_US = "https://api.pcloud.com"
API_BASE_EU = "https://eapi.pcloud.com"

# OAuth2
OAUTH2_AUTHORIZE = "https://my.pcloud.com/oauth2/authorize"
OAUTH2_TOKEN = "https://api.pcloud.com/oauth2_token"

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

