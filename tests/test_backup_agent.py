"""Tests for the pCloud backup agent (list/get/delete/upload/download)."""

from __future__ import annotations

from collections.abc import AsyncIterator
import json
import logging
from unittest.mock import call

from homeassistant.components.backup import AgentBackup, BackupAgentError, BackupNotFound
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pcloud_backup.api import PCloudAPIError
from custom_components.pcloud_backup.backup import (
    METADATA_VERSION,
    PCloudBackupAgent,
    PCloudBackupError,
)
from custom_components.pcloud_backup.const import (
    CONF_PERMANENT_DELETE,
    CONF_UPLOAD_TIMEOUT_SECONDS,
    DOMAIN,
)

from .common import (
    AGENT_ID,
    BACKUP_FOLDER,
    BACKUP_FOLDER_ID,
    ENTRY_ID,
    ORPHAN_FILE_ID,
    ORPHAN_KEY,
    PAIRED_FILE_ID,
    PAIRED_KEY,
    PAIRED_METADATA_FILE_ID,
    load_json_fixture,
    metadata_bytes,
)

ORPHAN_BACKUP_ID = f"{AGENT_ID}:{ORPHAN_KEY}"


@pytest.fixture
def agent(hass: HomeAssistant, config_entry: MockConfigEntry, mock_api) -> PCloudBackupAgent:
    """Return an agent whose config entry exists (not set up) and API is mocked."""
    config_entry.add_to_hass(hass)
    return PCloudBackupAgent(hass, ENTRY_ID, api=mock_api, name=config_entry.title)


def _set_options(hass: HomeAssistant, entry_id: str = ENTRY_ID, **options) -> None:
    entry = hass.config_entries.async_get_entry(entry_id)
    hass.config_entries.async_update_entry(entry, options={**entry.options, **options})


def _agent_backup(**overrides) -> AgentBackup:
    data = {
        "addons": [],
        "backup_id": "a1b2c3d4",
        "date": "2025-01-02T03:00:00+00:00",
        "database_included": True,
        "extra_metadata": {"instance_id": "abc"},
        "folders": [],
        "homeassistant_included": True,
        "homeassistant_version": "2025.1.0",
        "name": "Automatic backup 2025.1.0",
        "protected": True,
        "size": 1000,
    }
    data.update(overrides)
    return AgentBackup.from_dict(data)


def _metadata_with(**backup_overrides) -> bytes:
    payload = load_json_fixture("metadata_v2_sample.json")
    payload["backup"].update(backup_overrides)
    return metadata_bytes(payload)


# --- list -------------------------------------------------------------------


async def test_list_backups_pairs_tar_and_metadata(agent: PCloudBackupAgent, mock_api) -> None:
    """A .tar with a .metadata.json is restored from metadata; orphans use file info."""
    backups = await agent.async_list_backups()

    mock_api.async_get_folder_id.assert_awaited_once_with(BACKUP_FOLDER)
    mock_api.async_list_folder.assert_awaited_once_with(BACKUP_FOLDER_ID)
    mock_api.async_download_file.assert_awaited_once_with(PAIRED_METADATA_FILE_ID)

    # Sorted newest first; metadata files are not listed as backups.
    assert [b.backup_id for b in backups] == ["a1b2c3d4", ORPHAN_BACKUP_ID]

    paired, orphan = backups
    assert paired.name == "Automatic backup 2025.1.0"
    assert paired.protected is True
    assert paired.homeassistant_version == "2025.1.0"
    # Size always comes from the actual file on pCloud.
    assert paired.size == 1048576

    assert orphan.name == "Manual backup 2024-12-01.tar"
    assert orphan.size == 524288
    assert orphan.date == "2024-12-01T03:00:00+00:00"
    assert orphan.protected is False


async def test_list_backups_api_error_returns_empty(agent: PCloudBackupAgent, mock_api) -> None:
    """API failures are logged and an empty list is returned."""
    mock_api.async_get_folder_id.side_effect = PCloudAPIError("down")
    assert await agent.async_list_backups() == []


async def test_list_backups_without_config_entry(hass: HomeAssistant, mock_api) -> None:
    """A missing config entry yields no backups."""
    agent = PCloudBackupAgent(hass, "missing", api=mock_api)
    assert await agent.async_list_backups() == []
    mock_api.async_get_folder_id.assert_not_called()


