"""Backup agent implementation for pCloud."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import AsyncIterator, Callable, Coroutine
from pathlib import Path
from urllib.parse import unquote
from typing import Any

from homeassistant.components.backup import (
    AgentBackup,
    BackupAgent,
    BackupAgentError,
    BackupNotFound,
    suggested_filename,
)
from homeassistant.core import HomeAssistant, callback

from .api import PCloudAPI, PCloudAPIError
from .const import (
    CONF_BACKUP_FOLDER,
    CONF_PERMANENT_DELETE,
    CONF_UPLOAD_TIMEOUT_SECONDS,
    DATA_BACKUP_AGENT_LISTENERS,
    DEFAULT_BACKUP_FOLDER,
    DEFAULT_PERMANENT_DELETE,
    DEFAULT_UPLOAD_TIMEOUT_SECONDS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

METADATA_SUFFIX = ".metadata.json"
METADATA_VERSION = 2
SUPPORTED_METADATA_VERSIONS = {None, 1, METADATA_VERSION}


async def async_get_backup_agents(hass: HomeAssistant) -> list[BackupAgent]:
    """Return the list of pCloud backup agents."""
    agents: list[BackupAgent] = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        api: PCloudAPI | None = getattr(entry, "runtime_data", None)
        if api is None:
            continue
        agent = PCloudBackupAgent(
            hass=hass,
            config_entry_id=entry.entry_id,
            api=api,
            name=entry.title or "pCloud",
        )
        agents.append(agent)
    return agents


@callback
def async_register_backup_agents_listener(
    hass: HomeAssistant,
    *,
    listener: Callable[[], None],
    **kwargs: Any,
) -> Callable[[], None]:
    """Register a listener to be called when agents are added or removed."""
    hass.data.setdefault(DATA_BACKUP_AGENT_LISTENERS, []).append(listener)

    @callback
    def remove_listener() -> None:
        """Remove the registered listener."""
        listeners = hass.data.get(DATA_BACKUP_AGENT_LISTENERS, [])
        if listener in listeners:
            listeners.remove(listener)
            if not listeners:
                hass.data.pop(DATA_BACKUP_AGENT_LISTENERS)

    return remove_listener


class PCloudBackupError(BackupAgentError):
    """Base exception for pCloud backup errors."""

    pass


class PCloudBackupAgent(BackupAgent):
    """pCloud Backup Agent implementation."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry_id: str,
        *,
        api: PCloudAPI | None = None,
        name: str = "pCloud",
    ) -> None:
        """Initialize the backup agent."""
        self.hass = hass
        self.config_entry_id = config_entry_id
        self._api: PCloudAPI | None = api
        # Required attributes for BackupAgent
        self.domain = DOMAIN
        self.unique_id = config_entry_id
        self.name = name
        # Slug must be in format "{domain}.{unique_id}" for Home Assistant backup system
        self.slug = f"{DOMAIN}.{config_entry_id}"

    def _metadata_key_from_backup_name(self, filename: str) -> str:
        """Return metadata key (name without .tar) for a backup filename."""
        if filename.endswith(".tar"):
            return filename[: -len(".tar")]
        return filename

    def _metadata_key_from_metadata_name(self, filename: str) -> str:
        """Return metadata key for a metadata filename."""
        return filename[: -len(METADATA_SUFFIX)]

    def _metadata_key_from_input(self, backup_name_or_id: str) -> str:
        """Return metadata key from backup identifier (name or backup_id)."""
        file_name = self._extract_backup_filename(backup_name_or_id)
        return self._metadata_key_from_backup_name(file_name)

    def _metadata_key_for_backup(self, backup: AgentBackup) -> str:
        """Return metadata key for a specific AgentBackup."""
        if backup.backup_id.startswith(f"{self.slug}:"):
            return backup.backup_id.split(":", 1)[1]
        return Path(suggested_filename(backup)).stem

    def _decode_display_name(self, name: str) -> str:
        """Return a human friendly name for display from stored filename."""
        decoded = unquote(name)
        # Replace underscores with spaces if the string looks slugified
        if "_" in decoded and " " not in decoded:
            decoded = decoded.replace("_", " ")
        return decoded

    async def _async_get_folder_items(
        self, folder_id: int
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        """Return dicts of backup files and metadata files keyed by metadata key."""
        files = await self.api.async_list_folder(folder_id)
        backup_files: dict[str, dict[str, Any]] = {}
        metadata_files: dict[str, dict[str, Any]] = {}

        for file_item in files:
            name = file_item.get("name", "")
            if not name:
                continue
            if name.endswith(METADATA_SUFFIX):
                metadata_key = self._metadata_key_from_metadata_name(name)
                metadata_files[metadata_key] = file_item
            else:
                metadata_key = self._metadata_key_from_backup_name(name)
                backup_files[metadata_key] = file_item

        return backup_files, metadata_files

    async def _async_load_metadata(
        self,
        metadata_item: dict[str, Any],
        backup_name: str,
        metadata_key: str,
        backup_file_item: dict[str, Any],
    ) -> AgentBackup | None:
        """Load metadata file and return AgentBackup if available."""
        file_id = metadata_item.get("fileid")
        if file_id is None:
            return None
        try:
            metadata_bytes = await self.api.async_download_file(file_id)
            payload = json.loads(metadata_bytes.decode("utf-8"))
            metadata_version = payload.pop("metadata_version", None)
            if metadata_version not in SUPPORTED_METADATA_VERSIONS:
                _LOGGER.debug(
                    "Ignoring metadata for %s due to unsupported version %s",
                    backup_name,
                    metadata_version,
                )
                return None
            backup_dict = payload.get("backup", payload)
            backup_dict = dict(backup_dict)  # shallow copy
            if "metadata_version" in backup_dict:
                backup_dict = {
                    key: value for key, value in backup_dict.items() if key != "metadata_version"
                }
            if "extra_metadata" not in backup_dict:
                backup_dict = {**backup_dict, "extra_metadata": {}}
            extra_metadata = dict(backup_dict["extra_metadata"])
            
            # Keep the original backup_id from metadata (it should already be correct)
            # If the metadata has our slug-based format, try to restore from extra_metadata
            current_backup_id = backup_dict.get("backup_id", "")
            if current_backup_id.startswith(f"{self.slug}:"):
                # Metadata has our slug format, try to restore original
                original_backup_id = extra_metadata.get("original_backup_id")
                if original_backup_id:
                    backup_dict["backup_id"] = original_backup_id
                # If no original_backup_id, keep the slug format (old metadata)
            # Otherwise, keep the backup_id as-is (it's already correct)

            backup_dict["extra_metadata"] = extra_metadata

            if (size := backup_file_item.get("size")) is not None:
                backup_dict = {**backup_dict, "size": size}
            agent_backup = AgentBackup.from_dict(backup_dict)
            return agent_backup
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "Failed to load metadata for backup %s (key=%s file=%s): %r; metadata_item=%s",
                backup_name,
                metadata_key,
                file_id,
                err,
                metadata_item,
            )
            return None

    def _create_backup_from_file(
        self, file_item: dict[str, Any], metadata_key: str
    ) -> AgentBackup:
        """Create AgentBackup object from file information as a fallback."""
        backup_dict = self.api.parse_backup_info(file_item)
        fallback_name = self._decode_display_name(backup_dict.get("name", metadata_key))
        backup_id = f"{self.slug}:{metadata_key}"
        return AgentBackup(
            name=fallback_name,
            date=backup_dict.get("modified", ""),
            size=backup_dict.get("size", 0),
            backup_id=backup_id,
            addons=[],
            database_included=True,
            extra_metadata={},
            folders=[],
            homeassistant_included=True,
            homeassistant_version="",
            protected=False,
        )

    async def _async_get_agent_backup(
        self,
        backup_files: dict[str, dict[str, Any]],
        metadata_files: dict[str, dict[str, Any]],
        metadata_key: str,
    ) -> AgentBackup | None:
        """Return AgentBackup constructed from available data."""
        file_item = backup_files.get(metadata_key)
        if file_item is None:
            return None
        backup_name = file_item.get("name", metadata_key)
        metadata_item = metadata_files.get(metadata_key)
        if metadata_item:
            agent_backup = await self._async_load_metadata(
                metadata_item, backup_name, metadata_key, file_item
            )
            if agent_backup:
                return agent_backup

        return self._create_backup_from_file(file_item, metadata_key)

    async def _async_collect_backups(
        self, folder_id: int
    ) -> tuple[
        dict[str, AgentBackup],
        dict[str, str],
        dict[str, dict[str, Any]],
        dict[str, dict[str, Any]],
    ]:
        """Collect backups and return lookup maps."""
        backup_files, metadata_files = await self._async_get_folder_items(folder_id)
        backups_by_key: dict[str, AgentBackup] = {}
        backup_id_index: dict[str, str] = {}

        for metadata_key in backup_files:
            agent_backup = await self._async_get_agent_backup(
                backup_files, metadata_files, metadata_key
            )
            if not agent_backup:
                continue
            backups_by_key[metadata_key] = agent_backup
            backup_id_index[agent_backup.backup_id] = metadata_key
            original_backup_id = agent_backup.extra_metadata.get("original_backup_id")
            if isinstance(original_backup_id, str):
                backup_id_index[original_backup_id] = metadata_key

        return backups_by_key, backup_id_index, backup_files, metadata_files

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
                raise PCloudBackupError("pCloud API not initialized")
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

    async def async_get_backup(self, backup_name: str, **kwargs: Any) -> AgentBackup:
        """Get backup information by name.

        Home Assistant 2025.10+ requires a concrete AgentBackup or a raised
        BackupNotFound / BackupAgentError — returning None is deprecated.
        """
        try:
            config_entry = self.hass.config_entries.async_get_entry(self.config_entry_id)
            if config_entry is None:
                _LOGGER.error("Config entry not found")
                raise PCloudBackupError("pCloud backup agent: config entry not found")

            options = config_entry.options
            backup_folder = options.get(CONF_BACKUP_FOLDER, DEFAULT_BACKUP_FOLDER)

            folder_id = await self.api.async_get_folder_id(backup_folder)
            backups_by_key, backup_id_index, _, _ = await self._async_collect_backups(
                folder_id
            )

            # Try lookup by backup_id first
            if backup_name in backup_id_index:
                return backups_by_key[backup_id_index[backup_name]]

            # Fallback to metadata key derived from input
            metadata_key = self._metadata_key_from_input(backup_name)
            if metadata_key in backups_by_key:
                return backups_by_key[metadata_key]

            # As a final fallback, compare against derived keys from metadata
            for _key, agent_backup in backups_by_key.items():
                if self._metadata_key_for_backup(agent_backup) == metadata_key:
                    return agent_backup

            raise BackupNotFound(f"Backup {backup_name} not found in pCloud")

        except BackupNotFound:
            raise
        except PCloudAPIError as err:
            _LOGGER.error("Failed to get backup: %s", err, exc_info=True)
            raise PCloudBackupError(f"Failed to get backup: {err}") from err
        except Exception as err:
            _LOGGER.exception("Unexpected error getting backup")
            raise PCloudBackupError(f"Unexpected error getting backup: {err}") from err

    async def async_list_backups(self, **kwargs: Any) -> list[AgentBackup]:
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
            backups_by_key, _, _, _ = await self._async_collect_backups(folder_id)

            backups = list(backups_by_key.values())
            backups.sort(key=lambda backup: backup.date or "", reverse=True)
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
                raise PCloudBackupError("Config entry not found")

            options = config_entry.options
            backup_folder = options.get(CONF_BACKUP_FOLDER, DEFAULT_BACKUP_FOLDER)

            # Get or create folder
            folder_id = await self.api.async_get_folder_id(backup_folder)

            # Determine filenames
            backup_name = suggested_filename(backup)
            metadata_key = Path(backup_name).stem
            metadata_filename = f"{metadata_key}{METADATA_SUFFIX}"

            # Remove any existing files with the same key to avoid duplicates
            backup_files, metadata_files = await self._async_get_folder_items(folder_id)
            for existing in (
                backup_files.get(metadata_key),
                metadata_files.get(metadata_key),
            ):
                if existing and existing.get("fileid"):
                    try:
                        await self.api.async_delete_file(existing["fileid"])
                    except Exception as cleanup_err:  # noqa: BLE001
                        _LOGGER.warning(
                            "Failed to remove existing item %s before upload: %s",
                            existing.get("name"),
                            cleanup_err,
                        )

            # Stream backup directly from Home Assistant to pCloud
            # Uses dual-path strategy: FIFO if size known, temp file in /backup if unknown
            _LOGGER.info("Starting upload of backup %s to pCloud", backup_name)
            
            stream = await open_stream()
            backup_metadata_size = getattr(backup, "size", 0) or 0
            
            if backup_metadata_size > 0:
                _LOGGER.info(
                    "Backup metadata reports size: %d bytes (%.2f MB) - will use FIFO path",
                    backup_metadata_size,
                    backup_metadata_size / (1024 * 1024),
                )
            else:
                _LOGGER.info(
                    "Backup size unknown - will use temp file in /backup directory"
                )
            
            # Stream directly from backup iterator to pCloud
            # Pass file_size to enable FIFO path when size is known
            upload_timeout_s = int(
                options.get(CONF_UPLOAD_TIMEOUT_SECONDS, DEFAULT_UPLOAD_TIMEOUT_SECONDS)
            )
            await self.api.async_upload_file_from_stream(
                folder_id,
                backup_name,
                stream,
                file_size=backup_metadata_size if backup_metadata_size > 0 else None,
                upload_total_seconds=upload_timeout_s,
            )

            # Upload metadata so we can faithfully reconstruct the AgentBackup
            # CRITICAL: Preserve the original backup_id for HA to match decryption keys
            # Do NOT change backup_id - HA uses it to find decryption keys
            backup_dict = backup.as_dict()
            original_backup_id = backup_dict.get("backup_id")
            
            # Store slug-based ID in extra_metadata for lookup purposes
            # but keep the original backup_id unchanged
            extra_metadata = dict(backup_dict.get("extra_metadata", {}))
            extra_metadata["slug_backup_id"] = f"{self.slug}:{metadata_key}"
            if original_backup_id and extra_metadata.get("original_backup_id") is None:
                extra_metadata["original_backup_id"] = original_backup_id
            
            # Keep the original backup_id unchanged (needed for HA decryption key matching)
            # Do NOT overwrite it with slug-based format
            backup_dict["extra_metadata"] = extra_metadata
            metadata_payload = json.dumps(
                {"metadata_version": METADATA_VERSION, "backup": backup_dict},
                ensure_ascii=False,
            ).encode("utf-8")
            try:
                await self.api.async_upload_file(
                    folder_id, metadata_filename, metadata_payload
                )
            except Exception as err:
                _LOGGER.error(
                    "Failed to upload metadata for backup %s: %s", backup_name, err
                )
                # Attempt to remove the backup file so we don't leave a partial upload
                try:
                    latest_backup_files, _ = await self._async_get_folder_items(
                        folder_id
                    )
                    backup_file = latest_backup_files.get(metadata_key)
                    if backup_file and backup_file.get("fileid"):
                        await self.api.async_delete_file(backup_file["fileid"])
                except Exception as cleanup_err:  # noqa: BLE001
                    _LOGGER.warning(
                        "Failed to clean up backup %s after metadata error: %s",
                        backup_name,
                        cleanup_err,
                    )
                raise

            _LOGGER.info("Successfully uploaded backup %s", backup_name)

        except PCloudAPIError as err:
            _LOGGER.error("Failed to upload backup: %s", err, exc_info=True)
            raise PCloudBackupError(f"Failed to upload backup: {err}") from err
        except Exception as err:
            _LOGGER.exception("Unexpected error uploading backup")
            raise PCloudBackupError(f"Unexpected error uploading backup: {err}") from err

    async def async_download_backup(
        self, backup_id: str, **kwargs: Any
    ) -> AsyncIterator[bytes]:
        """Download a backup from pCloud and return as an async stream.
        
        This method is called by Home Assistant to download backups for decryption
        checks and actual downloads. It returns an async iterator that yields
        chunks of the backup file.
        
        This implementation streams directly from pCloud to Home Assistant,
        similar to how the OneDrive integration works. No temp files are used.
        
        Args:
            backup_id: The backup ID (can be backup_id or backup name)
            **kwargs: Additional keyword arguments that Home Assistant may pass,
                     such as 'progress' callback or 'expected_size'
            
        Returns:
            AsyncIterator[bytes]: An async iterator that yields chunks of backup file data
        """
        # Log the download request with any kwargs for debugging
        if kwargs:
            _LOGGER.info(
                "Starting download of backup %s (kwargs: %s)",
                backup_id,
                {k: v for k, v in kwargs.items() if k != "progress"},  # Don't log callbacks
            )
        else:
            _LOGGER.info("Starting download of backup %s", backup_id)
        
        try:
            config_entry = self.hass.config_entries.async_get_entry(self.config_entry_id)
            if config_entry is None:
                raise PCloudBackupError("Config entry not found")

            options = config_entry.options
            backup_folder = options.get(CONF_BACKUP_FOLDER, DEFAULT_BACKUP_FOLDER)

            folder_id = await self.api.async_get_folder_id(backup_folder)
            backups_by_key, backup_id_index, backup_files, _ = await self._async_collect_backups(
                folder_id
            )

            metadata_key = backup_id_index.get(backup_id)
            if metadata_key is None:
                metadata_key = self._metadata_key_from_input(backup_id)

            backup_file = backup_files.get(metadata_key)
            if backup_file is None and metadata_key in backups_by_key:
                derived_key = self._metadata_key_for_backup(backups_by_key[metadata_key])
                backup_file = backup_files.get(derived_key)
                metadata_key = derived_key

            if backup_file is None:
                _LOGGER.warning("Backup %s not found in pCloud", backup_id)
                raise BackupNotFound(f"Backup {backup_id} not found in pCloud")

            # Get file ID for download
            file_id = backup_file.get("fileid")
            if file_id is None:
                _LOGGER.error("Backup %s has no file ID", backup_id)
                raise BackupNotFound(f"Backup {backup_id} has no file ID")

            _LOGGER.debug(
                "Downloading backup %s from pCloud file ID %s",
                backup_id,
                file_id,
            )

            # Return stream directly - exactly like OneDrive
            # The response lifecycle is managed by async_download_file_stream
            # Response stays open during the entire read, matching OneDrive's behavior
            return self.api.async_download_file_stream(file_id)

        except BackupNotFound:
            # Re-raise BackupNotFound as-is (it's already a BackupAgentError subclass)
            raise
        except PCloudAPIError as err:
            _LOGGER.error(
                "pCloud API error downloading backup %s: %s",
                backup_id,
                err,
                exc_info=True,
            )
            raise PCloudBackupError(f"Failed to download backup: {err}") from err
        except Exception as err:
            _LOGGER.exception(
                "Unexpected error downloading backup %s",
                backup_id,
            )
            raise PCloudBackupError(f"Unexpected error downloading backup: {err}") from err

    async def async_delete_backup(self, backup_name: str, **kwargs: Any) -> None:
        """Delete a backup from pCloud."""
        try:
            config_entry = self.hass.config_entries.async_get_entry(self.config_entry_id)
            if config_entry is None:
                raise PCloudBackupError("Config entry not found")

            options = config_entry.options
            backup_folder = options.get(CONF_BACKUP_FOLDER, DEFAULT_BACKUP_FOLDER)

            folder_id = await self.api.async_get_folder_id(backup_folder)
            (
                backups_by_key,
                backup_id_index,
                backup_files,
                metadata_files,
            ) = await self._async_collect_backups(folder_id)

            metadata_key = backup_id_index.get(backup_name)
            if metadata_key is None:
                metadata_key = self._metadata_key_from_input(backup_name)

            backup_file = backup_files.get(metadata_key)
            if backup_file is None and metadata_key in backups_by_key:
                derived_key = self._metadata_key_for_backup(backups_by_key[metadata_key])
                backup_file = backup_files.get(derived_key)
                metadata_key = derived_key

            if backup_file is None:
                raise BackupNotFound(f"Backup {backup_name} not found in pCloud")

            # Delete file
            file_id = backup_file.get("fileid")
            if file_id is None:
                raise BackupNotFound(f"Backup {backup_name} has no file ID")

            permanent_delete = options.get(
                CONF_PERMANENT_DELETE, DEFAULT_PERMANENT_DELETE
            )

            _LOGGER.info("Deleting backup %s from pCloud", backup_name)
            await self.api.async_delete_file(file_id)
            if permanent_delete:
                await self._async_trash_clear(file_id, backup_name)
            # Also remove metadata file if present
            metadata_item = metadata_files.get(metadata_key)
            if metadata_item and metadata_item.get("fileid"):
                try:
                    await self.api.async_delete_file(metadata_item["fileid"])
                    if permanent_delete:
                        await self._async_trash_clear(
                            metadata_item["fileid"], backup_name
                        )
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning(
                        "Failed to delete metadata for backup %s: %s", backup_name, err
                    )

            _LOGGER.info("Successfully deleted backup %s", backup_name)

        except BackupNotFound:
            # Re-raise BackupNotFound as-is (it's already a BackupAgentError subclass)
            raise
        except PCloudAPIError as err:
            _LOGGER.error("Failed to delete backup: %s", err, exc_info=True)
            raise PCloudBackupError(f"Failed to delete backup: {err}") from err
        except Exception as err:
            _LOGGER.exception("Unexpected error deleting backup")
            raise PCloudBackupError(f"Unexpected error deleting backup: {err}") from err

    async def _async_trash_clear(self, file_id: int, backup_name: str) -> None:
        """Permanently purge a deleted file from Trash.

        Best-effort: failures are logged but don't fail the overall delete,
        since deletefile already succeeded and the backup is no longer
        listed - it would just remain in Trash until manually emptied.
        """
        try:
            await self.api.async_trash_clear(file_id)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "Failed to purge backup %s from Trash: %s", backup_name, err
            )


