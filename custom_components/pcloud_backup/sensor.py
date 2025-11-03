"""Sensor platform for pCloud Backup monitoring."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .api import PCloudAPI, PCloudAPIError
from .const import (
    ATTR_LAST_REMOTE_BACKUP,
    ATTR_LAST_SYNC_STATUS,
    ATTR_REMOTE_BACKUP_COUNT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

SENSOR_TYPES: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="remote_backup_count",
        name="Remote Backup Count",
        icon="mdi:cloud",
        native_unit_of_measurement="backups",
        state_class=SensorStateClass.TOTAL,
    ),
    SensorEntityDescription(
        key="last_remote_backup",
        name="Last Remote Backup",
        icon="mdi:clock-outline",
        device_class="timestamp",
    ),
    SensorEntityDescription(
        key="last_sync_status",
        name="Last Sync Status",
        icon="mdi:sync",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up pCloud Backup sensors from a config entry."""
    api: PCloudAPI = hass.data[DOMAIN][entry.entry_id]

    coordinator = PCloudBackupCoordinator(hass, api, entry)

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    async_add_entities(
        PCloudBackupSensor(coordinator, description, entry)
        for description in SENSOR_TYPES
    )


class PCloudBackupCoordinator(DataUpdateCoordinator):
    """Coordinator for pCloud Backup sensor data."""

    def __init__(self, hass: HomeAssistant, api: PCloudAPI, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=5),
        )
        self.api = api
        self.entry = entry
        self._last_sync_status = "Unknown"
        self._last_sync_error: str | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from pCloud API."""
        try:
            from .backup import PCloudBackupAgent

            backup_agent = PCloudBackupAgent(self.hass, self.entry.entry_id)
            # Access API property to ensure it's loaded
            _ = backup_agent.api
            _LOGGER.debug("Fetching backup list for sensors")
            backups = await backup_agent.async_list_backups()
            _LOGGER.info("Sensor update: Found %d backups", len(backups))

            if backups:
                # Get the most recent backup
                latest_backup = backups[0]
                _LOGGER.debug("Latest backup: %s", latest_backup.get("name"))
                last_backup_str = latest_backup.get("modified", "")
                try:
                    last_backup_dt = datetime.fromisoformat(
                        last_backup_str.replace("Z", "+00:00")
                    )
                    if last_backup_dt.tzinfo is None:
                        last_backup_dt = last_backup_dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
                    _LOGGER.debug("Latest backup date: %s", last_backup_dt.isoformat())
                except (ValueError, TypeError) as err:
                    _LOGGER.warning("Failed to parse backup date '%s': %s", last_backup_str, err)
                    last_backup_dt = None
            else:
                _LOGGER.debug("No backups found")
                last_backup_dt = None

            self._last_sync_status = "OK"
            self._last_sync_error = None

            result = {
                ATTR_REMOTE_BACKUP_COUNT: len(backups),
                ATTR_LAST_REMOTE_BACKUP: last_backup_dt.isoformat() if last_backup_dt else None,
                ATTR_LAST_SYNC_STATUS: "OK",
            }
            _LOGGER.debug("Sensor data: %s", result)
            return result

        except PCloudAPIError as err:
            _LOGGER.error("Error updating pCloud backup data: %s", err)
            self._last_sync_status = "Failed"
            self._last_sync_error = str(err)
            return {
                ATTR_REMOTE_BACKUP_COUNT: self.data.get(ATTR_REMOTE_BACKUP_COUNT, 0)
                if self.data
                else 0,
                ATTR_LAST_REMOTE_BACKUP: self.data.get(ATTR_LAST_REMOTE_BACKUP)
                if self.data
                else None,
                ATTR_LAST_SYNC_STATUS: "Failed",
            }
        except Exception as err:
            _LOGGER.exception("Unexpected error updating pCloud backup data")
            self._last_sync_status = "Failed"
            self._last_sync_error = str(err)
            return {
                ATTR_REMOTE_BACKUP_COUNT: self.data.get(ATTR_REMOTE_BACKUP_COUNT, 0)
                if self.data
                else 0,
                ATTR_LAST_REMOTE_BACKUP: self.data.get(ATTR_LAST_REMOTE_BACKUP)
                if self.data
                else None,
                ATTR_LAST_SYNC_STATUS: "Failed",
            }


class PCloudBackupSensor(CoordinatorEntity, SensorEntity):
    """Representation of a pCloud Backup sensor."""

    def __init__(
        self,
        coordinator: PCloudBackupCoordinator,
        description: SensorEntityDescription,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_name = f"pCloud Backup {description.name}"

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        if self.coordinator.data is None:
            return None

        key = self.entity_description.key
        value = self.coordinator.data.get(key)

        if key == "last_remote_backup" and value:
            # Convert ISO string to timestamp for device_class timestamp
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
                return dt
            except (ValueError, TypeError):
                return None

        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes."""
        if self.entity_description.key == "last_sync_status" and self.coordinator._last_sync_error:
            return {"error": self.coordinator._last_sync_error}
        return None

