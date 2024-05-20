"""Representation of a WeMo Dimmer device."""
from __future__ import annotations

import time

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

    _end_transition_time: int | None = None
    _transition_towards_off: bool | None = None

    def get_state(self, force_update: bool = False) -> int:
        """Update the state & brightness for the Dimmer."""
        # While transitioning, the state is not certain, brightness can change
        # at any moment as well as on/off state. Force update in this case.
        force_update = force_update or self._end_transition_time is not None
        state = super().get_state(force_update)
        if (
            self._end_transition_time is not None
            and int(time.time()) > self._end_transition_time
        ):
            self._end_transition_time = None
            self._transition_towards_off = None
        return state

    def set_brightness(self, brightness: int, transition: int = 0) -> None:
        """Set the brightness of this device to an integer between 1-100."""
        # WeMo only supports values between 1-100.
        # If 0 is requested, then turn the light off instead.
        brightness = min(int(brightness), 100)
        # Cancel any ongoing transition
        if self._end_transition_time is not None:
            self.basicevent.SetBinaryState(fader="0:-1:0:0:0")
            self._end_transition_time = int(time.time())
            self._transition_towards_off = None
        if brightness > 0:
            if transition > 0:
                # If the device is in the off state, the default behavior of
                # the fader parameter is to turn on to the last brightness
                # level and then fade from there to the desired final
                # brightness. Instead, if off, turn on the device to minimum
                # brightness and fade from there.
                if not self.get_state():
                    self.basicevent.SetBinaryState(BinaryState=1, brightness=1)
                self.basicevent.SetBinaryState(
                    BinaryState=1, fader=f"{transition}:0:1:0:{brightness}"
                )
                self._end_transition_time = int(time.time()) + transition
                self._transition_towards_off = False
            else:
                self.basicevent.SetBinaryState(
                    BinaryState=1, brightness=brightness
                )
            self._state = 1
            self._brightness = brightness
        else:
            if transition > 0 and self.get_state():
                self.basicevent.SetBinaryState(
                    BinaryState=0, fader=f"{transition}:0:1:0:0"
                )
                self._end_transition_time = int(time.time()) + transition
                self._transition_towards_off = True
                self._state = 0
            else:
                super().off()

    def off(self, transition: int = 0) -> None:
        """Turn this device off."""
        self.set_brightness(0, transition)

    def on(self, transition: int = 0) -> None:  # pylint: disable=invalid-name
        """Turn this device on."""
        if transition > 0 and not self.get_state():
            self.set_brightness(self.get_brightness(), transition)
        else:
            # If we are transitioning towards off, just cancel it.
            # Seems like the most sensible thing we can do.
            if (
                self._end_transition_time is not None
                and self._transition_towards_off
            ):
                self.set_brightness(self.get_brightness(), 0)
            else:
                super().on()

    def toggle(self, transition: int = 0) -> None:
        """Toggle the switch's state."""
        if self.get_state():
            self.off(transition)
        else:
            self.on(transition)
