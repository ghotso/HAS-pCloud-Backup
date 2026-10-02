"""Tests for PCloudAPI parsing helpers, endpoint contracts and upload path selection."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import aiohttp
from aioresponses import aioresponses
from homeassistant.core import HomeAssistant
import pytest

from custom_components.pcloud_backup.api import (
    DEFAULT_TRANSFER_TOTAL_SECONDS,
    PCloudAPI,
    PCloudAPIError,
    _upload_client_timeout,
)
from custom_components.pcloud_backup.auth import PCloudOAuth2Auth
from custom_components.pcloud_backup.const import API_BASE_EU, API_BASE_US

from .common import ACCESS_TOKEN, EU_BASE, load_json_fixture, request_calls


@pytest.fixture
def api(hass: HomeAssistant) -> PCloudAPI:
    """PCloudAPI (EU) using a static OAuth2 token."""
    return PCloudAPI(hass, "eu", PCloudOAuth2Auth(hass, "eu", access_token=ACCESS_TOKEN))


# --- Pure helpers -----------------------------------------------------------


@pytest.mark.parametrize(
    ("region", "base"),
    [("eu", API_BASE_EU), ("EU", API_BASE_EU), ("us", API_BASE_US), ("other", API_BASE_US)],
)
def test_base_url_selection(region: str, base: str) -> None:
    """EU selects eapi.pcloud.com, anything else the US endpoint."""
    api = PCloudAPI(None, region, PCloudOAuth2Auth(None, region, access_token="x"))
    assert api._base_url == base


def test_upload_client_timeout() -> None:
    """Upload timeout keeps a 30 s connect timeout and the configured total."""
    timeout = _upload_client_timeout(600)
    assert timeout.connect == 30
    assert timeout.total == 600.0


@pytest.mark.parametrize(
    "modified",
    [
        1735787100,
        "1735787100",
        "Thu, 02 Jan 2025 03:05:00 +0000",
        "Thu, 02 Jan 2025 03:05:00 -0000",
    ],
)
def test_parse_backup_info_modified_formats(api: PCloudAPI, modified: object) -> None:
    """Unix numbers/strings and RFC 2822 dates all parse to the same UTC instant."""
    info = api.parse_backup_info({"fileid": 1, "name": "a.tar", "size": 10, "modified": modified})
    expected = datetime(2025, 1, 2, 3, 5, tzinfo=UTC).isoformat()
    assert info == {
        "fileid": 1,
        "name": "a.tar",
        "size": 10,
        "modified": expected,
        "created": expected,
    }


@pytest.mark.parametrize("item", [{}, {"modified": 0}, {"modified": "not a date"}])
def test_parse_backup_info_missing_or_invalid_modified(api: PCloudAPI, item: dict) -> None:
    """Missing/invalid timestamps fall back to "now" and defaults are filled in."""
    before = datetime.now(UTC)
    info = api.parse_backup_info(item)
    after = datetime.now(UTC)
    assert before <= datetime.fromisoformat(info["modified"]) <= after
    assert info["name"] == ""
    assert info["size"] == 0
    assert info["fileid"] is None


# --- Endpoint contracts -----------------------------------------------------


async def test_delete_file_contract(api: PCloudAPI) -> None:
    """async_delete_file -> POST /deletefile with fileid."""
    with aioresponses() as mocked:
        mocked.post(f"{EU_BASE}/deletefile?fileid=101", payload={"result": 0})
        await api.async_delete_file(101)

    (call,) = request_calls(mocked, "POST", "/deletefile")
    assert call.kwargs["params"] == {"fileid": 101}


async def test_trash_clear_contract(api: PCloudAPI) -> None:
    """async_trash_clear -> POST /trash_clear with fileid."""
    with aioresponses() as mocked:
        mocked.post(f"{EU_BASE}/trash_clear?fileid=101", payload={"result": 0})
        await api.async_trash_clear(101)

    (call,) = request_calls(mocked, "POST", "/trash_clear")
    assert call.kwargs["params"] == {"fileid": 101}


async def test_list_folder_returns_files_only(api: PCloudAPI) -> None:
    """async_list_folder -> GET /listfolder and drops sub-folders."""
    with aioresponses() as mocked:
        mocked.get(
            f"{EU_BASE}/listfolder?folderid=42", payload=load_json_fixture("listfolder_sample.json")
        )
        files = await api.async_list_folder(42)

    assert [item["fileid"] for item in files] == [101, 102, 201]


async def test_get_folder_id_walks_existing_path(api: PCloudAPI) -> None:
    """Existing folders are matched case-insensitively segment by segment."""
    with aioresponses() as mocked:
        mocked.get(
            f"{EU_BASE}/listfolder?folderid=0",
            payload={
                "result": 0,
                "metadata": {"contents": [{"name": "HA", "isfolder": True, "folderid": 5}]},
            },
        )
        mocked.get(
            f"{EU_BASE}/listfolder?folderid=5",
            payload={
                "result": 0,
                "metadata": {"contents": [{"name": "backups", "isfolder": True, "folderid": 9}]},
            },
        )
        assert await api.async_get_folder_id("/ha/Backups/") == 9

    assert request_calls(mocked, "POST", "/createfolder") == []


async def test_get_folder_id_creates_missing_folder(api: PCloudAPI) -> None:
    """A missing segment is created with POST /createfolder."""
    with aioresponses() as mocked:
        mocked.get(
            f"{EU_BASE}/listfolder?folderid=0", payload={"result": 0, "metadata": {"contents": []}}
        )
        mocked.post(
            f"{EU_BASE}/createfolder?folderid=0&name=Backups",
            payload={"result": 0, "metadata": {"folderid": 77}},
        )
        assert await api.async_get_folder_id("/Backups") == 77

    (call,) = request_calls(mocked, "POST", "/createfolder")
    assert call.kwargs["params"] == {"folderid": 0, "name": "Backups"}


async def test_get_folder_id_root(api: PCloudAPI) -> None:
    """The root path resolves to folder 0 without any request."""
    with aioresponses():
        assert await api.async_get_folder_id("/") == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"result": 0, "hosts": ["c1.pcloud.com", "c2.pcloud.com"], "path": "/dl/file.tar"},
        {"result": 0, "metadata": {"hosts": ["c1.pcloud.com"], "path": "/dl/file.tar"}},
    ],
)
async def test_get_file_link(api: PCloudAPI, payload: dict) -> None:
    """async_get_file_link builds https://<first host><path> from either response shape."""
    with aioresponses() as mocked:
        mocked.get(f"{EU_BASE}/getfilelink?fileid=101", payload=payload)
        assert await api.async_get_file_link(101) == "https://c1.pcloud.com/dl/file.tar"


