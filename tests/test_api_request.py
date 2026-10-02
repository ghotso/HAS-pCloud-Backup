"""Tests for PCloudAPI._request: auth, retries and error wrapping."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import aiohttp
from aioresponses import aioresponses
from homeassistant.core import HomeAssistant
import pytest

from custom_components.pcloud_backup.api import STANDARD_TIMEOUT, PCloudAPI, PCloudAPIError
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


async def test_auth_error_refreshes_and_retries_once(oauth_api: PCloudAPI) -> None:
    """Auth error 1000 triggers one token refresh and a single retry."""
    get_token = AsyncMock(side_effect=["old-token", "new-token"])
    refresh = AsyncMock()
    with (
        aioresponses() as mocked,
        patch.object(oauth_api.auth, "get_auth_token", get_token),
        patch.object(oauth_api.auth, "refresh_token_if_needed", refresh),
    ):
        mocked.get(f"{EU_BASE}/userinfo", payload={"result": 1000, "error": "Log in required."})
        mocked.get(f"{EU_BASE}/userinfo", payload={"result": 0, "email": "ok"})
        result = await oauth_api._request("GET", "/userinfo")

    assert result == {"result": 0, "email": "ok"}
    refresh.assert_awaited_once()
    calls = request_calls(mocked, "GET", "/userinfo")
    assert len(calls) == 2
    assert calls[1].kwargs["headers"]["Authorization"] == "Bearer new-token"


async def test_auth_error_refresh_failure_raises(oauth_api: PCloudAPI) -> None:
    """If the refresh fails, the original pCloud error is raised."""
    with (
        aioresponses() as mocked,
        patch.object(
            oauth_api.auth, "refresh_token_if_needed", AsyncMock(side_effect=RuntimeError("nope"))
        ),
    ):
        mocked.get(f"{EU_BASE}/userinfo", payload={"result": 1000, "error": "Log in required."})
        with pytest.raises(PCloudAPIError, match="Log in required"):
            await oauth_api._request("GET", "/userinfo")

    assert len(request_calls(mocked, "GET", "/userinfo")) == 1


async def test_auth_error_persisting_after_retry_raises(oauth_api: PCloudAPI) -> None:
    """A second auth error after the retry is not retried again."""
    with (
        aioresponses() as mocked,
        patch.object(oauth_api.auth, "refresh_token_if_needed", AsyncMock()),
    ):
        mocked.post(
            f"{EU_BASE}/deletefile?fileid=1",
            payload={"result": 1002, "error": "No full path or name/folderid provided."},
            repeat=True,
        )
        with pytest.raises(PCloudAPIError, match="No full path"):
            await oauth_api._request("POST", "/deletefile", {"fileid": 1})

    assert len(request_calls(mocked, "POST", "/deletefile")) == 2


async def test_network_error_is_wrapped(oauth_api: PCloudAPI) -> None:
    """aiohttp client errors are wrapped in PCloudAPIError."""
    with aioresponses() as mocked:
        mocked.get(f"{EU_BASE}/userinfo", exception=aiohttp.ClientConnectionError("boom"))
        with pytest.raises(PCloudAPIError, match="Network error: boom") as exc_info:
            await oauth_api._request("GET", "/userinfo")

    assert isinstance(exc_info.value.__cause__, aiohttp.ClientConnectionError)


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
