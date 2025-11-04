"""Backup agent implementation for pCloud."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

from homeassistant.components.backup import AgentBackup, BackupAgent
from homeassistant.core import HomeAssistant

from .api import PCloudAPI, PCloudAPIError
from .const import (
    CONF_BACKUP_FOLDER,
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
        self.hass = hass
        self.config_entry_id = config_entry_id
        self._api: PCloudAPI | None = None
        # Required attributes for BackupAgent
        self.domain = DOMAIN
        self.unique_id = config_entry_id
        self.name = "pCloud"
        # Slug must be in format "{domain}.{unique_id}" for Home Assistant backup system
        self.slug = f"{DOMAIN}.{config_entry_id}"

    @property
    def available(self) -> bool:
        """Return if the backup agent is available.
        
        This property must return True for the agent to be usable.
        Must be synchronous - no async calls allowed.
        
        The agent is considered available if the integration domain exists
        in hass.data, meaning the integration has been loaded. The API
        will be lazy-loaded when needed via the api property.
        """
        try:
            # Fast path: If API is cached, we're definitely available
            if self._api is not None:
                return True
            
            # Check if domain exists in hass.data (integration is loaded)
            # If domain exists, the integration is set up and agent should be available
            domain_data = self.hass.data.get(DOMAIN)
            if domain_data is None:
                # Domain not loaded yet
                _LOGGER.debug("Backup agent %s not available - domain not loaded", self.config_entry_id)
                return False
            
            # Domain exists - integration is loaded, so agent is available
            # API will be loaded lazily when needed
            return True
        except Exception as err:
            _LOGGER.error("Error checking backup agent availability: %s", err, exc_info=True)
            # On error, return False to be safe
            return False

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

    def _extract_backup_filename(self, backup_name_or_id: str) -> str:
        """Extract the actual filename from backup_id or backup_name.
        
        If backup_name_or_id is in format 'slug:filename', extract filename.
        Otherwise return as-is.
        """
        if ":" in backup_name_or_id:
            # Format: "pcloud_backup.01K9504EC5X6R90WCNA877EABZ:testbup2.tar"
            # Extract the part after the colon
            return backup_name_or_id.split(":", 1)[1]
        return backup_name_or_id

    async def async_get_backup(self, backup_name: str) -> AgentBackup | None:
        """Get backup information by name."""
        try:
            config_entry = self.hass.config_entries.async_get_entry(self.config_entry_id)
            if config_entry is None:
                _LOGGER.error("Config entry not found")
                return None

            options = config_entry.options
            backup_folder = options.get(CONF_BACKUP_FOLDER, DEFAULT_BACKUP_FOLDER)

            # Get folder ID
            folder_id = await self.api.async_get_folder_id(backup_folder)

            # List files to find the backup
            files = await self.api.async_list_folder(folder_id)

            # Extract actual filename from backup_id if needed
            backup_name = self._extract_backup_filename(backup_name)

            # Normalize backup name - check both with and without .tar
            backup_name_with_ext = backup_name if backup_name.endswith(".tar") else f"{backup_name}.tar"
            backup_name_without_ext = backup_name.removesuffix(".tar")

            # Find the backup file (check both variants)
            for file_item in files:
                file_name = file_item.get("name", "")
                if file_name == backup_name or file_name == backup_name_with_ext or file_name == backup_name_without_ext:
                    backup_dict = self.api.parse_backup_info(file_item)
                    # Convert to AgentBackup object with all required parameters
                    backup_id = f"{self.slug}:{backup_dict.get('name', backup_name)}"
                    return AgentBackup(
                        name=backup_dict.get("name", backup_name),
                        date=backup_dict.get("modified", ""),
                        size=backup_dict.get("size", 0),
                        backup_id=backup_id,
                        addons=[],  # We don't have addon info from pCloud
                        database_included=True,  # Assume included
                        extra_metadata={},  # No extra metadata available
                        folders=[],  # We don't have folder info from pCloud
                        homeassistant_included=True,  # Assume included
                        homeassistant_version="",  # Not available from pCloud
                        protected=False,  # Default to not protected
                    )

            return None

        except PCloudAPIError as err:
            _LOGGER.error("Failed to get backup: %s", err)
            return None
        except Exception as err:
            _LOGGER.exception("Unexpected error getting backup")
            return None

    async def async_list_backups(self) -> list[AgentBackup]:
        """List all backups in pCloud."""
        try:
            config_entry = self.hass.config_entries.async_get_entry(self.config_entry_id)
            if config_entry is None:
                _LOGGER.error("Config entry not found for listing backups")
                return []

            options = config_entry.options
            backup_folder = options.get(CONF_BACKUP_FOLDER, DEFAULT_BACKUP_FOLDER)
            _LOGGER.debug("Listing backups from folder: %s", backup_folder)

            # Get or create folder
            folder_id = await self.api.async_get_folder_id(backup_folder)
            _LOGGER.debug("Backup folder ID: %s", folder_id)

            # List files in folder
            files = await self.api.async_list_folder(folder_id)
            _LOGGER.info("Found %d files in backup folder", len(files))

            # Parse backup info and convert to AgentBackup objects
            backup_dicts = [self.api.parse_backup_info(file_item) for file_item in files]
            _LOGGER.debug("Parsed %d backups: %s", len(backup_dicts), [b.get("name") for b in backup_dicts])

            # Sort by modified date (newest first)
            backup_dicts.sort(key=lambda x: x.get("modified", ""), reverse=True)

            # Convert dicts to AgentBackup objects
            backups = []
            for backup_dict in backup_dicts:
                backup_name = backup_dict.get("name", "")
                if backup_name:
                    # Create AgentBackup object with all required parameters
                    backup_id = f"{self.slug}:{backup_name}"
                    backup = AgentBackup(
                        name=backup_name,
                        date=backup_dict.get("modified", ""),
                        size=backup_dict.get("size", 0),
                        backup_id=backup_id,
                        addons=[],  # We don't have addon info from pCloud
                        database_included=True,  # Assume included (default for backups)
                        extra_metadata={},  # No extra metadata available
                        folders=[],  # We don't have folder info from pCloud
                        homeassistant_included=True,  # Assume included
                        homeassistant_version="",  # Not available from pCloud
                        protected=False,  # Default to not protected
                    )
                    backups.append(backup)

            _LOGGER.info("Returning %d backups from pCloud", len(backups))
            return backups

        except PCloudAPIError as err:
            _LOGGER.error("Failed to list backups: %s", err, exc_info=True)
            return []
        except Exception as err:
            _LOGGER.exception("Unexpected error listing backups")
            return []

    async def async_upload_backup(
        self,
        *,
        open_stream: Callable[[], Coroutine[Any, Any, AsyncIterator[bytes]]],
        backup: AgentBackup,
        **kwargs: Any,
    ) -> None:
        """Upload a backup to pCloud.
        
        Args:
            open_stream: A function returning an async iterator that yields bytes.
            backup: Metadata about the backup that should be uploaded.
        """
        try:
            config_entry = self.hass.config_entries.async_get_entry(self.config_entry_id)
            if config_entry is None:
                raise RuntimeError("Config entry not found")

            options = config_entry.options
            backup_folder = options.get(CONF_BACKUP_FOLDER, DEFAULT_BACKUP_FOLDER)

            # Get or create folder
            folder_id = await self.api.async_get_folder_id(backup_folder)

            # Get backup name from backup object and ensure .tar extension
            backup_name = backup.name
            if not backup_name.endswith(".tar"):
                backup_name = f"{backup_name}.tar"

            # Read backup data from stream
            _LOGGER.info("Uploading backup %s to pCloud", backup_name)
            backup_data = b""
            stream = await open_stream()
            async for chunk in stream:
                backup_data += chunk

            # Upload to pCloud
            await self.api.async_upload_file(folder_id, backup_name, backup_data)
            _LOGGER.info("Successfully uploaded backup %s", backup_name)

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

            # Extract actual filename from backup_id if needed
            backup_name = self._extract_backup_filename(backup_name)

            # Normalize backup name - check both with and without .tar
            backup_name_with_ext = backup_name if backup_name.endswith(".tar") else f"{backup_name}.tar"
            backup_name_without_ext = backup_name.removesuffix(".tar")

            # Find the backup file (check both variants)
            backup_file = None
            for file_item in files:
                file_name = file_item.get("name", "")
                if file_name == backup_name or file_name == backup_name_with_ext or file_name == backup_name_without_ext:
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

            # Extract actual filename from backup_id if needed
            backup_name = self._extract_backup_filename(backup_name)

            # Normalize backup name - check both with and without .tar
            backup_name_with_ext = backup_name if backup_name.endswith(".tar") else f"{backup_name}.tar"
            backup_name_without_ext = backup_name.removesuffix(".tar")

            # Find the backup file (check both variants)
            backup_file = None
            for file_item in files:
                file_name = file_item.get("name", "")
                if file_name == backup_name or file_name == backup_name_with_ext or file_name == backup_name_without_ext:
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

