"""pCloud API wrapper."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import aiohttp
from homeassistant.util import dt as dt_util

from .auth import PCloudAuth
from .const import API_BASE_EU, API_BASE_US

_LOGGER = logging.getLogger(__name__)

# Timeout configuration for HTTP requests
# Connect timeout: time to establish connection
# Total timeout: total time for entire request (important for large uploads)
CONNECT_TIMEOUT = aiohttp.ClientTimeout(connect=30)  # 30 seconds to connect
UPLOAD_TIMEOUT = aiohttp.ClientTimeout(connect=30, total=3600)  # 1 hour total for large uploads
DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(connect=30, total=3600)  # 1 hour total for large downloads
STANDARD_TIMEOUT = aiohttp.ClientTimeout(connect=30, total=120)  # 2 minutes for standard requests


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

        # Set default timeout if not specified
        if "timeout" not in kwargs:
            kwargs["timeout"] = STANDARD_TIMEOUT

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
        
        try:
            async with session.post(
                url, params=params, data=form_data, headers=headers, timeout=STANDARD_TIMEOUT
            ) as response:
                result = await response.json()
                
                # Check for pCloud API errors
                if isinstance(result, dict) and result.get("result") != 0:
                    error_msg = result.get("error", "Unknown error")
                    raise PCloudAPIError(f"pCloud API error: {error_msg}")
                
                return result
        except asyncio.TimeoutError as err:
            _LOGGER.error("Upload timeout for %s: %s", filename, err)
            raise PCloudAPIError(f"Upload timeout for {filename}") from err
        except aiohttp.ClientError as err:
            _LOGGER.error("Network error uploading %s: %s", filename, err)
            raise PCloudAPIError(f"Network error uploading {filename}: {err}") from err

    async def async_upload_file_from_stream(
        self, folder_id: int, filename: str, stream: AsyncIterator[bytes]
    ) -> dict[str, Any]:
        """Upload a large file from async stream to pCloud using streaming to avoid disk space and memory.
        
        This method streams directly from an async iterator to pCloud using FormData
        (same format as async_upload_file_from_path) to ensure exact compatibility with pCloud API.
        Uses a file-like wrapper around the async iterator so FormData can handle it correctly.
        """
        _LOGGER.info("Starting streaming upload of %s directly from backup stream to pCloud", filename)
        
        session = await self._get_session()
        
        # Determine if using OAuth2 (Bearer token) or digest auth (auth parameter)
        is_oauth2 = hasattr(self.auth, "_access_token") and not hasattr(self.auth, "username")
        
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
        
        # Use FormData (same as working async_upload_file_from_path) to ensure exact format compatibility
        # Create a file-like wrapper that bridges async iterator to FormData using a queue
        from io import BytesIO, IOBase
        from queue import Queue, Empty
        from threading import Event
        
        # Create async generator that reads from stream and puts chunks in queue
        bytes_streamed = 0
        chunk_count = 0
        queue: Queue[bytes | None] = Queue(maxsize=10)  # Buffer up to 10 chunks
        stream_done = Event()
        stream_error: Exception | None = None
        
        async def _stream_to_queue():
            """Read from async iterator and put chunks in queue for synchronous reading."""
            nonlocal bytes_streamed, chunk_count, stream_error
            try:
                async for chunk in stream:
                    bytes_streamed += len(chunk)
                    chunk_count += 1
                    await asyncio.to_thread(queue.put, chunk)
                    
                    # Log progress every 100MB or every 1000 chunks
                    if chunk_count % 1000 == 0 or bytes_streamed % (100 * 1024 * 1024) < len(chunk):
                        _LOGGER.debug(
                            "Upload stream progress: %d MB (%d chunks)", 
                            bytes_streamed // (1024 * 1024),
                            chunk_count
                        )
            except Exception as err:
                stream_error = err
            finally:
                await asyncio.to_thread(queue.put, None)  # Signal EOF
                stream_done.set()
        
        # Create file-like object that reads from queue (non-blocking for event loop)
        # Inherit from IOBase so aiohttp recognizes it as a file-like object
        class QueueFileLike(IOBase):
            """File-like wrapper that reads from queue populated by async iterator."""
            def __init__(self, queue: Queue, done_event: Event):
                super().__init__()
                self.queue = queue
                self.done_event = done_event
                self._buffer = BytesIO()
                self._closed = False
            
            def read(self, size: int = -1) -> bytes:
                """Read bytes from queue - blocks thread but not event loop.
                
                aiohttp.FormData calls read() with various sizes. We need to:
                1. Return immediately if we have data available
                2. Wait for data if queue is not empty or stream is not done
                3. Return empty bytes only when stream is truly done
                """
                if self._closed:
                    return b""
                
                # Get current buffer size - need to reset position first
                buffer_value = self._buffer.getvalue()
                current_size = len(buffer_value)
                buffer_pos = self._buffer.tell()
                available_size = current_size - buffer_pos
                
                # If we have enough data in buffer at current position, return it immediately
                if size > 0 and available_size >= size:
                    self._buffer.seek(buffer_pos)
                    data = self._buffer.read(size)
                    # Keep remaining data
                    remaining_pos = self._buffer.tell()
                    remaining_value = self._buffer.getvalue()
                    if remaining_pos < len(remaining_value):
                        remaining = remaining_value[remaining_pos:]
                        self._buffer = BytesIO(remaining)
                    else:
                        self._buffer = BytesIO()
                    return data
                
                # Need more data - read from queue until we have enough or stream is done
                max_wait_iterations = 1000  # Allow more iterations for large files
                iterations = 0
                
                while iterations < max_wait_iterations:
                    iterations += 1
                    
                    # Try to get chunk from queue (non-blocking first, then blocking)
                    chunk = None
                    try:
                        chunk = self.queue.get_nowait()
                    except Empty:
                        # Queue is empty, check if stream is done
                        if self.done_event.is_set():
                            # Stream is done and queue is empty - we're at EOF
                            break
                        # Stream not done yet, wait a bit for data
                        try:
                            chunk = self.queue.get(timeout=0.05)  # 50ms timeout
                        except Empty:
                            # Still no data after timeout
                            # If we have some data in buffer, return it (partial read is okay)
                            if available_size > 0:
                                self._buffer.seek(buffer_pos)
                                if size == -1:
                                    data = self._buffer.read()
                                    self._buffer = BytesIO()
                                else:
                                    data = self._buffer.read(size)
                                    remaining_pos = self._buffer.tell()
                                    remaining_value = self._buffer.getvalue()
                                    if remaining_pos < len(remaining_value):
                                        remaining = remaining_value[remaining_pos:]
                                        self._buffer = BytesIO(remaining)
                                    else:
                                        self._buffer = BytesIO()
                                return data
                            # No data available yet, continue waiting
                            continue
                    
                    # Got a chunk from queue
                    if chunk is None:  # EOF marker
                        break
                    
                    # Append chunk to buffer (seek to end to append)
                    current_buffer_value = self._buffer.getvalue()
                    self._buffer.seek(0, 2)  # Seek to end
                    self._buffer.write(chunk)
                    buffer_value = self._buffer.getvalue()
                    current_size = len(buffer_value)
                    available_size = current_size - buffer_pos
                    
                    # If we have enough data and size was specified, break
                    if size > 0 and available_size >= size:
                        break
                
                # Read from buffer at current position
                self._buffer.seek(buffer_pos)
                if size == -1:
                    # Return all available data
                    data = self._buffer.read()
                    self._buffer = BytesIO()
                    return data
                else:
                    # Return requested amount (or what we have)
                    data = self._buffer.read(size)
                    # Keep remaining data
                    remaining_pos = self._buffer.tell()
                    remaining_value = self._buffer.getvalue()
                    if remaining_pos < len(remaining_value):
                        remaining = remaining_value[remaining_pos:]
                        self._buffer = BytesIO(remaining)
                    else:
                        self._buffer = BytesIO()
                    return data
            
            def readable(self) -> bool:
                """Check if stream is readable."""
                return not self._closed
            
            def seekable(self) -> bool:
                """Check if stream is seekable (not supported)."""
                return False
            
            def writable(self) -> bool:
                """Check if stream is writable (not supported)."""
                return False
            
            def __iter__(self):
                """Make file-like object iterable for aiohttp compatibility."""
                return self
            
            def __next__(self):
                """Next chunk for iteration."""
                chunk = self.read(8192)  # 8KB chunks
                if not chunk:
                    raise StopIteration
                return chunk
            
            def tell(self) -> int:
                """Return current position (not meaningful for stream, but required by some APIs)."""
                return len(self._buffer.getvalue())
            
            def close(self):
                """Close the file-like object."""
                self._closed = True
                self._buffer.close()
                super().close()
        
        # Create FormData with parameters BEFORE file (per pCloud docs)
        form_data = aiohttp.FormData()
        form_data.add_field("folderid", str(folder_id))
        form_data.add_field("filename", filename)
        form_data.add_field("nopartial", "1")
        
        # Start background task to populate queue
        stream_task = asyncio.create_task(_stream_to_queue())
        
        # Create file-like wrapper and add to form data
        file_like = QueueFileLike(queue, stream_done)
        try:
            form_data.add_field(
                "file",
                file_like,
                filename=filename,
                content_type="application/octet-stream",
            )
            
            _LOGGER.debug("Uploading %s to pCloud...", filename)
            try:
                async with session.post(
                    url, params=params, data=form_data, headers=headers, timeout=UPLOAD_TIMEOUT
                ) as response:
                    result = await response.json()
                    
                    # Wait for stream task to complete
                    try:
                        await stream_task
                    except Exception:
                        pass  # Already handled in task
                    
                    if stream_error:
                        raise PCloudAPIError(f"Stream error: {stream_error}") from stream_error
                    
                    # Check for pCloud API errors
                    if isinstance(result, dict) and result.get("result") != 0:
                        error_msg = result.get("error", "Unknown error")
                        error_code = result.get("result")
                        _LOGGER.error(
                            "pCloud API error uploading %s (code %s): %s",
                            filename,
                            error_code,
                            error_msg,
                        )
                        raise PCloudAPIError(f"pCloud API error: {error_msg}")
                    
                    _LOGGER.info(
                        "Successfully uploaded %s to pCloud (%d MB, %d chunks)",
                        filename,
                        bytes_streamed // (1024 * 1024),
                        chunk_count
                    )
                    return result
                    
            except asyncio.TimeoutError as err:
                # Cancel stream task immediately on timeout
                if not stream_task.done():
                    stream_task.cancel()
                    try:
                        await stream_task
                    except (asyncio.CancelledError, Exception):
                        pass
                _LOGGER.error(
                    "Upload timeout for %s after %d seconds. This may occur with very large backups. "
                    "Consider checking network connection and pCloud server status.",
                    filename,
                    UPLOAD_TIMEOUT.total
                )
                raise PCloudAPIError(f"Upload timeout for {filename}") from err
            except (aiohttp.ClientOSError, aiohttp.ServerDisconnectedError) as err:
                # Cancel stream task immediately on connection error
                if not stream_task.done():
                    stream_task.cancel()
                    try:
                        await stream_task
                    except (asyncio.CancelledError, Exception):
                        pass
                # Connection reset or server disconnected
                _LOGGER.error(
                    "Connection error during upload of %s: %s. "
                    "pCloud may have rejected the request format or connection was interrupted.",
                    filename,
                    err,
                    exc_info=True
                )
                raise PCloudAPIError(
                    f"Connection error uploading {filename}: {err}"
                ) from err
            except aiohttp.ClientError as err:
                # Cancel stream task immediately on client error
                if not stream_task.done():
                    stream_task.cancel()
                    try:
                        await stream_task
                    except (asyncio.CancelledError, Exception):
                        pass
                _LOGGER.error(
                    "Client error uploading %s: %s",
                    filename,
                    err,
                    exc_info=True
                )
                raise PCloudAPIError(f"Client error uploading {filename}: {err}") from err
            except Exception as err:
                # Cancel stream task immediately on any error
                if not stream_task.done():
                    stream_task.cancel()
                    try:
                        await stream_task
                    except (asyncio.CancelledError, Exception):
                        pass
                _LOGGER.exception("Unexpected error uploading %s", filename)
                raise PCloudAPIError(f"Unexpected error uploading {filename}: {err}") from err
            finally:
                # Ensure stream task is cancelled if still running
                if not stream_task.done():
                    stream_task.cancel()
                    try:
                        await stream_task
                    except (asyncio.CancelledError, Exception):
                        pass
        finally:
            file_like.close()

    async def async_upload_file_from_path(
        self, folder_id: int, filename: str, file_path: str
    ) -> dict[str, Any]:
        """Upload a large file from disk to pCloud using streaming to avoid loading entire file in memory.
        
        This method streams the file from disk during upload, making it suitable
        for large files (e.g., multi-gigabyte backups) without exhausting memory.
        Note: This method requires disk space equal to the file size.
        """
        _LOGGER.debug("Starting streaming upload of %s from %s", filename, file_path)
        
        # Verify file exists and get size
        try:
            file_stat = await asyncio.to_thread(Path(file_path).stat)
            file_size_bytes = file_stat.st_size
            _LOGGER.info(
                "Uploading %s (%d MB) to pCloud folder %d",
                filename,
                file_size_bytes // (1024 * 1024),
                folder_id,
            )
        except Exception as err:
            _LOGGER.error("Failed to stat file %s: %s", file_path, err)
            raise PCloudAPIError(f"File not found or inaccessible: {file_path}") from err
        
        session = await self._get_session()
        
        # Determine if using OAuth2 (Bearer token) or digest auth (auth parameter)
        is_oauth2 = hasattr(self.auth, "_access_token") and not hasattr(self.auth, "username")
        
        # Create form data for multipart upload
        form_data = aiohttp.FormData()
        form_data.add_field("folderid", str(folder_id))
        form_data.add_field("filename", filename)
        form_data.add_field("nopartial", "1")
        
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
        
        # Open file in binary mode and add to form data
        # aiohttp.FormData can handle file objects and will stream them
        try:
            # Open file asynchronously to avoid blocking
            file_obj = await asyncio.to_thread(open, file_path, "rb")
            
            try:
                form_data.add_field(
                    "file",
                    file_obj,
                    filename=filename,
                    content_type="application/octet-stream",
                )
                
                _LOGGER.debug("Uploading %s to pCloud...", filename)
                try:
                    async with session.post(
                        url, params=params, data=form_data, headers=headers, timeout=UPLOAD_TIMEOUT
                    ) as response:
                        result = await response.json()
                        
                        # Check for pCloud API errors
                        if isinstance(result, dict) and result.get("result") != 0:
                            error_msg = result.get("error", "Unknown error")
                            error_code = result.get("result")
                            _LOGGER.error(
                                "pCloud API error uploading %s (code %s): %s",
                                filename,
                                error_code,
                                error_msg,
                            )
                            raise PCloudAPIError(f"pCloud API error: {error_msg}")
                        
                        _LOGGER.info("Successfully uploaded %s to pCloud", filename)
                        return result
                        
                except asyncio.TimeoutError as err:
                    _LOGGER.error(
                        "Upload timeout for %s after %d seconds", filename, UPLOAD_TIMEOUT.total
                    )
                    raise PCloudAPIError(f"Upload timeout for {filename}") from err
                except aiohttp.ClientError as err:
                    _LOGGER.error("Network error uploading %s: %s", filename, err, exc_info=True)
                    raise PCloudAPIError(f"Network error uploading {filename}: {err}") from err
                except Exception as err:
                    _LOGGER.exception("Unexpected error uploading %s", filename)
                    raise PCloudAPIError(f"Unexpected error uploading {filename}: {err}") from err
                    
            finally:
                # Always close the file
                await asyncio.to_thread(file_obj.close)
                
        except OSError as err:
            _LOGGER.error("Failed to open file %s: %s", file_path, err)
            raise PCloudAPIError(f"Failed to open file {file_path}: {err}") from err

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
        """Download a small file from pCloud (returns bytes).
        
        For large files, use async_download_file_to_path instead to avoid
        loading the entire file into memory.
        """
        download_link = await self.async_get_file_link(file_id)
        session = await self._get_session()
        
        # Download links from pCloud don't require auth token
        try:
            async with session.get(download_link, timeout=STANDARD_TIMEOUT) as response:
                if response.status != 200:
                    raise PCloudAPIError(f"Download failed with status {response.status}")
                return await response.read()
        except asyncio.TimeoutError as err:
            _LOGGER.error("Download timeout for file %d: %s", file_id, err)
            raise PCloudAPIError(f"Download timeout for file {file_id}") from err
        except aiohttp.ClientError as err:
            _LOGGER.error("Network error downloading file %d: %s", file_id, err)
            raise PCloudAPIError(f"Network error downloading file {file_id}: {err}") from err

    async def async_download_file_to_path(self, file_id: int, file_path: str) -> None:
        """Download a large file from pCloud and write it to disk using streaming.
        
        This method streams the file to disk during download, making it suitable
        for large files without exhausting memory.
        """
        _LOGGER.info("Downloading file %d from pCloud to %s", file_id, file_path)
        
        download_link = await self.async_get_file_link(file_id)
        session = await self._get_session()
        
        # Download links from pCloud don't require auth token
        try:
            async with session.get(download_link, timeout=DOWNLOAD_TIMEOUT) as response:
                if response.status != 200:
                    raise PCloudAPIError(f"Download failed with status {response.status}")
                
                # Stream the response to disk
                # Use executor for file I/O to avoid blocking event loop
                file_obj = await asyncio.to_thread(open, file_path, "wb")
                bytes_downloaded = 0
                
                try:
                    async for chunk in response.content.iter_chunked(8192):  # 8KB chunks
                        await asyncio.to_thread(file_obj.write, chunk)
                        bytes_downloaded += len(chunk)
                        # Log progress every 100MB
                        if bytes_downloaded % (100 * 1024 * 1024) < 8192:
                            _LOGGER.debug(
                                "Download progress: %d MB downloaded", bytes_downloaded // (1024 * 1024)
                            )
                    
                    await asyncio.to_thread(file_obj.flush)
                    _LOGGER.info(
                        "Successfully downloaded file %d (%d MB) to %s",
                        file_id,
                        bytes_downloaded // (1024 * 1024),
                        file_path,
                    )
                finally:
                    await asyncio.to_thread(file_obj.close)
                    
        except asyncio.TimeoutError as err:
            _LOGGER.error("Download timeout for file %d after %d seconds", file_id, DOWNLOAD_TIMEOUT.total)
            raise PCloudAPIError(f"Download timeout for file {file_id}") from err
        except aiohttp.ClientError as err:
            _LOGGER.error("Network error downloading file %d: %s", file_id, err, exc_info=True)
            raise PCloudAPIError(f"Network error downloading file {file_id}: {err}") from err
        except OSError as err:
            _LOGGER.error("Failed to write file %s: %s", file_path, err)
            raise PCloudAPIError(f"Failed to write file {file_path}: {err}") from err
        except Exception as err:
            _LOGGER.exception("Unexpected error downloading file %d", file_id)
            raise PCloudAPIError(f"Unexpected error downloading file {file_id}: {err}") from err

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

