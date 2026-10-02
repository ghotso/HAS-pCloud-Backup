"""Tests for the pCloud Backup config and options flows."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import AbortFlow, FlowResultType, InvalidData
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from custom_components.pcloud_backup.api import PCloudAPIError
from custom_components.pcloud_backup.config_flow import (
    PCloudConfigFlow,
    _backup_options_schema,
)
from custom_components.pcloud_backup.const import (
    CONF_BACKUP_FOLDER,
    CONF_PERMANENT_DELETE,
    CONF_REGION,
    CONF_UPLOAD_TIMEOUT_SECONDS,
    DEFAULT_BACKUP_FOLDER,
    DEFAULT_PERMANENT_DELETE,
    DEFAULT_UPLOAD_TIMEOUT_SECONDS,
    DOMAIN,
    MAX_UPLOAD_TIMEOUT_SECONDS,
    MIN_UPLOAD_TIMEOUT_SECONDS,
)

from .common import ACCESS_TOKEN, BACKUP_FOLDER, load_json_fixture

USER_INPUT = {
    CONF_BACKUP_FOLDER: "/My Backups",
    CONF_UPLOAD_TIMEOUT_SECONDS: 7200,
    CONF_PERMANENT_DELETE: True,
}
TOKEN_DATA = {
    "auth_implementation": "pcloud_backup",
    "token": {"access_token": ACCESS_TOKEN, "token_type": "bearer"},
}


# --- Shared schema ----------------------------------------------------------


def _schema(**overrides) -> vol.Schema:
    kwargs = {
        "backup_folder_default": DEFAULT_BACKUP_FOLDER,
        "upload_timeout_default": DEFAULT_UPLOAD_TIMEOUT_SECONDS,
        "permanent_delete_default": DEFAULT_PERMANENT_DELETE,
    }
    kwargs.update(overrides)
    return _backup_options_schema(**kwargs)


def test_options_schema_defaults() -> None:
    """Empty input is filled with the provided defaults."""
    assert _schema()({}) == {
        CONF_BACKUP_FOLDER: DEFAULT_BACKUP_FOLDER,
        CONF_UPLOAD_TIMEOUT_SECONDS: DEFAULT_UPLOAD_TIMEOUT_SECONDS,
        CONF_PERMANENT_DELETE: False,
    }
    assert _schema(permanent_delete_default=True)({})[CONF_PERMANENT_DELETE] is True


@pytest.mark.parametrize(
    "timeout", [MIN_UPLOAD_TIMEOUT_SECONDS, MAX_UPLOAD_TIMEOUT_SECONDS, "3600"]
)
def test_options_schema_accepts_timeouts_in_range(timeout: int | str) -> None:
    """Timeouts within [min, max] are accepted and coerced to int."""
    result = _schema()({CONF_UPLOAD_TIMEOUT_SECONDS: timeout})
    assert result[CONF_UPLOAD_TIMEOUT_SECONDS] == int(timeout)


@pytest.mark.parametrize(
    "timeout", [MIN_UPLOAD_TIMEOUT_SECONDS - 1, MAX_UPLOAD_TIMEOUT_SECONDS + 1, "abc"]
)
def test_options_schema_rejects_invalid_timeouts(timeout: int | str) -> None:
    """Out-of-range or non-numeric timeouts are rejected."""
    with pytest.raises(vol.Invalid):
        _schema()({CONF_UPLOAD_TIMEOUT_SECONDS: timeout})


# --- Options flow -----------------------------------------------------------


async def test_options_flow_shows_current_values(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """The options form is pre-filled from the entry options."""
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert result["data_schema"]({}) == {
        CONF_BACKUP_FOLDER: BACKUP_FOLDER,
        CONF_UPLOAD_TIMEOUT_SECONDS: 3600,
        CONF_PERMANENT_DELETE: False,
    }


async def test_options_flow_persists_options(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Folder, timeout and permanent_delete are stored on the entry."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=USER_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == USER_INPUT


async def test_options_flow_rejects_too_small_timeout(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """The flow manager rejects input outside the timeout range."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)

    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={**USER_INPUT, CONF_UPLOAD_TIMEOUT_SECONDS: 10},
        )


