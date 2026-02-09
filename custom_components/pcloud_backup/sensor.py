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
    ATTR_ACCOUNT_USED_SPACE,
    ATTR_FREE_SPACE,
    ATTR_LAST_REMOTE_BACKUP,
    ATTR_LAST_SYNC_STATUS,
    ATTR_REMOTE_BACKUP_COUNT,
    ATTR_USED_SPACE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Binary units (1024) to match pCloud and common storage UIs (Windows, macOS, etc.)
_DATA_SIZE_UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")


def _bytes_to_human_readable(value: int | float | None) -> tuple[float | None, str]:
    """Convert byte value to human-readable (value, unit) using binary (1024) units."""
    if value is None:
        return (None, "B")
    try:
        value = int(float(value))
    except (TypeError, ValueError):
        return (None, "B")
    if value < 0:
        return (None, "B")
    if value == 0:
        return (0.0, "B")
    for i, unit in enumerate(_DATA_SIZE_UNITS):
        if value < 1024 ** (i + 1) or unit == _DATA_SIZE_UNITS[-1]:
            divisor = 1024**i
            return (round(value / divisor, 2), unit)
    return (round(value / (1024 ** (len(_DATA_SIZE_UNITS) - 1)), 2), _DATA_SIZE_UNITS[-1])


SENSOR_TYPES: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="remote_backup_count",
        translation_key="remote_backup_count",
        icon="mdi:cloud",
        native_unit_of_measurement="backups",
        state_class=SensorStateClass.TOTAL,
    ),
    SensorEntityDescription(
        key="last_remote_backup",
        translation_key="last_remote_backup",
        icon="mdi:clock-outline",
        device_class="timestamp",
    ),
    SensorEntityDescription(
        key="last_sync_status",
        translation_key="last_sync_status",
        icon="mdi:sync",
    ),
    SensorEntityDescription(
        key="free_space",
        translation_key="free_space",
        icon="mdi:cloud-outline",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="used_space",
        translation_key="used_space_by_backups",
        icon="mdi:cloud-upload",
        state_class=SensorStateClass.TOTAL,
    ),
    SensorEntityDescription(
        key="account_used_space",
        translation_key="account_used_space",
        icon="mdi:cloud",
        state_class=SensorStateClass.TOTAL,
    ),
)

_SIZE_SENSOR_KEYS = ("free_space", "used_space", "account_used_space")


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
                # Get the most recent backup (now AgentBackup object)
                latest_backup = backups[0]
                _LOGGER.debug("Latest backup: %s", latest_backup.name)
                last_backup_str = latest_backup.date
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

            # Calculate total used space by summing all backup sizes
            total_used_space = sum(backup.size for backup in backups if backup.size)

            # Fetch userinfo to get quota information
            account_used_space = None
            try:
                userinfo = await self.api.async_get_userinfo()
                quota = userinfo.get("quota", 0)
                used_quota = userinfo.get("usedquota", 0)
                account_used_space = used_quota
                free_space = max(0, quota - used_quota)
                _LOGGER.debug(
                    "Userinfo: quota=%d, used_quota=%d, free_space=%d",
                    quota,
                    used_quota,
                    free_space,
                )
            except Exception as err:
                _LOGGER.warning("Failed to fetch userinfo: %s", err)
                free_space = None

            self._last_sync_status = "OK"
            self._last_sync_error = None

            result = {
                ATTR_REMOTE_BACKUP_COUNT: len(backups),
                ATTR_LAST_REMOTE_BACKUP: last_backup_dt.isoformat() if last_backup_dt else None,
                ATTR_LAST_SYNC_STATUS: "OK",
                ATTR_USED_SPACE: total_used_space,
                ATTR_FREE_SPACE: free_space,
                ATTR_ACCOUNT_USED_SPACE: account_used_space,
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
                ATTR_USED_SPACE: self.data.get(ATTR_USED_SPACE, 0) if self.data else 0,
                ATTR_FREE_SPACE: self.data.get(ATTR_FREE_SPACE) if self.data else None,
                ATTR_ACCOUNT_USED_SPACE: self.data.get(ATTR_ACCOUNT_USED_SPACE)
                if self.data
                else None,
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
                ATTR_USED_SPACE: self.data.get(ATTR_USED_SPACE, 0) if self.data else 0,
                ATTR_FREE_SPACE: self.data.get(ATTR_FREE_SPACE) if self.data else None,
                ATTR_ACCOUNT_USED_SPACE: self.data.get(ATTR_ACCOUNT_USED_SPACE)
                if self.data
                else None,
            }


class PCloudBackupSensor(CoordinatorEntity, SensorEntity):
    """Representation of a pCloud Backup sensor."""

    _attr_has_entity_name = True

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
        # Cache for size sensors: (display_value, unit) so state write sees human-readable form
        self._size_display_value: float | None = None
        self._size_display_unit: str = "B"

    async def async_added_to_hass(self) -> None:
        """Populate size display cache from coordinator data so first state write is correct."""
        await super().async_added_to_hass()
        if self.entity_description.key in _SIZE_SENSOR_KEYS and self.coordinator.data is not None:
            self._size_display_value, self._size_display_unit = self._size_display()

    def _handle_coordinator_update(self) -> None:
        """Cache human-readable value/unit for size sensors, then let parent write state."""
        if self.entity_description.key in _SIZE_SENSOR_KEYS:
            self._size_display_value, self._size_display_unit = self._size_display()
        super()._handle_coordinator_update()

    def _size_display(self) -> tuple[float | None, str]:
        """Get human-readable (value, unit) for size sensors from coordinator data."""
        if self.coordinator.data is None:
            return (None, "B")
        key = self.entity_description.key
        raw = self.coordinator.data.get(key)
        return _bytes_to_human_readable(raw)

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        if self.coordinator.data is None:
            return None

        key = self.entity_description.key
        value = self.coordinator.data.get(key)

        if key in _SIZE_SENSOR_KEYS:
            # Use cached display value after coordinator update; else compute from data (e.g. first load)
            if self._size_display_value is not None:
                return self._size_display_value
            display_value, _ = _bytes_to_human_readable(value)
            return display_value

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
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit of the sensor; dynamic for size sensors."""
        if self.entity_description.key in _SIZE_SENSOR_KEYS:
            # Use cached unit when set (after coordinator update), else compute from data
            if self.coordinator.data is not None:
                _, unit = self._size_display()
                return unit
            return self._size_display_unit
        return self.entity_description.native_unit_of_measurement

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes."""
        if self.entity_description.key == "last_sync_status" and self.coordinator._last_sync_error:
            return {"error": self.coordinator._last_sync_error}
        return None

