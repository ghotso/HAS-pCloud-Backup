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

    async def async_resolve_external_data(self, external_data: dict[str, Any]) -> dict[str, Any]:
        """Resolve external data to tokens."""
        # pCloud returns locationid and hostname in the redirect
        # We need to extract these and store them
        code = external_data.get("code")
        locationid = external_data.get("locationid")
        hostname = external_data.get("hostname")
        
        # Determine region from locationid (1=US, 2=EU)
        region = "eu" if locationid == 2 else "us"
        
        # Exchange code for token
        from homeassistant.helpers.aiohttp_client import async_get_clientsession
        session = async_get_clientsession(self.hass)
        
        # Get redirect URI from the implementation
        redirect_uri = self.redirect_uri
        
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
        
        async with session.post(OAUTH2_TOKEN, data=data) as response:
            result = await response.json()
            
            if result.get("result") != 0:
                error_msg = result.get("error", "Unknown error")
                raise ValueError(f"Token exchange failed: {error_msg}")
            
            access_token = result.get("access_token")
            if not access_token:
                raise ValueError("No access token received")
            
            return {
                "token": {
                    "access_token": access_token,
                    "token_type": "bearer",
                },
                "region": region,
                "hostname": hostname,
                "locationid": locationid,
            }




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
        
        # Use the OAuth2 flow
        return await self.async_step_pick_implementation(user_input)

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> FlowResult:
        """Create an entry for the flow."""
        # Extract region from OAuth callback (locationid: 1=US, 2=EU)
        region = data.get("region", "us")
        access_token = data["token"]["access_token"]

        # Test connection and get user info
        try:
            auth = create_auth(
                hass=self.hass,
                region=region,
                access_token=access_token,
            )
            api = PCloudAPI(hass=self.hass, region=region, auth=auth)
            
            # Get user info
            user_info = await api.async_test_connection()
            await api.async_close()

            # Use email as unique ID
            email = user_info.get("email", "")
            if email:
                await self.async_set_unique_id(email)
                self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=f"pCloud Backup ({region.upper()})",
                data={
                    **data,
                    CONF_REGION: region,
                },
                options={
                    CONF_BACKUP_FOLDER: DEFAULT_BACKUP_FOLDER,
                },
            )
        except PCloudAPIError as err:
            _LOGGER.error("Connection test failed: %s", err)
            return self.async_abort(reason="cannot_connect")
        except Exception as err:
            _LOGGER.exception("Unexpected error during OAuth setup")
            return self.async_abort(reason="unknown")

