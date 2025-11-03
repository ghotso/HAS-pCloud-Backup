"""Backup agent implementation for pCloud."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.backup import BackupAgent
from homeassistant.core import HomeAssistant

from .api import PCloudAPI, PCloudAPIError
from .const import (
    CONF_BACKUP_FOLDER,
    CONF_RETENTION_COUNT,
    CONF_RETENTION_DAYS,
    DEFAULT_BACKUP_FOLDER,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class BackupNotFound(Exception):
    """Raised when a backup is not found."""


class PCloudBackupAgent(BackupAgent):
    """pCloud Backup Agent implementation."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry_id: str,
    ) -> None:
        """Initialize the backup agent."""
        super().__init__(hass, DOMAIN)
        self.config_entry_id = config_entry_id
        self._api: PCloudAPI | None = None

    @property
    def api(self) -> PCloudAPI:
        """Get the pCloud API instance."""
        if self._api is None:
            # Get API from hass data
            api = self.hass.data.get(DOMAIN, {}).get(self.config_entry_id)
            if api is None:
                raise RuntimeError("pCloud API not initialized")
            self._api = api
        return self._api

    async def async_list_backups(self) -> list[dict[str, Any]]:
        """List all backups in pCloud."""
        try:
            config_entry = self.hass.config_entries.async_get_entry(self.config_entry_id)
            if config_entry is None:
                _LOGGER.error("Config entry not found")
                return []

            options = config_entry.options
            backup_folder = options.get(CONF_BACKUP_FOLDER, DEFAULT_BACKUP_FOLDER)

            # Get or create folder
            folder_id = await self.api.async_get_folder_id(backup_folder)

            # List files in folder
            files = await self.api.async_list_folder(folder_id)

            # Parse backup info
            backups = [self.api.parse_backup_info(file_item) for file_item in files]

            # Sort by modified date (newest first)
            backups.sort(key=lambda x: x.get("modified", ""), reverse=True)

            return backups

        except PCloudAPIError as err:
            _LOGGER.error("Failed to list backups: %s", err)
            return []
        except Exception as err:
            _LOGGER.exception("Unexpected error listing backups")
            return []

    async def async_upload_backup(self, backup_path: str, backup_name: str) -> None:
        """Upload a backup to pCloud."""
        try:
            config_entry = self.hass.config_entries.async_get_entry(self.config_entry_id)
            if config_entry is None:
                raise RuntimeError("Config entry not found")

            options = config_entry.options
            backup_folder = options.get(CONF_BACKUP_FOLDER, DEFAULT_BACKUP_FOLDER)

            # Get or create folder
            folder_id = await self.api.async_get_folder_id(backup_folder)

            # Read backup file
            with open(backup_path, "rb") as backup_file:
                backup_data = backup_file.read()

            # Upload to pCloud
            _LOGGER.info("Uploading backup %s to pCloud", backup_name)
            await self.api.async_upload_file(folder_id, backup_name, backup_data)
            _LOGGER.info("Successfully uploaded backup %s", backup_name)

            # Apply retention policy
            await self._apply_retention_policy(folder_id, options)

        except FileNotFoundError:
            _LOGGER.error("Backup file not found: %s", backup_path)
            raise
        except PCloudAPIError as err:
            _LOGGER.error("Failed to upload backup: %s", err)
            raise
        except Exception as err:
            _LOGGER.exception("Unexpected error uploading backup")
            raise

    async def async_download_backup(
        self, backup_name: str, backup_path: str
    ) -> None:
        """Download a backup from pCloud."""
        try:
            config_entry = self.hass.config_entries.async_get_entry(self.config_entry_id)
            if config_entry is None:
                raise RuntimeError("Config entry not found")

            options = config_entry.options
            backup_folder = options.get(CONF_BACKUP_FOLDER, DEFAULT_BACKUP_FOLDER)

            # Get folder ID
            folder_id = await self.api.async_get_folder_id(backup_folder)

            # List files to find the backup
            files = await self.api.async_list_folder(folder_id)

            # Find the backup file
            backup_file = None
            for file_item in files:
                if file_item.get("name") == backup_name:
                    backup_file = file_item
                    break

            if backup_file is None:
                raise BackupNotFound(f"Backup {backup_name} not found in pCloud")

            # Download file
            file_id = backup_file.get("fileid")
            if file_id is None:
                raise BackupNotFound(f"Backup {backup_name} has no file ID")

            _LOGGER.info("Downloading backup %s from pCloud", backup_name)
            backup_data = await self.api.async_download_file(file_id)

            # Write to local path
            with open(backup_path, "wb") as local_file:
                local_file.write(backup_data)

            _LOGGER.info("Successfully downloaded backup %s", backup_name)

        except BackupNotFound:
            raise
        except PCloudAPIError as err:
            _LOGGER.error("Failed to download backup: %s", err)
            raise BackupNotFound(f"Failed to download backup: {err}") from err
        except Exception as err:
            _LOGGER.exception("Unexpected error downloading backup")
            raise BackupNotFound(f"Unexpected error: {err}") from err

    async def async_delete_backup(self, backup_name: str) -> None:
        """Delete a backup from pCloud."""
        try:
            config_entry = self.hass.config_entries.async_get_entry(self.config_entry_id)
            if config_entry is None:
                raise RuntimeError("Config entry not found")

            options = config_entry.options
            backup_folder = options.get(CONF_BACKUP_FOLDER, DEFAULT_BACKUP_FOLDER)

            # Get folder ID
            folder_id = await self.api.async_get_folder_id(backup_folder)

            # List files to find the backup
            files = await self.api.async_list_folder(folder_id)

            # Find the backup file
            backup_file = None
            for file_item in files:
                if file_item.get("name") == backup_name:
                    backup_file = file_item
                    break

            if backup_file is None:
                raise BackupNotFound(f"Backup {backup_name} not found in pCloud")

            # Delete file
            file_id = backup_file.get("fileid")
            if file_id is None:
                raise BackupNotFound(f"Backup {backup_name} has no file ID")

            _LOGGER.info("Deleting backup %s from pCloud", backup_name)
            await self.api.async_delete_file(file_id)
            _LOGGER.info("Successfully deleted backup %s", backup_name)

        except BackupNotFound:
            raise
        except PCloudAPIError as err:
            _LOGGER.error("Failed to delete backup: %s", err)
            raise BackupNotFound(f"Failed to delete backup: {err}") from err
        except Exception as err:
            _LOGGER.exception("Unexpected error deleting backup")
            raise BackupNotFound(f"Unexpected error: {err}") from err

    async def _apply_retention_policy(
        self, folder_id: int, options: dict[str, Any]
    ) -> None:
        """Apply retention policy to backups."""
        try:
            retention_count = options.get(CONF_RETENTION_COUNT)
            retention_days = options.get(CONF_RETENTION_DAYS)

            if not retention_count and not retention_days:
                return

            # List all backups
            files = await self.api.async_list_folder(folder_id)
            backups = [self.api.parse_backup_info(file_item) for file_item in files]

            # Sort by modified date (oldest first for deletion)
            backups.sort(key=lambda x: x.get("modified", ""))

            to_delete: list[dict[str, Any]] = []

            if retention_count:
                # Keep only the last N backups
                if len(backups) > retention_count:
                    to_delete = backups[: len(backups) - retention_count]

            if retention_days:
                # Remove backups older than N days
                cutoff_date = datetime.now() - timedelta(days=retention_days)
                for backup in backups:
                    modified_str = backup.get("modified", "")
                    try:
                        modified_dt = datetime.fromisoformat(modified_str.replace("Z", "+00:00"))
                        if modified_dt.replace(tzinfo=None) < cutoff_date:
                            # Only add if not already marked for deletion
                            if backup not in to_delete:
                                to_delete.append(backup)
                    except (ValueError, TypeError):
                        _LOGGER.warning("Could not parse backup date: %s", modified_str)

            # Delete backups
            for backup in to_delete:
                file_id = backup.get("fileid")
                backup_name = backup.get("name", "unknown")
                if file_id:
                    try:
                        _LOGGER.info("Deleting old backup due to retention policy: %s", backup_name)
                        await self.api.async_delete_file(file_id)
                    except Exception as err:
                        _LOGGER.warning("Failed to delete old backup %s: %s", backup_name, err)

        except Exception as err:
            _LOGGER.warning("Error applying retention policy: %s", err)

