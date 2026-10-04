"""Keeps the device registry in step with what a DTU reports."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .coordinator import DtuCoordinator
from .entity import dtu_device_info


class DeviceSynchroniser:
    """Coordinator listener that registers the DTU device and updates models.

    The DTU device is written only when its hostname, firmware version or chip
    model changed. An Inverter's model is written once it becomes known, which
    can be long after the device was created.
    """

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, coordinator: DtuCoordinator
    ) -> None:
        """Create the synchroniser for one DTU."""
        self._hass = hass
        self._entry = entry
        self._coordinator = coordinator
        self._dtu_state: tuple[str, str, str] | None = None
        self._models: dict[str, str] = {}

    def __call__(self) -> None:
        """Bring the registry up to date with the latest snapshot."""
        snapshot = self._coordinator.data
        if snapshot is None:
            return
        registry = dr.async_get(self._hass)

        state = (snapshot.hostname, snapshot.firmware_version, snapshot.chip_model)
        if state != self._dtu_state:
            registry.async_get_or_create(
                config_entry_id=self._entry.entry_id,
                config_subentry_id=self._coordinator.subentry.subentry_id,
                **dtu_device_info(self._coordinator),
            )
            self._dtu_state = state

        for serial, inverter in snapshot.inverters.items():
            if inverter.model is None or self._models.get(serial) == inverter.model:
                continue
            self._models[serial] = inverter.model
            device = registry.async_get_device_by_identifier(
                (DOMAIN, serial), self._entry.entry_id
            )
            if device is not None and device.model != inverter.model:
                registry.async_update_device(device.id, model=inverter.model)
