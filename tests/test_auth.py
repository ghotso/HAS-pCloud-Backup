"""Tests for the pCloud authentication helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import re
from unittest.mock import patch

from aioresponses import aioresponses
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pcloud_backup.auth import (
    TOKEN_EXPIRY_BUFFER,
    PCloudDigestAuth,
    PCloudOAuth2Auth,
    create_auth,
)
from custom_components.pcloud_backup.const import API_BASE_EU, API_BASE_US

from .common import ACCESS_TOKEN, ENTRY_ID, EU_BASE, US_BASE


def _digest_auth(**kwargs) -> PCloudDigestAuth:
    return PCloudDigestAuth(
        hass=None, region="EU", username="User@Example.com", password="s3cret", **kwargs
    )


# --- Digest auth: pure helpers ---------------------------------------------


def test_digest_auth_normalises_region_and_username() -> None:
    """Region and username are lower-cased and the EU base URL is selected."""
    auth = _digest_auth()
    assert auth.region == "eu"
    assert auth.username == "user@example.com"
    assert auth._base_url == API_BASE_EU


def test_calculate_password_digest_known_value() -> None:
    """sha1(password + sha1(lower(username)) + digest) matches a known vector."""
    auth = _digest_auth()
    assert (
        auth._calculate_password_digest("DIGEST123") == "c9307a56c16636bee6b76d15f90b01cfffbf736a"
    )


def test_parse_pcloud_datetime_rfc2822() -> None:
    """RFC 2822 strings (pCloud's format) are parsed timezone-aware."""
    parsed = _digest_auth()._parse_pcloud_datetime("Fri, 27 Sep 2013 10:15:46 +0000")
    assert parsed == datetime(2013, 9, 27, 10, 15, 46, tzinfo=UTC)


def test_parse_pcloud_datetime_naive_rfc2822_becomes_utc() -> None:
    """RFC 2822 with -0000 (unknown zone) is treated as UTC."""
    parsed = _digest_auth()._parse_pcloud_datetime("Fri, 27 Sep 2013 10:15:46 -0000")
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)


def test_parse_pcloud_datetime_iso_like_fallback() -> None:
    """The ISO-like fallback format is accepted and made UTC-aware."""
    parsed = _digest_auth()._parse_pcloud_datetime("2024-05-06 07:08:09")
    assert parsed == datetime(2024, 5, 6, 7, 8, 9, tzinfo=UTC)


@pytest.mark.parametrize("value", ["1700000000", "garbage", ""])
def test_parse_pcloud_datetime_unparseable(value: str) -> None:
    """Unix timestamps and garbage are not datetime strings for this helper."""
    assert _digest_auth()._parse_pcloud_datetime(value) is None


def test_is_token_expired_without_token() -> None:
    """No token means expired."""
    assert _digest_auth()._is_token_expired() is True


def test_is_token_expired_respects_buffer() -> None:
    """A token expiring within TOKEN_EXPIRY_BUFFER is treated as expired."""
    auth = _digest_auth()
    auth._auth_token = "tok"
    now = datetime.now(UTC)

    auth._token_expire = now + TOKEN_EXPIRY_BUFFER + timedelta(minutes=1)
    assert auth._is_token_expired() is False

    auth._token_expire = now + TOKEN_EXPIRY_BUFFER - timedelta(minutes=1)
    assert auth._is_token_expired() is True


def test_is_token_expired_inactive_expiry() -> None:
    """The inactivity expiry is checked with the same buffer."""
    auth = _digest_auth()
    auth._auth_token = "tok"
    auth._token_expire = datetime.now(UTC) + timedelta(days=30)
    auth._token_expire_inactive = datetime.now(UTC) + timedelta(minutes=1)
    assert auth._is_token_expired() is True


def test_update_token_expiration_defaults() -> None:
    """Without expiry info the token defaults to 30 days / 7 days inactive."""
    auth = _digest_auth()
    auth._update_token_expiration({})
    assert auth._token_expire - auth._token_created == timedelta(days=30)
    assert auth._token_expire_inactive - auth._token_created == timedelta(days=7)


def test_update_token_expiration_uses_response_and_authexpire() -> None:
    """Parsed response dates win; authexpire is the fallback for unparseable values."""
    auth = _digest_auth(authexpire=3600, authinactiveexpire=600)
    auth._update_token_expiration(
        {"expire": "garbage", "expire_inactive": "Fri, 27 Sep 2030 10:15:46 +0000"}
    )
    assert auth._token_expire - auth._token_created == timedelta(seconds=3600)
    assert auth._token_expire_inactive == datetime(2030, 9, 27, 10, 15, 46, tzinfo=UTC)