async def test_metadata_restores_original_backup_id(agent: PCloudBackupAgent, mock_api) -> None:
    """Slug-format IDs stored by older versions are mapped back to the original ID."""
    mock_api.async_download_file.return_value = _metadata_with(backup_id=f"{AGENT_ID}:{PAIRED_KEY}")

    backups = await agent.async_list_backups()

    assert backups[0].backup_id == "a1b2c3d4"


async def test_metadata_slug_id_kept_without_original(agent: PCloudBackupAgent, mock_api) -> None:
    """Without original_backup_id the slug-format ID is kept."""
    mock_api.async_download_file.return_value = _metadata_with(
        backup_id=f"{AGENT_ID}:{PAIRED_KEY}", extra_metadata={}
    )

    backups = await agent.async_list_backups()

    assert backups[0].backup_id == f"{AGENT_ID}:{PAIRED_KEY}"


async def test_metadata_v1_without_wrapper(agent: PCloudBackupAgent, mock_api) -> None:
    """Legacy metadata (no version, no "backup" wrapper) is still understood."""
    payload = load_json_fixture("metadata_v2_sample.json")["backup"]
    del payload["extra_metadata"]
    mock_api.async_download_file.return_value = metadata_bytes(payload)

    backups = await agent.async_list_backups()

    assert backups[0].backup_id == "a1b2c3d4"
    assert backups[0].extra_metadata == {}


@pytest.mark.parametrize(
    "raw",
    [
        metadata_bytes({"metadata_version": 99, "backup": {"backup_id": "x"}}),
        b"{not json",
    ],
    ids=["unsupported-version", "corrupt-json"],
)
async def test_unusable_metadata_falls_back_to_file(
    agent: PCloudBackupAgent, mock_api, raw: bytes
) -> None:
    """Unsupported or broken metadata falls back to the file-based backup."""
    mock_api.async_download_file.return_value = raw

    backups = await agent.async_list_backups()

    paired = next(b for b in backups if b.backup_id == f"{AGENT_ID}:{PAIRED_KEY}")
    assert paired.name == f"{PAIRED_KEY.replace('_', ' ')}.tar"
    assert paired.size == 1048576
    assert paired.extra_metadata == {}


# --- get --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("identifier", "expected_id"),
    [
        ("a1b2c3d4", "a1b2c3d4"),
        (f"{AGENT_ID}:{PAIRED_KEY}", "a1b2c3d4"),
        (f"{PAIRED_KEY}.tar", "a1b2c3d4"),
        (ORPHAN_BACKUP_ID, ORPHAN_BACKUP_ID),
    ],
)
async def test_get_backup_found(
    agent: PCloudBackupAgent, identifier: str, expected_id: str
) -> None:
    """Backups can be looked up by backup_id, slug ID or filename."""
    backup = await agent.async_get_backup(identifier)
    assert backup.backup_id == expected_id


async def test_get_backup_not_found(agent: PCloudBackupAgent) -> None:
    """Unknown backups raise BackupNotFound."""
    with pytest.raises(BackupNotFound):
        await agent.async_get_backup("does-not-exist")


async def test_get_backup_api_error(agent: PCloudBackupAgent, mock_api) -> None:
    """API errors surface as BackupAgentError."""
    mock_api.async_list_folder.side_effect = PCloudAPIError("down")
    with pytest.raises(BackupAgentError, match="down"):
        await agent.async_get_backup("a1b2c3d4")


# --- delete -----------------------------------------------------------------


async def test_delete_backup_moves_to_trash(agent: PCloudBackupAgent, mock_api) -> None:
    """Default delete removes backup + metadata but keeps them in Trash."""
    await agent.async_delete_backup("a1b2c3d4")

    assert mock_api.async_delete_file.await_args_list == [
        call(PAIRED_FILE_ID),
        call(PAIRED_METADATA_FILE_ID),
    ]
    mock_api.async_trash_clear.assert_not_called()


async def test_delete_backup_without_metadata(agent: PCloudBackupAgent, mock_api) -> None:
    """Backups without a metadata file only delete the archive."""
    await agent.async_delete_backup(ORPHAN_BACKUP_ID)

    mock_api.async_delete_file.assert_awaited_once_with(ORPHAN_FILE_ID)