async def test_get_file_link_invalid_response(api: PCloudAPI) -> None:
    """A response without hosts/path raises PCloudAPIError."""
    with aioresponses() as mocked:
        mocked.get(f"{EU_BASE}/getfilelink?fileid=101", payload={"result": 0})
        with pytest.raises(PCloudAPIError, match="Invalid getfilelink response"):
            await api.async_get_file_link(101)


async def test_download_file(api: PCloudAPI) -> None:
    """async_download_file resolves the link and returns the body."""
    with aioresponses() as mocked:
        mocked.get(
            f"{EU_BASE}/getfilelink?fileid=102",
            payload={"result": 0, "hosts": ["c1.pcloud.com"], "path": "/dl/meta.json"},
        )
        mocked.get("https://c1.pcloud.com/dl/meta.json", body=b'{"a": 1}')
        assert await api.async_download_file(102) == b'{"a": 1}'


async def test_download_file_http_error(api: PCloudAPI) -> None:
    """A non-200 download raises PCloudAPIError."""
    with aioresponses() as mocked:
        mocked.get(
            f"{EU_BASE}/getfilelink?fileid=102",
            payload={"result": 0, "hosts": ["c1.pcloud.com"], "path": "/dl/meta.json"},
        )
        mocked.get("https://c1.pcloud.com/dl/meta.json", status=404)
        with pytest.raises(PCloudAPIError, match="status 404"):
            await api.async_download_file(102)


async def test_test_connection_and_userinfo(api: PCloudAPI) -> None:
    """async_test_connection / async_get_userinfo -> GET /userinfo."""
    userinfo = load_json_fixture("userinfo_sample.json")
    with aioresponses() as mocked:
        mocked.get(f"{EU_BASE}/userinfo", payload=userinfo, repeat=True)
        assert await api.async_test_connection() == userinfo
        assert await api.async_get_userinfo() == userinfo