async def test_digest_get_auth_token_logs_in_and_caches(hass: HomeAssistant) -> None:
    """get_auth_token performs getdigest + userinfo login once and caches the token."""
    auth = PCloudDigestAuth(hass, "eu", "user@example.com", "s3cret")
    with aioresponses() as mocked:
        mocked.get(f"{EU_BASE}/getdigest", payload={"result": 0, "digest": "DIGEST123"})
        mocked.get(
            re.compile(rf"^{re.escape(EU_BASE)}/userinfo\?"),
            payload={"result": 0, "auth": "digest-token"},
        )
        assert await auth.get_auth_token() == "digest-token"
        # Second call is served from cache (no further mocks registered).
        assert await auth.get_auth_token() == "digest-token"

        login = next(calls for key, calls in mocked.requests.items() if key[1].path == "/userinfo")
        params = login[0].kwargs["params"]
        assert params["getauth"] == "1"
        assert params["username"] == "user@example.com"
        assert params["passworddigest"] == auth._calculate_password_digest("DIGEST123")


async def test_digest_get_auth_token_login_failure(hass: HomeAssistant) -> None:
    """A non-zero login result raises ValueError."""
    auth = PCloudDigestAuth(hass, "us", "user@example.com", "wrong")
    with aioresponses() as mocked:
        mocked.get(f"{US_BASE}/getdigest", payload={"result": 0, "digest": "D"})
        mocked.get(
            re.compile(rf"^{re.escape(US_BASE)}/userinfo\?"),
            payload={"result": 2000, "error": "Log in failed."},
        )
        with pytest.raises(ValueError, match="Log in failed"):
            await auth.get_auth_token()


# --- OAuth2 auth ------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs", [{}, {"config_entry_id": ENTRY_ID, "access_token": ACCESS_TOKEN}]
)
def test_oauth_requires_exactly_one_token_source(kwargs: dict) -> None:
    """Either a config entry id or a static token must be given, not both/neither."""
    with pytest.raises(ValueError, match="exactly one"):
        PCloudOAuth2Auth(None, "us", **kwargs)


async def test_oauth_static_token() -> None:
    """A static token (config flow) is returned as-is."""
    auth = PCloudOAuth2Auth(None, "US", access_token="static")
    assert auth.region == "us"
    assert auth._base_url == API_BASE_US
    assert await auth.get_auth_token() == "static"
    # Refresh is a no-op without a config entry.
    await auth.refresh_token_if_needed()


async def test_oauth_token_from_config_entry(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """The token is read from the config entry on every call (picks up refreshes)."""
    config_entry.add_to_hass(hass)
    auth = PCloudOAuth2Auth(hass, "eu", config_entry_id=ENTRY_ID)
    assert await auth.get_auth_token() == ACCESS_TOKEN

    hass.config_entries.async_update_entry(
        config_entry, data={**config_entry.data, "token": {"access_token": "rotated"}}
    )
    assert await auth.get_auth_token() == "rotated"


def test_oauth_token_from_config_entry_not_bound() -> None:
    """A static-token auth cannot read from a config entry."""
    auth = PCloudOAuth2Auth(None, "us", access_token="static")
    with pytest.raises(RuntimeError):
        auth._token_from_config_entry()


async def test_oauth_token_from_missing_entry(hass: HomeAssistant) -> None:
    """A missing config entry raises ValueError."""
    auth = PCloudOAuth2Auth(hass, "eu", config_entry_id="does-not-exist")
    with pytest.raises(ValueError, match="not found"):
        auth._token_from_config_entry()


@pytest.mark.parametrize(
    ("token", "message"),
    [("not-a-dict", "no OAuth token data"), ({"token_type": "bearer"}, "no access_token")],
)
async def test_oauth_token_from_entry_invalid_token(
    hass: HomeAssistant, token: object, message: str
) -> None:
    """Malformed token data in the entry raises ValueError."""
    entry = MockConfigEntry(domain="pcloud_backup", data={"token": token})
    entry.add_to_hass(hass)
    auth = PCloudOAuth2Auth(hass, "eu", config_entry_id=entry.entry_id)
    with pytest.raises(ValueError, match=message):
        auth._token_from_config_entry()


async def test_oauth_refresh_skipped_without_auth_implementation(hass: HomeAssistant) -> None:
    """Entries without auth_implementation skip the HA OAuth2 refresh."""
    entry = MockConfigEntry(domain="pcloud_backup", data={"token": {"access_token": "x"}})
    entry.add_to_hass(hass)
    auth = PCloudOAuth2Auth(hass, "eu", config_entry_id=entry.entry_id)
    await auth.refresh_token_if_needed()
    assert await auth.get_auth_token() == "x"


async def test_oauth_refresh_is_noop(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """pCloud has no refresh tokens: no OAuth implementation is resolved (#37)."""
    config_entry.add_to_hass(hass)
    auth = PCloudOAuth2Auth(hass, "eu", config_entry_id=ENTRY_ID)
    with patch(
        "homeassistant.helpers.config_entry_oauth2_flow.async_get_config_entry_implementation"
    ) as get_impl:
        await auth.refresh_token_if_needed()

    get_impl.assert_not_called()
    assert config_entry.data["token"]["access_token"] == ACCESS_TOKEN


def test_create_auth_returns_oauth() -> None:
    """create_auth always builds OAuth2 auth now."""
    auth = create_auth(None, "eu", access_token="tok")
    assert isinstance(auth, PCloudOAuth2Auth)
    assert auth._base_url == API_BASE_EU
