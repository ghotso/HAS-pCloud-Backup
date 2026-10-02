"""Tests for the large backup upload paths: pCloud result errors and cleanup."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
import tempfile

from aioresponses import aioresponses
from homeassistant.core import HomeAssistant
import pytest

from custom_components.pcloud_backup.api import PCloudAPI, PCloudAPIError, PCloudAuthError
from custom_components.pcloud_backup.auth import PCloudOAuth2Auth

from .common import ACCESS_TOKEN, EU_BASE, request_calls

AUTH_ERROR = {"result": 1000, "error": "Log in required."}
API_ERROR = {"result": 2008, "error": "User is over quota."}


@pytest.fixture
def api(hass: HomeAssistant) -> PCloudAPI:
    """PCloudAPI (EU) using a static OAuth2 token."""
    return PCloudAPI(hass, "eu", PCloudOAuth2Auth(hass, "eu", access_token=ACCESS_TOKEN))


@pytest.fixture
def created_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    """Create the upload's temp file / FIFO under tmp_path and record its path."""
    paths: list[str] = []
    original_mkstemp = tempfile.mkstemp

    def _mkstemp(suffix=None, prefix=None, dir=None, text=False):  # noqa: A002
        fd, path = original_mkstemp(suffix=suffix, prefix=prefix, dir=tmp_path, text=text)
        paths.append(path)
        return fd, path

    monkeypatch.setattr(tempfile, "mkstemp", _mkstemp)
    return paths


async def _stream() -> AsyncIterator[bytes]:
    yield b"0123456789"


def _assert_removed(created_paths: list[str], tmp_path: Path, suffix: str) -> None:
    """Exactly one temp file / FIFO was created (with the given suffix) and it is gone."""
    assert len(created_paths) == 1
    assert created_paths[0].endswith(suffix)
    assert not Path(created_paths[0]).exists()
    assert list(tmp_path.iterdir()) == []


# --- async_upload_file_from_path ----------------------------------------------


@pytest.mark.parametrize(
    ("code", "message"),
    [(1000, "Log in required."), (2000, "Log in failed."), (2094, "Invalid 'access_token'.")],
)
async def test_upload_from_path_auth_error(
    api: PCloudAPI, tmp_path: Path, code: int, message: str
) -> None:
    """An auth result code from the file-path upload raises PCloudAuthError."""
    backup = tmp_path / "b.tar"
    backup.write_bytes(b"0123456789")
    with aioresponses() as mocked:
        mocked.post(
            f"{EU_BASE}/uploadfile", payload={"result": code, "error": message}, repeat=True
        )
        with pytest.raises(PCloudAuthError) as exc_info:
            await api.async_upload_file_from_path(42, "b.tar", str(backup))

    assert str(exc_info.value) == f"pCloud authentication failed: {message}"
    # Never retried: one POST only.
    assert len(request_calls(mocked, "POST", "/uploadfile")) == 1


async def test_upload_from_path_api_error_unchanged(api: PCloudAPI, tmp_path: Path) -> None:
    """A non-auth result code keeps the existing PCloudAPIError and message."""
    backup = tmp_path / "b.tar"
    backup.write_bytes(b"0123456789")
    with aioresponses() as mocked:
        mocked.post(f"{EU_BASE}/uploadfile", payload=API_ERROR)
        with pytest.raises(PCloudAPIError) as exc_info:
            await api.async_upload_file_from_path(42, "b.tar", str(backup))

    assert not isinstance(exc_info.value, PCloudAuthError)
    assert str(exc_info.value) == (
        "Unexpected error uploading b.tar: pCloud API error: User is over quota."
    )


# --- temp-file path (unknown size) ----------------------------------------------


async def test_upload_via_tempfile_auth_error(
    api: PCloudAPI, tmp_path: Path, created_paths: list[str]
) -> None:
    """An auth error on the temp-file path reaches the caller and the temp file is removed."""
    with aioresponses() as mocked:
        mocked.post(f"{EU_BASE}/uploadfile", payload=AUTH_ERROR, repeat=True)
        with pytest.raises(PCloudAuthError) as exc_info:
            await api.async_upload_file_from_stream(42, "b.tar", _stream(), file_size=None)

    assert str(exc_info.value) == "pCloud authentication failed: Log in required."
    assert len(request_calls(mocked, "POST", "/uploadfile")) == 1
    _assert_removed(created_paths, tmp_path, ".tar")


async def test_upload_via_tempfile_api_error_unchanged(
    api: PCloudAPI, tmp_path: Path, created_paths: list[str]
) -> None:
    """A non-auth error on the temp-file path keeps the existing PCloudAPIError and message."""
    with aioresponses() as mocked:
        mocked.post(f"{EU_BASE}/uploadfile", payload=API_ERROR)
        with pytest.raises(PCloudAPIError) as exc_info:
            await api.async_upload_file_from_stream(42, "b.tar", _stream(), file_size=None)

    assert not isinstance(exc_info.value, PCloudAuthError)
    assert str(exc_info.value) == (
        "Error uploading b.tar: Unexpected error uploading b.tar: "
        "pCloud API error: User is over quota."
    )
    _assert_removed(created_paths, tmp_path, ".tar")


# --- FIFO path (known size) -------------------------------------------------------


async def test_upload_via_fifo_auth_error(
    api: PCloudAPI, tmp_path: Path, created_paths: list[str]
) -> None:
    """An auth error on the FIFO path reaches the caller and the FIFO is removed."""
    with aioresponses() as mocked:
        mocked.post(f"{EU_BASE}/uploadfile", payload=AUTH_ERROR, repeat=True)
        with pytest.raises(PCloudAuthError) as exc_info:
            await api.async_upload_file_from_stream(42, "b.tar", _stream(), file_size=10)

    assert str(exc_info.value) == "pCloud authentication failed: Log in required."
    assert len(request_calls(mocked, "POST", "/uploadfile")) == 1
    _assert_removed(created_paths, tmp_path, ".tar.fifo")


async def test_upload_via_fifo_api_error_unchanged(
    api: PCloudAPI, tmp_path: Path, created_paths: list[str]
) -> None:
    """A non-auth error on the FIFO path keeps the existing PCloudAPIError and message."""
    with aioresponses() as mocked:
        mocked.post(f"{EU_BASE}/uploadfile", payload=API_ERROR)
        with pytest.raises(PCloudAPIError) as exc_info:
            await api.async_upload_file_from_stream(42, "b.tar", _stream(), file_size=10)

    assert not isinstance(exc_info.value, PCloudAuthError)
    assert str(exc_info.value) == (
        "FIFO upload failed for b.tar: pCloud API error: User is over quota."
    )
    _assert_removed(created_paths, tmp_path, ".tar.fifo")
