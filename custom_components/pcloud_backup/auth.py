"""Authentication abstraction for pCloud API."""
from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from homeassistant.util import dt as dt_util

from .const import API_BASE_EU, API_BASE_US

_LOGGER = logging.getLogger(__name__)

# OAuth2 is now the default authentication method

# Token expiration buffer (refresh 5 minutes before expiration)
TOKEN_EXPIRY_BUFFER = timedelta(minutes=5)


class PCloudAuth(ABC):
    """Abstract base class for pCloud authentication."""

    @abstractmethod
    async def get_auth_token(self) -> str:
        """Get authentication token."""
        pass

    @abstractmethod
    async def refresh_token_if_needed(self) -> None:
        """Refresh token if it's expired."""
        pass

    async def close(self) -> None:
        """Clean up resources."""
        pass


class PCloudDigestAuth(PCloudAuth):
    """Digest authentication for pCloud API."""

    def __init__(
        self,
        hass: Any,
        region: str,
        username: str,
        password: str,
        authexpire: int | None = None,
        authinactiveexpire: int | None = None,
    ) -> None:
        """Initialize digest authentication.
        
        Args:
            hass: Home Assistant instance
            region: pCloud region (us/eu)
            username: pCloud username (email)
            password: pCloud password
            authexpire: Token expiration time in seconds from now (optional)
            authinactiveexpire: Inactive expiration time in seconds from now (optional)
        """
        self.hass = hass
        self.region = region.lower()
        self.username = username.lower()
        self.password = password
        self._base_url = API_BASE_EU if region.lower() == "eu" else API_BASE_US
        self._auth_token: str | None = None
        self._token_expire: datetime | None = None
        self._token_expire_inactive: datetime | None = None
        self._token_created: datetime | None = None
        self._authexpire = authexpire
        self._authinactiveexpire = authinactiveexpire

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get Home Assistant's aiohttp session."""
        from homeassistant.helpers.aiohttp_client import async_get_clientsession
        return async_get_clientsession(self.hass)

    async def close(self) -> None:
        """Clean up resources (no-op for shared session)."""
        pass

    async def _get_digest(self) -> str:
        """Get digest from pCloud API."""
        session = await self._get_session()
        url = f"{self._base_url}/getdigest"

        async with session.get(url) as response:
            result = await response.json()
            if result.get("result") != 0:
                raise ValueError(f"Failed to get digest: {result.get('error', 'Unknown error')}")
            return result.get("digest", "")

    def _calculate_password_digest(self, digest: str) -> str:
        """Calculate password digest.

        Formula: sha1(password + sha1(lowercase(username)) + digest)
        """
        # SHA1 of lowercase username
        username_hash = hashlib.sha1(self.username.encode("utf-8")).hexdigest()

        # SHA1 of password + username_hash + digest
        password_digest = hashlib.sha1(
            (self.password + username_hash + digest).encode("utf-8")
        ).hexdigest()

        return password_digest

    def _is_token_expired(self) -> bool:
        """Check if the current token is expired or about to expire."""
        if not self._auth_token:
            return True
        
        now = datetime.now(dt_util.UTC)
        
        # Check absolute expiration
        if self._token_expire and now >= (self._token_expire - TOKEN_EXPIRY_BUFFER):
            _LOGGER.debug("Token is expired or expiring soon (absolute)")
            return True
        
        # Check inactive expiration
        if self._token_expire_inactive and now >= (self._token_expire_inactive - TOKEN_EXPIRY_BUFFER):
            _LOGGER.debug("Token is expired or expiring soon (inactive)")
            return True
        
        return False

    def _parse_pcloud_datetime(self, dt_str: str) -> datetime | None:
        """Parse pCloud datetime string.
        
        pCloud returns dates in RFC 2822 format like: "Fri, 27 Sep 2013 10:15:46 +0000"
        """
        try:
            # Try standard datetime parsing first
            from email.utils import parsedate_to_datetime
            parsed_dt = parsedate_to_datetime(dt_str)
            # Ensure timezone-aware (parsedate_to_datetime may return naive)
            if parsed_dt.tzinfo is None:
                parsed_dt = parsed_dt.replace(tzinfo=dt_util.UTC)
            return parsed_dt
        except (ImportError, ValueError, TypeError):
            # Fallback: try common formats
            formats = [
                "%a, %d %b %Y %H:%M:%S %z",  # RFC 2822 with timezone
                "%a, %d %b %Y %H:%M:%S",     # RFC 2822 without timezone
                "%Y-%m-%d %H:%M:%S",         # ISO-like
            ]
            for fmt in formats:
                try:
                    parsed_dt = datetime.strptime(dt_str, fmt)
                    # Ensure timezone-aware (strptime may return naive)
                    if parsed_dt.tzinfo is None:
                        parsed_dt = parsed_dt.replace(tzinfo=dt_util.UTC)
                    return parsed_dt
                except ValueError:
                    continue
            return None

    def _update_token_expiration(self, result: dict[str, Any]) -> None:
        """Update token expiration information from API response."""
        self._token_created = datetime.now(dt_util.UTC)
        
        # Parse expiration times if provided
        expire = result.get("expire")
        expire_inactive = result.get("expire_inactive")
        
        if expire:
            # expire is a datetime string from pCloud
            parsed_expire = self._parse_pcloud_datetime(expire) if isinstance(expire, str) else None
            if parsed_expire:
                self._token_expire = parsed_expire
            elif self._authexpire:
                # Fallback: if authexpire was set, calculate from creation time
                self._token_expire = self._token_created + timedelta(seconds=self._authexpire)
            else:
                # Default: 30 days if not specified
                self._token_expire = self._token_created + timedelta(days=30)
        elif self._authexpire:
            self._token_expire = self._token_created + timedelta(seconds=self._authexpire)
        else:
            # Default: 30 days if not specified
            self._token_expire = self._token_created + timedelta(days=30)
        
        if expire_inactive:
            parsed_expire_inactive = (
                self._parse_pcloud_datetime(expire_inactive) if isinstance(expire_inactive, str) else None
            )
            if parsed_expire_inactive:
                self._token_expire_inactive = parsed_expire_inactive
            elif self._authinactiveexpire:
                self._token_expire_inactive = self._token_created + timedelta(seconds=self._authinactiveexpire)
            else:
                # Default: 7 days of inactivity
                self._token_expire_inactive = self._token_created + timedelta(days=7)
        elif self._authinactiveexpire:
            self._token_expire_inactive = self._token_created + timedelta(seconds=self._authinactiveexpire)
        else:
            # Default: 7 days of inactivity
            self._token_expire_inactive = self._token_created + timedelta(days=7)

    async def get_auth_token(self) -> str:
        """Get authentication token using digest authentication."""
        # Check if token exists and is still valid
        if self._auth_token and not self._is_token_expired():
            return self._auth_token

        # Token expired or doesn't exist, get a new one
        _LOGGER.debug("Getting new authentication token")
        
        # Get digest
        digest = await self._get_digest()

        # Calculate password digest
        password_digest = self._calculate_password_digest(digest)

        # Login to get auth token
        session = await self._get_session()
        url = f"{self._base_url}/userinfo"

        params = {
            "getauth": "1",
            "logout": "1",
            "username": self.username,
            "digest": digest,
            "passworddigest": password_digest,
        }
        
        # Add expiration parameters if specified
        if self._authexpire:
            params["authexpire"] = str(self._authexpire)
        if self._authinactiveexpire:
            params["authinactiveexpire"] = str(self._authinactiveexpire)

        async with session.get(url, params=params) as response:
            result = await response.json()

            if result.get("result") != 0:
                error_msg = result.get("error", "Unknown error")
                raise ValueError(f"Authentication failed: {error_msg}")

            self._auth_token = result.get("auth")
            if not self._auth_token:
                raise ValueError("No auth token received from pCloud")

            # Update expiration information
            self._update_token_expiration(result)

            _LOGGER.debug(
                "Successfully authenticated with pCloud. Token expires: %s, inactive expires: %s",
                self._token_expire,
                self._token_expire_inactive,
            )
            return self._auth_token

    async def refresh_token_if_needed(self) -> None:
        """Refresh token if needed. For digest auth, we re-authenticate."""
        # Clear current token and expiration info
        self._auth_token = None
        self._token_expire = None
        self._token_expire_inactive = None
        self._token_created = None
        
        # Re-authenticate to get a new token
        _LOGGER.debug("Refreshing authentication token")
        await self.get_auth_token()
    
    def update_inactive_expiration(self) -> None:
        """Update inactive expiration time (called when token is used)."""
        # According to pCloud docs, expire_inactive is extended each time token is used
        # We update it to be N days from now (where N is the inactive expire period)
        if self._token_expire_inactive and self._token_created:
            inactive_period = self._token_expire_inactive - self._token_created
            self._token_expire_inactive = datetime.now(dt_util.UTC) + inactive_period
            _LOGGER.debug("Updated inactive expiration to: %s", self._token_expire_inactive)


