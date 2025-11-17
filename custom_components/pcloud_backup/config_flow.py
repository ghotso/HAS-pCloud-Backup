"""Config flow for pCloud Backup integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_entry_oauth2_flow

from .api import PCloudAPI, PCloudAPIError
from .auth import create_auth
from .const import (
    CONF_BACKUP_FOLDER,
    CONF_REGION,
    DEFAULT_BACKUP_FOLDER,
    DOMAIN,
    OAUTH2_AUTHORIZE,
    OAUTH2_CLIENT_ID,
    OAUTH2_CLIENT_SECRET,
    OAUTH2_TOKEN,
)

_LOGGER = logging.getLogger(__name__)


class PCloudOptionsFlowHandler(OptionsFlow):
    """Handle options flow for pCloud Backup."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BACKUP_FOLDER,
                        default=options.get(CONF_BACKUP_FOLDER, DEFAULT_BACKUP_FOLDER),
                    ): str,
                }
            ),
        )


@callback
def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
    """Get the options flow for this handler."""
    return PCloudOptionsFlowHandler(config_entry)


class PCloudOAuth2Implementation(config_entry_oauth2_flow.LocalOAuth2Implementation):
    """OAuth2 implementation for pCloud."""

    def __init__(self, hass: Any) -> None:
        """Initialize pCloud OAuth2 implementation."""
        super().__init__(
            hass,
            DOMAIN,
            OAUTH2_CLIENT_ID,
            OAUTH2_CLIENT_SECRET,
            OAUTH2_AUTHORIZE,
            OAUTH2_TOKEN,
        )
        # Store additional OAuth data for later use
        self._oauth_data: dict[str, Any] = {}

    async def async_resolve_external_data(self, external_data: dict[str, Any]) -> dict[str, Any]:
        """Resolve external data to tokens."""
        
        # pCloud returns locationid and hostname in the redirect
        # We need to extract these and store them
        # The code might be in different places depending on how HA passes it
        code = external_data.get("code")
        if not code:
            # Sometimes the code might be in query parameters
            code = external_data.get("query", {}).get("code") if isinstance(external_data.get("query"), dict) else None
        
        locationid = external_data.get("locationid")
        if not locationid:
            # Try to get from query params
            locationid = external_data.get("query", {}).get("locationid") if isinstance(external_data.get("query"), dict) else None
            if locationid:
                try:
                    locationid = int(locationid)
                except (ValueError, TypeError):
                    locationid = None
        
        hostname = external_data.get("hostname")
        if not hostname:
            # Try to get from query params
            hostname = external_data.get("query", {}).get("hostname") if isinstance(external_data.get("query"), dict) else None
        
        if not code:
            _LOGGER.error("No authorization code found in external_data: %s", external_data)
            raise ValueError("No authorization code found in OAuth callback")
        
        # Determine region from locationid (1=US, 2=EU)
        # Default to US if locationid is not available
        region = "eu" if locationid == 2 else "us"
        
        # pCloud might require token exchange on the same endpoint as authorization
        # Try both US and EU endpoints if we don't know the region
        token_endpoints = []
        if locationid == 2:
            # EU user - use EU endpoint
            token_endpoints = ["https://eapi.pcloud.com/oauth2_token"]
        elif locationid == 1:
            # US user - use US endpoint
            token_endpoints = ["https://api.pcloud.com/oauth2_token"]
        else:
            # Unknown region - try both
            token_endpoints = [
                "https://api.pcloud.com/oauth2_token",  # US
                "https://eapi.pcloud.com/oauth2_token",  # EU
            ]
        
        # Exchange code for token
        from homeassistant.helpers.aiohttp_client import async_get_clientsession
        session = async_get_clientsession(self.hass)
        
        # Get redirect URI from the implementation
        # This should match what was used in the authorization request
        redirect_uri = self.redirect_uri
        
        # Check if external_data contains the redirect_uri that was actually used
        # Home Assistant might pass this through in the state object
        state = external_data.get("state", {})
        if isinstance(state, dict):
            actual_redirect_uri = state.get("redirect_uri")
            if actual_redirect_uri:
                redirect_uri = actual_redirect_uri
        else:
            # Also check directly in external_data
            actual_redirect_uri = external_data.get("redirect_uri")
            if actual_redirect_uri:
                redirect_uri = actual_redirect_uri
        
        # According to pCloud docs, only client_id, client_secret, and code are required
        # But some OAuth2 providers require redirect_uri to match exactly
        # Try without redirect_uri first (as per pCloud docs), then with it if that fails
        data_without_redirect = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
        }
        
        # Try token exchange on each endpoint
        last_error = None
        result = None
        
        for token_endpoint in token_endpoints:
            async with session.post(token_endpoint, data=data_without_redirect) as response:
                result = await response.json()
                
                if result.get("result") == 0:
                    # Success!
                    # Update region based on endpoint used
                    if "eapi" in token_endpoint:
                        region = "eu"
                    break
                
                last_error = result.get("error", "Unknown error")
                error_code = result.get("result")
        
        # Check if we have a successful result
        if result is not None and result.get("result") == 0:
            # Token exchange succeeded - process the result
            access_token = result.get("access_token")
            if not access_token:
                raise ValueError("No access token received")
            
            # Build token dict with required fields for Home Assistant
            token_dict = {
                "access_token": access_token,
                "token_type": "bearer",
            }
            
            # Add expires_in if provided by pCloud (OAuth2 standard)
            expires_in = result.get("expires_in")
            if expires_in:
                token_dict["expires_in"] = expires_in
            else:
                # Set a default expiration (pCloud tokens don't expire, but HA expects this)
                # Use a large value (10 years) to indicate long-lived token
                token_dict["expires_in"] = 315360000  # 10 years in seconds
            
            # Add refresh_token if provided
            refresh_token = result.get("refresh_token")
            if refresh_token:
                token_dict["refresh_token"] = refresh_token
            
            # Store additional OAuth data for use in async_oauth_create_entry
            self._oauth_data = {
                "region": region,
                "hostname": hostname,
                "locationid": locationid,
            }
            
            # Home Assistant expects just the token dict, not a dict with additional keys
            return token_dict
        
        # If we get here, token exchange failed - try with redirect_uri
        if result is None or result.get("result") != 0:
            # All endpoints failed without redirect_uri, try with redirect_uri
            if result and result.get("result") != 0:
                error_msg = last_error or result.get("error", "Unknown error")
                error_code = result.get("result") if result else None
                
                # If it failed, try with redirect_uri on all endpoints
                # Try both possible redirect URIs that might be configured
                redirect_uris_to_try = [
                    redirect_uri,  # The one from Home Assistant
                    "https://my.home-assistant.io/redirect/oauth",  # Home Assistant Cloud redirect
                    "https://my.home-assistant.io/auth/external/callback",  # Alternative callback
                ]
                
                if error_code == 2012:  # Invalid 'code' provided
                    last_error = None
                    success = False
                    
                    for uri_to_try in redirect_uris_to_try:
                        if not uri_to_try:
                            continue
                            
                        data_with_redirect = {
                            "client_id": self.client_id,
                            "client_secret": self.client_secret,
                            "code": code,
                            "redirect_uri": uri_to_try,
                        }
                        
                        for token_endpoint in token_endpoints:
                            async with session.post(token_endpoint, data=data_with_redirect) as response2:
                                result2 = await response2.json()
                                
                                if result2.get("result") == 0:
                                    # Success!
                                    result = result2
                                    success = True
                                    # Update region based on endpoint used
                                    if "eapi" in token_endpoint:
                                        region = "eu"
                                    # Process successful result
                                    access_token = result.get("access_token")
                                    if not access_token:
                                        raise ValueError("No access token received")
                                    
                                    # Build token dict with required fields for Home Assistant
                                    token_dict = {
                                        "access_token": access_token,
                                        "token_type": "bearer",
                                    }
                                    
                                    # Add expires_in if provided by pCloud (OAuth2 standard)
                                    expires_in = result.get("expires_in")
                                    if expires_in:
                                        token_dict["expires_in"] = expires_in
                                    else:
                                        # Set a default expiration (pCloud tokens don't expire, but HA expects this)
                                        # Use a large value (10 years) to indicate long-lived token
                                        token_dict["expires_in"] = 315360000  # 10 years in seconds
                                    
                                    # Add refresh_token if provided
                                    refresh_token = result.get("refresh_token")
                                    if refresh_token:
                                        token_dict["refresh_token"] = refresh_token
                                    
                                    # Store additional OAuth data for use in async_oauth_create_entry
                                    self._oauth_data = {
                                        "region": region,
                                        "hostname": hostname,
                                        "locationid": locationid,
                                    }
                                    
                                    # Home Assistant expects just the token dict, not a dict with additional keys
                                    return token_dict
                                else:
                                    last_error = result2.get("error", "Unknown error")
                                    error_code2 = result2.get("result")
                            
                            if success:
                                break
                        
                        if success:
                            break
                    
                    if not success:
                        # All redirect URIs failed
                        _LOGGER.error(
                            "Token exchange failed with all redirect URIs. Last error: %s, client_id=%s",
                            last_error,
                            self.client_id,
                        )
                        raise ValueError(f"Token exchange failed: {last_error or 'Unknown error'}")
            else:
                _LOGGER.error(
                    "Token exchange failed: code=%s, error=%s, client_id=%s",
                    error_code,
                    error_msg,
                    self.client_id,
                )
                raise ValueError(f"Token exchange failed: {error_msg}")
        
        # If we get here, all attempts failed
        raise ValueError("Token exchange failed: All attempts failed")




