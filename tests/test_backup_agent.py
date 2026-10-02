"""Tests for the pCloud backup agent (list/get/delete/upload/download)."""

from __future__ import annotations

from collections.abc import AsyncIterator
import json
from unittest.mock import call

from homeassistant.components.backup import AgentBackup, BackupAgentError, BackupNotFound
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pcloud_backup.api import PCloudAPIError, PCloudAuthError
from custom_components.pcloud_backup.backup import (
    METADATA_VERSION,
    BackupMetadataCache,
    PCloudBackupAgent,
    PCloudBackupAuthError,
    PCloudBackupError,
)
from custom_components.pcloud_backup.const import (
    CONF_UPLOAD_TIMEOUT_SECONDS,
    DATA_METADATA_CACHE,
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
    listfolder_files,
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


# --- metadata cache -----------------------------------------------------------


@pytest.fixture
def metadata_cache(hass: HomeAssistant) -> BackupMetadataCache:
    """Install the per-entry metadata cache as entry setup does."""
    cache = BackupMetadataCache()
    hass.data[DATA_METADATA_CACHE] = {ENTRY_ID: cache}
    return cache


def _with_metadata_item(**changes) -> list[dict]:
    files = listfolder_files()
    for item in files:
        if item["fileid"] == PAIRED_METADATA_FILE_ID:
            item.update(changes)
    return files


async def test_metadata_cache_hit_avoids_download(
    hass: HomeAssistant, agent: PCloudBackupAgent, mock_api, metadata_cache
) -> None:
    """Unchanged metadata files are downloaded once, even across new agent objects."""
    first = await agent.async_list_backups()
    # HA re-creates agents when it reloads them; the cache lives per entry.
    second_agent = PCloudBackupAgent(hass, ENTRY_ID, api=mock_api)
    second = await second_agent.async_list_backups()

    assert first == second
    assert second[0].backup_id == "a1b2c3d4"
    mock_api.async_download_file.assert_awaited_once_with(PAIRED_METADATA_FILE_ID)
    assert mock_api.async_list_folder.await_count == 2
    assert len(metadata_cache) == 1


async def test_metadata_cache_payload_not_mutated(
    agent: PCloudBackupAgent, mock_api, metadata_cache
) -> None:
    """Building the AgentBackup does not alter the cached payload."""
    await agent.async_list_backups()
    backups = await agent.async_list_backups()

    assert backups[0].backup_id == "a1b2c3d4"
    assert backups[0].size == 1048576
    cached = metadata_cache.get(PAIRED_METADATA_FILE_ID, 2222222222222222222)
    assert cached == load_json_fixture("metadata_v2_sample.json")


async def test_metadata_cache_miss_on_hash_change(
    agent: PCloudBackupAgent, mock_api, metadata_cache
) -> None:
    """A changed content hash invalidates the cached payload."""
    await agent.async_list_backups()
    mock_api.async_list_folder.return_value = _with_metadata_item(hash=999)
    mock_api.async_download_file.return_value = _metadata_with(name="Renamed")

    backups = await agent.async_list_backups()

    assert backups[0].name == "Renamed"
    assert mock_api.async_download_file.await_count == 2


async def test_metadata_without_hash_is_not_cached(
    agent: PCloudBackupAgent, mock_api, metadata_cache
) -> None:
    """Listings without a content hash always download the metadata."""
    files = _with_metadata_item()
    for item in files:
        item.pop("hash", None)
    mock_api.async_list_folder.return_value = files

    await agent.async_list_backups()
    await agent.async_list_backups()

    assert mock_api.async_download_file.await_count == 2
    assert len(metadata_cache) == 0


async def test_failed_metadata_load_is_not_cached(
    agent: PCloudBackupAgent, mock_api, metadata_cache
) -> None:
    """A transient failure falls back to file info and is retried next listing."""
    mock_api.async_download_file.side_effect = [PCloudAPIError("timed out"), metadata_bytes()]

    first = await agent.async_list_backups()
    second = await agent.async_list_backups()

    assert first[0].backup_id == f"{AGENT_ID}:{PAIRED_KEY}"
    assert second[0].backup_id == "a1b2c3d4"
    assert mock_api.async_download_file.await_count == 2


async def test_metadata_cache_pruned_to_current_listing(
    agent: PCloudBackupAgent, mock_api, metadata_cache
) -> None:
    """Entries for metadata files that disappeared from the folder are dropped."""
    metadata_cache.set(9999, 1, {"stale": True})
    await agent.async_list_backups()
    assert metadata_cache.get(9999, 1) is None
    assert len(metadata_cache) == 1

    mock_api.async_list_folder.return_value = [
        item for item in listfolder_files() if item["fileid"] != PAIRED_METADATA_FILE_ID
    ]
    await agent.async_list_backups()

    assert len(metadata_cache) == 0


def test_metadata_cache_class() -> None:
    """get() misses on unknown ids and hash changes; prune()/clear() bound the cache."""
    cache = BackupMetadataCache()
    cache.set(1, "a", {"x": 1})
    cache.set(2, "b", {"y": 2})

    assert cache.get(1, "a") == {"x": 1}
    assert cache.get(1, "changed") is None
    assert cache.get(3, "a") is None

    cache.prune([2, 3])
    assert (cache.get(1, "a"), len(cache)) == (None, 1)
    cache.clear()
    assert len(cache) == 0


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
    """Delete removes backup + metadata (pCloud keeps them in its Trash)."""
    await agent.async_delete_backup("a1b2c3d4")

    assert mock_api.async_delete_file.await_args_list == [
        call(PAIRED_FILE_ID),
        call(PAIRED_METADATA_FILE_ID),
    ]


async def test_delete_backup_without_metadata(agent: PCloudBackupAgent, mock_api) -> None:
    """Backups without a metadata file only delete the archive."""
    await agent.async_delete_backup(ORPHAN_BACKUP_ID)

    mock_api.async_delete_file.assert_awaited_once_with(ORPHAN_FILE_ID)


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


# --- authentication errors / reauth -------------------------------------------


def _reauth_flows(hass: HomeAssistant) -> list:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"]["source"] == SOURCE_REAUTH
    ]


