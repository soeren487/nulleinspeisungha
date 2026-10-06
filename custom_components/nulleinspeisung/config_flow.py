"""Config flow for the Nulleinspeisung integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    LocationSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .battery_gateway import GxConnectionError, async_probe
from .const import (
    CONF_BATTERY_BACKED,
    CONF_BATTERY_CAPACITY,
    CONF_GRID_METER,
    CONF_GRID_METER_SIGN,
    CONF_GX_HOST,
    CONF_GX_PORT,
    CONF_GX_PORTAL_ID,
    CONF_INVERTERS,
    CONF_LATITUDE,
    CONF_LOCATION,
    CONF_LONGITUDE,
    CONF_MAC,
    CONF_NAME,
    CONF_NOTIFY_TARGET,
    CONF_PASSWORD,
    CONF_RESTART_WAIT,
    CONF_STALENESS_TIME,
    CONF_SUN_ANGLE,
    CONF_TIBBER_HOME,
    CONF_TIBBER_TOKEN,
    CONF_URL,
    DEFAULT_GX_PORT,
    DEFAULT_RESTART_WAIT,
    DEFAULT_STALENESS_TIME,
    DEFAULT_SUN_ANGLE,
    DOMAIN,
    SIGN_EXPORT,
    SIGN_IMPORT,
    SUBENTRY_TYPE_DTU,
    SUBENTRY_TYPE_HOUSE,
)
from .dtu_client import (
    DtuAuthError,
    DtuClient,
    DtuConnectionError,
    normalize_base_url,
)
from .dtu_models import DtuIdentity
from .house import house_subentries, inverters_of_other_houses, known_inverters
from .price_source import (
    TibberAuthError,
    TibberConnectionError,
    TibberHome,
    async_create_client,
)

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
        vol.Optional(CONF_TIBBER_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
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
        """DTUs and Houses are added as subentries of the config entry."""
        return {
            SUBENTRY_TYPE_DTU: DtuSubentryFlow,
            SUBENTRY_TYPE_HOUSE: HouseSubentryFlow,
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the owner to confirm adding the integration."""
        if user_input is not None:
            return self.async_create_entry(title="Nulleinspeisung", data={})
        return self.async_show_form(step_id="user")


class NulleinspeisungOptionsFlow(OptionsFlow):
    """Options of the integration: notification target and Tibber token."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the notification target and the Tibber token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {k: v for k, v in user_input.items() if v not in (None, "")}
            token = str(data.get(CONF_TIBBER_TOKEN, "")).strip()
            if token:
                data[CONF_TIBBER_TOKEN] = token
                try:
                    client = await async_create_client(self.hass, token)
                    await client.async_homes()
                except TibberAuthError:
                    errors[CONF_TIBBER_TOKEN] = "invalid_token"
                except TibberConnectionError:
                    errors["base"] = "cannot_connect"
            else:
                data.pop(CONF_TIBBER_TOKEN, None)
            if not errors:
                return self.async_create_entry(data=data)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, user_input or self.config_entry.options
            ),
            errors=errors,
        )


class DtuSubentryFlow(ConfigSubentryFlow):
    """Add a DTU, or change its address and password later."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask for address and password of a new DTU."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"existing": ""}
        if user_input is not None:
            identity = await self._async_identify(user_input, errors)
            if identity is not None:
                existing = next(
                    (
                        subentry
                        for subentry in self._get_entry().subentries.values()
                        if subentry.unique_id == identity.serial
                    ),
                    None,
                )
                if existing is None:
                    return self.async_create_entry(
                        title=identity.hostname,
                        data=self._data(user_input, identity),
                        unique_id=identity.serial,
                    )
                if await self._is_other_device(existing, identity):
                    errors[CONF_URL] = "duplicate_serial"
                    placeholders["existing"] = existing.title
                else:
                    return self.async_abort(reason="already_configured")
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(DTU_SCHEMA, user_input),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def _is_other_device(
        self, existing: ConfigSubentry, identity: DtuIdentity
    ) -> bool:
        """Whether the DTU has the serial of ``existing`` but is another device.

        The hardware addresses decide. The existing DTU is asked for its own
        when none is stored (added before they were); if either is unknown the
        answer is ``False``.
        """
        if identity.mac is None:
            return False
        mac = existing.data.get(CONF_MAC)
        if mac is None:
            try:
                mac = (await self._async_client(existing.data).async_identify()).mac
            except DtuAuthError, DtuConnectionError:
                return False
        return mac is not None and mac != identity.mac

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
                    data=self._data(user_input, identity),
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
        try:
            return await self._async_client(user_input).async_identify()
        except DtuAuthError:
            errors["base"] = "invalid_auth"
        except DtuConnectionError:
            errors["base"] = "cannot_connect"
        return None

    def _async_client(self, data: Mapping[str, Any]) -> DtuClient:
        """A client for the DTU with the address and password in ``data``."""
        return DtuClient(
            async_get_clientsession(self.hass), data[CONF_URL], data[CONF_PASSWORD]
        )

    @staticmethod
    def _data(user_input: dict[str, Any], identity: DtuIdentity) -> dict[str, Any]:
        """What is stored for the DTU."""
        mac = {CONF_MAC: identity.mac} if identity.mac else {}
        return {
            **mac,
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


def _inverter_selector(options: dict[str, str]) -> SelectSelector:
    """A multi-select over Inverters, labelled for the owner."""
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                SelectOptionDict(value=serial, label=label)
                for serial, label in options.items()
            ],
            multiple=True,
            mode=SelectSelectorMode.LIST,
        )
    )