# --- folder_path step -------------------------------------------------------


@pytest.fixture
def mock_flow_api() -> Generator[MagicMock]:
    """Patch PCloudAPI as used by the config flow."""
    with patch("custom_components.pcloud_backup.config_flow.PCloudAPI", autospec=True) as cls:
        api = cls.return_value
        api.async_test_connection = AsyncMock(
            return_value=load_json_fixture("userinfo_sample.json")
        )
        api.async_get_folder_id = AsyncMock(return_value=42)
        api.async_close = AsyncMock()
        yield api


def _flow(hass: HomeAssistant) -> PCloudConfigFlow:
    """Return a flow that has completed OAuth (EU account)."""
    flow = PCloudConfigFlow()
    flow.hass = hass
    flow.handler = DOMAIN
    flow.context = {"source": SOURCE_USER}
    flow.flow_impl = MagicMock(
        _oauth_data={"region": "eu", "hostname": "eapi.pcloud.com", "locationid": 2}
    )
    return flow


async def test_oauth_create_entry_shows_folder_form(hass: HomeAssistant) -> None:
    """After OAuth the user is asked for the backup folder settings."""
    flow = _flow(hass)

    result = await flow.async_oauth_create_entry(TOKEN_DATA)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "folder_path"
    assert result["errors"] == {}
    assert flow._oauth_region == "eu"


async def test_folder_path_creates_entry(hass: HomeAssistant, mock_flow_api: MagicMock) -> None:
    """Valid input creates the entry with region data and options."""
    flow = _flow(hass)
    await flow.async_oauth_create_entry(TOKEN_DATA)

    result = await flow.async_step_folder_path(USER_INPUT)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "pCloud Backup (EU)"
    assert result["data"] == {
        **TOKEN_DATA,
        CONF_REGION: "eu",
        "hostname": "eapi.pcloud.com",
        "locationid": 2,
    }
    assert result["options"] == USER_INPUT
    assert flow.unique_id == "user@example.com"
    mock_flow_api.async_get_folder_id.assert_awaited_once_with("/My Backups")
    mock_flow_api.async_close.assert_awaited_once()


async def test_folder_path_invalid_folder(hass: HomeAssistant, mock_flow_api: MagicMock) -> None:
    """A folder that cannot be resolved/created shows invalid_folder_path."""
    mock_flow_api.async_get_folder_id.side_effect = PCloudAPIError("Invalid folder name")
    flow = _flow(hass)
    await flow.async_oauth_create_entry(TOKEN_DATA)

    result = await flow.async_step_folder_path(USER_INPUT)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "folder_path"
    assert result["errors"] == {"base": "invalid_folder_path"}
    mock_flow_api.async_close.assert_awaited_once()


@pytest.mark.parametrize(
    ("error", "expected"),
    [(PCloudAPIError("down"), "cannot_connect"), (RuntimeError("boom"), "unknown")],
)
async def test_folder_path_connection_errors(
    hass: HomeAssistant, mock_flow_api: MagicMock, error: Exception, expected: str
) -> None:
    """Connection test failures are mapped to form errors."""
    mock_flow_api.async_test_connection.side_effect = error
    flow = _flow(hass)
    await flow.async_oauth_create_entry(TOKEN_DATA)

    result = await flow.async_step_folder_path(USER_INPUT)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


async def test_folder_path_already_configured(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_flow_api: MagicMock
) -> None:
    """Re-adding the same pCloud account aborts."""
    config_entry.add_to_hass(hass)
    flow = _flow(hass)
    await flow.async_oauth_create_entry(TOKEN_DATA)

    with pytest.raises(AbortFlow) as exc_info:
        await flow.async_step_folder_path(USER_INPUT)

    assert exc_info.value.reason == "already_configured"
