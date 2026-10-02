"""Fixtures for the pCloud Backup tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, create_autospec, patch

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pcloud_backup.api import PCloudAPI
from custom_components.pcloud_backup.const import DOMAIN

from .common import (
    BACKUP_FOLDER_ID,
    ENTRY_DATA,
    ENTRY_ID,
    ENTRY_OPTIONS,
    listfolder_files,
    load_json_fixture,
    metadata_bytes,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading custom integrations in all tests."""


@pytest.fixture(autouse=True)
def initialize_backup(hass: HomeAssistant) -> None:
    """Initialize backup data like HA bootstrap does on older HA versions.

    On older HA versions (incl. the minimum supported 2025.3), bootstrap calls
    helpers.backup.async_initialize_backup() before the backup component is set
    up; the test harness skips bootstrap, so do it here. Newer HA removed it.
    """
    try:
        from homeassistant.helpers.backup import async_initialize_backup
    except ImportError:
        return
    async_initialize_backup(hass)


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a v2 (OAuth2) config entry that is not yet added to hass."""
    return MockConfigEntry(
        domain=DOMAIN,
        entry_id=ENTRY_ID,
        version=2,
        title="pCloud Backup (EU)",
        unique_id="user@example.com",
        data=dict(ENTRY_DATA),
        options=dict(ENTRY_OPTIONS),
    )


@pytest.fixture
def mock_api() -> PCloudAPI:
    """Return an autospecced PCloudAPI instance backed by the sample fixtures."""
    api = create_autospec(PCloudAPI, instance=True)
    api.async_get_folder_id.return_value = BACKUP_FOLDER_ID
    api.async_list_folder.return_value = listfolder_files()
    api.async_download_file.return_value = metadata_bytes()
    api.async_get_userinfo.return_value = load_json_fixture("userinfo_sample.json")
    # parse_backup_info is pure: use the real implementation.
    api.parse_backup_info.side_effect = lambda item: PCloudAPI.parse_backup_info(api, item)
    return api


@pytest.fixture
def mock_pcloud() -> Generator[dict[str, AsyncMock]]:
    """Patch the network-facing PCloudAPI methods used during entry setup."""
    userinfo = load_json_fixture("userinfo_sample.json")
    mocks = {
        "async_test_connection": AsyncMock(return_value=userinfo),
        "async_get_userinfo": AsyncMock(return_value=userinfo),
        "async_get_folder_id": AsyncMock(return_value=BACKUP_FOLDER_ID),
        "async_list_folder": AsyncMock(return_value=listfolder_files()),
        "async_download_file": AsyncMock(return_value=metadata_bytes()),
        "async_close": AsyncMock(),
    }
    with patch.multiple(PCloudAPI, **mocks):
        yield mocks
