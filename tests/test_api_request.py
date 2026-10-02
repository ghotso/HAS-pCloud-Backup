"""Tests for PCloudAPI._request and download helpers: auth, retries and error wrapping."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, patch

import aiohttp
from aioresponses import aioresponses
from homeassistant.core import HomeAssistant
import pytest

from custom_components.pcloud_backup import api as api_module
from custom_components.pcloud_backup.api import (
    STANDARD_TIMEOUT,
    PCloudAPI,
    PCloudAPIError,
    PCloudAuthError,
)
from custom_components.pcloud_backup.auth import PCloudDigestAuth, PCloudOAuth2Auth

from .common import ACCESS_TOKEN, EU_BASE, request_calls


@pytest.fixture
def oauth_api(hass: HomeAssistant) -> PCloudAPI:
    """PCloudAPI using a static OAuth2 token."""
    return PCloudAPI(hass, "eu", PCloudOAuth2Auth(hass, "eu", access_token=ACCESS_TOKEN))


@pytest.fixture
def digest_api(hass: HomeAssistant) -> PCloudAPI:
    """PCloudAPI using digest auth with an already cached token."""
    auth = PCloudDigestAuth(hass, "eu", "user@example.com", "s3cret")
    auth._auth_token = "digest-token"
    return PCloudAPI(hass, "eu", auth)


async def test_success_returns_payload(oauth_api: PCloudAPI) -> None:
    """result == 0 returns the decoded JSON body."""
    with aioresponses() as mocked:
        mocked.get(f"{EU_BASE}/userinfo", payload={"result": 0, "email": "user@example.com"})
        result = await oauth_api._request("GET", "/userinfo")

    assert result == {"result": 0, "email": "user@example.com"}


async def test_oauth_sends_bearer_header(oauth_api: PCloudAPI) -> None:
    """OAuth2 tokens go into the Authorization header, not the query string."""
    with aioresponses() as mocked:
        mocked.get(f"{EU_BASE}/listfolder?folderid=0", payload={"result": 0})
        await oauth_api._request("GET", "/listfolder", {"folderid": 0})

    (call,) = request_calls(mocked, "GET", "/listfolder")
    assert call.kwargs["headers"]["Authorization"] == f"Bearer {ACCESS_TOKEN}"
    assert "auth" not in call.kwargs["params"]
    assert call.kwargs["timeout"] == STANDARD_TIMEOUT


async def test_digest_sends_auth_param(digest_api: PCloudAPI) -> None:
    """Digest tokens go into the `auth` query parameter."""
    with aioresponses() as mocked:
        mocked.get(f"{EU_BASE}/userinfo?auth=digest-token", payload={"result": 0})
        await digest_api._request("GET", "/userinfo")

    (call,) = request_calls(mocked, "GET", "/userinfo")
    assert call.kwargs["params"] == {"auth": "digest-token"}
    assert "Authorization" not in call.kwargs["headers"]


async def test_api_error_raises(oauth_api: PCloudAPI) -> None:
    """A non-zero, non-auth result raises PCloudAPIError with pCloud's message."""
    with aioresponses() as mocked:
        mocked.get(
            f"{EU_BASE}/userinfo", payload={"result": 2005, "error": "Directory does not exist."}
        )
        with pytest.raises(PCloudAPIError, match="Directory does not exist"):
            await oauth_api._request("GET", "/userinfo")


@pytest.mark.parametrize(
    ("code", "message"),
    [
        (1000, "Log in required."),
        (2000, "Log in failed."),
        (2094, "Invalid 'access_token' provided."),
    ],
)
async def test_auth_error_raises_auth_error_without_refresh(
    oauth_api: PCloudAPI, code: int, message: str
) -> None:
    """pCloud auth errors raise PCloudAuthError at once: no refresh, no retry."""
    refresh = AsyncMock()
    with (
        aioresponses() as mocked,
        patch.object(oauth_api.auth, "refresh_token_if_needed", refresh),
    ):
        mocked.get(f"{EU_BASE}/userinfo", payload={"result": code, "error": message}, repeat=True)
        with pytest.raises(PCloudAuthError, match=re.escape(message)):
            await oauth_api._request("GET", "/userinfo")

    refresh.assert_not_awaited()
    assert len(request_calls(mocked, "GET", "/userinfo")) == 1