class PCloudConfigFlow(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Handle a config flow for pCloud Backup."""

    DOMAIN = DOMAIN
    VERSION = 2
    OPTIONS_FLOW = async_get_options_flow

    @property
    def logger(self) -> logging.Logger:
        """Return logger."""
        return _LOGGER

    @property
    def extra_authorize_data(self) -> dict[str, Any]:
        """Extra data that needs to be appended to the authorize url."""
        return {"response_type": "code"}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - redirect to OAuth2."""
        # Check if OAuth2 credentials are configured
        if not OAUTH2_CLIENT_ID or not OAUTH2_CLIENT_SECRET:
            return self.async_abort(
                reason="oauth2_not_configured",
                description_placeholders={
                    "error": "OAuth2 client credentials not configured. Please set OAUTH2_CLIENT_ID and OAUTH2_CLIENT_SECRET in const.py"
                },
            )

        # Set up OAuth2 implementation
        self.flow_impl = PCloudOAuth2Implementation(self.hass)
        
        # Use the OAuth2 flow - directly go to auth step since we only have one implementation
        return await self.async_step_auth()

    async def async_step_pick_implementation(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the step to pick implementation."""
        # Only OAuth2 is supported, so directly go to auth step
        if not hasattr(self, "flow_impl") or self.flow_impl is None:
            self.flow_impl = PCloudOAuth2Implementation(self.hass)
        return await self.async_step_auth()

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> FlowResult:
        """Create an entry for the flow."""
        # Store OAuth data for use in the folder path step
        oauth_data = getattr(self.flow_impl, "_oauth_data", {})
        self._oauth_region = oauth_data.get("region", "us")
        self._oauth_hostname = oauth_data.get("hostname")
        self._oauth_locationid = oauth_data.get("locationid")
        self._oauth_data_dict = data
        
        # Go to folder path configuration step
        return await self.async_step_folder_path()
    
    async def async_step_folder_path(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure the backup folder path."""
        errors = {}
        
        if user_input is not None:
            backup_folder = user_input.get(CONF_BACKUP_FOLDER, DEFAULT_BACKUP_FOLDER)
            
            # Test connection and validate folder path
            try:
                region = getattr(self, "_oauth_region", "us")
                data = getattr(self, "_oauth_data_dict", {})
                access_token = data["token"]["access_token"]
                
                auth = create_auth(
                    hass=self.hass,
                    region=region,
                    access_token=access_token,
                )
                api = PCloudAPI(hass=self.hass, region=region, auth=auth)
                
                # Test connection
                user_info = await api.async_test_connection()
                
                # Validate folder path by trying to get folder ID
                try:
                    await api.async_get_folder_id(backup_folder)
                except Exception as folder_err:
                    errors["base"] = "invalid_folder_path"
                    _LOGGER.error("Invalid folder path: %s", folder_err)
                    await api.async_close()
                    # Continue to show form with error
                else:
                    await api.async_close()
                    
                    # Use email as unique ID
                    email = user_info.get("email", "")
                    if email:
                        await self.async_set_unique_id(email)
                        self._abort_if_unique_id_configured()
                    
                    # Create entry with folder path
                    return self.async_create_entry(
                        title=f"pCloud Backup ({region.upper()})",
                        data={
                            **data,
                            CONF_REGION: region,
                            "hostname": getattr(self, "_oauth_hostname"),
                            "locationid": getattr(self, "_oauth_locationid"),
                        },
                        options={
                            CONF_BACKUP_FOLDER: backup_folder,
                        },
                    )
            except PCloudAPIError as err:
                _LOGGER.error("Connection test failed: %s", err)
                errors["base"] = "cannot_connect"
            except Exception as err:
                _LOGGER.exception("Unexpected error during setup")
                errors["base"] = "unknown"
        
        return self.async_show_form(
            step_id="folder_path",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BACKUP_FOLDER,
                        default=DEFAULT_BACKUP_FOLDER,
                    ): str,
                }
            ),
            errors=errors,
        )

