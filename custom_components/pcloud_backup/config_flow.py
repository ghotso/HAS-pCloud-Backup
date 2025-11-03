"""Config flow for pCloud Backup integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .api import PCloudAPI, PCloudAPIError
from .auth import create_auth
from .const import (
    CONF_BACKUP_FOLDER,
    CONF_PASSWORD,
    CONF_REGION,
    CONF_RETENTION_COUNT,
    CONF_RETENTION_DAYS,
    CONF_USERNAME,
    DEFAULT_BACKUP_FOLDER,
    DEFAULT_RETENTION_COUNT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


@callback
def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
    """Get the options flow for this handler."""
    return PCloudOptionsFlowHandler(config_entry)


class PCloudConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for pCloud Backup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate input
            region = user_input[CONF_REGION]
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            # Test connection
            try:
                auth = create_auth(
                    hass=self.hass,
                    region=region,
                    username=username,
                    password=password,
                )
                api = PCloudAPI(hass=self.hass, region=region, auth=auth)
                await api.async_test_connection()
                await api.async_close()

                # Check if already configured
                await self.async_set_unique_id(username)
                self._abort_if_unique_id_configured()

                # Create entry
                return self.async_create_entry(
                    title=f"pCloud Backup ({region.upper()})",
                    data={
                        CONF_REGION: region,
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                    },
                    options={
                        CONF_BACKUP_FOLDER: DEFAULT_BACKUP_FOLDER,
                        CONF_RETENTION_COUNT: DEFAULT_RETENTION_COUNT,
                        CONF_RETENTION_DAYS: None,
                    },
                )

            except PCloudAPIError as err:
                _LOGGER.error("Connection test failed: %s", err)
                errors["base"] = "cannot_connect"
            except ValueError as err:
                _LOGGER.error("Authentication failed: %s", err)
                errors["base"] = "invalid_auth"
            except Exception as err:
                _LOGGER.exception("Unexpected error during connection test")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_REGION, default="us"): vol.In(
                        {
                            "us": "United States (US)",
                            "eu": "Europe (EU)",
                        }
                    ),
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )


# OAuth2 Config Flow (for future use when pCloud fixes their portal)
# To switch back to OAuth2:
# 1. Set USE_OAUTH2 = True in auth.py
# 2. Uncomment the code below and comment out PCloudConfigFlow above
# 3. Update __init__.py to use OAuth2 flow handler

"""
from homeassistant.helpers import config_entry_oauth2_flow


class PCloudOAuth2FlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    \"\"\"Handle a config flow for pCloud Backup (OAuth2 version).\"\"\"

    DOMAIN = DOMAIN
    VERSION = 1
    OPTIONS_FLOW = async_get_options_flow

    def __init__(self) -> None:
        \"\"\"Initialize the config flow.\"\"\"
        super().__init__()
        self._region: str | None = None

    @property
    def logger(self) -> logging.Logger:
        \"\"\"Return logger.\"\"\"
        return _LOGGER

    @property
    def extra_authorize_data(self) -> dict[str, Any]:
        \"\"\"Extra data that needs to be appended to the authorize url.\"\"\"
        return {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        \"\"\"Handle the initial step.\"\"\"
        if user_input is not None:
            self._region = user_input[CONF_REGION]
            return await self.async_step_pick_implementation()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_REGION, default="us"): vol.In(
                        {
                            "us": "United States (US)",
                            "eu": "Europe (EU)",
                        }
                    ),
                }
            ),
        )

    async def async_step_pick_implementation(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        \"\"\"Handle the picking of implementation.\"\"\"
        return await self.async_step_user_oauth2()

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> FlowResult:
        \"\"\"Create an entry for the flow.\"\"\"
        region = self._region or "us"

        try:
            auth = create_auth(
                hass=self.hass,
                region=region,
                access_token=data["token"]["access_token"],
            )
            api = PCloudAPI(hass=self.hass, region=region, auth=auth)
            await api.async_test_connection()
            await api.async_close()

            return self.async_create_entry(
                title=f"pCloud Backup ({region.upper()})",
                data={
                    CONF_REGION: region,
                    **data,
                },
                options={
                    CONF_BACKUP_FOLDER: DEFAULT_BACKUP_FOLDER,
                    CONF_RETENTION_COUNT: DEFAULT_RETENTION_COUNT,
                    CONF_RETENTION_DAYS: None,
                },
            )
        except Exception as err:
            _LOGGER.exception("Unexpected error during connection test")
            return self.async_abort(reason="cannot_connect")
"""


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
                    vol.Optional(
                        CONF_RETENTION_COUNT,
                        default=options.get(CONF_RETENTION_COUNT, DEFAULT_RETENTION_COUNT),
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
                    vol.Optional(
                        CONF_RETENTION_DAYS,
                        default=options.get(CONF_RETENTION_DAYS),
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=365)),
                }
            ),
        )