NO_TIBBER_HOME = "none"
"""Choice meaning that the House has no Tibber home."""


class HouseSubentryFlow(ConfigSubentryFlow):
    """Add a House, or change everything about it later.

    Four steps: the House itself, its Inverters, which of those are
    Battery-backed (skipped when there are none), and the AC Battery.
    """

    def __init__(self) -> None:
        """Start with nothing collected."""
        super().__init__()
        self._collected: dict[str, Any] = {}
        self._title = ""
        self._homes: list[TibberHome] | None = None
        self._homes_fetched = False

    @property
    def _is_reconfigure(self) -> bool:
        """Whether an existing House is being changed."""
        return self.source == SOURCE_RECONFIGURE

    def _current(self) -> dict[str, Any]:
        """The stored data of the House being reconfigured, else nothing."""
        return (
            dict(self._get_reconfigure_subentry().data) if self._is_reconfigure else {}
        )

    def _own_id(self) -> str | None:
        """Subentry id of the House being reconfigured."""
        return (
            self._get_reconfigure_subentry().subentry_id
            if self._is_reconfigure
            else None
        )

    async def _async_tibber_homes(self) -> list[TibberHome] | None:
        """The account's Tibber homes; ``None`` without a token or when unreachable."""
        if not self._homes_fetched:
            self._homes_fetched = True
            token = self._get_entry().options.get(CONF_TIBBER_TOKEN)
            if token:
                try:
                    client = await async_create_client(self.hass, token)
                    self._homes = await client.async_homes()
                except TibberAuthError, TibberConnectionError:
                    self._homes = None
        return self._homes

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask for name, location and Grid Meter of a new House."""
        return await self._async_step_house("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change name, location and Grid Meter of an existing House."""
        return await self._async_step_house("reconfigure", user_input)

    async def _async_step_house(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        """Step one: the House itself."""
        errors: dict[str, str] = {}
        homes = await self._async_tibber_homes()
        if user_input is not None:
            name = str(user_input[CONF_NAME]).strip()
            others = {
                s.title.casefold()
                for s in house_subentries(self._get_entry())
                if s.subentry_id != self._own_id()
            }
            if not name:
                errors[CONF_NAME] = "name_required"
            elif name.casefold() in others:
                errors[CONF_NAME] = "name_exists"
            elif (
                chosen_home := self._chosen_home(user_input, homes)
            ) and chosen_home in self._homes_of_other_houses():
                errors[CONF_TIBBER_HOME] = "tibber_home_assigned"
            else:
                self._title = name
                self._collected = {
                    CONF_LATITUDE: float(user_input[CONF_LOCATION][CONF_LATITUDE]),
                    CONF_LONGITUDE: float(user_input[CONF_LOCATION][CONF_LONGITUDE]),
                    CONF_GRID_METER: user_input[CONF_GRID_METER],
                    CONF_GRID_METER_SIGN: user_input[CONF_GRID_METER_SIGN],
                }
                if chosen_home:
                    self._collected[CONF_TIBBER_HOME] = chosen_home
                return await self.async_step_inverters()

        current = self._current()
        values = user_input or {
            CONF_NAME: self._get_reconfigure_subentry().title
            if self._is_reconfigure
            else "",
            CONF_LOCATION: {
                CONF_LATITUDE: current.get(CONF_LATITUDE, self.hass.config.latitude),
                CONF_LONGITUDE: current.get(CONF_LONGITUDE, self.hass.config.longitude),
            },
            CONF_GRID_METER: current.get(CONF_GRID_METER),
            CONF_GRID_METER_SIGN: current.get(CONF_GRID_METER_SIGN, SIGN_IMPORT),
            CONF_TIBBER_HOME: current.get(CONF_TIBBER_HOME) or NO_TIBBER_HOME,
        }
        schema: dict[Any, Any] = {
            vol.Required(CONF_NAME, default=values[CONF_NAME]): TextSelector(),
            vol.Required(CONF_LOCATION, default=values[CONF_LOCATION]): (
                LocationSelector()
            ),
            vol.Required(
                CONF_GRID_METER,
                **(
                    {"default": values[CONF_GRID_METER]}
                    if values[CONF_GRID_METER]
                    else {}
                ),
            ): EntitySelector(
                EntitySelectorConfig(domain="sensor", device_class="power")
            ),
            vol.Required(
                CONF_GRID_METER_SIGN, default=values[CONF_GRID_METER_SIGN]
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[SIGN_IMPORT, SIGN_EXPORT],
                    translation_key="grid_meter_sign",
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
        }
        if homes is not None:
            schema[vol.Optional(CONF_TIBBER_HOME, default=values[CONF_TIBBER_HOME])] = (
                SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=NO_TIBBER_HOME, label="None"),
                            *(
                                SelectOptionDict(value=home.id, label=home.nickname)
                                for home in homes
                            ),
                        ],
                        translation_key="tibber_home",
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
            )
        return self.async_show_form(
            step_id=step_id, data_schema=vol.Schema(schema), errors=errors
        )

    def _chosen_home(
        self, user_input: dict[str, Any], homes: list[TibberHome] | None
    ) -> str | None:
        """The Tibber home to store: the owner's choice, else the stored one.

        Without the select (no token, or Tibber unreachable) the House keeps
        the home it has.
        """
        if homes is None:
            return self._current().get(CONF_TIBBER_HOME) or None
        chosen = user_input.get(CONF_TIBBER_HOME, NO_TIBBER_HOME)
        return None if chosen == NO_TIBBER_HOME else chosen

    def _homes_of_other_houses(self) -> set[str]:
        """Tibber homes used by any House except the one being edited."""
        return {
            home
            for subentry in house_subentries(self._get_entry())
            if subentry.subentry_id != self._own_id()
            if (home := subentry.data.get(CONF_TIBBER_HOME))
        }

    def _offered(self) -> dict[str, str]:
        """Inverters this House may take, serial to label.

        Every Inverter any DTU knows, without those of other Houses, plus the
        ones already assigned here even when their DTU is currently unknown.
        """
        taken = inverters_of_other_houses(self._get_entry(), self._own_id())
        known = known_inverters(self.hass, self._get_entry())
        offered = {s: label for s, label in known.items() if s not in taken}
        for serial in self._current().get(CONF_INVERTERS, ()):
            offered.setdefault(serial, serial)
        return offered

    async def async_step_inverters(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Step two: which Inverters belong to the House."""
        errors: dict[str, str] = {}
        offered = self._offered()
        if user_input is not None:
            chosen = list(dict.fromkeys(user_input[CONF_INVERTERS]))
            taken = inverters_of_other_houses(self._get_entry(), self._own_id())
            if taken.intersection(chosen):
                errors["base"] = "inverter_assigned"
            else:
                self._collected[CONF_INVERTERS] = chosen
                if not chosen:
                    self._collected[CONF_BATTERY_BACKED] = []
                    return await self.async_step_ac_battery()
                return await self.async_step_battery_backed()
        default = (
            user_input[CONF_INVERTERS]
            if user_input
            else self._current().get(CONF_INVERTERS, [])
        )
        return self.async_show_form(
            step_id="inverters",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_INVERTERS,
                        default=[s for s in default if s in offered],
                    ): _inverter_selector(offered)
                }
            ),
            errors=errors,
        )

    async def async_step_battery_backed(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Step three: which of the chosen Inverters are Battery-backed."""
        chosen = self._collected[CONF_INVERTERS]
        known = known_inverters(self.hass, self._get_entry())
        options = {serial: known.get(serial, serial) for serial in chosen}
        if user_input is not None:
            self._collected[CONF_BATTERY_BACKED] = [
                s for s in chosen if s in user_input[CONF_BATTERY_BACKED]
            ]
            return await self.async_step_ac_battery()
        previous = self._current().get(CONF_BATTERY_BACKED, [])
        return self.async_show_form(
            step_id="battery_backed",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BATTERY_BACKED,
                        default=[s for s in previous if s in options],
                    ): _inverter_selector(options)
                }
            ),
        )

    async def async_step_ac_battery(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Step four: the GX of the House's AC Battery, if it has one."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = str(user_input.get(CONF_GX_HOST) or "").strip()
            if not host:
                return self._async_finish()
            port = int(user_input.get(CONF_GX_PORT, DEFAULT_GX_PORT))
            try:
                portal_id = await async_probe(host, port)
            except GxConnectionError:
                errors[CONF_GX_HOST] = "cannot_connect"
            else:
                if portal_id in self._portal_ids_of_other_houses():
                    errors[CONF_GX_HOST] = "gx_assigned"
                else:
                    self._collected[CONF_GX_HOST] = host
                    self._collected[CONF_GX_PORT] = port
                    self._collected[CONF_GX_PORTAL_ID] = portal_id
                    capacity = user_input.get(CONF_BATTERY_CAPACITY)
                    if capacity is not None:
                        self._collected[CONF_BATTERY_CAPACITY] = float(capacity)
                    return self._async_finish()
        values = user_input or self._current()
        suggested: dict[Any, Any] = {}
        for key in (CONF_GX_HOST, CONF_BATTERY_CAPACITY):
            if values.get(key) is not None:
                suggested[key] = {"description": {"suggested_value": values[key]}}
        return self.async_show_form(
            step_id="ac_battery",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_GX_HOST, **suggested.get(CONF_GX_HOST, {})
                    ): TextSelector(),
                    vol.Optional(
                        CONF_GX_PORT,
                        default=int(values.get(CONF_GX_PORT, DEFAULT_GX_PORT)),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1,
                            max=65535,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_BATTERY_CAPACITY,
                        **suggested.get(CONF_BATTERY_CAPACITY, {}),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0.1,
                            max=1000,
                            step=0.1,
                            unit_of_measurement="kWh",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    def _portal_ids_of_other_houses(self) -> set[str]:
        """Portal ids of the GXes used by any House except the one being edited."""
        return {
            portal_id
            for subentry in house_subentries(self._get_entry())
            if subentry.subentry_id != self._own_id()
            if (portal_id := subentry.data.get(CONF_GX_PORTAL_ID))
        }

    def _async_finish(self) -> SubentryFlowResult:
        """Store the House."""
        if self._is_reconfigure:
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                title=self._title,
                data=self._collected,
            )
        return self.async_create_entry(
            title=self._title, data=self._collected, unique_id=uuid4().hex
        )