async def _call_agent(agent: PCloudBackupAgent, operation: str) -> None:
    if operation == "list":
        await agent.async_list_backups()
    elif operation == "get":
        await agent.async_get_backup("a1b2c3d4")
    elif operation == "delete":
        await agent.async_delete_backup("a1b2c3d4")
    elif operation == "download":
        await agent.async_download_backup("a1b2c3d4")
    else:
        await agent.async_upload_backup(open_stream=_open_stream, backup=_agent_backup())


OPERATIONS = ["list", "get", "delete", "download", "upload"]


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_auth_error_starts_reauth(
    hass: HomeAssistant, agent: PCloudBackupAgent, mock_api, operation: str
) -> None:
    """A rejected token starts reauth and raises a BackupAgentError with a clear message."""
    mock_api.async_get_folder_id.side_effect = PCloudAuthError(
        "pCloud authentication failed: Log in required."
    )

    with pytest.raises(PCloudBackupAuthError, match="Re-authenticate") as exc_info:
        await _call_agent(agent, operation)
    await hass.async_block_till_done()

    assert isinstance(exc_info.value, BackupAgentError)
    (flow,) = _reauth_flows(hass)
    assert flow["context"]["entry_id"] == ENTRY_ID
    assert flow["step_id"] == "reauth_confirm"


async def test_repeated_auth_errors_start_one_reauth_flow(
    hass: HomeAssistant, agent: PCloudBackupAgent, mock_api
) -> None:
    """Home Assistant de-duplicates reauth flows for the same entry."""
    mock_api.async_get_folder_id.side_effect = PCloudAuthError("Log in required.")

    for _ in range(2):
        with pytest.raises(PCloudBackupAuthError):
            await agent.async_list_backups()
        await hass.async_block_till_done()

    assert len(_reauth_flows(hass)) == 1


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_non_auth_errors_do_not_start_reauth(
    hass: HomeAssistant, agent: PCloudBackupAgent, mock_api, operation: str
) -> None:
    """Other API errors keep their behaviour and never start reauth."""
    mock_api.async_get_folder_id.side_effect = PCloudAPIError("down")

    try:
        await _call_agent(agent, operation)
    except PCloudBackupError as err:
        assert not isinstance(err, PCloudBackupAuthError)
    await hass.async_block_till_done()

    assert _reauth_flows(hass) == []
