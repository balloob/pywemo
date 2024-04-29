"""Representation of a WeMo Maker device."""

from __future__ import annotations

from typing import TypedDict

from .api.attributes import AttributeDevice
from .api.service import RequiredService


class _Attributes(TypedDict, total=False):
    Switch: int
    Sensor: int
    SwitchMode: int
    SensorPresent: int


class Maker(AttributeDevice):
    """Representation of a WeMo Maker device."""

    _state_property = "switch_state"  # Required by AttributeDevice.
    _attributes: _Attributes  # Required by AttributeDevice.

    @property
    def _required_services(self) -> list[RequiredService]:
        return super()._required_services + [
            RequiredService(name="basicevent", actions=["SetBinaryState"]),
        ]

    def set_state(self, state: int) -> None:
        """Set the state of this device to on or off."""
        # The Maker has a momentary mode - so it's not safe to assume
        # the state is what you just set, so re-read it from the device
        self.basicevent.SetBinaryState(BinaryState=int(state))
        self.get_state(True)

    @property
    def switch_state(self) -> int:
        """Return the state of the switch."""
        return self._attributes["Switch"]

    @property
    def sensor_state(self) -> int:
        """Return the state of the sensor."""
        return self._attributes["Sensor"]

    @property
    def switch_mode(self) -> int:
        """Return the switch mode of the sensor."""
        return self._attributes["SwitchMode"]

    @property
    def has_sensor(self) -> int:
        """Return whether the device has a sensor."""
        return self._attributes["SensorPresent"]