async def test_upload_file_small(api: PCloudAPI) -> None:
    """async_upload_file posts multipart data to /uploadfile with the Bearer token."""
    with aioresponses() as mocked:
        mocked.post(f"{EU_BASE}/uploadfile", payload={"result": 0, "fileids": [5]})
        result = await api.async_upload_file(42, "x.metadata.json", b"{}")

    assert result == {"result": 0, "fileids": [5]}
    (call,) = request_calls(mocked, "POST", "/uploadfile")
    assert call.kwargs["headers"]["Authorization"] == f"Bearer {ACCESS_TOKEN}"


async def test_upload_file_api_error(api: PCloudAPI) -> None:
    """A pCloud error on upload raises PCloudAPIError."""
    with aioresponses() as mocked:
        mocked.post(f"{EU_BASE}/uploadfile", payload={"result": 2008, "error": "Over quota."})
        with pytest.raises(PCloudAPIError, match="Over quota"):
            await api.async_upload_file(42, "x.metadata.json", b"{}")


# --- Upload path selection --------------------------------------------------


async def _stream():
    yield b"data"


async def test_upload_known_size_uses_fifo(api: PCloudAPI) -> None:
    """A known file size selects the FIFO path with the configured timeout."""
    stream = _stream()
    with (
        patch.object(api, "_async_upload_via_fifo", AsyncMock(return_value={"result": 0})) as fifo,
        patch.object(api, "_async_upload_via_tempfile", AsyncMock()) as tmp,
    ):
        result = await api.async_upload_file_from_stream(
            42, "b.tar", stream, file_size=1234, upload_total_seconds=900
        )

    assert result == {"result": 0}
    tmp.assert_not_awaited()
    fifo.assert_awaited_once()
    folder_id, filename, passed_stream, size, timeout = fifo.await_args.args
    assert (folder_id, filename, passed_stream, size) == (42, "b.tar", stream, 1234)
    assert timeout.total == 900
    assert timeout.connect == 30


@pytest.mark.parametrize("file_size", [None, 0])
async def test_upload_unknown_size_uses_tempfile(api: PCloudAPI, file_size: int | None) -> None:
    """Unknown (or zero) size selects the temp-file path; default timeout is 24 h."""
    with (
        patch.object(api, "_async_upload_via_fifo", AsyncMock()) as fifo,
        patch.object(
            api, "_async_upload_via_tempfile", AsyncMock(return_value={"result": 0})
        ) as tmp,
    ):
        await api.async_upload_file_from_stream(42, "b.tar", _stream(), file_size=file_size)

    fifo.assert_not_awaited()
    tmp.assert_awaited_once()
    timeout = tmp.await_args.args[3]
    assert timeout.total == DEFAULT_TRANSFER_TOTAL_SECONDS


async def test_upload_fifo_connection_reset_asks_for_retry(api: PCloudAPI) -> None:
    """A connection reset on the FIFO path is re-raised with retry guidance."""
    with (
        patch.object(
            api,
            "_async_upload_via_fifo",
            AsyncMock(side_effect=PCloudAPIError("[Errno 104] Connection reset by peer")),
        ),
        pytest.raises(PCloudAPIError, match="Please retry"),
    ):
        await api.async_upload_file_from_stream(42, "b.tar", _stream(), file_size=10)


async def test_upload_fifo_other_errors_propagate(api: PCloudAPI) -> None:
    """Other FIFO errors propagate unchanged."""
    error = PCloudAPIError("Over quota")
    with (
        patch.object(api, "_async_upload_via_fifo", AsyncMock(side_effect=error)),
        pytest.raises(PCloudAPIError) as exc_info,
    ):
        await api.async_upload_file_from_stream(42, "b.tar", _stream(), file_size=10)

    assert exc_info.value is error


async def test_upload_file_from_path_passes_timeout(api: PCloudAPI, tmp_path) -> None:
    """async_upload_file_from_path uses the given client timeout for the POST."""
    backup = tmp_path / "b.tar"
    backup.write_bytes(b"0123456789")
    timeout = aiohttp.ClientTimeout(connect=30, total=1234)
    with aioresponses() as mocked:
        mocked.post(f"{EU_BASE}/uploadfile", payload={"result": 0})
        await api.async_upload_file_from_path(
            42, "b.tar", str(backup), upload_client_timeout=timeout
        )

    (call,) = request_calls(mocked, "POST", "/uploadfile")
    assert call.kwargs["timeout"] == timeout
