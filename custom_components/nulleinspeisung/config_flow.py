"""Config flow for the Nulleinspeisung integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_NOTIFY_TARGET,
    CONF_PASSWORD,
    CONF_RESTART_WAIT,
    CONF_STALENESS_TIME,
    CONF_SUN_ANGLE,
    CONF_URL,
    DEFAULT_RESTART_WAIT,
    DEFAULT_STALENESS_TIME,
    DEFAULT_SUN_ANGLE,
    DOMAIN,
    SUBENTRY_TYPE_DTU,
)
from .dtu_client import (
    DtuAuthError,
    DtuClient,
    DtuConnectionError,
    normalize_base_url,
)
from .dtu_models import DtuIdentity

DTU_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL)
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Optional(CONF_SUN_ANGLE, default=DEFAULT_SUN_ANGLE): NumberSelector(
            NumberSelectorConfig(
                min=-10,
                max=45,
                step=0.5,
                unit_of_measurement="°",
                mode=NumberSelectorMode.BOX,
            )
        ),
        vol.Optional(
            CONF_STALENESS_TIME, default=DEFAULT_STALENESS_TIME
        ): NumberSelector(
            NumberSelectorConfig(
                min=30,
                max=3600,
                step=1,
                unit_of_measurement="s",
                mode=NumberSelectorMode.BOX,
            )
        ),
        vol.Optional(CONF_RESTART_WAIT, default=DEFAULT_RESTART_WAIT): NumberSelector(
            NumberSelectorConfig(
                min=60,
                max=3600,
                step=1,
                unit_of_measurement="s",
                mode=NumberSelectorMode.BOX,
            )
        ),
    }
)

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_NOTIFY_TARGET): EntitySelector(
            EntitySelectorConfig(domain="notify")
        ),
    }
)


class NulleinspeisungConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add the single Nulleinspeisung config entry.

    A second attempt is refused by Home Assistant because the manifest sets
    ``single_config_entry``.
    """

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """The owner can choose where repair issues are also sent."""
        return NulleinspeisungOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """A DTU is added as a subentry of the config entry."""
        return {SUBENTRY_TYPE_DTU: DtuSubentryFlow}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the owner to confirm adding the integration."""
        if user_input is not None:
            return self.async_create_entry(title="Nulleinspeisung", data={})
        return self.async_show_form(step_id="user")


class NulleinspeisungOptionsFlow(OptionsFlow):
    """Options of the integration: the optional notification target."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the notification target."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, self.config_entry.options
            ),
        )


class DtuSubentryFlow(ConfigSubentryFlow):
    """Add a DTU, or change its address and password later."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask for address and password of a new DTU."""
        errors: dict[str, str] = {}
        if user_input is not None:
            identity = await self._async_identify(user_input, errors)
            if identity is not None:
                if any(
                    subentry.unique_id == identity.serial
                    for subentry in self._get_entry().subentries.values()
                ):
                    return self.async_abort(reason="already_configured")
                return self.async_create_entry(
                    title=identity.hostname,
                    data=self._data(user_input),
                    unique_id=identity.serial,
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(DTU_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change address and password of an existing DTU."""
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            identity = await self._async_identify(user_input, errors)
            if identity is not None:
                if identity.serial != subentry.unique_id:
                    return self.async_abort(reason="wrong_dtu")
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    title=identity.hostname,
                    data=self._data(user_input),
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                DTU_SCHEMA,
                user_input
                or {
                    key: value
                    for key, value in subentry.data.items()
                    if key != CONF_PASSWORD
                },
            ),
            errors=errors,
        )

    async def _async_identify(
        self, user_input: dict[str, Any], errors: dict[str, str]
    ) -> DtuIdentity | None:
        """Ask the DTU who it is; on failure put the reason into ``errors``."""
        client = DtuClient(
            async_get_clientsession(self.hass),
            user_input[CONF_URL],
            user_input[CONF_PASSWORD],
        )
        try:
            return await client.async_identify()
        except DtuAuthError:
            errors["base"] = "invalid_auth"
        except DtuConnectionError:
            errors["base"] = "cannot_connect"
        return None

    @staticmethod
    def _data(user_input: dict[str, Any]) -> dict[str, Any]:
        """What is stored for the DTU."""
        return {
            CONF_URL: normalize_base_url(user_input[CONF_URL]),
            CONF_PASSWORD: user_input[CONF_PASSWORD],
            CONF_SUN_ANGLE: float(user_input.get(CONF_SUN_ANGLE, DEFAULT_SUN_ANGLE)),
            CONF_STALENESS_TIME: float(
                user_input.get(CONF_STALENESS_TIME, DEFAULT_STALENESS_TIME)
            ),
            CONF_RESTART_WAIT: float(
                user_input.get(CONF_RESTART_WAIT, DEFAULT_RESTART_WAIT)
            ),
        }