class PCloudOAuth2Auth(PCloudAuth):
    """OAuth2 authentication for pCloud API."""

    def __init__(
        self,
        hass: Any,
        region: str,
        *,
        config_entry_id: str | None = None,
        access_token: str | None = None,
    ) -> None:
        """Initialize OAuth2 authentication.

        Prefer ``config_entry_id`` so the current access token is always read from
        the config entry (e.g. after reauth or Home Assistant token refresh). Use
        ``access_token`` only during the config flow before an entry exists.
        """
        if bool(config_entry_id) == bool(access_token):
            raise ValueError("Specify exactly one of config_entry_id or access_token")

        self.hass = hass
        self.region = region.lower()
        self._config_entry_id = config_entry_id
        self._access_token = access_token
        self._base_url = API_BASE_EU if region.lower() == "eu" else API_BASE_US

    def _token_from_config_entry(self) -> str:
        """Return the current OAuth access token from the config entry."""
        if not self._config_entry_id:
            raise RuntimeError("OAuth auth is not bound to a config entry")
        entry = self.hass.config_entries.async_get_entry(self._config_entry_id)
        if entry is None:
            raise ValueError(f"Config entry {self._config_entry_id} not found")
        token_container = entry.data.get("token")
        if not isinstance(token_container, dict):
            raise ValueError("Config entry has no OAuth token data")
        access = token_container.get("access_token")
        if not access:
            raise ValueError("Config entry has no access_token")
        return str(access)

    async def get_auth_token(self) -> str:
        """Get OAuth2 access token."""
        if self._config_entry_id is not None:
            return self._token_from_config_entry()
        if self._access_token is None:
            raise ValueError("OAuth2 static access token is missing")
        return self._access_token

    async def refresh_token_if_needed(self) -> None:
        """Refresh OAuth2 token via Home Assistant when possible."""
        if self._config_entry_id is None:
            return

        entry = self.hass.config_entries.async_get_entry(self._config_entry_id)
        if entry is None:
            return

        if "auth_implementation" not in entry.data:
            _LOGGER.debug(
                "Skipping OAuth refresh: config entry %s has no auth_implementation",
                self._config_entry_id,
            )
            return

        try:
            from homeassistant.helpers.config_entry_oauth2_flow import (
                OAuth2Session,
                async_get_config_entry_implementation,
            )
        except ImportError:
            return

        try:
            impl = await async_get_config_entry_implementation(self.hass, entry)
        except ValueError as err:
            _LOGGER.warning("Could not resolve OAuth implementation: %s", err)
            return

        try:
            session = OAuth2Session(self.hass, entry, impl)
            await session.async_ensure_token_valid()
        except Exception as err:
            _LOGGER.warning("OAuth token refresh failed: %s", err)

    async def close(self) -> None:
        """Close OAuth2 session."""
        pass


def create_auth(
    hass: Any,
    region: str,
    *,
    config_entry_id: str | None = None,
    access_token: str | None = None,
) -> PCloudAuth:
    """Create OAuth2 authentication instance.

    Args:
        hass: Home Assistant instance
        region: pCloud region (us/eu)
        config_entry_id: If set, read access_token from this config entry on each request.
        access_token: Static token (config flow only, before entry is created).

    Returns:
        PCloudAuth instance
    """
    return PCloudOAuth2Auth(
        hass,
        region,
        config_entry_id=config_entry_id,
        access_token=access_token,
    )

