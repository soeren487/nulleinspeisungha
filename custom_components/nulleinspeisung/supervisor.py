"""Watches one DTU: stuck decision, automatic restarts and repair issues."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import dt as dt_util

from . import solar
from .const import (
    CONF_NOTIFY_TARGET,
    CONF_RESTART_WAIT,
    CONF_STALENESS_TIME,
    CONF_SUN_ANGLE,
    DEFAULT_RESTART_WAIT,
    DEFAULT_STALENESS_TIME,
    DEFAULT_SUN_ANGLE,
    DOMAIN,
)
from .dtu_client import DtuAuthError, DtuClient, DtuConnectionError
from .dtu_health import (
    Action,
    InverterObservation,
    RestartPolicy,
    StuckSettings,
    is_stuck,
)
from .dtu_models import DtuSnapshot
from .house_knowledge import HouseKnowledge

_LOGGER = logging.getLogger(__name__)

MIN_WAIT_MARGIN = 30
"""Seconds the wait after a restart exceeds the staleness time at least."""

ISSUE_UNREACHABLE = "dtu_unreachable"
ISSUE_NOT_HELPING = "restart_not_helping"

ENGLISH_TITLES = {
    ISSUE_UNREACHABLE: "DTU {dtu} unreachable",
    ISSUE_NOT_HELPING: "Restarting DTU {dtu} does not help",
}
"""Used when no translation can be loaded."""


class DtuSupervisor:
    """Owns the restart state of one DTU and carries out what health decides."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        subentry: ConfigSubentry,
        client: DtuClient,
        knowledge: HouseKnowledge | None = None,
    ) -> None:
        """Create the supervisor for the DTU described by ``subentry``."""
        self._hass = hass
        self._entry = entry
        self._subentry = subentry
        self._client = client
        self._knowledge = knowledge or HouseKnowledge((), {})
        data = subentry.data
        self._settings = StuckSettings(
            sun_angle=float(data.get(CONF_SUN_ANGLE, DEFAULT_SUN_ANGLE)),
            staleness_time=float(data.get(CONF_STALENESS_TIME, DEFAULT_STALENESS_TIME)),
        )
        # The outcome of a restart can only be judged once the data of a
        # restarted DTU has had time to go stale again.
        wait = max(
            float(data.get(CONF_RESTART_WAIT, DEFAULT_RESTART_WAIT)),
            self._settings.staleness_time + MIN_WAIT_MARGIN,
        )
        self._policy = RestartPolicy(timedelta(seconds=wait))
        self._last_answer = dt_util.utcnow()
        self._armed = False
        self.automatic_restart = True
        self.stuck: bool | None = None
        """Latest stuck decision; ``None`` while the DTU does not answer."""
        self.restart_count = 0
        self.last_restart: datetime | None = None

    @property
    def staleness_time(self) -> float:
        """Seconds after which Inverter data of this DTU counts as stale."""
        return self._settings.staleness_time

    def arm(self) -> None:
        """Allow automatic restarts, once the owner's switch state is restored."""
        self._armed = True

    async def async_observe(self, snapshot: DtuSnapshot) -> None:
        """Judge a snapshot the DTU just delivered and act on it."""
        now = dt_util.utcnow()
        self._last_answer = now
        self._resolve(ISSUE_UNREACHABLE)
        looks_stuck = is_stuck(
            [
                InverterObservation(
                    i.data_age, is_pv=not self._knowledge.is_battery_backed(i.serial)
                )
                for i in snapshot.inverters.values()
            ],
            solar.sun_elevation(self._hass),
            self._settings,
            other_dtu_producing=self._knowledge.other_dtu_producing(
                self._subentry.subentry_id, snapshot.inverters
            ),
        )
        verdict = self._policy.evaluate(now, looks_stuck)
        if verdict.action is Action.RESTART and self.automatic_restart and self._armed:
            await self.async_restart()
        self.stuck = looks_stuck or self._policy.holding_stuck(now)
        if verdict.not_helping:
            self._raise(ISSUE_NOT_HELPING)
        else:
            self._resolve(ISSUE_NOT_HELPING)

    def observe_silence(self) -> None:
        """Note that the DTU did not answer. It is never restarted then."""
        self.stuck = None
        silent = dt_util.utcnow() - self._last_answer
        if silent > timedelta(seconds=self._settings.staleness_time):
            self._raise(ISSUE_UNREACHABLE)

    async def async_restart(self) -> None:
        """Restart the DTU now, for whatever reason, and remember it."""
        now = dt_util.utcnow()
        self._policy.record_restart(now)
        try:
            await self._client.async_restart()
        except (DtuConnectionError, DtuAuthError) as err:
            _LOGGER.warning("Restarting DTU %s failed: %s", self._subentry.title, err)
            return
        self.restart_count += 1
        self.last_restart = now
        _LOGGER.info("Restarted DTU %s", self._subentry.title)

    def _issue_id(self, kind: str) -> str:
        return f"{kind}_{self._subentry.unique_id}"

    def _resolve(self, kind: str) -> None:
        ir.async_delete_issue(self._hass, DOMAIN, self._issue_id(kind))

    def _raise(self, kind: str) -> None:
        """Raise a repair issue, and tell the notification target once."""
        issue_id = self._issue_id(kind)
        if ir.async_get(self._hass).async_get_issue(DOMAIN, issue_id) is not None:
            return
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=kind,
            translation_placeholders={"dtu": self._subentry.title},
        )
        target = self._entry.options.get(CONF_NOTIFY_TARGET)
        if target:
            self._hass.async_create_task(self._async_notify(target, kind))

    async def _async_notify(self, target: str, kind: str) -> None:
        """Send the translated title of an issue to the notification target."""
        title = f"component.{DOMAIN}.issues.{kind}.title"
        translations = await async_get_translations(
            self._hass, self._hass.config.language, "issues", [DOMAIN]
        )
        text = translations.get(title, ENGLISH_TITLES[kind]).format(
            dtu=self._subentry.title
        )
        try:
            await self._hass.services.async_call(
                "notify",
                "send_message",
                {"entity_id": target, "message": text},
                blocking=True,
            )
        except Exception:
            _LOGGER.exception("Sending the notification failed")
