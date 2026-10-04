"""Polling of one DTU."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, DTU_UPDATE_INTERVAL
from .dtu_client import DtuClient, DtuConnectionError
from .dtu_models import DtuSnapshot
from .supervisor import DtuSupervisor

_LOGGER = logging.getLogger(__name__)


class DtuCoordinator(DataUpdateCoordinator[DtuSnapshot]):
    """Keeps the snapshot of one DTU current."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        subentry: ConfigSubentry,
        client: DtuClient,
    ) -> None:
        """Create the coordinator for the DTU described by ``subentry``."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {subentry.title}",
            update_interval=DTU_UPDATE_INTERVAL,
        )
        self.subentry = subentry
        self.client = client
        self.supervisor = DtuSupervisor(hass, entry, subentry, client)

    @property
    def dtu_serial(self) -> str:
        """Serial of the DTU, which is also the subentry's unique id."""
        assert self.subentry.unique_id is not None
        return self.subentry.unique_id

    async def _async_update_data(self) -> DtuSnapshot:
        """Fetch a fresh snapshot."""
        try:
            snapshot = await self.client.async_fetch_snapshot()
        except DtuConnectionError as err:
            self.supervisor.observe_silence()
            raise UpdateFailed(str(err)) from err
        await self.supervisor.async_observe(snapshot)
        return snapshot
