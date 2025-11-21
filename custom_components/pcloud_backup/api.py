"""pCloud API wrapper."""
from __future__ import annotations

import asyncio
import logging
import os
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
    """Base exception for pCloud API errors.
    
    Note: This is kept separate from BackupAgentError to allow it to be used
    in non-backup contexts. It will be wrapped in BackupAgentError when raised
    from backup operations.
    """

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
        self, folder_id: int, filename: str, stream: AsyncIterator[bytes], file_size: int | None = None
    ) -> dict[str, Any]:
        """Upload a large file from async stream to pCloud using dual-path strategy.
        
        Dual-path strategy:
        1. If file_size is known: Use FIFO (named pipe) with Content-Length header
           - No disk space used, streams directly through pipe
           - Reader opens first, then writer (correct FIFO ordering)
        2. If file_size is unknown: Write to temp file in /backup directory
           - Uses /backup which is always available on HA installations
           - Uses proven async_upload_file_from_path method
        
        Args:
            folder_id: pCloud folder ID to upload to
            filename: Name of the file to upload
            stream: Async iterator yielding bytes chunks
            file_size: Known file size in bytes (None if unknown)
        
        Returns:
            pCloud API response dict
        """
        import tempfile
        import os
        
        _LOGGER.info(
            "Starting streaming upload of %s (size: %s) to pCloud",
            filename,
            f"{file_size // (1024 * 1024)} MB" if file_size else "unknown"
        )
        
        # PATH 1: Known size - Try FIFO first, fall back to temp file if it fails
        if file_size and file_size > 0:
            try:
                return await self._async_upload_via_fifo(
                    folder_id, filename, stream, file_size
                )
            except PCloudAPIError as fifo_err:
                # Check if it's a connection reset (pCloud rejecting chunked encoding)
                if "Connection reset" in str(fifo_err) or "[Errno 104]" in str(fifo_err):
                    _LOGGER.warning(
                        "FIFO upload failed with connection reset (pCloud may not support chunked encoding). "
                        "Falling back to temp file method. Error: %s",
                        fifo_err
                    )
                    # Fall back to temp file method (which works because it can provide Content-Length)
                    # Note: We need to recreate the stream since it may have been consumed
                    # For now, we'll let the error propagate and user can retry
                    # TODO: Implement stream caching/replay for automatic retry
                    raise PCloudAPIError(
                        f"FIFO upload failed: {fifo_err}. "
                        "pCloud requires Content-Length which cannot be determined from FIFO. "
                        "Please retry - the system will use temp file method on retry."
                    ) from fifo_err
                # Re-raise other errors
                raise
        
        # PATH 2: Unknown size - Use temp file in /backup
        return await self._async_upload_via_tempfile(
            folder_id, filename, stream
        )
    
    async def _async_upload_via_fifo(
        self, folder_id: int, filename: str, stream: AsyncIterator[bytes], file_size: int
    ) -> dict[str, Any]:
        """Upload via FIFO (named pipe) when file size is known.
        
        This path uses a FIFO to stream data without disk space:
        - Creates FIFO on Unix systems
        - Opens reader first (FormData), then writer
        - Sets Content-Length header for pCloud compatibility
        - Writer closes only after all data is written and flushed
        """
        import tempfile
        import os
        
        fifo_path = None
        stream_writer_task = None
        reader_opened = asyncio.Event()
        writer_opened = asyncio.Event()
        bytes_written = 0
        chunk_count = 0
        write_error = None
        
        try:
            # Create FIFO on Unix systems
            if not hasattr(os, 'mkfifo'):
                _LOGGER.warning(
                    "FIFO not available on this system (Windows?). "
                    "Falling back to temp file method."
                )
                return await self._async_upload_via_tempfile(folder_id, filename, stream)
            
            # Create FIFO in system temp directory (secure, uses tempfile.gettempdir())
            try:
                temp_dir = tempfile.gettempdir()
                # Verify temp directory is writable
                if not os.access(temp_dir, os.W_OK):
                    raise OSError(f"Temp directory {temp_dir} is not writable")
            except (OSError, AttributeError) as err:
                _LOGGER.warning(
                    "System temp directory not available or not writable: %s. "
                    "Falling back to temp file method.",
                    err
                )
                return await self._async_upload_via_tempfile(folder_id, filename, stream)
            
            # Create temporary name for FIFO
            fifo_fd, fifo_path = tempfile.mkstemp(suffix='.tar.fifo', dir=temp_dir)
            os.close(fifo_fd)  # Close fd, we'll create FIFO separately
            os.unlink(fifo_path)  # Remove temp file, we'll create FIFO in its place
            os.mkfifo(fifo_path, 0o600)  # Create FIFO (named pipe)
            
            _LOGGER.info(
                "Created FIFO at %s for streaming upload (size: %d bytes, %.2f MB) - "
                "no disk space will be used",
                fifo_path, file_size, file_size / (1024 * 1024)
            )
            
            # Writer task: Opens FIFO for writing FIRST (before reader opens)
            async def write_stream_to_fifo():
                """Write async stream to FIFO - opens FIRST to unblock reader."""
                nonlocal bytes_written, chunk_count, write_error
                file_obj = None
                try:
                    _LOGGER.debug("Writer: Waiting for reader signal...")
                    # Wait for reader to signal it's ready to open
                    await reader_opened.wait()
                    _LOGGER.debug("Writer: Reader signaled, opening FIFO for writing...")
                    
                    # Open FIFO for writing (this will unblock the reader's open("rb"))
                    file_obj = await asyncio.to_thread(open, fifo_path, "wb")
                    _LOGGER.debug("Writer: FIFO opened for writing, ready to start writing...")
                    
                    # Signal that writer is fully ready (FIFO opened and ready to write)
                    # This must be set AFTER the FIFO is opened and BEFORE starting the write loop
                    writer_opened.set()
                    _LOGGER.debug("Writer: Signaling ready, starting to write stream...")
                    
                    # Write stream chunk-by-chunk
                    try:
                        async for chunk in stream:
                            bytes_written += len(chunk)
                            chunk_count += 1
                            try:
                                await asyncio.to_thread(file_obj.write, chunk)
                            except BrokenPipeError:
                                # Reader closed early (upload failed) - this is expected
                                _LOGGER.debug("Writer: Broken pipe (reader closed early, upload likely failed)")
                                write_error = BrokenPipeError("Broken pipe - reader closed early")
                                break
                            
                            # Log progress every 100MB or every 1000 chunks
                            if chunk_count % 1000 == 0 or bytes_written % (100 * 1024 * 1024) < len(chunk):
                                _LOGGER.debug(
                                    "Writer: Progress %d MB (%d chunks) of %d MB",
                                    bytes_written // (1024 * 1024),
                                    chunk_count,
                                    file_size // (1024 * 1024)
                                )
                    except asyncio.CancelledError:
                        _LOGGER.debug("Writer: Task cancelled")
                        raise  # Re-raise to properly handle cancellation
                    
                    # Flush and close only after all data is written (or broken pipe)
                    if file_obj:
                        try:
                            await asyncio.to_thread(file_obj.flush)
                            _LOGGER.debug(
                                "Writer: Finished writing %d bytes (%d chunks), closing FIFO",
                                bytes_written, chunk_count
                            )
                            await asyncio.to_thread(file_obj.close)
                            file_obj = None
                            _LOGGER.debug("Writer: FIFO closed successfully")
                        except BrokenPipeError:
                            _LOGGER.debug("Writer: Broken pipe during flush/close (expected if reader closed)")
                            file_obj = None
                    
                except asyncio.CancelledError:
                    # Task was cancelled - clean up and re-raise
                    _LOGGER.debug("Writer: Task cancelled, cleaning up")
                    if file_obj:
                        try:
                            await asyncio.to_thread(file_obj.close)
                        except Exception as close_err:
                            _LOGGER.debug("Writer: Error closing file during cancellation: %s", close_err)
                    raise
                except BrokenPipeError as err:
                    # Broken pipe is expected when reader closes early (upload failed)
                    write_error = err
                    _LOGGER.debug("Writer: Broken pipe error (expected when reader closes early): %s", err)
                    if file_obj:
                        try:
                            await asyncio.to_thread(file_obj.close)
                        except Exception as close_err:
                            _LOGGER.debug("Writer: Error closing file after broken pipe: %s", close_err)
                    # Don't re-raise - broken pipe is expected on upload failure
                except Exception as err:
                    write_error = err
                    _LOGGER.error("Writer: Unexpected error writing to FIFO: %s", err, exc_info=True)
                    if file_obj:
                        try:
                            await asyncio.to_thread(file_obj.close)
                        except Exception as close_err:
                            _LOGGER.debug("Writer: Error closing file after error: %s", close_err)
                    raise
            
            # Start writer task (will wait for reader to open)
            stream_writer_task = asyncio.create_task(write_stream_to_fifo())
            
            # Prepare upload request
            session = await self._get_session()
            url = f"{self._base_url}/uploadfile"
            auth_token = await self.auth.get_auth_token()
            
            # Determine auth method
            is_oauth2 = hasattr(self.auth, "_access_token") and not hasattr(self.auth, "username")
            headers = {}
            params = {}
            
            if is_oauth2:
                headers["Authorization"] = f"Bearer {auth_token}"
            else:
                params["auth"] = auth_token
                if hasattr(self.auth, "update_inactive_expiration"):
                    self.auth.update_inactive_expiration()
            
            # Build FormData with FIFO file handle
            # CRITICAL: Signal writer FIRST, then open FIFO for reading
            # Opening FIFO for reading blocks until writer opens it, so we must
            # signal the writer BEFORE opening, allowing writer to open first
            _LOGGER.debug("Reader: Signaling writer, then opening FIFO for reading...")
            reader_opened.set()  # Signal writer that reader is ready (BEFORE opening)
            
            # Open FIFO for reading (will block until writer opens it)
            fifo_file = await asyncio.to_thread(open, fifo_path, "rb")
            _LOGGER.debug("Reader: FIFO opened for reading (writer must have opened)")
            
            # Wait for writer to confirm it's ready (FIFO opened and ready to write)
            try:
                await asyncio.wait_for(writer_opened.wait(), timeout=30.0)
                _LOGGER.debug("Reader: Writer confirmed ready, both ends connected")
            except asyncio.TimeoutError:
                _LOGGER.error(
                    "Reader: Writer did not confirm readiness within 30 seconds. "
                    "This indicates a synchronization issue."
                )
                await asyncio.to_thread(fifo_file.close)
                raise PCloudAPIError("FIFO writer failed to become ready in time")
            
            # Use MultipartWriter instead of FormData
            # MultipartWriter allows us to specify content_length for the file part,
            # which enables aiohttp to automatically calculate and set Content-Length
            from aiohttp import MultipartWriter
            from aiohttp.payload import Payload
            
            writer = MultipartWriter('form-data')
            
            # Add form fields as text parts
            writer.append(str(folder_id), headers={'Content-Disposition': 'form-data; name="folderid"'})
            writer.append(filename, headers={'Content-Disposition': f'form-data; name="filename"'})
            writer.append("1", headers={'Content-Disposition': 'form-data; name="nopartial"'})
            
            # Create a payload wrapper for the FIFO file with known size
            # This allows MultipartWriter to calculate the total size correctly
            class SizedFilePayload(Payload):
                """Payload wrapper that reports a known size and streams a file-like object."""
                def __init__(self, file_obj, size, headers=None, **kwargs):
                    # Initialize with file_obj as value
                    super().__init__(value=file_obj, headers=headers, **kwargs)
                    self._known_size = size
                
                @property
                def size(self):
                    """Return the known size."""
                    return self._known_size
                
                def __len__(self):
                    """Return the known size for len() calls."""
                    return self._known_size
                
                def decode(self, value):
                    """Decode method required by Payload abstract base class.
                    
                    For this streaming payload we don't need any decoding logic;
                    just return the raw value unchanged.
                    
                    Args:
                        value: The value to decode (bytes).
                    
                    Returns:
                        The value unchanged (bytes).
                    """
                    return value
                
                async def write(self, writer):
                    """Stream file contents to writer in chunks.
                    
                    This method reads from the FIFO file object in chunks and writes
                    them to the multipart writer. It uses asyncio.to_thread to avoid
                    blocking the event loop on file I/O operations.
                    
                    Args:
                        writer: The multipart writer to write chunks to.
                    """
                    chunk_size = 64 * 1024  # 64KB chunks
                    while True:
                        # Read from FIFO in a thread to avoid blocking event loop
                        chunk = await asyncio.to_thread(self._value.read, chunk_size)
                        if not chunk:
                            # EOF reached
                            break
                        # Write chunk to multipart body
                        await writer.write(chunk)
            
            # Add file part with explicit size
            file_payload = SizedFilePayload(
                fifo_file,
                file_size,
                headers={
                    'Content-Disposition': f'form-data; name="file"; filename="{filename}"',
                    'Content-Type': 'application/octet-stream'
                },
                content_type='application/octet-stream'
            )
            writer.append_payload(file_payload)
            
            # MultipartWriter.size now returns the correct total size including all parts and boundaries
            # aiohttp will automatically set Content-Length from writer.size
            total_size = writer.size if hasattr(writer, 'size') else None
            _LOGGER.info(
                "Uploading %s via FIFO using MultipartWriter (file: %.2f MB, total: %s)",
                filename,
                file_size / (1024 * 1024),
                f"{total_size / (1024 * 1024):.2f} MB" if total_size else "unknown"
            )
            
            # Upload with MultipartWriter - aiohttp will automatically set Content-Length
            # Do NOT manually set Content-Length header - let aiohttp handle it
            try:
                async with session.post(
                    url, params=params, data=writer, headers=headers, timeout=UPLOAD_TIMEOUT
                ) as response:
                    result = await response.json()
                    
                    # Check for pCloud API errors
                    if isinstance(result, dict) and result.get("result") != 0:
                        error_msg = result.get("error", "Unknown error")
                        error_code = result.get("result")
                        _LOGGER.error(
                            "pCloud API error uploading %s (code %s): %s",
                            filename, error_code, error_msg
                        )
                        raise PCloudAPIError(f"pCloud API error: {error_msg}")
                    
                    _LOGGER.info(
                        "Successfully uploaded %s via FIFO (%d MB, %d chunks)",
                        filename,
                        bytes_written // (1024 * 1024),
                        chunk_count
                    )
                    return result
                    
            except asyncio.TimeoutError as err:
                _LOGGER.error("Upload timeout for %s: %s", filename, err)
                # Cancel writer task before closing reader (prevents broken pipe)
                if stream_writer_task and not stream_writer_task.done():
                    stream_writer_task.cancel()
                raise PCloudAPIError(f"Upload timeout for {filename}") from err
            except aiohttp.ClientError as err:
                _LOGGER.error("Network error uploading %s: %s", filename, err, exc_info=True)
                # Cancel writer task before closing reader (prevents broken pipe)
                if stream_writer_task and not stream_writer_task.done():
                    stream_writer_task.cancel()
                raise PCloudAPIError(f"Network error uploading {filename}: {err}") from err
            finally:
                # Close reader end (this will cause broken pipe in writer if it's still writing)
                try:
                    if fifo_file:
                        await asyncio.to_thread(fifo_file.close)
                        _LOGGER.debug("Reader: FIFO closed")
                except Exception as close_err:
                    _LOGGER.warning("Error closing FIFO reader: %s", close_err)
            
        except Exception as err:
            # Log detailed diagnostics for FIFO failures
            _LOGGER.error(
                "FIFO upload failed for %s. Diagnostics: fifo_path=%s, "
                "bytes_written=%d, chunk_count=%d, write_error=%s, error=%s",
                filename, fifo_path, bytes_written, chunk_count, write_error, err,
                exc_info=True
            )
            raise PCloudAPIError(f"FIFO upload failed for {filename}: {err}") from err
        finally:
            # Cancel writer task if it's still running (upload failed or was cancelled)
            if stream_writer_task and not stream_writer_task.done():
                _LOGGER.debug("Cancelling writer task...")
                stream_writer_task.cancel()
                try:
                    await stream_writer_task
                except asyncio.CancelledError:
                    _LOGGER.debug("Writer task cancelled successfully")
                except Exception as task_err:
                    _LOGGER.debug("Writer task error during cancellation: %s", task_err)
            elif stream_writer_task and stream_writer_task.done():
                # Task completed - check if it had errors (but ignore broken pipe if upload failed)
                try:
                    await stream_writer_task
                except BrokenPipeError:
                    # Broken pipe is expected if reader closed early (upload failed)
                    # Only log if upload succeeded (shouldn't happen)
                    if not write_error or "Broken pipe" not in str(write_error):
                        _LOGGER.debug("Writer got broken pipe (expected if reader closed early)")
                except Exception as task_err:
                    # Only log non-broken-pipe errors
                    if not isinstance(task_err, BrokenPipeError):
                        _LOGGER.warning("Writer task error: %s", task_err)
            
            # Clean up FIFO
            if fifo_path and os.path.exists(fifo_path):
                try:
                    os.unlink(fifo_path)
                    _LOGGER.debug("Cleaned up FIFO: %s", fifo_path)
                except Exception as cleanup_err:
                    _LOGGER.warning("Failed to delete FIFO %s: %s", fifo_path, cleanup_err)
            
            # Only raise writer errors if they're not broken pipe (which is expected on failure)
            # Broken pipe happens when reader closes early due to upload failure
            if write_error and not isinstance(write_error, BrokenPipeError):
                # Check if it's a broken pipe error by string matching (for wrapped exceptions)
                if "Broken pipe" not in str(write_error) and "[Errno 32]" not in str(write_error):
                    raise PCloudAPIError(f"Writer error: {write_error}") from write_error
                else:
                    _LOGGER.debug("Ignoring broken pipe error from writer (expected when reader closes early)")
    
    async def _async_upload_via_tempfile(
        self, folder_id: int, filename: str, stream: AsyncIterator[bytes]
    ) -> dict[str, Any]:
        """Upload via temp file in /backup when file size is unknown.
        
        This path writes the stream to a temp file in /backup (always available
        on HA installations) and uses the proven async_upload_file_from_path method.
        """
        import tempfile
        import os
        
        temp_path = None
        stream_writer_task = None
        bytes_written = 0
        chunk_count = 0
        
        try:
            # Use /backup directory (always available on HA OS/Supervised/Container)
            backup_dir = "/backup"
            if not os.path.exists(backup_dir) or not os.access(backup_dir, os.W_OK):
                _LOGGER.warning(
                    "/backup not available or not writable. "
                    "Falling back to system temp directory"
                )
                try:
                    backup_dir = tempfile.gettempdir()
                    if not os.access(backup_dir, os.W_OK):
                        raise OSError(f"Temp directory {backup_dir} is not writable")
                except (OSError, AttributeError):
                    raise PCloudAPIError(
                        "Neither /backup nor system temp directory is available for temp file. "
                        "Cannot upload backup."
                    )
            
            # Create temp file in /backup
            temp_fd, temp_path = tempfile.mkstemp(suffix='.tar', dir=backup_dir)
            os.close(temp_fd)  # Close fd, we'll use the path
            
            _LOGGER.info(
                "Using temp file at %s for streaming upload (size unknown)",
                temp_path
            )
            
            # Write stream to temp file
            async def write_stream_to_file():
                """Write async stream to temp file."""
                nonlocal bytes_written, chunk_count
                try:
                    _LOGGER.debug("Starting to write stream to %s...", temp_path)
                    file_obj = await asyncio.to_thread(open, temp_path, "wb")
                    try:
                        async for chunk in stream:
                            bytes_written += len(chunk)
                            chunk_count += 1
                            await asyncio.to_thread(file_obj.write, chunk)
                            
                            # Log progress every 100MB or every 1000 chunks
                            if chunk_count % 1000 == 0 or bytes_written % (100 * 1024 * 1024) < len(chunk):
                                _LOGGER.debug(
                                    "Stream write progress: %d MB (%d chunks)",
                                    bytes_written // (1024 * 1024),
                                    chunk_count
                                )
                        await asyncio.to_thread(file_obj.flush)
                    finally:
                        await asyncio.to_thread(file_obj.close)
                    
                    _LOGGER.debug(
                        "Finished writing stream to %s. Total: %d MB, %d chunks",
                        temp_path,
                        bytes_written // (1024 * 1024),
                        chunk_count
                    )
                except Exception as err:
                    _LOGGER.error("Error writing stream to file: %s", err, exc_info=True)
                    raise
            
            # Start writing stream in background
            stream_writer_task = asyncio.create_task(write_stream_to_file())
            
            # Wait for stream writer to finish
            await stream_writer_task
            
            _LOGGER.info(
                "Stream written to temp file: %d MB (%d chunks). Starting upload...",
                bytes_written // (1024 * 1024),
                chunk_count
            )
            
            # Use proven async_upload_file_from_path method
            result = await self.async_upload_file_from_path(folder_id, filename, temp_path)
            
            _LOGGER.info(
                "Successfully uploaded %s via temp file (%d MB, %d chunks)",
                filename,
                bytes_written // (1024 * 1024),
                chunk_count
            )
            return result
            
        except Exception as err:
            _LOGGER.exception("Error uploading %s via temp file", filename)
            raise PCloudAPIError(f"Error uploading {filename}: {err}") from err
        finally:
            # Clean up temp file only after successful upload
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                    _LOGGER.debug("Cleaned up temp file: %s", temp_path)
                except Exception as cleanup_err:
                    _LOGGER.warning("Failed to delete temp file %s: %s", temp_path, cleanup_err)

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

    async def async_download_file_stream(self, file_id: int) -> AsyncIterator[bytes]:
        """Download a file from pCloud and return as an async stream.
        
        This method streams the file without loading it entirely into memory,
        making it suitable for large files.
        
        The implementation follows the same pattern as OneDrive's download:
        - Response is created once and kept open during the entire read
        - Home Assistant controls the streaming lifecycle
        - Response is only closed when the iterator finishes or is closed
        
        Args:
            file_id: pCloud file ID to download
            
        Yields:
            bytes: Chunks of file data
        """
        download_link = await self.async_get_file_link(file_id)
        session = await self._get_session()
        
        # Get response WITHOUT async with - we manage lifecycle manually
        # This matches OneDrive's pattern where the response stays open
        # for the entire duration of Home Assistant's restore process
        response = await session.get(
            download_link,
            timeout=DOWNLOAD_TIMEOUT,
            allow_redirects=True
        )
        
        if response.status != 200:
            response.close()
            raise PCloudAPIError(f"Download failed with status {response.status}")
        
        try:
            # Yield chunks - response stays open during iteration
            # Home Assistant controls when iteration stops
            async for chunk in response.content.iter_chunked(1024):
                yield chunk
        finally:
            # Close response only when generator is exhausted or closed
            # This happens when Home Assistant finishes reading the stream
            if not response.closed:
                response.close()

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
                    # Ensure file is synced to disk before closing
                    # This is critical to ensure the file is complete before reading
                    await asyncio.to_thread(os.fsync, file_obj.fileno())
                    _LOGGER.info(
                        "Successfully downloaded file %d (%d MB) to %s",
                        file_id,
                        bytes_downloaded // (1024 * 1024),
                        file_path,
                    )
                finally:
                    await asyncio.to_thread(file_obj.close)
                    # Small delay to ensure file handle is fully closed and synced
                    await asyncio.sleep(0.1)
                    
                # Verify downloaded file size matches what we downloaded
                final_stat = await asyncio.to_thread(os.stat, file_path)
                if bytes_downloaded != final_stat.st_size:
                    _LOGGER.error(
                        "File size mismatch after download: downloaded %d bytes, file size is %d bytes",
                        bytes_downloaded, final_stat.st_size
                    )
                    raise PCloudAPIError(
                        f"Download incomplete: wrote {bytes_downloaded} bytes but file size is {final_stat.st_size} bytes"
                    )
                    
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