async def test_delete_backup_permanent(
    hass: HomeAssistant, agent: PCloudBackupAgent, mock_api, caplog: pytest.LogCaptureFixture
) -> None:
    """permanent_delete also purges both files from Trash."""
    _set_options(hass, **{CONF_PERMANENT_DELETE: True})

    await agent.async_delete_backup("a1b2c3d4")

    assert mock_api.async_delete_file.await_args_list == [
        call(PAIRED_FILE_ID),
        call(PAIRED_METADATA_FILE_ID),
    ]
    assert mock_api.async_trash_clear.await_args_list == [
        call(PAIRED_FILE_ID),
        call(PAIRED_METADATA_FILE_ID),
    ]
    assert "permanently purging" not in caplog.text


async def test_delete_backup_permanent_trash_clear_failure_warns(
    hass: HomeAssistant, agent: PCloudBackupAgent, mock_api, caplog: pytest.LogCaptureFixture
) -> None:
    """A failing trash_clear does not fail the delete but logs a warning."""
    _set_options(hass, **{CONF_PERMANENT_DELETE: True})
    mock_api.async_trash_clear.side_effect = [PCloudAPIError("trash busy"), None]

    with caplog.at_level(logging.WARNING):
        await agent.async_delete_backup("a1b2c3d4")

    assert mock_api.async_delete_file.await_count == 2
    assert mock_api.async_trash_clear.await_count == 2
    assert "Failed to purge backup for backup a1b2c3d4 from Trash: trash busy" in caplog.text
    assert "permanently purging one or more related files" in caplog.text


async def test_delete_metadata_failure_is_not_fatal(
    agent: PCloudBackupAgent, mock_api, caplog: pytest.LogCaptureFixture
) -> None:
    """Failing to delete the metadata file only logs a warning."""
    mock_api.async_delete_file.side_effect = [None, PCloudAPIError("meta gone")]

    await agent.async_delete_backup("a1b2c3d4")

    assert "Failed to delete metadata for backup a1b2c3d4" in caplog.text


async def test_delete_backup_not_found(agent: PCloudBackupAgent, mock_api) -> None:
    """Deleting an unknown backup raises BackupNotFound and deletes nothing."""
    with pytest.raises(BackupNotFound):
        await agent.async_delete_backup("does-not-exist")
    mock_api.async_delete_file.assert_not_called()


async def test_delete_backup_api_error(agent: PCloudBackupAgent, mock_api) -> None:
    """A failing deletefile surfaces as BackupAgentError."""
    mock_api.async_delete_file.side_effect = PCloudAPIError("denied")
    with pytest.raises(PCloudBackupError, match="denied"):
        await agent.async_delete_backup("a1b2c3d4")


@pytest.mark.parametrize("file_id", [None, 0, -1, "101"])
async def test_trash_clear_skips_invalid_file_ids(
    agent: PCloudBackupAgent, mock_api, file_id: object
) -> None:
    """Invalid file IDs are never sent to trash_clear."""
    assert await agent._async_trash_clear(file_id, "b", "backup") is False
    mock_api.async_trash_clear.assert_not_called()


# --- upload -----------------------------------------------------------------


async def _open_stream() -> AsyncIterator[bytes]:
    async def _gen() -> AsyncIterator[bytes]:
        yield b"chunk"

    return _gen()


async def test_upload_backup(hass: HomeAssistant, agent: PCloudBackupAgent, mock_api) -> None:
    """Upload streams the archive, then writes v2 metadata with the original ID."""
    mock_api.async_list_folder.return_value = []
    backup = _agent_backup()

    await agent.async_upload_backup(open_stream=_open_stream, backup=backup)

    mock_api.async_delete_file.assert_not_called()
    mock_api.async_upload_file_from_stream.assert_awaited_once()
    args, kwargs = mock_api.async_upload_file_from_stream.await_args
    assert args[:2] == (BACKUP_FOLDER_ID, f"{PAIRED_KEY}.tar")
    assert kwargs == {"file_size": 1000, "upload_total_seconds": 3600}

    folder_id, filename, payload = mock_api.async_upload_file.await_args.args
    assert (folder_id, filename) == (BACKUP_FOLDER_ID, f"{PAIRED_KEY}.metadata.json")
    metadata = json.loads(payload)
    assert metadata["metadata_version"] == METADATA_VERSION
    assert metadata["backup"]["backup_id"] == "a1b2c3d4"
    assert metadata["backup"]["extra_metadata"] == {
        "instance_id": "abc",
        "original_backup_id": "a1b2c3d4",
        "slug_backup_id": f"{AGENT_ID}:{PAIRED_KEY}",
    }


