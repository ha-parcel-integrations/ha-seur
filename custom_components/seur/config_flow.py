"""Config flow for the SEUR parcel tracker integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    SEURApiClient,
    SEURApiError,
    SEURAuthError,
)
from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    DEFAULT_INCLUDE_HISTORY,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_USER_SCHEMA = vol.Schema(
    {vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str}
)


class SEURConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI-driven configuration flow for the SEUR integration."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> SEUROptionsFlowHandler:
        """Return the options flow handler."""
        return SEUROptionsFlowHandler()

    async def _validate(self, email: str, password: str) -> None:
        """Validate credentials against the live API.

        Uses the HA-managed session: this is a one-shot check, so it does not
        need the per-entry cookie jar that ``__init__.py`` sets up.
        """
        client = SEURApiClient(email, password, async_get_clientsession(self.hass))
        await client.async_login()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the credential form and validate on submit."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            try:
                await self._validate(email, user_input[CONF_PASSWORD])
            except SEURAuthError:
                errors["base"] = "invalid_auth"
            except SEURApiError as err:
                _LOGGER.warning(
                    "SEUR sign-in validation failed: %s (HTTP status=%s, JSON keys=%s)",
                    err.detail,
                    err.status_code,
                    err.response_keys,
                )
                errors["base"] = "cannot_connect"
            except aiohttp.ClientError as err:
                _LOGGER.warning(
                    "SEUR sign-in connection failed: %s", type(err).__name__
                )
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(email.casefold())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=email,
                    data=dict(user_input),
                    options={
                        CONF_DELIVERED_FILTER_TYPE: DEFAULT_DELIVERED_FILTER_TYPE,
                        CONF_DELIVERED_FILTER_AMOUNT: DEFAULT_DELIVERED_FILTER_AMOUNT,
                        CONF_INCLUDE_HISTORY: DEFAULT_INCLUDE_HISTORY,
                    },
                )

        return self.async_show_form(
            step_id="user", data_schema=_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauth after the credentials stopped working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for fresh credentials and update the existing entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            try:
                await self._validate(email, user_input[CONF_PASSWORD])
            except SEURAuthError:
                errors["base"] = "invalid_auth"
            except SEURApiError as err:
                _LOGGER.warning(
                    "SEUR reauthentication validation failed: %s (HTTP status=%s, JSON keys=%s)",
                    err.detail,
                    err.status_code,
                    err.response_keys,
                )
                errors["base"] = "cannot_connect"
            except aiohttp.ClientError as err:
                _LOGGER.warning(
                    "SEUR reauthentication connection failed: %s", type(err).__name__
                )
                errors["base"] = "cannot_connect"
            else:
                # Entering *another* account's credentials must abort rather
                # than silently rebind this entry to a different account.
                await self.async_set_unique_id(email.casefold())
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates=dict(user_input)
                )

        return self.async_show_form(
            step_id="reauth_confirm", data_schema=_USER_SCHEMA, errors=errors
        )


class SEUROptionsFlowHandler(OptionsFlow):
    """Manage delivered retention, history and polling in one sectioned form."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and handle the single sectioned options form."""
        if user_input is not None:
            delivered = user_input["delivered"]
            history = user_input["history"]
            # Reload so a changed history/delivered-retention setting takes
            # effect immediately. No update listener is registered —
            # combining the two is deprecated.
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
            return self.async_create_entry(
                title="",
                data={
                    CONF_DELIVERED_FILTER_TYPE: delivered[CONF_DELIVERED_FILTER_TYPE],
                    CONF_DELIVERED_FILTER_AMOUNT: int(
                        delivered[CONF_DELIVERED_FILTER_AMOUNT]
                    ),
                    CONF_INCLUDE_HISTORY: bool(history[CONF_INCLUDE_HISTORY]),
                },
            )

        current = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required("delivered"): section(
                    vol.Schema(
                        {
                            vol.Required(
                                CONF_DELIVERED_FILTER_TYPE,
                                default=current.get(
                                    CONF_DELIVERED_FILTER_TYPE,
                                    DEFAULT_DELIVERED_FILTER_TYPE,
                                ),
                            ): selector.SelectSelector(
                                selector.SelectSelectorConfig(
                                    options=["days", "parcels"],
                                    translation_key=CONF_DELIVERED_FILTER_TYPE,
                                    mode=selector.SelectSelectorMode.LIST,
                                )
                            ),
                            vol.Required(
                                CONF_DELIVERED_FILTER_AMOUNT,
                                default=current.get(
                                    CONF_DELIVERED_FILTER_AMOUNT,
                                    DEFAULT_DELIVERED_FILTER_AMOUNT,
                                ),
                            ): selector.NumberSelector(
                                selector.NumberSelectorConfig(
                                    min=1,
                                    max=365,
                                    step=1,
                                    mode=selector.NumberSelectorMode.BOX,
                                )
                            ),
                        }
                    ),
                    {"collapsed": False},
                ),
                vol.Required("history"): section(
                    vol.Schema(
                        {
                            vol.Required(
                                CONF_INCLUDE_HISTORY,
                                default=current.get(
                                    CONF_INCLUDE_HISTORY, DEFAULT_INCLUDE_HISTORY
                                ),
                            ): selector.BooleanSelector(),
                        }
                    ),
                    {"collapsed": True},
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
