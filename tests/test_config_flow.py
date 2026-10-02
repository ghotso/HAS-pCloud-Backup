"""Tests for the pCloud Backup config and options flows."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import AbortFlow, FlowResultType, InvalidData
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from custom_components.pcloud_backup.api import PCloudAPIError, PCloudAuthError
from custom_components.pcloud_backup.config_flow import (
    PCloudConfigFlow,
    _backup_options_schema,
)
from custom_components.pcloud_backup.const import (
    CONF_BACKUP_FOLDER,
    CONF_REGION,
    CONF_UPLOAD_TIMEOUT_SECONDS,
    DEFAULT_BACKUP_FOLDER,
    DEFAULT_UPLOAD_TIMEOUT_SECONDS,
    DOMAIN,
    MAX_UPLOAD_TIMEOUT_SECONDS,
    MIN_UPLOAD_TIMEOUT_SECONDS,
)

from .common import ACCESS_TOKEN, BACKUP_FOLDER, ENTRY_DATA, ENTRY_OPTIONS, load_json_fixture

USER_INPUT = {
    CONF_BACKUP_FOLDER: "/My Backups",
    CONF_UPLOAD_TIMEOUT_SECONDS: 7200,
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
    }
    kwargs.update(overrides)
    return _backup_options_schema(**kwargs)


def test_options_schema_defaults() -> None:
    """Empty input is filled with the provided defaults."""
    assert _schema()({}) == {
        CONF_BACKUP_FOLDER: DEFAULT_BACKUP_FOLDER,
        CONF_UPLOAD_TIMEOUT_SECONDS: DEFAULT_UPLOAD_TIMEOUT_SECONDS,
    }


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
    }


async def test_options_flow_persists_options(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Folder and timeout are stored on the entry."""
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


# --- Reauthentication ---------------------------------------------------------

NEW_TOKEN_DATA = {
    "auth_implementation": DOMAIN,
    "token": {"access_token": "new-token", "token_type": "bearer", "expires_in": 315360000},
}


@pytest.fixture
def fake_oauth() -> Generator[None]:
    """Skip the browser OAuth round trip: the auth step returns a new token."""

    async def _fake_auth(self: PCloudConfigFlow, user_input=None):
        self.flow_impl._oauth_data = {
            "region": "eu",
            "hostname": "eapi.pcloud.com",
            "locationid": 2,
        }
        return await self.async_oauth_create_entry(NEW_TOKEN_DATA)

    with patch.object(PCloudConfigFlow, "async_step_auth", _fake_auth):
        yield


async def _start_reauth(hass: HomeAssistant, entry: MockConfigEntry) -> dict:
    entry.async_start_reauth(hass)
    await hass.async_block_till_done()
    (flow,) = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    return flow


async def test_reauth_flow_updates_token_and_reloads(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_pcloud: dict[str, AsyncMock],
    mock_flow_api: MagicMock,
    fake_oauth: None,
) -> None:
    """Re-linking the same account stores the new token, keeps options and reloads."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_pcloud["async_test_connection"].await_count == 1

    flow = await _start_reauth(hass, config_entry)
    assert flow["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(flow["flow_id"])

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], user_input={})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data == {**ENTRY_DATA, **NEW_TOKEN_DATA}
    assert config_entry.options == ENTRY_OPTIONS
    assert config_entry.unique_id == "user@example.com"
    mock_flow_api.async_get_folder_id.assert_not_called()  # no folder_path step
    mock_flow_api.async_close.assert_awaited_once()
    # Reloaded: setup ran again with the new token. (The entry's update listener
    # may reload it once more, so only check that a reload happened.)
    assert config_entry.state is ConfigEntryState.LOADED
    assert mock_pcloud["async_test_connection"].await_count >= 2


async def test_reauth_after_failed_setup_loads_entry(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_pcloud: dict[str, AsyncMock],
    mock_flow_api: MagicMock,
    fake_oauth: None,
) -> None:
    """End to end: revoked token fails setup, reauth fixes it and the entry loads."""
    mock_pcloud["async_test_connection"].side_effect = [
        PCloudAuthError("pCloud authentication failed: Log in required."),
        load_json_fixture("userinfo_sample.json"),
    ]
    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR

    (flow,) = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], user_input={})
    await hass.async_block_till_done()

    assert result["reason"] == "reauth_successful"
    assert config_entry.data["token"]["access_token"] == "new-token"
    assert config_entry.state is ConfigEntryState.LOADED


async def test_reauth_flow_wrong_account(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_flow_api: MagicMock,
    fake_oauth: None,
) -> None:
    """Signing in to a different pCloud account aborts and keeps the old token."""
    mock_flow_api.async_test_connection.return_value = {"result": 0, "email": "other@example.com"}
    config_entry.add_to_hass(hass)

    flow = await _start_reauth(hass, config_entry)
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], user_input={})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert config_entry.data == ENTRY_DATA


async def test_reauth_flow_cannot_connect(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_flow_api: MagicMock,
    fake_oauth: None,
) -> None:
    """If the new token cannot be verified, the flow aborts without changes."""
    mock_flow_api.async_test_connection.side_effect = PCloudAPIError("timed out")
    config_entry.add_to_hass(hass)

    flow = await _start_reauth(hass, config_entry)
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], user_input={})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"
    assert config_entry.data == ENTRY_DATA
    mock_flow_api.async_close.assert_awaited_once()