async def test_upload_backup_replaces_existing_files(
    hass: HomeAssistant, agent: PCloudBackupAgent, mock_api
) -> None:
    """Existing files with the same key are removed before uploading (dedup)."""
    _set_options(hass, **{CONF_UPLOAD_TIMEOUT_SECONDS: 7200})

    await agent.async_upload_backup(open_stream=_open_stream, backup=_agent_backup())

    assert mock_api.async_delete_file.await_args_list == [
        call(PAIRED_FILE_ID),
        call(PAIRED_METADATA_FILE_ID),
    ]
    assert mock_api.async_upload_file_from_stream.await_args.kwargs["upload_total_seconds"] == 7200


async def test_upload_backup_unknown_size(agent: PCloudBackupAgent, mock_api) -> None:
    """A zero size is passed on as unknown (temp-file path)."""
    mock_api.async_list_folder.return_value = []

    await agent.async_upload_backup(open_stream=_open_stream, backup=_agent_backup(size=0))

    assert mock_api.async_upload_file_from_stream.await_args.kwargs["file_size"] is None


async def test_upload_backup_stream_failure(agent: PCloudBackupAgent, mock_api) -> None:
    """Upload errors surface as BackupAgentError and no metadata is written."""
    mock_api.async_list_folder.return_value = []
    mock_api.async_upload_file_from_stream.side_effect = PCloudAPIError("Over quota")

    with pytest.raises(PCloudBackupError, match="Over quota"):
        await agent.async_upload_backup(open_stream=_open_stream, backup=_agent_backup())

    mock_api.async_upload_file.assert_not_called()


async def test_upload_metadata_failure_removes_backup(agent: PCloudBackupAgent, mock_api) -> None:
    """If metadata upload fails, the just-uploaded archive is cleaned up."""
    # First listing (dedup) is empty, second (cleanup) finds the new archive.
    mock_api.async_list_folder.side_effect = [
        [],
        [{"name": f"{PAIRED_KEY}.tar", "fileid": 555, "size": 1000}],
    ]
    mock_api.async_upload_file.side_effect = PCloudAPIError("metadata failed")

    with pytest.raises(PCloudBackupError, match="metadata failed"):
        await agent.async_upload_backup(open_stream=_open_stream, backup=_agent_backup())

    mock_api.async_delete_file.assert_awaited_once_with(555)


# --- download ---------------------------------------------------------------


async def test_download_backup(agent: PCloudBackupAgent, mock_api) -> None:
    """Downloads stream the archive file of the matching backup."""
    stream = await agent.async_download_backup("a1b2c3d4")

    mock_api.async_download_file_stream.assert_called_once_with(PAIRED_FILE_ID)
    assert stream is mock_api.async_download_file_stream.return_value


async def test_download_backup_not_found(agent: PCloudBackupAgent) -> None:
    """Unknown backups raise BackupNotFound."""
    with pytest.raises(BackupNotFound):
        await agent.async_download_backup("does-not-exist")


# --- availability / API lookup ----------------------------------------------


def test_available_with_cached_api(agent: PCloudBackupAgent) -> None:
    """An agent with an API instance is available."""
    assert agent.available is True


def test_available_depends_on_domain_data(hass: HomeAssistant) -> None:
    """Without a cached API the agent is available once the domain is loaded."""
    agent = PCloudBackupAgent(hass, ENTRY_ID)
    assert agent.available is False

    hass.data[DOMAIN] = {}
    assert agent.available is True


def test_api_lookup(hass: HomeAssistant, mock_api) -> None:
    """The API is lazily resolved from hass.data and cached."""
    agent = PCloudBackupAgent(hass, ENTRY_ID)
    with pytest.raises(PCloudBackupError, match="not initialized"):
        _ = agent.api

    hass.data[DOMAIN] = {ENTRY_ID: mock_api}
    assert agent.api is mock_api
    hass.data[DOMAIN] = {}
    assert agent.api is mock_api