@pytest.mark.parametrize("code", [1001, 1002, 2003])
async def test_non_auth_errors_are_plain_api_errors(oauth_api: PCloudAPI, code: int) -> None:
    """Parameter / permission errors are not treated as credential problems."""
    with aioresponses() as mocked:
        mocked.post(
            f"{EU_BASE}/deletefile?fileid=1",
            payload={"result": code, "error": "No full path or name/folderid provided."},
            repeat=True,
        )
        with pytest.raises(PCloudAPIError, match="No full path") as exc_info:
            await oauth_api._request("POST", "/deletefile", {"fileid": 1})

    assert not isinstance(exc_info.value, PCloudAuthError)
    assert len(request_calls(mocked, "POST", "/deletefile")) == 1


async def test_network_error_is_wrapped(oauth_api: PCloudAPI) -> None:
    """aiohttp client errors are wrapped in PCloudAPIError (after the GET retries)."""
    with aioresponses() as mocked:
        mocked.get(
            f"{EU_BASE}/userinfo", exception=aiohttp.ClientConnectionError("boom"), repeat=True
        )
        with pytest.raises(PCloudAPIError, match="Network error: boom") as exc_info:
            await oauth_api._request("GET", "/userinfo")

    assert isinstance(exc_info.value.__cause__, aiohttp.ClientConnectionError)
    assert len(request_calls(mocked, "GET", "/userinfo")) == 3


# --- Timeouts and retries -----------------------------------------------------


def test_standard_timeout_fails_fast() -> None:
    """Small API calls use a short total, connect and socket read timeout."""
    assert aiohttp.ClientTimeout(total=60, connect=15, sock_read=30) == STANDARD_TIMEOUT


async def test_timeout_has_clear_message(oauth_api: PCloudAPI) -> None:
    """A timeout names the endpoint and limit instead of an empty message."""
    with aioresponses() as mocked:
        mocked.get(f"{EU_BASE}/listfolder?folderid=0", exception=TimeoutError(), repeat=True)
        with pytest.raises(PCloudAPIError) as exc_info:
            await oauth_api._request("GET", "/listfolder", {"folderid": 0})

    assert str(exc_info.value) == "pCloud request /listfolder timed out after 60s"
    assert isinstance(exc_info.value.__cause__, TimeoutError)


async def test_server_timeout_message_keeps_detail(oauth_api: PCloudAPI) -> None:
    """aiohttp's socket read timeout is reported as a timeout with its detail."""
    with aioresponses() as mocked:
        mocked.get(
            f"{EU_BASE}/userinfo",
            exception=aiohttp.ServerTimeoutError("Timeout on reading data from socket"),
            repeat=True,
        )
        with pytest.raises(PCloudAPIError) as exc_info:
            await oauth_api._request("GET", "/userinfo")

    assert str(exc_info.value) == (
        "pCloud request /userinfo timed out after 60s (Timeout on reading data from socket)"
    )


@pytest.mark.parametrize(
    "error",
    [TimeoutError(), aiohttp.ClientConnectionError("reset"), aiohttp.ServerDisconnectedError()],
    ids=["timeout", "connection", "disconnected"],
)
async def test_get_retried_and_succeeds_on_second_attempt(
    oauth_api: PCloudAPI, error: Exception
) -> None:
    """Idempotent GETs are retried after a transient failure."""
    with aioresponses() as mocked:
        mocked.get(f"{EU_BASE}/userinfo", exception=error)
        mocked.get(f"{EU_BASE}/userinfo", payload={"result": 0, "email": "ok"})
        result = await oauth_api._request("GET", "/userinfo")

    assert result == {"result": 0, "email": "ok"}
    assert len(request_calls(mocked, "GET", "/userinfo")) == 2


