"""Representation of a WeMo Dimmer device."""

from __future__ import annotations

from .api.long_press import LongPressMixin
from .api.service import RequiredService
from .switch import Switch


class Dimmer(Switch):
    """Representation of a WeMo Dimmer device."""

    _brightness: int | None = None

    @property
    def _required_services(self) -> list[RequiredService]:
        return super()._required_services + [
            RequiredService(name="basicevent", actions=["SetBinaryState"]),
        ]

    def get_brightness(self, force_update: bool = False) -> int:
        """Get brightness from device."""
        self.get_state(force_update)
        assert self._brightness is not None
        return self._brightness

    def set_brightness(self, brightness: int) -> None:
        """Set the brightness of this device to an integer between 1-100."""
        # WeMo only supports values between 1-100. WeMo will ignore a 0
        # brightness value. If 0 is requested, then turn the light off instead.
        if brightness:
            self.basicevent.SetBinaryState(
                BinaryState=1, brightness=brightness
            )
            self._state = 1
            self._brightness = brightness
        else:
            self.off()

    def get_state(self, force_update: bool = False) -> int:
        """Update the state & brightness for the Dimmer."""
        force_update = force_update or (
            self._brightness is None
            and "brightness" not in self.basic_state_params
        )
        state = super().get_state(force_update)
        if force_update or self._brightness is None:
            try:
                brightness = int(self.basic_state_params.get("brightness", 0))
            except ValueError:
                brightness = 0
            self._brightness = brightness
        return state

    def subscription_update(self, _type: str, _param: str) -> bool:
        """Update the dimmer attributes due to a subscription update event."""
        if _type == "Brightness":
            try:
                self._brightness = int(_param)
            except ValueError:
                return False
            return True
        return super().subscription_update(_type, _param)


class DimmerLongPress(Dimmer, LongPressMixin):
    """WeMo Dimmer device that supports long press."""


class DimmerV2(Dimmer):
    """WeMo Dimmer version 2."""
