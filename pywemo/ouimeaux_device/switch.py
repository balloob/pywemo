"""Representation of a WeMo Switch device."""

from __future__ import annotations

from . import Device
from .api.service import RequiredService


class Switch(Device):
    """Representation of a WeMo Switch device."""

    @property
    def _required_services(self) -> list[RequiredService]:
        return super()._required_services + [
            RequiredService(name="basicevent", actions=["SetBinaryState"]),
        ]

    def set_state(self, state: int) -> None:
        """Set the state of this device to on or off."""
        self.basicevent.SetBinaryState(BinaryState=int(state))
        self._state = int(state)

    def off(self) -> None:
        """Turn this device off. If already off, will return "Error"."""
        self.set_state(0)

    def on(self) -> None:  # pylint: disable=invalid-name
        """Turn this device on. If already on, will return "Error"."""
        self.set_state(1)

    def toggle(self) -> None:
        """Toggle the switch's state."""
        self.set_state(not self.get_state())
