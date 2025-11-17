"""pCloud API wrapper."""
from __future__ import annotations

import logging
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

import aiohttp
from homeassistant.util import dt as dt_util

from .auth import PCloudAuth
from .const import API_BASE_EU, API_BASE_US

_LOGGER = logging.getLogger(__name__)


class PCloudAPIError(Exception):
    """Base exception for pCloud API errors."""

    pass


class PCloudAPI:
    """Async wrapper for pCloud REST API."""

    def __init__(
        self,
        hass: Any,
        region: str,
        auth: PCloudAuth,
    ) -> None:
        """Initialize the pCloud API."""
        self.hass = hass
        self.region = region.lower()
        self.auth = auth
        self._base_url = API_BASE_EU if region.lower() == "eu" else API_BASE_US

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get Home Assistant's aiohttp session."""
        from homeassistant.helpers.aiohttp_client import async_get_clientsession
        return async_get_clientsession(self.hass)

    async def async_close(self) -> None:
        """Close resources."""
        await self.auth.close()

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Make an API request."""
        session = await self._get_session()
        url = f"{self._base_url}{endpoint}"

        # Determine if using OAuth2 (Bearer token) or digest auth (auth parameter)
        # OAuth2 auth has _access_token but no username, digest has username
        is_oauth2 = hasattr(self.auth, "_access_token") and not hasattr(self.auth, "username")

        request_params = params or {}
        headers = kwargs.pop("headers", {})
        
        auth_token = await self.auth.get_auth_token()
        
        if is_oauth2:
            # OAuth2: Use Bearer token in Authorization header
            headers["Authorization"] = f"Bearer {auth_token}"
        else:
            # Digest auth: Use auth parameter
            request_params["auth"] = auth_token
            # Update inactive expiration (token usage extends inactive expiration)
            if hasattr(self.auth, "update_inactive_expiration"):
                self.auth.update_inactive_expiration()

        try:
            if method.upper() == "GET":
                async with session.get(url, params=request_params, headers=headers, **kwargs) as response:
                    result = await response.json()
            elif method.upper() == "POST":
                if files:
                    # For file uploads
                    form_data = aiohttp.FormData()
                    for key, value in request_params.items():
                        form_data.add_field(key, str(value))
                    for key, value in files.items():
                        if hasattr(value, "read"):
                            form_data.add_field(
                                key, value, filename=files.get(f"{key}_name", "file")
                            )
                        else:
                            form_data.add_field(key, value)
                    async with session.post(url, data=form_data, headers=headers, **kwargs) as response:
                        result = await response.json()
                else:
                    # Regular POST
                    async with session.post(
                        url, params=request_params, data=data, headers=headers, **kwargs
                    ) as response:
                        result = await response.json()
            else:
                raise PCloudAPIError(f"Unsupported HTTP method: {method}")

            # Check for pCloud API errors
            if isinstance(result, dict) and result.get("result") != 0:
                error_code = result.get("result")
                error_msg = result.get("error", "Unknown error")
                
                # If auth error, try to refresh token
                if error_code in (1000, 1001, 1002):  # Common auth errors
                    _LOGGER.debug("Auth error detected, refreshing token")
                    try:
                        await self.auth.refresh_token_if_needed()
                        # Retry request with new token
                        auth_token = await self.auth.get_auth_token()
                        if is_oauth2:
                            headers["Authorization"] = f"Bearer {auth_token}"
                        else:
                            request_params["auth"] = auth_token
                        # Retry the request
                        if method.upper() == "GET":
                            async with session.get(url, params=request_params, headers=headers, **kwargs) as retry_response:
                                result = await retry_response.json()
                        elif method.upper() == "POST":
                            if files:
                                form_data = aiohttp.FormData()
                                for key, value in request_params.items():
                                    form_data.add_field(key, str(value))
                                for key, value in files.items():
                                    if hasattr(value, "read"):
                                        form_data.add_field(
                                            key, value, filename=files.get(f"{key}_name", "file")
                                        )
                                    else:
                                        form_data.add_field(key, value)
                                async with session.post(url, data=form_data, headers=headers, **kwargs) as retry_response:
                                    result = await retry_response.json()
                            else:
                                async with session.post(
                                    url, params=request_params, data=data, headers=headers, **kwargs
                                ) as retry_response:
                                    result = await retry_response.json()
                        
                        # Check result again after retry
                        if isinstance(result, dict) and result.get("result") != 0:
                            error_msg = result.get("error", "Unknown error")
                            raise PCloudAPIError(f"pCloud API error: {error_msg}")
                    except Exception as refresh_err:
                        _LOGGER.warning("Failed to refresh token: %s", refresh_err)
                        raise PCloudAPIError(f"pCloud API error: {error_msg}") from refresh_err
                else:
                    raise PCloudAPIError(f"pCloud API error: {error_msg}")

            return result

        except aiohttp.ClientError as err:
            raise PCloudAPIError(f"Network error: {err}") from err
        except Exception as err:
            raise PCloudAPIError(f"Unexpected error: {err}") from err

    async def async_test_connection(self) -> dict[str, Any]:
        """Test the API connection and return user info."""
        try:
            result = await self._request("GET", "/userinfo")
            _LOGGER.debug("Connection test successful: %s", result.get("email"))
            return result
        except Exception as err:
            _LOGGER.error("Connection test failed: %s", err)
            raise

    async def async_get_folder_id(self, folder_path: str) -> int:
        """Get folder ID from path, creating if necessary."""
        # Start from root
        path_parts = [p for p in folder_path.strip("/").split("/") if p]
        current_folder_id = 0  # Root folder

        for folder_name in path_parts:
            # List current folder
            result = await self._request("GET", "/listfolder", {"folderid": current_folder_id})
            metadata = result.get("metadata", {})
            contents = metadata.get("contents", [])

            # Check if folder exists
            folder_found = False
            for item in contents:
                if (
                    item.get("isfolder")
                    and item.get("name", "").lower() == folder_name.lower()
                ):
                    current_folder_id = item["folderid"]
                    folder_found = True
                    break

            if not folder_found:
                # Create folder
                result = await self._request(
                    "POST", "/createfolder", {"folderid": current_folder_id, "name": folder_name}
                )
                metadata = result.get("metadata", {})
                current_folder_id = metadata.get("folderid", current_folder_id)

        return current_folder_id

    async def async_upload_file(
        self, folder_id: int, filename: str, file_data: bytes
    ) -> dict[str, Any]:
        """Upload a file to pCloud."""
        # For large files, we might need chunked upload, but for MVP we use simple upload
        session = await self._get_session()
        
        # Determine if using OAuth2 (Bearer token) or digest auth (auth parameter)
        is_oauth2 = hasattr(self.auth, "_access_token") and not hasattr(self.auth, "username")
        
        # Create form data for multipart upload
        form_data = aiohttp.FormData()
        form_data.add_field("folderid", str(folder_id))
        form_data.add_field("filename", filename)
        form_data.add_field("nopartial", "1")
        form_data.add_field(
            "file",
            file_data,
            filename=filename,
            content_type="application/octet-stream",
        )
        
        url = f"{self._base_url}/uploadfile"
        auth_token = await self.auth.get_auth_token()
        
        headers = {}
        params = {}
        
        if is_oauth2:
            # OAuth2: Use Bearer token in Authorization header
            headers["Authorization"] = f"Bearer {auth_token}"
        else:
            # Digest auth: Use auth parameter
            params["auth"] = auth_token
            # Update inactive expiration (token usage extends inactive expiration)
            if hasattr(self.auth, "update_inactive_expiration"):
                self.auth.update_inactive_expiration()
        
        async with session.post(url, params=params, data=form_data, headers=headers) as response:
            result = await response.json()
            
            # Check for pCloud API errors
            if isinstance(result, dict) and result.get("result") != 0:
                error_msg = result.get("error", "Unknown error")
                raise PCloudAPIError(f"pCloud API error: {error_msg}")
            
            return result

    async def async_list_folder(self, folder_id: int) -> list[dict[str, Any]]:
        """List files in a folder."""
        result = await self._request("GET", "/listfolder", {"folderid": folder_id})
        metadata = result.get("metadata", {})
        contents = metadata.get("contents", [])

        # Filter only files (not folders) - accept all files in backup folder
        # Home Assistant backups have .tar extension, but we accept all files
        # to be flexible with naming
        backup_files = [item for item in contents if not item.get("isfolder")]
        return backup_files

    async def async_get_file_link(self, file_id: int) -> str:
        """Get download link for a file."""
        result = await self._request("GET", "/getfilelink", {"fileid": file_id})

        hosts: list[str] | None = None
        path: str | None = None

        if isinstance(result, dict):
            if "hosts" in result and "path" in result:
                hosts = result.get("hosts")
                path = result.get("path")
            elif "metadata" in result:
                metadata = result["metadata"] or {}
                hosts = metadata.get("hosts")
                path = metadata.get("path")

        if not hosts or not path:
            raise PCloudAPIError(
                f"Invalid getfilelink response for file {file_id}: {result}"
            )

        host_entry = hosts[0] if isinstance(hosts, list) else hosts
        download_link = f"https://{host_entry}{path}"
        return download_link

    async def async_delete_file(self, file_id: int) -> None:
        """Delete a file from pCloud."""
        await self._request("POST", "/deletefile", {"fileid": file_id})

    async def async_download_file(self, file_id: int) -> bytes:
        """Download a file from pCloud."""
        download_link = await self.async_get_file_link(file_id)
        session = await self._get_session()
        
        # Download links from pCloud don't require auth token
        async with session.get(download_link) as response:
            if response.status != 200:
                raise PCloudAPIError(f"Download failed with status {response.status}")
            return await response.read()

    def parse_backup_info(self, file_item: dict[str, Any]) -> dict[str, Any]:
        """Parse backup file information."""
        modified = file_item.get("modified", 0)
        # pCloud may return modified as:
        # 1. Unix timestamp (number)
        # 2. Unix timestamp (string number)
        # 3. RFC 2822 date string (e.g. "Mon, 03 Nov 2025 16:39:17 +0000")
        if modified:
            try:
                if isinstance(modified, str):
                    # Try parsing as RFC 2822 date string first
                    try:
                        modified_dt = parsedate_to_datetime(modified)
                        if modified_dt.tzinfo is None:
                            modified_dt = modified_dt.replace(tzinfo=dt_util.UTC)
                    except (ValueError, TypeError):
                        # Try parsing as Unix timestamp string
                        modified_dt = datetime.fromtimestamp(float(modified), tz=dt_util.UTC)
                else:
                    # Unix timestamp as number
                    modified_dt = datetime.fromtimestamp(modified, tz=dt_util.UTC)
            except (ValueError, OSError, TypeError) as err:
                _LOGGER.warning("Failed to parse modified timestamp '%s': %s", modified, err)
                modified_dt = datetime.now(dt_util.UTC)
        else:
            modified_dt = datetime.now(dt_util.UTC)

        return {
            "fileid": file_item.get("fileid"),
            "name": file_item.get("name", ""),
            "size": file_item.get("size", 0),
            "modified": modified_dt.isoformat(),
            "created": modified_dt.isoformat(),
        }