async def test_get_gives_up_after_three_attempts_with_backoff(
    oauth_api: PCloudAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At most 3 attempts are made, waiting 1s and then 3s in between."""
    monkeypatch.setattr(api_module, "RETRY_DELAYS", (1.0, 3.0))
    sleep = AsyncMock()
    with (
        aioresponses() as mocked,
        patch("custom_components.pcloud_backup.api.asyncio.sleep", sleep),
    ):
        mocked.get(f"{EU_BASE}/userinfo", exception=TimeoutError(), repeat=True)
        with pytest.raises(PCloudAPIError, match="timed out"):
            await oauth_api._request("GET", "/userinfo")

    assert len(request_calls(mocked, "GET", "/userinfo")) == 3
    assert [c.args for c in sleep.await_args_list] == [(1.0,), (3.0,)]


@pytest.mark.parametrize(
    "error", [TimeoutError(), aiohttp.ClientConnectionError("reset")], ids=["timeout", "conn"]
)
async def test_post_is_never_retried(oauth_api: PCloudAPI, error: Exception) -> None:
    """Non-idempotent POSTs through _request (createfolder, deletefile) are not retried."""
    with aioresponses() as mocked:
        mocked.post(f"{EU_BASE}/deletefile?fileid=1", exception=error, repeat=True)
        with pytest.raises(PCloudAPIError):
            await oauth_api._request("POST", "/deletefile", {"fileid": 1})

    assert len(request_calls(mocked, "POST", "/deletefile")) == 1


async def test_api_error_is_not_retried(oauth_api: PCloudAPI) -> None:
    """pCloud API errors (non-zero result) are not transient and not retried."""
    with aioresponses() as mocked:
        mocked.get(
            f"{EU_BASE}/listfolder?folderid=0",
            payload={"result": 5000, "error": "Internal error. Try again later."},
            repeat=True,
        )
        with pytest.raises(PCloudAPIError, match="Internal error"):
            await oauth_api._request("GET", "/listfolder", {"folderid": 0})

    assert len(request_calls(mocked, "GET", "/listfolder")) == 1


async def test_retry_can_be_disabled(oauth_api: PCloudAPI) -> None:
    """retry=False makes a single attempt even for GETs (used during setup)."""
    with aioresponses() as mocked:
        mocked.get(f"{EU_BASE}/userinfo", exception=TimeoutError(), repeat=True)
        with pytest.raises(PCloudAPIError, match="timed out"):
            await oauth_api.async_test_connection(retry=False)

    assert len(request_calls(mocked, "GET", "/userinfo")) == 1


async def test_retry_is_logged_as_warning(
    oauth_api: PCloudAPI, caplog: pytest.LogCaptureFixture
) -> None:
    """A retried failure is logged with endpoint, error type and attempt duration."""
    with aioresponses() as mocked:
        mocked.get(f"{EU_BASE}/userinfo", exception=aiohttp.ServerDisconnectedError())
        mocked.get(f"{EU_BASE}/userinfo", payload={"result": 0})
        await oauth_api._request("GET", "/userinfo")

    (record,) = [r for r in caplog.records if "retrying" in r.getMessage()]
    assert record.levelname == "WARNING"
    assert re.search(
        r"pCloud request /userinfo failed after \d+\.\ds \(attempt 1 of 3\): "
        r"ServerDisconnectedError: .+; retrying in 0s",
        record.getMessage(),
    )


# --- Small file upload (backup metadata) ----------------------------------------


@pytest.mark.parametrize(
    "error", [TimeoutError(), aiohttp.ClientConnectionError("reset")], ids=["timeout", "conn"]
)
async def test_upload_file_retried_after_transient_error(
    oauth_api: PCloudAPI, error: Exception
) -> None:
    """The metadata upload is retried (uploadfile overwrites, nopartial drops partials)."""
    with aioresponses() as mocked:
        mocked.post(f"{EU_BASE}/uploadfile", exception=error)
        mocked.post(f"{EU_BASE}/uploadfile", payload={"result": 0, "fileids": [7]})
        result = await oauth_api.async_upload_file(42, "x.metadata.json", b"{}")

    assert result == {"result": 0, "fileids": [7]}
    calls = request_calls(mocked, "POST", "/uploadfile")
    assert len(calls) == 2
    # A fresh multipart body is built for every attempt.
    assert calls[0].kwargs["data"] is not calls[1].kwargs["data"]


async def test_upload_file_gives_up_after_three_attempts(oauth_api: PCloudAPI) -> None:
    """Persistent connection resets fail with a clear, non-empty message."""
    with aioresponses() as mocked:
        mocked.post(
            f"{EU_BASE}/uploadfile",
            exception=aiohttp.ClientOSError(104, "Connection reset by peer"),
            repeat=True,
        )
        with pytest.raises(PCloudAPIError, match="Network error uploading x.metadata.json: .+"):
            await oauth_api.async_upload_file(42, "x.metadata.json", b"{}")

    assert len(request_calls(mocked, "POST", "/uploadfile")) == 3


async def test_upload_file_timeout_message(oauth_api: PCloudAPI) -> None:
    """Upload timeouts name the file and the limit."""
    with aioresponses() as mocked:
        mocked.post(f"{EU_BASE}/uploadfile", exception=TimeoutError(), repeat=True)
        with pytest.raises(
            PCloudAPIError, match=r"pCloud upload of x.metadata.json timed out after 60s"
        ):
            await oauth_api.async_upload_file(42, "x.metadata.json", b"{}")


async def test_upload_file_api_error_not_retried(oauth_api: PCloudAPI) -> None:
    """pCloud API errors from uploadfile are raised at once, auth errors as PCloudAuthError."""
    with aioresponses() as mocked:
        mocked.post(
            f"{EU_BASE}/uploadfile",
            payload={"result": 1000, "error": "Log in required."},
            repeat=True,
        )
        with pytest.raises(PCloudAuthError):
            await oauth_api.async_upload_file(42, "x.metadata.json", b"{}")

    assert len(request_calls(mocked, "POST", "/uploadfile")) == 1


async def test_unexpected_error_message_is_never_empty(oauth_api: PCloudAPI) -> None:
    """Exceptions without a message are described by their type."""
    with aioresponses() as mocked:
        mocked.get(f"{EU_BASE}/userinfo", exception=ValueError())
        with pytest.raises(PCloudAPIError, match="^Unexpected error: ValueError$"):
            await oauth_api._request("GET", "/userinfo")


# --- Download helpers ---------------------------------------------------------

DOWNLOAD_URL = "https://c1.pcloud.com/dl/meta.json"


def _mock_file_link(mocked: aioresponses) -> None:
    mocked.get(
        f"{EU_BASE}/getfilelink?fileid=101",
        payload={"result": 0, "hosts": ["c1.pcloud.com"], "path": "/dl/meta.json"},
        repeat=True,
    )


async def test_download_file_retries_transient_errors(oauth_api: PCloudAPI) -> None:
    """The small-file (metadata) download is retried on a timeout."""
    with aioresponses() as mocked:
        _mock_file_link(mocked)
        mocked.get(DOWNLOAD_URL, exception=TimeoutError())
        mocked.get(DOWNLOAD_URL, body=b"{}")
        assert await oauth_api.async_download_file(101) == b"{}"

    assert len(request_calls(mocked, "GET", "/dl/meta.json")) == 2


async def test_download_file_timeout_message(oauth_api: PCloudAPI) -> None:
    """A download that keeps timing out gives up after 3 attempts with a clear error."""
    with aioresponses() as mocked:
        _mock_file_link(mocked)
        mocked.get(DOWNLOAD_URL, exception=TimeoutError(), repeat=True)
        with pytest.raises(PCloudAPIError) as exc_info:
            await oauth_api.async_download_file(101)

    assert str(exc_info.value) == "pCloud download of file 101 timed out after 60s"
    assert len(request_calls(mocked, "GET", "/dl/meta.json")) == 3


async def test_download_file_http_error_not_retried(oauth_api: PCloudAPI) -> None:
    """HTTP error statuses are not retried."""
    with aioresponses() as mocked:
        _mock_file_link(mocked)
        mocked.get(DOWNLOAD_URL, status=500, repeat=True)
        with pytest.raises(PCloudAPIError, match="status 500"):
            await oauth_api.async_download_file(101)

    assert len(request_calls(mocked, "GET", "/dl/meta.json")) == 1


async def test_download_stream_timeout_message(oauth_api: PCloudAPI) -> None:
    """Opening a backup download stream that times out raises a clear error."""
    with aioresponses() as mocked:
        _mock_file_link(mocked)
        mocked.get(DOWNLOAD_URL, exception=TimeoutError(), repeat=True)
        with pytest.raises(PCloudAPIError, match=r"^pCloud download of file 101 timed out after"):
            await anext(oauth_api.async_download_file_stream(101))

    assert len(request_calls(mocked, "GET", "/dl/meta.json")) == 1


async def test_download_to_path_timeout_message(oauth_api: PCloudAPI, tmp_path) -> None:
    """Large downloads to disk report timeouts clearly and are not retried."""
    with aioresponses() as mocked:
        _mock_file_link(mocked)
        mocked.get(DOWNLOAD_URL, exception=TimeoutError(), repeat=True)
        with pytest.raises(PCloudAPIError, match=r"^pCloud download of file 101 timed out after"):
            await oauth_api.async_download_file_to_path(101, str(tmp_path / "out.tar"))

    assert len(request_calls(mocked, "GET", "/dl/meta.json")) == 1


async def test_unsupported_method_raises(oauth_api: PCloudAPI) -> None:
    """Only GET and POST are supported."""
    with pytest.raises(PCloudAPIError, match="Unsupported HTTP method"):
        await oauth_api._request("DELETE", "/deletefile")


async def test_custom_timeout_is_passed_through(oauth_api: PCloudAPI) -> None:
    """A caller-provided timeout overrides the standard one."""
    timeout = aiohttp.ClientTimeout(total=5)
    with aioresponses() as mocked:
        mocked.get(f"{EU_BASE}/userinfo", payload={"result": 0})
        await oauth_api._request("GET", "/userinfo", timeout=timeout)

    (call,) = request_calls(mocked, "GET", "/userinfo")
    assert call.kwargs["timeout"] == timeout
